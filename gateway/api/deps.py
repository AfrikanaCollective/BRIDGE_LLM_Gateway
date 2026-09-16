"""Shared FastAPI dependencies: DB session, auth, and the GatewayService
singleton.

See ARCHITECTURE.md §3 step 1 for the auth flow this wraps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.api_keys import resolve_tenant
from gateway.core.service import GatewayService
from gateway.db.session import get_session
from gateway.models.tenant import ApiKey, Tenant


async def db_session() -> AsyncIterator[AsyncSession]:
    async with get_session() as session:
        yield session


async def authenticated_tenant(
    authorization: str | None = Header(default=None),
) -> tuple[Tenant, ApiKey]:
    """Resolves the caller's tenant with its own short-lived session,
    scoped to just this lookup — deliberately NOT `Depends(db_session)`.
    A `yield`-based FastAPI dependency stays checked out from the pool for
    the entire request, and callers of this dependency (chat/embeddings)
    can run for a long time on the provider round-trip plus failover
    attempts; holding a pooled DB connection for all of that starves the
    pool under backend slowness (this is the auth path CLAUDE.md calls
    load-bearing — a pool exhaustion here degrades every tenant, not just
    the one hitting a slow backend)."""
    async with get_session() as session:
        return await resolve_tenant(session, authorization)


def get_gateway_service(request: Request) -> GatewayService:
    """GatewayService is constructed once at app startup (see gateway/api/router.py
    `create_gateway_app` / app.py lifespan) and stashed on app.state — this
    dependency just retrieves it, it does not construct a new one per request."""
    return request.app.state.gateway_service
