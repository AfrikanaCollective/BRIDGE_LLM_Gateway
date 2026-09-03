"""
Configuration for the app.py process (FastAPI app shell + legacy
/generate-with-image endpoint). Gateway infra config (Redis, DB, routing,
rate limits, budgets) lives separately in gateway/config.py — see
ARCHITECTURE.md §1/§11 for why these aren't merged.

Trimmed after the ITF/NAR pipeline (agents/, clients/, prompts/) was
removed from this repo (PRD.md §11 changelog): OLLAMA_BASE_URL,
OLLAMA_KEEP_ALIVE, REQUEST_TIMEOUT, and the PROMPTS_*/TRACE_DIR settings
that pipeline used are gone. `gateway/admin/routing.yaml` is now the only
place backend URLs/keep_alive/timeouts are configured — see
ARCHITECTURE.md §6.1/§6.5.
"""

import os


class Config:
    """Application configuration"""

    # Default/primary model requested by the legacy /generate-with-image
    # handler (app.py). Actual backend routing/failover for this model is
    # defined in gateway/admin/routing.yaml, not here.
    MODEL_NAME: str = os.getenv(
        "MODEL_NAME",
        "qwen3.5:9b"
    )

    # API settings
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8443"))
    API_TITLE: str = os.getenv("API_TITLE", "Qwen LLM Gateway")
    API_VERSION: str = os.getenv("API_VERSION", "2.0.0")

    # SSL/TLS settings
    USE_HTTPS: bool = os.getenv("USE_HTTPS", "True").lower() in ("true", "1", "yes")
    SSL_CERT_FILE: str = os.getenv("SSL_CERT_FILE", "./certs/certificate.crt")
    SSL_KEY_FILE: str = os.getenv("SSL_KEY_FILE", "./certs/private.key")
    SSL_VERIFY: bool = os.getenv("SSL_VERIFY", "False").lower() in ("true", "1", "yes")

    # Concurrency: caps total concurrent generations across ALL tenants
    # (protects the shared GPU backends), distinct from and in addition to
    # the gateway's per-tenant rate limits — see app.py's lifespan comment.
    MAX_CONCURRENT_REQUESTS: int = int(
        os.getenv("MAX_CONCURRENT_REQUESTS", "10")
    )

    # Image settings
    MAX_IMAGE_SIZE_MB: int = int(
        os.getenv("MAX_IMAGE_SIZE_MB", "15")
    )
    ALLOWED_IMAGE_FORMATS: tuple = tuple(
        os.getenv("ALLOWED_IMAGE_FORMATS", "JPEG,PNG,GIF,WEBP").split(",")
    )

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def api_url(self) -> str:
        """Dynamically build API URL based on USE_HTTPS setting"""
        protocol = "https" if self.USE_HTTPS else "http"
        return f"{protocol}://{self.API_HOST}:{self.API_PORT}"
