import io
import re
import ssl
import json
import base64
import aiohttp
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from clients.image_generation import strip_markdown_code_blocks

# Import your config
from config import Config

config = Config()

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

try:
    from clients.image_generation import generate_with_image_async
    logger.info("✅ Image generation clients loaded successfully")
except ImportError:
    logger.warning("⚠️ Image generation clients not available (optional)")
    generate_with_image_async = None

# App state for managing async resources
app_state = {
    "client_session": None,
    "semaphore": None
}


# ============================================================
# SSL CONFIGURATION
# ============================================================

def get_ssl_context():
    """
    Create SSL context for aiohttp client
    Used for communicating with Ollama when HTTPS is enabled

    Returns:
        ssl.SSLContext or None: SSL context for secure connections
    """
    if not config.USE_HTTPS or config.SSL_VERIFY:
        return None

    # For self-signed certificates, disable SSL verification
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    logger.info("✅ SSL context configured (self-signed certificates allowed)")

    return ssl_context


def get_uvicorn_ssl_config():
    """
    Prepare SSL configuration for Uvicorn server

    Returns:
        tuple: (ssl_keyfile, ssl_certfile) or (None, None) if HTTPS disabled

    Raises:
        FileNotFoundError: If certificate or key files are missing
    """
    if not config.USE_HTTPS:
        logger.info("ℹ️  HTTPS disabled - running on HTTP")
        return None, None

    cert_path = Path(config.SSL_CERT_FILE)
    key_path = Path(config.SSL_KEY_FILE)

    # Verify certificate exists
    if not cert_path.exists():
        raise FileNotFoundError(
            f"SSL certificate not found: {cert_path.absolute()}\n"
            f"Expected at: {cert_path.absolute()}"
        )

    # Verify key exists
    if not key_path.exists():
        raise FileNotFoundError(
            f"SSL key not found: {key_path.absolute()}\n"
            f"Expected at: {key_path.absolute()}"
        )

    logger.info("✅ SSL certificates validated:")
    logger.info(f"   📜 Certificate: {cert_path.absolute()}")
    logger.info(f"   🔑 Key: {key_path.absolute()}")

    return str(key_path), str(cert_path)


def clean_json_string(s):
    """Remove section headers and fix malformed JSON"""

    # Remove section headers like "A: Mother's details", "B: Labour and Birth"
    s = re.sub(r'",\s*"[A-Z]:\s+[^"]*"', '"', s)
    s = re.sub(r'",\s*"[A-Z]:\s+[^"]+"', '"', s)

    # Fix escaped quotes in values
    s = s.replace("\\'", "'")

    return s


def repair_trailing_bare_strings(json_text: str) -> str:
    """
    Repairs LLM-generated JSON where a key's value is followed by
    additional un-keyed ("bare") string literals before the next real
    "key": pair, e.g.:

        "K: Action plan": "step 1", "step 2", "step 3", "Next key": "value"

    All bare strings following a key's value are merged into that
    value (joined by \\n), continuing until either:
      - a string token that is itself followed by a colon (a real key), or
      - the enclosing object/array closes.
    """
    # Tokenize into quoted strings and structural characters.
    token_pattern = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\],:]')
    tokens = token_pattern.findall(json_text)

    def is_str(tok: str) -> bool:
        return tok.startswith('"')

    out_tokens = []
    i, n = 0, len(tokens)

    while i < n:
        tok = tokens[i]
        out_tokens.append(tok)

        # Look for a completed "key": "value" pair
        if (
            is_str(tok)
            and i + 2 < n
            and tokens[i + 1] == ":"
            and is_str(tokens[i + 2])
        ):
            out_tokens.append(tokens[i + 1])  # the colon
            merged_value = json.loads(tokens[i + 2])  # unescape the value string

            j = i + 3
            # Keep consuming ", "<bare string>" as long as that string
            # is NOT itself followed by a colon (which would make it a key)
            while (
                j + 1 < n
                and tokens[j] == ","
                and is_str(tokens[j + 1])
                and not (j + 2 < n and tokens[j + 2] == ":")
            ):
                bare_str = json.loads(tokens[j + 1])
                merged_value += "\n" + bare_str
                j += 2  # consumed the comma + the bare string

            out_tokens.append(json.dumps(merged_value))  # re-escape merged value
            i = j
            continue

        i += 1

    return "".join(out_tokens)


