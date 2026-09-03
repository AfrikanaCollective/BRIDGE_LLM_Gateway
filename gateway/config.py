"""Gateway settings — pydantic-settings, env-driven.

Deliberately separate from the legacy `config.Config` class, which stays
scoped to `app.py`'s FastAPI app shell (SSL, host/port, the legacy
`/generate-with-image` handler's default model). See ARCHITECTURE.md §1
and §11 for why these aren't merged.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    # Redis (rate limiting, budget counters, circuit breaker state)
    redis_url: str = "redis://localhost:6379/0"

    # Durable store (tenants, api keys, usage records)
    database_url: str = "sqlite+aiosqlite:///./gateway.db"

    # Auth
    api_key_header: str = "Authorization"

    # Rate limiting
    default_requests_per_minute: int = 60
    default_burst: int = 10

    # Budgets
    default_budget_period: str = "monthly"

    # Routing / failover
    # Must be >= the longest chain in routing.yaml or trailing backends are
    # silently never tried. The real chain (gateway/admin/routing.yaml) is
    # 3 deep (primary + 2 heterogeneous-model secondaries) — this was 2
    # when the chain itself was only 2 deep; keep them in sync.
    max_failover_attempts: int = 3
    enable_health_poller: bool = True
    health_check_interval_seconds: int = 15
    circuit_breaker_failure_threshold: int = 3
    circuit_breaker_cooldown_seconds: int = 30
    provider_request_timeout_seconds: int = 600

    # Images
    max_image_size_mb: int = 15

    # Observability
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "qwen-llm-gateway"
    prometheus_metrics_path: str = "/metrics"
    log_level: str = "INFO"

    # Backend/model registry — path to the YAML config described in
    # ARCHITECTURE.md §6.1
    routing_config_path: str = "gateway/admin/routing.yaml"
    tenants_config_path: str = "gateway/admin/tenants.yaml"


settings = GatewaySettings()
