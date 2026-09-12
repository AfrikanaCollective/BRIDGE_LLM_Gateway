"""Embedding-path tests.

Covers: the routing.yaml `embedding_models:` section resolving separately
from `models:` (gateway/routing/registry.py), and
GatewayService.handle_embedding_request() going through the same shared
pipeline invariants as chat requests (default deny, fail-closed rate
limiting, budget reserve/reconcile/release, failover) — see
ARCHITECTURE-ESSENTIALS.md "Request pipeline" and CLAUDE.md "Default deny."
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fakeredis.aioredis import FakeRedis

from gateway.budget.tracker import BudgetTracker
from gateway.config import settings
from gateway.core.exceptions import AllProvidersUnavailable, BudgetExceeded, ModelNotEntitled
from gateway.core.service import GatewayService, PolicyResolver, _estimate_embedding_tokens
from gateway.models.embedding import EmbeddingRequest
from gateway.models.policy import BudgetPolicy, RateLimitPolicy
from gateway.models.provider import ProviderBackend
from gateway.models.tenant import ApiKey, Tenant
from gateway.providers.base import EmbeddingProviderResult
from gateway.ratelimit.token_bucket import TokenBucketLimiter
from gateway.routing.circuit_breaker import CircuitBreaker
from gateway.routing.registry import BackendRegistry

MODEL = "qllama/bge-large-en-v1.5:latest"


def test_estimate_embedding_tokens_uses_char_heuristic():
    assert _estimate_embedding_tokens(EmbeddingRequest(model=MODEL, input="a" * 40)) == 10
    assert _estimate_embedding_tokens(EmbeddingRequest(model=MODEL, input=["ab", "cd"])) == 1  # max(1, 4 // 4)


def test_embedding_request_input_normalizes_to_a_list():
    assert EmbeddingRequest(model=MODEL, input="hello").inputs == ["hello"]
    assert EmbeddingRequest(model=MODEL, input=["a", "b"]).inputs == ["a", "b"]


def test_real_routing_yaml_keeps_embedding_models_separate_from_chat_models():
    """gateway/admin/routing.yaml's embedding_models: section must resolve
    independently of models: — an embedding model must not show up as a
    routable chat model and vice versa."""
    breaker = CircuitBreaker(FakeRedis(), failure_threshold=3, cooldown_seconds=30)
    registry = BackendRegistry.from_yaml(settings.routing_config_path, breaker)

    assert MODEL in registry.embedding_backends_by_model
    assert MODEL not in registry.backends_by_model
    assert "qwen3.5:9b" in registry.backends_by_model
    assert "qwen3.5:9b" not in registry.embedding_backends_by_model


class _FakeEmbeddingProvider:
    """Serves canned results per backend id, or raises for backends listed
    in `failing`. Mirrors OllamaProvider's embed() contract without any
    HTTP."""

    def __init__(self, *, failing: set[str] = frozenset()):
        self.failing = failing
        self.calls: list[str] = []

    async def embed(self, *, backend: ProviderBackend, request: EmbeddingRequest) -> EmbeddingProviderResult:
        self.calls.append(backend.id)
        if backend.id in self.failing:
            raise AllProvidersUnavailable(f"{backend.id} is down")
        return EmbeddingProviderResult(
            embeddings=[[0.1, 0.2, 0.3] for _ in request.inputs],
            prompt_tokens=7,
            latency_ms=5,
            served_model=backend.target_model or request.model,
        )

    async def embedding_health_check(self, *, backend: ProviderBackend, model: str) -> None:
        if backend.id in self.failing:
            raise AllProvidersUnavailable(f"{backend.id} is down")


def _tenant() -> tuple[Tenant, ApiKey]:
    tenant = Tenant(id=uuid.uuid4(), name="t", created_at=datetime.now(timezone.utc))
    api_key = ApiKey(
        id=uuid.uuid4(), tenant_id=tenant.id, key_hash="h", prefix="gw_x", created_at=datetime.now(timezone.utc)
    )
    return tenant, api_key


def _build_service(*, embedding_provider, backends_by_model, policies: PolicyResolver) -> GatewayService:
    redis = FakeRedis()
    circuit_breaker = CircuitBreaker(redis, failure_threshold=3, cooldown_seconds=30)
    registry = BackendRegistry({}, circuit_breaker, embedding_backends_by_model=backends_by_model)
    recorded = []

    async def usage_sink(record):
        recorded.append(record)

    service = GatewayService(
        rate_limiter=TokenBucketLimiter(redis),
        budget_tracker=BudgetTracker(redis),
        registry=registry,
        circuit_breaker=circuit_breaker,
        provider=None,  # unused by the embedding path
        embedding_provider=embedding_provider,
        policy_resolver=policies,
        max_failover_attempts=3,
        usage_sink=usage_sink,
    )
    service._test_usage_records = recorded  # type: ignore[attr-defined]
    return service


def _policies(tenant_id, *, max_tokens=1_000_000) -> PolicyResolver:
    return PolicyResolver(
        rate_limit_policies={
            (tenant_id, MODEL): RateLimitPolicy(tenant_id=tenant_id, model=MODEL, requests_per_minute=60, burst=10)
        },
        budget_policies={
            (tenant_id, MODEL): BudgetPolicy(
                tenant_id=tenant_id, model=MODEL, period="monthly", max_tokens=max_tokens, on_exceed="block"
            )
        },
    )


@pytest.mark.asyncio
async def test_handle_embedding_request_happy_path():
    tenant, api_key = _tenant()
    backend = ProviderBackend(id="embed-primary", base_url="http://x", models=[MODEL])
    provider = _FakeEmbeddingProvider()
    service = _build_service(
        embedding_provider=provider, backends_by_model={MODEL: [backend]}, policies=_policies(tenant.id)
    )

    request = EmbeddingRequest(model=MODEL, input=["hello", "world"])
    response = await service.handle_embedding_request(tenant=tenant, api_key=api_key, request=request)

    assert response.model == MODEL
    assert response.backend_used == "embed-primary"
    assert len(response.embeddings) == 2
    assert response.usage.prompt_tokens == 7
    assert provider.calls == ["embed-primary"]
    assert service._test_usage_records[0].completion_tokens == 0
    assert service._test_usage_records[0].status == "success"


@pytest.mark.asyncio
async def test_handle_embedding_request_fails_over_to_next_backend():
    tenant, api_key = _tenant()
    primary = ProviderBackend(id="embed-primary", base_url="http://x", models=[MODEL], priority=0)
    secondary = ProviderBackend(id="embed-secondary", base_url="http://y", models=[MODEL], priority=1)
    provider = _FakeEmbeddingProvider(failing={"embed-primary"})
    service = _build_service(
        embedding_provider=provider,
        backends_by_model={MODEL: [primary, secondary]},
        policies=_policies(tenant.id),
    )

    response = await service.handle_embedding_request(
        tenant=tenant, api_key=api_key, request=EmbeddingRequest(model=MODEL, input="hello")
    )

    assert response.backend_used == "embed-secondary"
    assert provider.calls == ["embed-primary", "embed-secondary"]


@pytest.mark.asyncio
async def test_handle_embedding_request_default_deny_without_policy():
    tenant, api_key = _tenant()
    backend = ProviderBackend(id="embed-primary", base_url="http://x", models=[MODEL])
    service = _build_service(
        embedding_provider=_FakeEmbeddingProvider(),
        backends_by_model={MODEL: [backend]},
        policies=PolicyResolver(rate_limit_policies={}, budget_policies={}),
    )

    with pytest.raises(ModelNotEntitled):
        await service.handle_embedding_request(
            tenant=tenant, api_key=api_key, request=EmbeddingRequest(model=MODEL, input="hello")
        )


@pytest.mark.asyncio
async def test_handle_embedding_request_releases_reservation_on_budget_exceeded_downstream_failure():
    """All backends down after a successful budget reservation must release
    the full reservation, not leave it charged against the tenant (same
    invariant handle_request enforces for chat — ARCHITECTURE.md §12(a))."""
    tenant, api_key = _tenant()
    backend = ProviderBackend(id="embed-primary", base_url="http://x", models=[MODEL])
    service = _build_service(
        embedding_provider=_FakeEmbeddingProvider(failing={"embed-primary"}),
        backends_by_model={MODEL: [backend]},
        policies=_policies(tenant.id, max_tokens=100),
    )

    with pytest.raises(AllProvidersUnavailable):
        await service.handle_embedding_request(
            tenant=tenant, api_key=api_key, request=EmbeddingRequest(model=MODEL, input="hello")
        )

    # Reservation was released, so a second full-budget request still fits.
    service._embedding_provider.failing = set()  # "recover" the backend
    response = await service.handle_embedding_request(
        tenant=tenant, api_key=api_key, request=EmbeddingRequest(model=MODEL, input="hello")
    )
    assert response.backend_used == "embed-primary"


@pytest.mark.asyncio
async def test_handle_embedding_request_budget_exceeded_blocks_before_any_provider_call():
    tenant, api_key = _tenant()
    backend = ProviderBackend(id="embed-primary", base_url="http://x", models=[MODEL])
    provider = _FakeEmbeddingProvider()
    service = _build_service(
        embedding_provider=provider,
        backends_by_model={MODEL: [backend]},
        policies=_policies(tenant.id, max_tokens=1),  # any real request estimate exceeds this
    )

    with pytest.raises(BudgetExceeded):
        await service.handle_embedding_request(
            tenant=tenant, api_key=api_key, request=EmbeddingRequest(model=MODEL, input="a long enough input")
        )
    assert provider.calls == []  # rejected pre-dispatch, never reached the provider
