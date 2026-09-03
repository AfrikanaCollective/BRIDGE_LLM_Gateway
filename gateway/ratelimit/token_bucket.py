"""Redis-backed token bucket rate limiter.

See ARCHITECTURE.md §5. One bucket per (tenant_id, model), refilled and
consumed atomically via a Lua script (token_bucket.lua) to avoid the
classic read-then-write race under concurrent requests from the same
tenant.

Redis-down behavior is fail CLOSED (CLAUDE.md hard rule, PRD.md §10a): if
Redis is unreachable, `check_and_consume` raises rather than silently
allowing every request through.
"""

from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID

from redis.asyncio import Redis

from gateway.core.exceptions import RateLimitExceeded

_LUA_SCRIPT_PATH = Path(__file__).parent / "scripts" / "token_bucket.lua"


class TokenBucketLimiter:
    def __init__(self, redis: Redis):
        self._redis = redis
        self._script = redis.register_script(_LUA_SCRIPT_PATH.read_text())

    @staticmethod
    def _bucket_key(tenant_id: UUID, model: str) -> str:
        return f"ratelimit:{tenant_id}:{model}"

    async def check_and_consume(
        self,
        *,
        tenant_id: UUID,
        model: str,
        requests_per_minute: int,
        burst: int,
        cost: int = 1,
    ) -> None:
        """Raises RateLimitExceeded if the bucket has insufficient tokens.

        Redis connection errors propagate rather than being swallowed —
        callers (gateway/core/service.py) treat that as "reject the
        request", not "allow it through" (fail closed, see module docstring).
        """
        refill_rate_per_second = requests_per_minute / 60.0
        now = time.time()

        allowed, _tokens_remaining, retry_after_ms = await self._script(
            keys=[self._bucket_key(tenant_id, model)],
            args=[burst, refill_rate_per_second, now, cost],
        )

        if not allowed:
            # retry_after_ms comes back as whole milliseconds, not seconds —
            # see token_bucket.lua's docstring on why (Redis truncates a
            # Lua script's float replies to integers).
            raise RateLimitExceeded(
                f"Rate limit exceeded for tenant={tenant_id} model={model}",
                retry_after_seconds=float(retry_after_ms) / 1000.0,
            )
