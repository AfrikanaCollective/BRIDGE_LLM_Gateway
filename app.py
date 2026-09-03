import ssl
import json
import base64
import aiohttp
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from utils.clean_gen_response_from_image import (
    strip_markdown_code_blocks,
    clean_json_string,
    repair_trailing_bare_strings,
    repair_unescaped_quotes,
)

# Import your config
from config import Config

config = Config()

# Setup logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================
# GATEWAY MOUNTING (see ARCHITECTURE.md §9/§11)
# ------------------------------------------------------------
# The gateway (gateway/) is the single choke point every LLM request goes
# through — including this file's own /generate-with-image handler below,
# which delegates to GatewayService.handle_request() instead of calling
# Ollama directly. The ITF/NAR pipeline that used to call this in-process
# has been removed from this repo; the caller today is a separate,
# external web app reaching this endpoint over HTTP (see the code snippet
# in the PR/commit that made this change) — so this handler now requires a
# real `Authorization: Bearer <api_key>` header and resolves a real tenant,
# same as every other gateway route. GATEWAY_ROUTES_AVAILABLE also gates
# this handler: if the gateway package can't even be imported (missing
# deps), there is no second, insecure path to Ollama to fall back to — the
# handler returns 503 instead (see generate_with_image below).
# ============================================================
try:
    from gateway.api.router import router as gateway_router
    from gateway.api.admin_router import router as gateway_admin_router
    from gateway.api.deps import authenticated_tenant
    from gateway.core.bootstrap import build_gateway_service
    from gateway.core.exceptions import GatewayError, RateLimitExceeded
    from gateway.models.chat import ChatCompletionRequest, ContentPart, Message
    from gateway.models.tenant import ApiKey, Tenant
    GATEWAY_ROUTES_AVAILABLE = True
except ImportError:
    logger.warning("⚠️ Gateway not available yet (gateway/ dependencies not installed)")
    gateway_router = None
    gateway_admin_router = None
    authenticated_tenant = None
    build_gateway_service = None
    GatewayError = RateLimitExceeded = None
    ChatCompletionRequest = ContentPart = Message = None
    Tenant = ApiKey = None
    GATEWAY_ROUTES_AVAILABLE = False

# App state for managing async resources
app_state = {
    "client_session": None,
    "semaphore": None,
    "gateway_runtime": None,  # gateway.core.bootstrap.GatewayRuntime, once built
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


# clean_json_string / repair_trailing_bare_strings / repair_unescaped_quotes
# live in utils/clean_gen_response_from_image.py (ARCHITECTURE.md §9) — generic
# LLM-response JSON repair for the legacy /generate-with-image contract,
# applied to the raw string GatewayService returns; not gateway code
# (CLAUDE.md: gateway/ stays domain-agnostic).


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
    logger.info(f"🤖 Default model: {config.MODEL_NAME} (backend routing/failover: gateway/admin/routing.yaml)")
    logger.info(f"🔒 HTTPS Enabled: {config.USE_HTTPS}")
    logger.info(f"⚙️  Max Concurrent Requests: {config.MAX_CONCURRENT_REQUESTS}")

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

    # Create semaphore for concurrent request limiting — this caps total
    # concurrent generations across ALL tenants (protects the shared GPU),
    # distinct from and in addition to the gateway's per-tenant rate limits.
    app_state["semaphore"] = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)

    logger.info(f"✅ aiohttp ClientSession initialized")
    logger.info(f"✅ Semaphore set to {config.MAX_CONCURRENT_REQUESTS} concurrent requests")

    # Build the gateway (redis, DB, routing, circuit breaker, health poller)
    # and wire it up so /generate-with-image below can call
    # GatewayService.handle_request() instead of Ollama directly — see
    # ARCHITECTURE.md §9. If gateway infra (Redis/Postgres) isn't reachable,
    # this is caught rather than crashing the whole process: the legacy
    # endpoint reports 503 (see generate_with_image) instead of silently
    # falling back to a second, unmetered path to Ollama.
    if GATEWAY_ROUTES_AVAILABLE:
        try:
            runtime = await build_gateway_service(app_state["client_session"])
            app_state["gateway_runtime"] = runtime
            app.state.gateway_service = runtime.service
            logger.info("✅ Gateway service initialized (routing + rate limits ready; callers must authenticate)")
        except Exception as e:
            logger.error(f"❌ Gateway service failed to initialize: {e}", exc_info=True)
            logger.warning("⚠️ /generate-with-image and /v1/* routes will return 503 until this is fixed")

    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("=" * 60)
    logger.info("🛑 SHUTTING DOWN...")
    logger.info("=" * 60)
    if app_state["gateway_runtime"]:
        await app_state["gateway_runtime"].shutdown()
        logger.info("✅ Gateway runtime shut down")
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

