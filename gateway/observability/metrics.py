"""Prometheus metric definitions.

See ARCHITECTURE.md §8.1. This is the full metric surface for v1 — deep
per-function instrumentation is deliberately not here; that's what the two
OpenTelemetry spans (gateway/observability/tracing.py) are for, and
everything below is cheap enough to be always-on.
"""

from prometheus_client import Counter, Gauge, Histogram

REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total gateway requests",
    ["tenant", "model", "status"],
)

REQUEST_LATENCY_SECONDS = Histogram(
    "gateway_request_latency_seconds",
    "End-to-end gateway request latency",
    ["tenant", "model"],
)

TOKENS_TOTAL = Counter(
    "gateway_tokens_total",
    "Total tokens processed",
    ["tenant", "model", "kind"],  # kind: prompt | completion
)

RATE_LIMIT_REJECTIONS_TOTAL = Counter(
    "gateway_rate_limit_rejections_total",
    "Requests rejected by the rate limiter",
    ["tenant"],
)

BUDGET_REJECTIONS_TOTAL = Counter(
    "gateway_budget_rejections_total",
    "Requests rejected for exceeding budget",
    ["tenant", "model"],
)

FAILOVER_ATTEMPTS_TOTAL = Counter(
    "gateway_failover_attempts_total",
    "Provider failover attempts",
    ["model", "from_backend", "to_backend"],
)

BACKEND_HEALTH = Gauge(
    "gateway_backend_health",
    "1 if the backend passed its last health check for this model, else 0",
    ["backend_id", "model"],
)

MODEL_LOAD_SECONDS = Histogram(
    "gateway_model_load_seconds",
    "Observed latency attributable to a backend loading a model into VRAM "
    "(e.g. on failover to a cold backend) — expected, not an anomaly; see "
    "ARCHITECTURE.md §6.4.",
    ["backend_id", "model"],
)