# ============================================================
# STARTUP & SHUTDOWN HANDLERS
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle"""
    # Startup
    logger.info("=" * 60)
    logger.info("🚀 STARTING UP...")
    logger.info("=" * 60)
    logger.info(f"📡 Ollama URL: {config.OLLAMA_BASE_URL}")
    logger.info(f"🤖 Model: {config.MODEL_NAME}")
    logger.info(f"🔒 HTTPS Enabled: {config.USE_HTTPS}")
    logger.info(f"⚙️  Max Concurrent Requests: {config.MAX_CONCURRENT_REQUESTS}")
    logger.info(f"⏱️  Request Timeout: {config.REQUEST_TIMEOUT}s")

    # Create SSL context for Ollama communication
    ssl_context = get_ssl_context()

    # Create aiohttp session with optional SSL context
    if ssl_context:
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        app_state["client_session"] = aiohttp.ClientSession(
            connector=connector,
            read_bufsize=15 * 1024 * 1024,  # 15MB buffer
        )
    else:
        app_state["client_session"] = aiohttp.ClientSession(
            read_bufsize=15 * 1024 * 1024,  # 15MB buffer
        )

    # Create semaphore for concurrent request limiting
    app_state["semaphore"] = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)

    logger.info(f"✅ aiohttp ClientSession initialized")
    logger.info(f"✅ Semaphore set to {config.MAX_CONCURRENT_REQUESTS} concurrent requests")
    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("=" * 60)
    logger.info("🛑 SHUTTING DOWN...")
    logger.info("=" * 60)
    if app_state["client_session"]:
        await app_state["client_session"].close()
        logger.info("✅ aiohttp ClientSession closed")


# Create app with lifespan
app = FastAPI(
    title=config.API_TITLE,
    description="Multimodal API with image support",
    version=config.API_VERSION,
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "model": config.MODEL_NAME,
        "https_enabled": config.USE_HTTPS,
        "timestamp": datetime.now().isoformat()
    }


# ============================================================
# IMAGE GENERATION WITH TEXT
# ============================================================

@app.post("/generate-with-image")
async def generate_with_image(
        image: UploadFile = File(...),
        prompt: str = Form(...),
):
    """Generate response based on image and text prompt"""
    try:
        # Read and encode image
        image_data = await image.read()

        image_base64 = base64.b64encode(image_data).decode('utf-8')

        # Determine image type
        ext = image.filename.split('.')[-1].lower()
        media_type = "image/jpeg" if ext in ["jpg", "jpeg"] else f"image/{ext}"

        logger.info(f"Processing image: {image.filename} ({media_type})")
        logger.info(f"Image size: {len(image_data) / (1024 * 1024) :.2f} MBs")

        # Qwen 3.5:9B format - simpler structure
        # The trick: use a string content with special image token format
        # OR pass image separately in images field

        # Use /api/generate endpoint for vision models with images

        payload = {
            "model": config.MODEL_NAME,
            "prompt": prompt,
            "images": [image_base64],  # Vision models expect images here
            "stream": True,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": {
                "temperature": 0,
                "top_k": 1,
                "top_p": 1.0,
                "repeat_penalty": 1.0,  # No repeat penalty variance
                "seed": 42,
                "num_ctx": 8192,
            }
        }

        logger.info(f"Sending request to Ollama: {config.OLLAMA_BASE_URL}/api/generate")
        logger.debug(f"Payload: model={payload['model']}, image_size={len(image_base64)}, prompt_length={len(prompt)}")

        try:
            # Make request with semaphore
            async with app_state["semaphore"]:
                async with app_state["client_session"].post(
                        f"{config.OLLAMA_BASE_URL}/api/generate",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(
                            # connect=60,
                            total=config.REQUEST_TIMEOUT,
                            # sock_read=600  # Key setting for slow endpoints
                        )
                ) as response:

                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Ollama error: {response.status} - {error_text}")
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"Ollama error: {error_text}"
                        )

                    # ============================================
                    # PARSE STREAMING NDJSON
                    # ============================================
                    full_response = ""
                    metrics = {}
                    chunk_count = 0

                    async for line in response.content:
                        if not line:
                            continue

                        try:
                            chunk = json.loads(line)
                            full_response += chunk.get("response", "")
                            chunk_count += 1

                            # Log streaming progress
                            if chunk_count % 10 == 0:
                                logger.debug(
                                    f"Streaming... {chunk_count} chunks, {len(full_response)} chars")

                            # Check if generation complete
                            if chunk.get("done"):
                                metrics = {
                                    "total_duration": round(chunk.get("total_duration", 0) / 1_000_000_000, 2),
                                    "load_duration": round(chunk.get("load_duration", 0) / 1_000_000_000, 2),
                                    "prompt_eval_count": chunk.get("prompt_eval_count", 0),
                                    "prompt_eval_duration": round(
                                        chunk.get("prompt_eval_duration", 0) / 1_000_000_000, 2),
                                    "eval_count": chunk.get("eval_count", 0),
                                    "eval_duration": round(chunk.get("eval_duration", 0) / 1_000_000_000, 2),
                                }
                                logger.info(f"✓ Generation complete: {chunk_count}")
                                logger.info(f"  Metrics: {metrics}")
                                break

                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse JSON line: {repr(line[:100])}")
                            continue

        except asyncio.TimeoutError:
            logger.error(
                f"⏱️ Timed out while streaming from Ollama after {chunk_count} chunks "
                f"({len(full_response)} chars received so far)"
            )
            response_data = {
                "response": "Time out error",
                "model": config.MODEL_NAME,
                "timestamp": datetime.now().isoformat(),
                "metrics": metrics,
            }
            return response_data

        # ============================================
        # POST-PROCESS & RETURN
        # ============================================
        full_response = full_response.strip()

        if not full_response:
            logger.warning(f"Empty response from model. Last chunk: {chunk if 'chunk' in locals() else 'N/A'}")
            full_response = "No response generated from model."

        # ✅ TRY TO PARSE AS JSON (for structured extractions)
        content = full_response
        # Strip markdown code blocks if present
        content = strip_markdown_code_blocks(content)

        try:
            # Attempt JSON parsing
            content = repair_trailing_bare_strings(content)
            json_response = json.loads(content)
            logger.info(f"✓ Parsed response as JSON: {json_response}")

            # If it's a dict with a "response" key, extract that value
            if isinstance(json_response, dict) and "response" in json_response:
                cleaned_response = clean_json_string(json_response.get("response"))
                content = json.loads(cleaned_response)
                logger.info(f"✓ Extracted nested 'response' value: {content}")
            # Otherwise use the whole JSON as-is
            else:
                content = json.dumps(json_response, indent=2)
                logger.info(f"✓ Using full JSON response (not nested)")

        except json.JSONDecodeError as e:
            # If not JSON, use the raw response

            content = full_response
            logger.info(f"Response is plain text (not JSON)")

        # Format response
        response_data = {
            "response": content,
            "model": config.MODEL_NAME,
            "timestamp": datetime.now().isoformat(),
            "metrics": metrics
        }

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in generate_with_image: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# DOCS
# ============================================================

@app.get("/")
async def root():
    """API documentation"""
    return {
        "name": config.API_TITLE,
        "version": config.API_VERSION,
        "https_enabled": config.USE_HTTPS,
        "endpoints": {
            "health": "GET /health",
            "image_analysis": "POST /generate-with-image",
            "interactive_docs": "GET /docs",
            "openapi_schema": "GET /openapi.json"
        }
    }


# ============================================================
# MAIN - SERVER STARTUP
# ============================================================

if __name__ == "__main__":
    try:
        # Validate and get SSL configuration
        ssl_keyfile, ssl_certfile = get_uvicorn_ssl_config()

        # Log startup info
        protocol = "https" if config.USE_HTTPS else "http"
        logger.info("=" * 60)
        logger.info(f"🌐 Starting {config.API_TITLE} v{config.API_VERSION}")
        logger.info(f"   Server: {protocol}://{config.API_HOST}:{config.API_PORT}")
        logger.info(f"   Model: {config.MODEL_NAME}")
        logger.info("=" * 60)

        # Run Uvicorn server
        uvicorn.run(
            "app:app",
            host=config.API_HOST,
            port=config.API_PORT,
            ssl_keyfile=ssl_keyfile,
            ssl_certfile=ssl_certfile,
            log_level=config.LOG_LEVEL.lower(),
            reload=False,
        )

    except FileNotFoundError as e:
        logger.error(f"❌ SSL Configuration Error: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"❌ Fatal Error: {e}", exc_info=True)
        exit(1)


# curl -k -X POST https://localhost:8443/generate-with-image -F "image=@$(realpath ./ITF_72000767_page_1.png)" \
# -F "prompt=What do you see in this image?" > response.json