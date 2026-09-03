"""Reserve-then-reconcile budget tracking.

See ARCHITECTURE.md §3 (steps 3 and 6), §4.3, and PRD.md §10a. Exact token
counts aren't known until Ollama's final response chunk, so budgets are
enforced against a worst-case *reservation* (prompt estimate + num_predict)
before dispatch, then reconciled down to actual usage after. This bounds
overshoot to at most one in-flight request's worst case per tenant+model —
it does not guarantee exact enforcement, and that's a documented tradeoff,
not a bug.

The period key (e.g. "2026-09" for monthly) is computed once, at reservation
time, and carried through to reconciliation and the UsageRecord
(`budget_period_key`) so a request that straddles a period boundary is
billed entirely against the period it started in (PRD.md §10b).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from redis.asyncio import Redis

from gateway.core.exceptions import BudgetExceeded
from gateway.models.policy import BudgetPolicy


def current_period_key(period: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if period == "daily":
        return now.strftime("%Y-%m-%d")
    if period == "monthly":
        return now.strftime("%Y-%m")
    raise ValueError(f"Unknown budget period: {period!r}")


@dataclass(frozen=True)
class BudgetReservation:
    tenant_id: UUID
    model: str
    period_key: str
    reserved_tokens: int
    policy: BudgetPolicy


class BudgetTracker:
    def __init__(self, redis: Redis):
        self._redis = redis

    @staticmethod
    def _key(tenant_id: UUID, model: str, period_key: str) -> str:
        return f"budget:{tenant_id}:{model}:{period_key}"

    async def reserve(
        self,
        *,
        tenant_id: UUID,
        model: str,
        policy: BudgetPolicy,
        estimated_tokens: int,
    ) -> BudgetReservation:
        """Reserve `estimated_tokens` against the tenant+model+period counter.

        Raises BudgetExceeded if the policy is `on_exceed="block"` and this
        reservation would push usage past `max_tokens`. On `"warn"`, the
        reservation still succeeds (traffic isn't blocked) but callers
        should emit a budget_warning metric/log — see
        gateway/core/service.py.
        """
        period_key = current_period_key(policy.period)
        redis_key = self._key(tenant_id, model, period_key)

        new_total = await self._redis.incrby(redis_key, estimated_tokens)

        if policy.max_tokens is not None and new_total > policy.max_tokens:
            if policy.on_exceed == "block":
                await self._redis.incrby(redis_key, -estimated_tokens)  # release
                raise BudgetExceeded(
                    f"Budget exceeded for tenant={tenant_id} model={model} "
                    f"period={period_key}: {new_total}/{policy.max_tokens} tokens"
                )
            # on_exceed == "warn": fall through, caller logs/emits a metric.

        return BudgetReservation(
            tenant_id=tenant_id,
            model=model,
            period_key=period_key,
            reserved_tokens=estimated_tokens,
            policy=policy,
        )

    async def reconcile(self, reservation: BudgetReservation, actual_tokens: int) -> None:
        """Adjust the reservation down (or up) to the actual token count."""
        delta = actual_tokens - reservation.reserved_tokens
        if delta == 0:
            return
        redis_key = self._key(reservation.tenant_id, reservation.model, reservation.period_key)
        await self._redis.incrby(redis_key, delta)

    async def release(self, reservation: BudgetReservation) -> None:
        """Fully release a reservation — used when the request fails before
        any tokens were generated (provider error, all backends down)."""
        redis_key = self._key(reservation.tenant_id, reservation.model, reservation.period_key)
        await self._redis.incrby(redis_key, -reservation.reserved_tokens)
