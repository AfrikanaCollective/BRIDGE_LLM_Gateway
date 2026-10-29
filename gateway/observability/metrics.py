"""Prometheus metric definitions.

See ARCHITECTURE.md §8.1. This is the full metric surface for v1 — deep
per-function instrumentation is deliberately not here; that's what the two
OpenTelemetry spans (gateway/observability/tracing.py) are for, and
everything below is cheap enough to be always-on.
"""

from prometheus_client import Counter, Gauge, Histogram

# prometheus_client's default buckets top out at 10s, which LLM calls blow
# past routinely — every slower request lands in +Inf and histogram_quantile
# pins p50/p95/p99 at 10. These run up to provider_request_timeout_seconds
# (gateway/config.py, 600 by default) so anything short of a timeout gets a
# real bucket. Low end is kept fine-grained for embeddings.
LATENCY_BUCKETS_SECONDS = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0,
)

REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total gateway requests",
    ["tenant", "model", "status"],
)

REQUEST_LATENCY_SECONDS = Histogram(
    "gateway_request_latency_seconds",
    "End-to-end gateway request latency",
    ["tenant", "model"],
    buckets=LATENCY_BUCKETS_SECONDS,
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
    buckets=LATENCY_BUCKETS_SECONDS,
)
