"""Seed tenants/API keys/policies from gateway/admin/tenants.yaml.

v1 has no admin CRUD API (PRD.md §10c) — this script is the only way to
add/change a tenant. Run after migrations:

    python -m gateway.admin.seed

Prints each generated plaintext key exactly once (seed time only) — copy
it somewhere safe immediately; it is not recoverable afterward
(CLAUDE.md "Secrets": only key_hash is stored).

Every caller of this gateway — including the external web app that calls
`/generate-with-image` (see ARCHITECTURE.md §9) — authenticates as a real
tenant seeded here. There is no implicit/internal tenant: run this script
and hand the resulting key to whatever's calling the gateway before it can
authenticate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.api_keys import hash_api_key, key_prefix
from gateway.config import settings
from gateway.db.orm import ApiKeyORM, BudgetPolicyORM, RateLimitPolicyORM, TenantORM
from gateway.db.session import async_session_factory, create_all_for_tests


async def _seed_tenant_entry(session: AsyncSession, entry: dict) -> TenantORM:
    """Insert a tenant + api key + policies from one `tenants.yaml` entry.
    Not idempotent — running this twice for the same entry creates a
    duplicate tenant. Intended as a one-time provisioning step per tenant,
    not something re-run on every app startup."""
    tenant = TenantORM(name=entry["name"], status="active")
    session.add(tenant)
    await session.flush()  # populate tenant.id

    plaintext_key = entry["plaintext_key"]
    session.add(
        ApiKeyORM(
            tenant_id=tenant.id,
            key_hash=hash_api_key(plaintext_key),
            prefix=key_prefix(plaintext_key),
        )
    )

    rl = entry.get("rate_limit")
    if rl:
        session.add(
            RateLimitPolicyORM(
                tenant_id=tenant.id,
                model=rl.get("model", "*"),
                requests_per_minute=rl["requests_per_minute"],
                burst=rl["burst"],
            )
        )

    budget = entry.get("budget")
    if budget:
        session.add(
            BudgetPolicyORM(
                tenant_id=tenant.id,
                model=budget.get("model", "*"),
                period=budget["period"],
                max_tokens=budget.get("max_tokens"),
                max_requests=budget.get("max_requests"),
                on_exceed=budget.get("on_exceed", "block"),
            )
        )

    return tenant


async def seed_from_config(path: str | Path = None) -> None:
    path = Path(path or settings.tenants_config_path)
    data = yaml.safe_load(path.read_text())

    async with async_session_factory() as session:
        for entry in data.get("tenants", []):
            await _seed_tenant_entry(session, entry)
            print(f"Seeded tenant={entry['name']!r} key={entry['plaintext_key']!r} (save this now)")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(create_all_for_tests())  # dev convenience; use Alembic in prod
    asyncio.run(seed_from_config())
