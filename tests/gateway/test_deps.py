"""Tests for gateway/api/deps.py's DB session scoping.

`authenticated_tenant` is a dependency of every gateway route
(/v1/chat/completions, /v1/embeddings, /v1/generate-with-image) — routes
that can run for a long time on the provider round-trip plus failover
attempts (ARCHITECTURE.md §6.4). It must resolve the tenant with its own
short-lived DB session and release it immediately, NOT hold a
request-lifetime session via `Depends(db_session)` — a `yield`-based
FastAPI dependency stays checked out from the pool until the whole
request completes, and under backend slowness that starves the pool for
every tenant, not just the one hitting the slow backend (this was an
actual incident: `sqlalchemy.exc.TimeoutError: QueuePool limit ...
reached` correlated with raw 500s on /v1/chat/completions while Ollama
backends were flaky).
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.api import deps as deps_module
from gateway.auth.api_keys import hash_api_key, key_prefix
from gateway.db.orm import ApiKeyORM, Base, TenantORM


@pytest.mark.asyncio
async def test_authenticated_tenant_releases_its_session_before_returning(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    plaintext_key = "gw_test_key"
    async with session_factory() as seed_session:
        tenant = TenantORM(
            id=uuid.uuid4(), name="test-tenant", status="active", created_at=datetime.now(timezone.utc)
        )
        seed_session.add(tenant)
        await seed_session.flush()
        seed_session.add(
            ApiKeyORM(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                key_hash=hash_api_key(plaintext_key),
                prefix=key_prefix(plaintext_key),
                created_at=datetime.now(timezone.utc),
            )
        )
        await seed_session.commit()
        seeded_tenant_id = tenant.id

    session_events: list[str] = []

    @asynccontextmanager
    async def tracked_get_session():
        async with session_factory() as session:
            session_events.append("open")
            try:
                yield session
            finally:
                session_events.append("closed")

    monkeypatch.setattr(deps_module, "get_session", tracked_get_session)

    resolved_tenant, _ = await deps_module.authenticated_tenant(authorization=f"Bearer {plaintext_key}")

    # The session must already be closed by the time authenticated_tenant
    # returns — proving it isn't held open via a request-scoped yield
    # dependency for the rest of the request (rate limit -> route ->
    # provider call -> usage write).
    assert session_events == ["open", "closed"]
    assert resolved_tenant.id == seeded_tenant_id

    await engine.dispose()
