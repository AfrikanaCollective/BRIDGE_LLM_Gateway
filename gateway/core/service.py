"""GatewayService — the single choke point every LLM request passes through.

See ARCHITECTURE.md §3 for the full step-by-step lifecycle this
implements, and ARCHITECTURE-ESSENTIALS.md "One code path to Ollama" for
why nothing else in this codebase is allowed to call a provider directly.

Every HTTP route calls `handle_request()` (chat) or `handle_embedding_request()`
(embeddings) — the gateway's own `/v1/*` routes (gateway/api/router.py) and
app.py's legacy-shaped `/generate-with-image` handler alike. Both require a
real, seeded tenant's API key (gateway/api/deps.py::authenticated_tenant);
there is no internal/implicit tenant that bypasses auth — see
ARCHITECTURE.md §9 for why (the ITF/NAR pipeline that used to call this
in-process was removed from this repo; the current caller is a separate,
external web app).

`_dispatch()` holds the request-shape-agnostic spine of the pipeline (rate
limit -> budget reserve -> routed failover -> budget reconcile/release ->
usage record) shared by both public entrypoints, so the hard invariants
(default deny, fail-closed rate limiting, no unbounded queuing, no
200-with-error-body) apply identically to every request kind instead of
being reimplemented per kind.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, TypeVar
from uuid import UUID

from gateway.budget.tracker import BudgetTracker
from gateway.core.exceptions import (
    AllProvidersUnavailable,
    BudgetExceeded,
    GatewayError,
    ModelNotEntitled,
    ProviderTimeout,
)
from gateway.models.chat import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionUsage
from gateway.models.embedding import EmbeddingRequest, EmbeddingResponse, EmbeddingUsage
from gateway.models.policy import BudgetPolicy, RateLimitPolicy
from gateway.models.provider import ProviderBackend
from gateway.models.tenant import ApiKey, Tenant
from gateway.models.usage import UsageRecord, UsageStatus
from gateway.observability.metrics import (
    BUDGET_REJECTIONS_TOTAL,
    FAILOVER_ATTEMPTS_TOTAL,
    RATE_LIMIT_REJECTIONS_TOTAL,
    REQUEST_LATENCY_SECONDS,
    REQUESTS_TOTAL,
    TOKENS_TOTAL,
)
from gateway.providers.base import EmbeddingProvider, LLMProvider
from gateway.ratelimit.token_bucket import TokenBucketLimiter
from gateway.routing.circuit_breaker import CircuitBreaker
from gateway.routing.registry import BackendRegistry

ResultT = TypeVar("ResultT")


@dataclass
class PolicyResolver:
    """Looks up the RateLimitPolicy/BudgetPolicy for a tenant+model,
    falling back to a "*" wildcard policy, and to default-deny (raises
    ModelNotEntitled) if neither exists. See ARCHITECTURE.md §4.2 and
    CLAUDE.md "Default deny"."""

    rate_limit_policies: dict[tuple[UUID, str], RateLimitPolicy]
    budget_policies: dict[tuple[UUID, str], BudgetPolicy]

    def rate_limit_for(self, tenant_id: UUID, model: str) -> RateLimitPolicy:
        policy = self.rate_limit_policies.get((tenant_id, model)) or self.rate_limit_policies.get(
            (tenant_id, "*")
        )
        if policy is None:
            raise ModelNotEntitled(f"No rate-limit policy for tenant={tenant_id} model={model}")
        return policy

    def budget_for(self, tenant_id: UUID, model: str) -> BudgetPolicy:
        policy = self.budget_policies.get((tenant_id, model)) or self.budget_policies.get(
            (tenant_id, "*")
        )
        if policy is None:
            raise ModelNotEntitled(f"No budget policy for tenant={tenant_id} model={model}")
        return policy


def _estimate_embedding_tokens(request: EmbeddingRequest) -> int:
    """Worst-case prompt-token estimate for the pre-dispatch budget
    reservation, reconciled to the real prompt_eval_count once Ollama
    responds — same reserve-then-reconcile pattern chat requests use with
    `num_predict` (gateway/budget/tracker.py). There's no embedding
    equivalent of num_predict to read the estimate off, so this falls back
    to a standard ~4-chars-per-token heuristic; exactness doesn't matter
    since reconcile() immediately corrects it to the real count.
    """
    total_chars = sum(len(text) for text in request.inputs)
    return max(1, total_chars // 4)


@dataclass
class _DispatchOutcome(Generic[ResultT]):
    request_id: UUID
    served_model: str
    backend_used: str
    result: ResultT
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class GatewayService:
    def __init__(
        self,
        *,
        rate_limiter: TokenBucketLimiter,
        budget_tracker: BudgetTracker,
        registry: BackendRegistry,
        circuit_breaker: CircuitBreaker,
        provider: LLMProvider,
        embedding_provider: EmbeddingProvider,
        policy_resolver: PolicyResolver,
        max_failover_attempts: int,
        usage_sink,  # async callable(UsageRecord) -> None; see gateway/admin or db layer
    ):
        self._rate_limiter = rate_limiter
        self._budget_tracker = budget_tracker
        self._registry = registry
        self._circuit_breaker = circuit_breaker
        self._provider = provider
        self._embedding_provider = embedding_provider
        self._policies = policy_resolver
        self._max_failover_attempts = max_failover_attempts
        self._usage_sink = usage_sink

    async def handle_request(
        self, *, tenant: Tenant, api_key: ApiKey, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        async def call_provider(backend: ProviderBackend):
            return await self._provider.generate(backend=backend, request=request)

        outcome = await self._dispatch(
            tenant=tenant,
            api_key=api_key,
            model=request.model,
            estimated_tokens=request.num_predict,
            get_backends=self._registry.get_backends,
            call_provider=call_provider,
            usage_from_result=lambda r: (r.prompt_tokens, r.completion_tokens),
        )
        return ChatCompletionResponse(
            id=outcome.request_id,
            model=outcome.served_model,  # the model that actually answered — see ChatCompletionResponse.model
            backend_used=outcome.backend_used,
            content=outcome.result.content,
            finish_reason=outcome.result.finish_reason,
            usage=ChatCompletionUsage(
                prompt_tokens=outcome.prompt_tokens, completion_tokens=outcome.completion_tokens
            ),
            latency_ms=outcome.latency_ms,
        )

    async def handle_embedding_request(
        self, *, tenant: Tenant, api_key: ApiKey, request: EmbeddingRequest
    ) -> EmbeddingResponse:
        async def call_provider(backend: ProviderBackend):
            return await self._embedding_provider.embed(backend=backend, request=request)

        outcome = await self._dispatch(
            tenant=tenant,
            api_key=api_key,
            model=request.model,
            estimated_tokens=_estimate_embedding_tokens(request),
            get_backends=self._registry.get_embedding_backends,
            call_provider=call_provider,
            usage_from_result=lambda r: (r.prompt_tokens, 0),  # no completion tokens for an embedding call
        )
        return EmbeddingResponse(
            id=outcome.request_id,
            model=outcome.served_model,
            backend_used=outcome.backend_used,
            embeddings=outcome.result.embeddings,
            usage=EmbeddingUsage(prompt_tokens=outcome.prompt_tokens),
            latency_ms=outcome.latency_ms,
        )

    async def _dispatch(
        self,
        *,
        tenant: Tenant,
        api_key: ApiKey,
        model: str,
        estimated_tokens: int,
        get_backends: Callable[[str], Awaitable[list[ProviderBackend]]],
        call_provider: Callable[[ProviderBackend], Awaitable[ResultT]],
        usage_from_result: Callable[[ResultT], tuple[int, int]],
    ) -> _DispatchOutcome[ResultT]:
        request_id = uuid.uuid4()
        started = time.monotonic()
        status: UsageStatus = "success"
        prompt_tokens = completion_tokens = 0
        backend_used: str | None = None
        served_model: str | None = None
        failover_attempts = 0
        reservation = None

        try:
            rate_limit_policy = self._policies.rate_limit_for(tenant.id, model)
            budget_policy = self._policies.budget_for(tenant.id, model)

            try:
                await self._rate_limiter.check_and_consume(
                    tenant_id=tenant.id,
                    model=model,
                    requests_per_minute=rate_limit_policy.requests_per_minute,
                    burst=rate_limit_policy.burst,
                )
            except GatewayError:
                status = "rate_limited"
                RATE_LIMIT_REJECTIONS_TOTAL.labels(tenant=str(tenant.id)).inc()
                raise

            try:
                reservation = await self._budget_tracker.reserve(
                    tenant_id=tenant.id,
                    model=model,
                    policy=budget_policy,
                    estimated_tokens=estimated_tokens,
                )
            except BudgetExceeded:
                status = "budget_exceeded"
                BUDGET_REJECTIONS_TOTAL.labels(tenant=str(tenant.id), model=model).inc()
                raise

            backends = await get_backends(model)

            result: ResultT | None = None
            last_error: GatewayError | None = None
            for attempt, backend in enumerate(backends[: self._max_failover_attempts]):
                if attempt > 0:
                    failover_attempts += 1
                    FAILOVER_ATTEMPTS_TOTAL.labels(
                        model=model,
                        from_backend=backends[attempt - 1].id,
                        to_backend=backend.id,
                    ).inc()
                try:
                    result = await call_provider(backend)
                    backend_used = backend.id
                    served_model = result.served_model
                    prompt_tokens, completion_tokens = usage_from_result(result)
                    await self._circuit_breaker.record_success(backend.id, model)
                    break
                except (ProviderTimeout, AllProvidersUnavailable) as exc:
                    # Feed the circuit breaker from real traffic, not just the
                    # background health poller — a backend that starts
                    # failing live requests must be skipped on the *next*
                    # request too, not just after its next scheduled health
                    # check (ARCHITECTURE.md §6.3/§6.4).
                    await self._circuit_breaker.record_failure(backend.id, model)
                    last_error = exc
                    continue
            else:
                status = "all_providers_down" if not isinstance(last_error, ProviderTimeout) else "timeout"
                raise last_error or AllProvidersUnavailable(f"No backends available for {model}")

            await self._budget_tracker.reconcile(reservation, prompt_tokens + completion_tokens)

            TOKENS_TOTAL.labels(tenant=str(tenant.id), model=model, kind="prompt").inc(prompt_tokens)
            TOKENS_TOTAL.labels(tenant=str(tenant.id), model=model, kind="completion").inc(completion_tokens)

            return _DispatchOutcome(
                request_id=request_id,
                served_model=served_model,
                backend_used=backend_used,
                result=result,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

        except GatewayError as exc:
            if reservation is not None and status not in ("success",):
                # Request never produced tokens (rejected pre-dispatch or all
                # backends failed) — release the full reservation rather
                # than leaving it counted against the tenant's budget.
                await self._budget_tracker.release(reservation)
            if status == "success":
                status = "provider_error"
            exc.request_id = request_id
            raise
        finally:
            latency_ms = int((time.monotonic() - started) * 1000)
            REQUESTS_TOTAL.labels(tenant=str(tenant.id), model=model, status=status).inc()
            REQUEST_LATENCY_SECONDS.labels(tenant=str(tenant.id), model=model).observe(latency_ms / 1000)
            await self._usage_sink(
                UsageRecord(
                    id=uuid.uuid4(),
                    request_id=request_id,
                    tenant_id=tenant.id,
                    api_key_id=api_key.id,
                    model=model,
                    served_model=served_model,
                    backend_id=backend_used,
                    status=status,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    failover_attempts=failover_attempts,
                    budget_period_key=reservation.period_key if reservation else "unknown",
                    created_at=datetime.now(timezone.utc),
                )
            )
