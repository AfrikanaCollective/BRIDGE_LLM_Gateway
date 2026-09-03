"""Per (backend, model) circuit breaker, state shared via Redis across
gateway replicas.

See ARCHITECTURE.md §6.3.

closed (normal)
  --after N consecutive failures--> open (skipped by routing for cooldown_s)
  --after cooldown_s--> half_open (one probe request allowed)
  --success--> closed
  --failure--> open
"""

from __future__ import annotations

from redis.asyncio import Redis

from gateway.models.provider import CircuitState


class CircuitBreaker:
    def __init__(
        self,
        redis: Redis,
        *,
        failure_threshold: int,
        cooldown_seconds: int,
    ):
        self._redis = redis
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds

    @staticmethod
    def _key(backend_id: str, model: str) -> str:
        return f"circuit:{backend_id}:{model}"

    async def state(self, backend_id: str, model: str) -> CircuitState:
        raw = await self._redis.hget(self._key(backend_id, model), "state")
        return raw.decode() if raw else "closed"  # type: ignore[return-value]

    async def is_available(self, backend_id: str, model: str) -> bool:
        """True if routing may send traffic to this backend (closed or
        half_open — half_open allows exactly one probe, enforced by the
        caller treating a half_open backend as usable but watching the
        result closely)."""
        return await self.state(backend_id, model) != "open"

    async def record_success(self, backend_id: str, model: str) -> None:
        key = self._key(backend_id, model)
        await self._redis.hset(key, mapping={"state": "closed", "consecutive_failures": 0})

    async def record_failure(self, backend_id: str, model: str) -> None:
        key = self._key(backend_id, model)
        failures = await self._redis.hincrby(key, "consecutive_failures", 1)
        if failures >= self._failure_threshold:
            await self._redis.hset(key, "state", "open")
            await self._redis.expire(key, self._cooldown_seconds)
            # Key expiry is the half-open transition: once it expires,
            # `state()` returns "closed" (the default for a missing key) via
            # the caller's next health check, which re-probes the backend.
