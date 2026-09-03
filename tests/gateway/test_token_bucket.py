"""Token bucket rate limiter tests.

Uses fakeredis so these run without a live Redis instance. See
ARCHITECTURE.md §5 for the design being tested: atomic refill+consume,
burst capacity, and rejection once the bucket is empty.
"""

import uuid

import pytest
from fakeredis.aioredis import FakeRedis

from gateway.core.exceptions import RateLimitExceeded
from gateway.ratelimit.token_bucket import TokenBucketLimiter


@pytest.fixture
async def limiter():
    redis = FakeRedis()
    yield TokenBucketLimiter(redis)
    await redis.aclose()


@pytest.mark.asyncio
async def test_allows_requests_within_burst(limiter):
    tenant_id = uuid.uuid4()
    for _ in range(5):
        await limiter.check_and_consume(
            tenant_id=tenant_id, model="qwen3.5:9b", requests_per_minute=60, burst=5
        )


@pytest.mark.asyncio
async def test_rejects_once_burst_exhausted(limiter):
    tenant_id = uuid.uuid4()
    for _ in range(3):
        await limiter.check_and_consume(
            tenant_id=tenant_id, model="qwen3.5:9b", requests_per_minute=60, burst=3
        )

    with pytest.raises(RateLimitExceeded) as exc_info:
        await limiter.check_and_consume(
            tenant_id=tenant_id, model="qwen3.5:9b", requests_per_minute=60, burst=3
        )
    assert exc_info.value.retry_after_seconds > 0


@pytest.mark.asyncio
async def test_buckets_are_isolated_per_tenant_and_model(limiter):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await limiter.check_and_consume(
        tenant_id=tenant_a, model="qwen3.5:9b", requests_per_minute=60, burst=1
    )
    # tenant_b has its own bucket — must not be affected by tenant_a's usage.
    await limiter.check_and_consume(
        tenant_id=tenant_b, model="qwen3.5:9b", requests_per_minute=60, burst=1
    )
