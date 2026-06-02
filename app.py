import io
import ssl
import json
import base64
import aiohttp
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from PIL import Image
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
        app_state["client_session"] = aiohttp.ClientSession(connector=connector)
    else:
        app_state["client_session"] = aiohttp.ClientSession()

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
        img = Image.open(io.BytesIO(image_data))

        max_height = 1500
        if img.height > max_height:
            # Calculate new width maintaining aspect ratio
            aspect_ratio = img.width / img.height
            new_width = int(max_height * aspect_ratio)
            img = img.resize((new_width, max_height), Image.Resampling.LANCZOS)
            logger.info(f"Resized to: {img.size} (width={new_width}, height={max_height})")

        # Convert RGBA/PNG to RGB JPEG
        if img.mode in ('RGBA', 'LA', 'P'):
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                rgb_img.paste(img, mask=img.split()[-1])
            else:
                rgb_img.paste(img)
            img = rgb_img
            logger.info(f"Converted {img.mode} to RGB")

        # Compress to JPEG (quality 95 for balance)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=95, optimize=True)
        image_data = buffer.getvalue()

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
            }
        }

        logger.info(f"Sending request to Ollama: {config.OLLAMA_BASE_URL}/api/generate")
        logger.debug(f"Payload: model={payload['model']}, image_size={len(image_base64)}, prompt_length={len(prompt)}")

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

                '''
                result = await response.json()
                logger.debug(f"Ollama response keys: {list(result.keys())}")

                # Extract content - /api/generate returns "response" field
                content = result.get("response", "")

                # ✅ FIX: Strip markdown code blocks
                content = strip_markdown_code_blocks(content)

                if not content:
                    logger.warning(f"Empty content from Ollama. Response: {result}")
                    content = "No response generated from model."

                logger.info(f"Generated content length: {len(content)} characters")

                # Extract metrics
                metrics = {
                    "total_duration": round(result.get("total_duration", 0) / 1_000_000_000, 2),
                    "load_duration": round(result.get("load_duration", 0) / 1_000_000_000, 2),
                    "prompt_eval_count": result.get("prompt_eval_count"),
                    "prompt_eval_duration": round(result.get("prompt_eval_duration", 0) / 1_000_000_000, 2),
                    "eval_count": result.get("eval_count"),
                    "eval_duration": round(result.get("eval_duration", 0) / 1_000_000_000, 2),
                }
                '''

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
                logger.info(f"Raw response : {content}\n\n")

                try:
                    # Attempt JSON parsing
                    json_response = json.loads(content)
                    logger.info(f"✓ Parsed response as JSON: {json_response}")

                    # If it's a dict with a "response" key, extract that value
                    if isinstance(json_response, dict) and "response" in json_response:
                        content = json_response["response"]
                        logger.info(f"✓ Extracted nested 'response' value")
                    # Otherwise use the whole JSON as-is
                    else:
                        content = json.dumps(json_response, indent=2)
                        logger.info(f"✓ Using full JSON response (not nested)")

                except json.JSONDecodeError:
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