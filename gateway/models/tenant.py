"""Tenant and API key models.

See ARCHITECTURE.md §4.1. Tenants and keys are seeded from config in v1
(gateway/admin/seed.py) — there is no self-serve CRUD API yet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

TenantStatus = Literal["active", "suspended"]


class Tenant(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: TenantStatus = "active"
    created_at: datetime


class ApiKey(BaseModel):
    """A hashed API key belonging to a tenant.

    ``key_hash`` is a SHA-256 hex digest of the plaintext key. The plaintext
    is only ever seen at creation time (gateway/admin/seed.py) and is never
    stored or logged. ``prefix`` (e.g. "gw_live_ab12") is safe to display/log
    for identifying a key without revealing it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    key_hash: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
