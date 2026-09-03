"""Background health poller.

See ARCHITECTURE.md §6.2 and CLAUDE.md hard rule: health checks must run a
cheap REAL generation against the model, not just ping a liveness endpoint
— a backend can be reachable while the model has been evicted from VRAM
(idle past keep_alive) or is stuck loading, and a liveness-only check would
report it healthy right up until a real request hangs for the full
provider timeout.
"""

from __future__ import annotations

import asyncio
import logging

from gateway.core.exceptions import GatewayError
from gateway.models.provider import ProviderBackend
from gateway.observability.metrics import BACKEND_HEALTH
from gateway.providers.base import LLMProvider
from gateway.routing.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


class HealthPoller:
    def __init__(
        self,
        backends_by_model: dict[str, list[ProviderBackend]],
        provider: LLMProvider,
        circuit_breaker: CircuitBreaker,
        *,
        interval_seconds: int,
    ):
        self._backends_by_model = backends_by_model
        self._provider = provider
        self._circuit_breaker = circuit_breaker
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run_forever(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self._interval_seconds)

    async def _poll_once(self) -> None:
        for model, backends in self._backends_by_model.items():
            for backend in backends:
                await self._check_one(backend, model)

    async def _check_one(self, backend: ProviderBackend, model: str) -> None:
        try:
            # A cheap real generation (num_predict=1), not a liveness ping —
            # this is what catches "model evicted from VRAM" cases that
            # `/api/tags` alone would miss.
            await self._provider.health_check(backend=backend, model=model)
        except GatewayError as exc:
            logger.warning("Health check failed for backend=%s model=%s: %s", backend.id, model, exc)
            await self._circuit_breaker.record_failure(backend.id, model)
            BACKEND_HEALTH.labels(backend_id=backend.id, model=model).set(0)
        else:
            await self._circuit_breaker.record_success(backend.id, model)
            BACKEND_HEALTH.labels(backend_id=backend.id, model=model).set(1)