# Mount gateway routes (auth/rate-limit/budget/failover-backed) alongside
# the legacy endpoints below. See the GATEWAY MOUNTING note above.
if GATEWAY_ROUTES_AVAILABLE:
    app.include_router(gateway_router)
    app.include_router(gateway_admin_router)

    # Without this, a GatewayError raised anywhere on the /v1/* routes
    # (auth failure in the `authenticated_tenant` dependency, rate limit,
    # budget, all-providers-down, ...) has no FastAPI handler and becomes
    # an unhandled exception -> generic 500 "Internal Server Error",
    # silently defeating the typed-status-code contract those errors exist
    # for (CLAUDE.md: "No endpoint returns HTTP 200 with an error in the
    # body" — the flip side of that rule is routes must actually surface
    # the right non-2xx status, not fall through to a 500). app.py's own
    # legacy /generate-with-image handler catches GatewayError manually;
    # this handler covers every route that doesn't (gateway/api/router.py,
    # gateway/api/admin_router.py).
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        headers = {}
        if isinstance(exc, RateLimitExceeded):
            headers["Retry-After"] = str(max(1, round(exc.retry_after_seconds)))
        return JSONResponse(status_code=exc.http_status, content=exc.to_body(), headers=headers)


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


if GATEWAY_ROUTES_AVAILABLE:
    @app.get("/metrics")
    async def metrics():
        """Prometheus scrape endpoint. See ARCHITECTURE.md §8.1."""
        from fastapi import Response
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================================================
# IMAGE GENERATION WITH TEXT
# ============================================================

async def _unavailable_tenant_dep():
    """Stand-in auth dependency used only when gateway/ failed to import —
    keeps the route signature valid while still failing closed with 503
    (never silently skips auth)."""
    raise HTTPException(status_code=503, detail="Gateway is not available")


_generate_with_image_auth_dep = authenticated_tenant if GATEWAY_ROUTES_AVAILABLE else _unavailable_tenant_dep


