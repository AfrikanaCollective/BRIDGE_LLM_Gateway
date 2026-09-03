"""SQLAlchemy ORM models for the durable store.

Mirrors gateway/models/*.py (Pydantic) intentionally as a separate, simple
set of classes rather than a single double-mapped hierarchy — see
ARCHITECTURE.md §4 preamble. Postgres in production (DATABASE_URL),
SQLite for dev/tests.

Schema changes go through Alembic migrations in gateway/db/migrations/, not
create_all() in production paths (CLAUDE.md "Migrations").
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


class TenantORM(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApiKeyORM(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    key_hash: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RateLimitPolicyORM(Base):
    __tablename__ = "rate_limit_policies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False, default="*")
    requests_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    burst: Mapped[int] = mapped_column(Integer, nullable=False)


class BudgetPolicyORM(Base):
    __tablename__ = "budget_policies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False, default="*")
    period: Mapped[str] = mapped_column(String, nullable=False)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    on_exceed: Mapped[str] = mapped_column(String, default="block")
    alert_threshold_pct: Mapped[int] = mapped_column(Integer, default=80)


class UsageRecordORM(Base):
    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    api_key_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("api_keys.id"), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    served_model: Mapped[str | None] = mapped_column(String, nullable=True)
    backend_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    failover_attempts: Mapped[int] = mapped_column(Integer, default=0)
    budget_period_key: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProviderBackendORM(Base):
    """Optional durable mirror of the routing.yaml backend list, for
    dashboards/admin visibility. The routing registry (gateway/routing/
    registry.py) treats the YAML file as the source of truth at startup;
    this table is not read on the hot path."""

    __tablename__ = "provider_backends"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
