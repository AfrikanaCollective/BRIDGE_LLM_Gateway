"""Provider backend and health/circuit-breaker models.

See ARCHITECTURE.md §4.4 and §6. ProviderBackend is static config loaded at
startup (no hot-reload in v1). BackendHealth is runtime state cached in
Redis and refreshed by the background poller (gateway/routing/health.py).

Failover chains are not required to run the same model on every backend —
this deployment's actual chain fails over from qwen3.5:9b (primary) to
qwen3.6:35b and qwen3.8:27b-q4_K_M on different hardware (see
gateway/admin/routing.yaml). `target_model` is what makes that possible:
each (logical model, backend) routing entry gets its own ProviderBackend
copy carrying the model name to actually request from *that* backend,
distinct from the logical model name the tenant requested and is
rate-limited/budgeted against — see ARCHITECTURE.md §6.5.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CircuitState = Literal["closed", "open", "half_open"]


class ProviderBackend(BaseModel):
    id: str = Field(description='e.g. "ollama-primary"')
    base_url: str
    models: list[str] = Field(description="Models this backend can serve")
    priority: int = Field(default=0, description="Lower is tried first, within a model")
    enabled: bool = True
    keep_alive: str = Field(
        default="5m",
        description=(
            "Ollama keep_alive for this backend. Set high on every backend in a "
            "model's failover chain — a cold backend (model evicted from VRAM) "
            "trades an outage for a load-time latency spike on failover; see "
            "ARCHITECTURE.md §6.4."
        ),
    )
    target_model: str | None = Field(
        default=None,
        description=(
            "The model name to actually request from THIS backend when it's "
            "used to serve a given logical/requested model — e.g. a request "
            "for \"qwen3.5:9b\" that fails over to a secondary backend running "
            "qwen3.6:35b instead. None means \"same name as requested\" (the "
            "common case: identical model mirrored across backends). Set per "
            "routing-table entry by gateway/routing/registry.py, not by hand."
        ),
    )


class BackendHealth(BaseModel):
    backend_id: str
    model: str
    healthy: bool
    consecutive_failures: int = 0
    circuit_state: CircuitState = "closed"
    last_checked_at: datetime
    last_error: str | None = None