@app.post("/generate-with-image")
async def generate_with_image(
        image: UploadFile = File(...),
        prompt: str = Form(...),
        tenant_and_key: tuple = Depends(_generate_with_image_auth_dep),
):
    """Generate response based on image and text prompt.

    Legacy-shaped response contract (`{response, model, timestamp,
    metrics}`), kept for the external web app that already calls this path
    (see ARCHITECTURE.md §9). Delegates to GatewayService.handle_request()
    instead of calling Ollama directly, and requires a real
    `Authorization: Bearer <api_key>` header — this is a genuinely external
    caller now (the in-process ITF/NAR pipeline that used to call this was
    removed from this repo), so it authenticates like any other gateway
    tenant. No bypass (CLAUDE.md hard rule): a missing/invalid key gets a
    401 here exactly like it would on `/v1/generate-with-image`.
    """
    if not GATEWAY_ROUTES_AVAILABLE or app_state["gateway_runtime"] is None:
        # No second, unmetered path to Ollama to fall back to — see the
        # GATEWAY MOUNTING note near the top of this file.
        raise HTTPException(status_code=503, detail="Gateway is not available")

    tenant, api_key = tenant_and_key
    runtime = app_state["gateway_runtime"]

    # Bug fix carried over from the design review (PRD.md §10b): this limit
    # was defined in config.py but never actually enforced anywhere.
    image_data = await image.read()
    max_bytes = config.MAX_IMAGE_SIZE_MB * 1024 * 1024
    if len(image_data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Image is {len(image_data) / 1024 / 1024:.1f}MB, exceeds {config.MAX_IMAGE_SIZE_MB}MB limit",
        )

    image_base64 = base64.b64encode(image_data).decode("utf-8")
    ext = (image.filename or "").rsplit(".", 1)[-1].lower()
    media_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"

    logger.info(f"Processing image: {image.filename} ({media_type})")
    logger.info(f"Image size: {len(image_data) / (1024 * 1024):.2f} MBs")

    chat_request = ChatCompletionRequest(
        model=config.MODEL_NAME,
        messages=[
            Message(
                role="user",
                content=[
                    ContentPart(type="text", text=prompt),
                    ContentPart(type="image_base64", image_base64=image_base64, media_type=media_type),
                ],
            )
        ],
    )

    try:
        # Semaphore still bounds total concurrent GPU load across all
        # tenants; the gateway's per-tenant token bucket is a separate,
        # additional layer (see the lifespan comment above).
        async with app_state["semaphore"]:
            result = await runtime.service.handle_request(
                tenant=tenant,
                api_key=api_key,
                request=chat_request,
            )
    except GatewayError as exc:
        # Typed errors map to real status codes — no 200-with-error-body
        # (the old timeout-returns-200 bug this replaces, PRD.md §10a).
        logger.error(f"Gateway error in generate_with_image: {exc.message}")
        raise HTTPException(status_code=exc.http_status, detail=exc.message)

    # ============================================
    # POST-PROCESS & RETURN — generic LLM-response JSON repair applied to
    # GatewayService's raw string output, not gateway logic; see
    # utils/clean_gen_response_from_image.py.
    # ============================================
    full_response = result.content.strip()

    if not full_response:
        logger.warning("Empty response from model.")
        full_response = "No response generated from model."

    content = strip_markdown_code_blocks(full_response)

    try:
        content = repair_trailing_bare_strings(content)
        content = repair_unescaped_quotes(content)
        # strict=False: tolerate literal control chars (raw \n, \t) inside
        # string values — the model nests nested JSON as a nested string but
        # doesn't reliably escape newlines inside it, which strict mode rejects
        json_response = json.loads(content, strict=False)
        logger.info(f"✓ Parsed response as JSON: {json_response}")

        if isinstance(json_response, dict) and "response" in json_response:
            cleaned_response = clean_json_string(json_response.get("response"))
            content = json.loads(cleaned_response, strict=False)
            logger.info(f"✓ Extracted nested 'response' value: {content}")
        else:
            content = json.dumps(json_response, indent=2)
            logger.info(f"✓ Using full JSON response (not nested)")

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed: {e}. Raw (first 300 chars): {full_response[:300]!r}")
        content = full_response
        logger.info(f"Response is plain text (not JSON)")

    if result.finish_reason == "length":
        logger.warning(
            f"⚠️ Generation hit num_predict limit (completion_tokens={result.usage.completion_tokens}) "
            f"without a natural stop — possible repetition loop even after tuning."
        )

    return {
        "response": content,
        "model": result.model,
        "timestamp": datetime.now().isoformat(),
        "metrics": {
            "backend_used": result.backend_used,
            "latency_ms": result.latency_ms,
            "prompt_eval_count": result.usage.prompt_tokens,
            "eval_count": result.usage.completion_tokens,
            "done_reason": result.finish_reason,
        },
    }


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
            "metrics": "GET /metrics",
            "image_analysis": "POST /generate-with-image",
            "chat_completions": "POST /v1/chat/completions",
            "generate_with_image_v1": "POST /v1/generate-with-image",
            "usage": "GET /v1/usage",
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


# curl -k -X POST https://localhost:8443/generate-with-image \
#   -H "Authorization: Bearer <api_key>" \
#   -F "image=@$(realpath ./example.png)" \
#   -F "prompt=What do you see in this image?" > response.json
#
# <api_key> comes from `python -m gateway.admin.seed` — see
# gateway/admin/tenants.yaml and README.md "Provisioning a tenant".