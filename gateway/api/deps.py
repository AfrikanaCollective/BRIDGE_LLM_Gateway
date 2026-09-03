"""Shared FastAPI dependencies: DB session, auth, and the GatewayService
singleton.

See ARCHITECTURE.md §3 step 1 for the auth flow this wraps.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Header, Request
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
    session: AsyncSession = Depends(db_session),
) -> tuple[Tenant, ApiKey]:
    return await resolve_tenant(session, authorization)


def get_gateway_service(request: Request) -> GatewayService:
    """GatewayService is constructed once at app startup (see gateway/api/router.py
    `create_gateway_app` / app.py lifespan) and stashed on app.state — this
    dependency just retrieves it, it does not construct a new one per request."""
    return request.app.state.gateway_service
