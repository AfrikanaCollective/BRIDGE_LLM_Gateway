"""Wires a GatewayService (and its dependencies) from GatewaySettings.

This is where app.py's lifespan gets a working GatewayService without
having to know how the pieces fit together — see ARCHITECTURE.md §2 for
the component diagram this assembles. Called once at startup; the
resulting GatewayRuntime is stashed on `app.state` (gateway/api/deps.py
reads it back out).

Every caller — the gateway's own `/v1/*` routes and app.py's legacy
`/generate-with-image` handler alike — authenticates as a real, seeded
external tenant via `gateway.api.deps.authenticated_tenant`. There is no
implicit/internal tenant here: this repo no longer has an in-process
consumer (the ITF/NAR pipeline was removed — see ARCHITECTURE.md §9), so
every caller reaches the gateway over HTTP with a real API key. Provision
tenants with `python -m gateway.admin.seed` (gateway/admin/tenants.yaml)
before anything can authenticate — this file does not auto-seed one.

Deliberately not using Alembic yet (gateway/db/migrations/README.md) —
`create_all_for_tests()` is used here too, logged loudly, until migrations
are wired up. Don't copy this pattern into a real production rollout
without reading that README first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp
from redis.asyncio import Redis
from sqlalchemy import select

from gateway.budget.tracker import BudgetTracker
from gateway.config import GatewaySettings, settings
from gateway.core.service import GatewayService, PolicyResolver
from gateway.db.orm import BudgetPolicyORM, RateLimitPolicyORM, UsageRecordORM
from gateway.db.session import async_session_factory, create_all_for_tests
from gateway.models.policy import BudgetPolicy, RateLimitPolicy
from gateway.models.usage import UsageRecord
from gateway.providers.ollama import OllamaProvider
from gateway.ratelimit.token_bucket import TokenBucketLimiter
from gateway.routing.circuit_breaker import CircuitBreaker
from gateway.routing.health import HealthPoller
from gateway.routing.registry import BackendRegistry

logger = logging.getLogger(__name__)


@dataclass
class GatewayRuntime:
    service: GatewayService
    redis: Redis
    health_poller: HealthPoller | None

    async def shutdown(self) -> None:
        if self.health_poller is not None:
            await self.health_poller.stop()
        await self.redis.aclose()


async def _load_policy_resolver(session) -> PolicyResolver:
    """Full-table load at startup — static/no-hot-reload, same as
    routing.yaml (ARCHITECTURE.md §6.1). Fine at v1 tenant counts; revisit
    if this ever needs to scale past a few hundred policies."""
    rl_rows = (await session.execute(select(RateLimitPolicyORM))).scalars().all()
    budget_rows = (await session.execute(select(BudgetPolicyORM))).scalars().all()

    rate_limit_policies = {
        (row.tenant_id, row.model): RateLimitPolicy.model_validate(row) for row in rl_rows
    }
    budget_policies = {(row.tenant_id, row.model): BudgetPolicy.model_validate(row) for row in budget_rows}

    return PolicyResolver(rate_limit_policies=rate_limit_policies, budget_policies=budget_policies)


def _make_usage_sink():
    async def usage_sink(record: UsageRecord) -> None:
        async with async_session_factory() as session:
            session.add(
                UsageRecordORM(
                    id=record.id,
                    request_id=record.request_id,
                    tenant_id=record.tenant_id,
                    api_key_id=record.api_key_id,
                    model=record.model,
                    served_model=record.served_model,
                    backend_id=record.backend_id,
                    status=record.status,
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    latency_ms=record.latency_ms,
                    failover_attempts=record.failover_attempts,
                    budget_period_key=record.budget_period_key,
                    created_at=record.created_at,
                )
            )
            await session.commit()

    return usage_sink


async def build_gateway_service(
    http_session: aiohttp.ClientSession, gateway_settings: GatewaySettings = settings
) -> GatewayRuntime:
    logger.info("Initializing gateway schema (dev create_all — see gateway/db/migrations/README.md)")
    await create_all_for_tests()

    async with async_session_factory() as session:
        policy_resolver = await _load_policy_resolver(session)

    redis = Redis.from_url(gateway_settings.redis_url)

    circuit_breaker = CircuitBreaker(
        redis,
        failure_threshold=gateway_settings.circuit_breaker_failure_threshold,
        cooldown_seconds=gateway_settings.circuit_breaker_cooldown_seconds,
    )
    registry = BackendRegistry.from_yaml(gateway_settings.routing_config_path, circuit_breaker)
    provider = OllamaProvider(
        http_session, request_timeout_seconds=gateway_settings.provider_request_timeout_seconds
    )

    service = GatewayService(
        rate_limiter=TokenBucketLimiter(redis),
        budget_tracker=BudgetTracker(redis),
        registry=registry,
        circuit_breaker=circuit_breaker,
        provider=provider,
        policy_resolver=policy_resolver,
        max_failover_attempts=gateway_settings.max_failover_attempts,
        usage_sink=_make_usage_sink(),
    )

    health_poller = None
    if gateway_settings.enable_health_poller:
        health_poller = HealthPoller(
            registry.backends_by_model,
            provider,
            circuit_breaker,
            interval_seconds=gateway_settings.health_check_interval_seconds,
        )
        health_poller.start()

    logger.info(
        "Gateway service ready (models=%s). Callers must authenticate with a "
        "seeded tenant API key — see gateway/admin/seed.py.",
        list(registry.backends_by_model.keys()),
    )

    return GatewayRuntime(
        service=service,
        redis=redis,
        health_poller=health_poller,
    )
