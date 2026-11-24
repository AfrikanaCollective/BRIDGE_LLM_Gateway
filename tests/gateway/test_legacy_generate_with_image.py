"""Tests for app.py's legacy POST /generate-with-image `model` form field.

The field is optional: omitting it must keep the pre-change behaviour
(config.MODEL_NAME), so the external web app that calls this path
(ARCHITECTURE.md §9) is unaffected. A caller-supplied model is passed
through as `request.model`, which is what entitlement is keyed on — a
tenant with no policy for it must get 403 (default deny), never silently
fall back to the default model. An explicit empty value (`model=`) is
treated by FastAPI as omitted and also gets the default.

The gateway service is faked here (no Redis/DB/Ollama), but the 403 path
uses the real PolicyResolver so the default-deny lookup under test is the
production one, not a re-implementation.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import httpx

import app as app_module
from gateway.core.service import PolicyResolver
from gateway.models.chat import ChatCompletionRequest, ChatCompletionResponse, ChatCompletionUsage
from gateway.models.policy import BudgetPolicy, RateLimitPolicy
from gateway.models.tenant import ApiKey, Tenant

ENTITLED_MODEL = "qwen3.8:27b"
UNENTITLED_MODEL = "qwen3.6:35b"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeGatewayService:
    """Records the request it receives and enforces entitlement via the
    real PolicyResolver, the same lookup GatewayService._dispatch() runs
    first."""

    def __init__(self, policies: PolicyResolver) -> None:
        self._policies = policies
        self.requests: list[ChatCompletionRequest] = []

    async def handle_request(self, *, tenant: Tenant, api_key: ApiKey, request: ChatCompletionRequest):
        self.requests.append(request)
        self._policies.rate_limit_for(tenant.id, request.model)
        self._policies.budget_for(tenant.id, request.model)
        return ChatCompletionResponse(
            id=uuid.uuid4(),
            model=request.model,
            backend_used="fake-backend",
            content='{"ok": true, "n": 5}',
            finish_reason="stop",
            usage=ChatCompletionUsage(prompt_tokens=3, completion_tokens=2),
            latency_ms=1,
        )


@pytest.fixture
def tenant_and_key() -> tuple[Tenant, ApiKey]:
    now = datetime.now(timezone.utc)
    tenant = Tenant(id=uuid.uuid4(), name="test-tenant", status="active", created_at=now)
    key = ApiKey(id=uuid.uuid4(), tenant_id=tenant.id, key_hash="0" * 64, prefix="gw_test_0000", created_at=now)
    return tenant, key


@pytest.fixture
def service(tenant_and_key) -> FakeGatewayService:
    tenant, _ = tenant_and_key
    entitled = [app_module.config.MODEL_NAME, ENTITLED_MODEL]
    return FakeGatewayService(
        PolicyResolver(
            rate_limit_policies={
                (tenant.id, m): RateLimitPolicy(tenant_id=tenant.id, model=m, requests_per_minute=60, burst=10)
                for m in entitled
            },
            budget_policies={
                (tenant.id, m): BudgetPolicy(tenant_id=tenant.id, model=m, period="daily", max_requests=100)
                for m in entitled
            },
        )
    )


@pytest.fixture
async def client(monkeypatch, service, tenant_and_key):
    # ASGITransport doesn't run the lifespan: wire up only the app_state
    # the handler reads, with a fake runtime.
    monkeypatch.setitem(app_module.app_state, "gateway_runtime", SimpleNamespace(service=service))
    monkeypatch.setitem(app_module.app_state, "semaphore", asyncio.Semaphore(1))
    app_module.app.dependency_overrides[app_module._generate_with_image_auth_dep] = lambda: tenant_and_key
    try:
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app_module.app.dependency_overrides.clear()


async def _post(client: httpx.AsyncClient, **form: str) -> httpx.Response:
    return await client.post(
        "/generate-with-image",
        files={"image": ("form.png", PNG_BYTES, "image/png")},
        data={"prompt": "extract", **form},
    )


async def test_omitted_model_defaults_to_config_model_name(client, service):
    resp = await _post(client)

    assert resp.status_code == 200, resp.text
    assert service.requests[-1].model == app_module.config.MODEL_NAME
    # Legacy response contract unchanged.
    body = resp.json()
    assert set(body) == {"response", "model", "timestamp", "metrics"}
    # Existing legacy behaviour: a JSON object without a nested "response"
    # key is returned re-serialised as a string.
    assert json.loads(body["response"]) == {"ok": True, "n": 5}


async def test_explicit_entitled_model_is_passed_through(client, service):
    resp = await _post(client, model=ENTITLED_MODEL)

    assert resp.status_code == 200, resp.text
    assert service.requests[-1].model == ENTITLED_MODEL
    assert resp.json()["model"] == ENTITLED_MODEL


async def test_unentitled_model_is_rejected_403_not_downgraded(client, service):
    resp = await _post(client, model=UNENTITLED_MODEL)

    assert resp.status_code == 403, resp.text
    # Must have been evaluated as the requested model, not swapped for
    # the default the tenant *is* entitled to.
    assert service.requests[-1].model == UNENTITLED_MODEL


async def test_empty_model_falls_back_to_default_not_passed_through(client, service):
    resp = await _post(client, model="")

    assert resp.status_code == 200, resp.text
    assert service.requests[-1].model == app_module.config.MODEL_NAME
