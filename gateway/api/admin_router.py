"""Read-only admin/usage routes.

v1 deliberately ships no tenant/API-key CRUD API (PRD.md §10c — config-
seeded instead, see gateway/admin/seed.py). This router is limited to what
a tenant needs to see its own usage.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.api.deps import authenticated_tenant, db_session
from gateway.budget.tracker import current_period_key
from gateway.db.orm import UsageRecordORM
from gateway.models.tenant import ApiKey, Tenant

router = APIRouter(prefix="/v1", tags=["admin"])


@router.get("/usage")
async def get_usage(
    tenant_and_key: tuple[Tenant, ApiKey] = Depends(authenticated_tenant),
    session: AsyncSession = Depends(db_session),
) -> dict:
    tenant, _ = tenant_and_key
    period_key = current_period_key("monthly", now=datetime.now(timezone.utc))

    result = await session.execute(
        select(UsageRecordORM).where(
            UsageRecordORM.tenant_id == tenant.id,
            UsageRecordORM.budget_period_key == period_key,
        )
    )
    records = result.scalars().all()

    return {
        "tenant_id": str(tenant.id),
        "period": period_key,
        "requests": len(records),
        "prompt_tokens": sum(r.prompt_tokens for r in records),
        "completion_tokens": sum(r.completion_tokens for r in records),
        "by_status": {
            status: sum(1 for r in records if r.status == status)
            for status in {r.status for r in records}
        },
    }
