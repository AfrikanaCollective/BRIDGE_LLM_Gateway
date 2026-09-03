"""API key hashing / auth-header parsing tests.

See ARCHITECTURE.md §3 step 1 and CLAUDE.md "Secrets" — keys are hashed,
never logged/returned in plaintext, and a missing/malformed header must
fail closed (AuthenticationError) before any DB lookup happens.

The DB-backed tests below matter more than they used to: since the ITF/NAR
pipeline was removed from this repo, there is no more internal/implicit
tenant (ARCHITECTURE.md §9) — every caller, including the external web app
that hits /generate-with-image, is authenticated exclusively through this
path. A bug here is a real auth bypass, not a convenience feature.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.auth.api_keys import hash_api_key, key_prefix, resolve_tenant
from gateway.core.exceptions import AuthenticationError
from gateway.db.orm import ApiKeyORM, Base, TenantORM


def test_hash_is_deterministic_and_not_reversible_lookalike():
    h1 = hash_api_key("gw_live_abcdef123456")
    h2 = hash_api_key("gw_live_abcdef123456")
    assert h1 == h2
    assert h1 != "gw_live_abcdef123456"
    assert len(h1) == 64  # sha256 hex digest


def test_key_prefix_is_short_and_safe_to_log():
    assert key_prefix("gw_live_abcdef123456") == "gw_live_abcd"


@pytest.mark.asyncio
async def test_missing_header_rejected_before_db_lookup():
    # session=None is fine here: a missing/malformed header must fail
    # before resolve_tenant ever touches the DB session.
    with pytest.raises(AuthenticationError):
        await resolve_tenant(session=None, authorization_header=None)


@pytest.mark.asyncio
async def test_malformed_header_rejected_before_db_lookup():
    with pytest.raises(AuthenticationError):
        await resolve_tenant(session=None, authorization_header="NotBearer abc123")


@pytest.mark.asyncio
async def test_empty_bearer_token_rejected():
    with pytest.raises(AuthenticationError):
        await resolve_tenant(session=None, authorization_header="Bearer ")


@pytest.fixture
async def db_session():
    """Isolated in-memory SQLite per test — not the module-global engine
    bound to GATEWAY_DATABASE_URL, so these tests can't leak state into
    (or pick up stale state from) a real dev database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


async def _seed_tenant(session, *, plaintext_key: str, status: str = "active", revoked: bool = False):
    tenant = TenantORM(id=uuid.uuid4(), name="web-app-test", status=status, created_at=datetime.now(timezone.utc))
    session.add(tenant)
    await session.flush()
    api_key = ApiKeyORM(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        key_hash=hash_api_key(plaintext_key),
        prefix=key_prefix(plaintext_key),
        created_at=datetime.now(timezone.utc),
        revoked_at=datetime.now(timezone.utc) if revoked else None,
    )
    session.add(api_key)
    await session.commit()
    return tenant, api_key


@pytest.mark.asyncio
async def test_unknown_key_rejected(db_session):
    await _seed_tenant(db_session, plaintext_key="gw_real_key")
    with pytest.raises(AuthenticationError):
        await resolve_tenant(db_session, "Bearer gw_totally_wrong_key")


@pytest.mark.asyncio
async def test_revoked_key_rejected(db_session):
    await _seed_tenant(db_session, plaintext_key="gw_revoked_key", revoked=True)
    with pytest.raises(AuthenticationError):
        await resolve_tenant(db_session, "Bearer gw_revoked_key")


@pytest.mark.asyncio
async def test_suspended_tenant_rejected(db_session):
    await _seed_tenant(db_session, plaintext_key="gw_suspended_key", status="suspended")
    with pytest.raises(AuthenticationError):
        await resolve_tenant(db_session, "Bearer gw_suspended_key")


@pytest.mark.asyncio
async def test_valid_key_resolves_the_seeded_tenant_exactly(db_session):
    """This is the case that matters most post-removal of the ITF/NAR
    pipeline: a valid external key must resolve to the REAL tenant that
    key belongs to — not a default, not an internal one, not "any" tenant.
    """
    seeded_tenant, seeded_key = await _seed_tenant(db_session, plaintext_key="gw_valid_key")

    resolved_tenant, resolved_key = await resolve_tenant(db_session, "Bearer gw_valid_key")

    assert resolved_tenant.id == seeded_tenant.id
    assert resolved_tenant.name == "web-app-test"
    assert resolved_key.id == seeded_key.id
    assert resolved_key.prefix == key_prefix("gw_valid_key")
