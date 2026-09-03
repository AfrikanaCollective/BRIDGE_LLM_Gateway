"""API key -> Tenant resolution.

See ARCHITECTURE.md §3 step 1 and CLAUDE.md "Secrets" — keys are stored as
SHA-256 hashes, never logged or returned after creation. A missing,
malformed, unknown, or revoked key raises AuthenticationError; there is no
anonymous path through the gateway.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.exceptions import AuthenticationError
from gateway.db.orm import ApiKeyORM, TenantORM
from gateway.models.tenant import ApiKey, Tenant


def hash_api_key(plaintext_key: str) -> str:
    return hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()


def key_prefix(plaintext_key: str) -> str:
    return plaintext_key[:12]


async def resolve_tenant(
    session: AsyncSession, authorization_header: str | None
) -> tuple[Tenant, ApiKey]:
    """Resolve a raw ``Authorization`` header value to (Tenant, ApiKey).

    Raises AuthenticationError for any of: missing header, malformed
    "Bearer <key>" shape, unknown key, or revoked key. Deliberately does not
    distinguish these cases in the response body (avoid leaking whether a
    key format is "close" to valid) though the message may differ in logs.
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise AuthenticationError("Missing or malformed Authorization header")

    plaintext_key = authorization_header.removeprefix("Bearer ").strip()
    if not plaintext_key:
        raise AuthenticationError("Empty API key")

    key_hash = hash_api_key(plaintext_key)

    result = await session.execute(select(ApiKeyORM).where(ApiKeyORM.key_hash == key_hash))
    api_key_row = result.scalar_one_or_none()
    if api_key_row is None or api_key_row.revoked_at is not None:
        raise AuthenticationError("Invalid or revoked API key")

    tenant_row = await session.get(TenantORM, api_key_row.tenant_id)
    if tenant_row is None or tenant_row.status != "active":
        raise AuthenticationError("Tenant not found or suspended")

    return Tenant.model_validate(tenant_row), ApiKey.model_validate(api_key_row)
