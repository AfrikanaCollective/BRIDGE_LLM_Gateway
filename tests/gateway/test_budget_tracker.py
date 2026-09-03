"""Budget reserve/reconcile tests.

See ARCHITECTURE.md §4.3/§12(a): budgets are enforced against a
worst-case reservation, then reconciled to actual usage. These tests cover
the two edge cases the design review called out (PRD.md §10b): blocking
vs. warning on exceed, and reconciliation not leaving stale reservations
behind on release.
"""

import uuid

import pytest
from fakeredis.aioredis import FakeRedis

from gateway.budget.tracker import BudgetTracker, current_period_key
from gateway.core.exceptions import BudgetExceeded
from gateway.models.policy import BudgetPolicy


@pytest.fixture
async def tracker():
    redis = FakeRedis()
    yield BudgetTracker(redis)
    await redis.aclose()


def _policy(**overrides) -> BudgetPolicy:
    defaults = dict(
        tenant_id=uuid.uuid4(),
        model="qwen3.5:9b",
        period="monthly",
        max_tokens=1000,
        on_exceed="block",
    )
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


@pytest.mark.asyncio
async def test_reserve_within_budget_succeeds(tracker):
    policy = _policy(max_tokens=1000)
    reservation = await tracker.reserve(
        tenant_id=policy.tenant_id, model=policy.model, policy=policy, estimated_tokens=500
    )
    assert reservation.reserved_tokens == 500
    assert reservation.period_key == current_period_key("monthly")


@pytest.mark.asyncio
async def test_reserve_over_budget_blocks_and_releases(tracker):
    policy = _policy(max_tokens=100, on_exceed="block")
    with pytest.raises(BudgetExceeded):
        await tracker.reserve(
            tenant_id=policy.tenant_id, model=policy.model, policy=policy, estimated_tokens=500
        )
    # The failed reservation must not leave a stale increment behind —
    # a subsequent request for a smaller amount should still fit.
    reservation = await tracker.reserve(
        tenant_id=policy.tenant_id, model=policy.model, policy=policy, estimated_tokens=50
    )
    assert reservation.reserved_tokens == 50


@pytest.mark.asyncio
async def test_reserve_over_budget_with_warn_proceeds(tracker):
    policy = _policy(max_tokens=100, on_exceed="warn")
    reservation = await tracker.reserve(
        tenant_id=policy.tenant_id, model=policy.model, policy=policy, estimated_tokens=500
    )
    assert reservation.reserved_tokens == 500


@pytest.mark.asyncio
async def test_reconcile_adjusts_down_to_actual_usage(tracker):
    policy = _policy(max_tokens=1000)
    reservation = await tracker.reserve(
        tenant_id=policy.tenant_id, model=policy.model, policy=policy, estimated_tokens=500
    )
    await tracker.reconcile(reservation, actual_tokens=120)

    # A second reservation should now see only 120 tokens consumed, not the
    # original 500-token worst-case estimate.
    second = await tracker.reserve(
        tenant_id=policy.tenant_id, model=policy.model, policy=policy, estimated_tokens=800
    )
    assert second.reserved_tokens == 800  # 120 + 800 = 920, still under 1000


@pytest.mark.asyncio
async def test_release_fully_refunds_reservation(tracker):
    policy = _policy(max_tokens=100)
    reservation = await tracker.reserve(
        tenant_id=policy.tenant_id, model=policy.model, policy=policy, estimated_tokens=100
    )
    await tracker.release(reservation)

    # Fully refunded — a new full-size reservation should fit again.
    again = await tracker.reserve(
        tenant_id=policy.tenant_id, model=policy.model, policy=policy, estimated_tokens=100
    )
    assert again.reserved_tokens == 100
