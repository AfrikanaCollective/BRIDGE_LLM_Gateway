"""Rate-limit and budget policy models.

See ARCHITECTURE.md §4.2. A tenant+model pair with no matching policy is
treated as NOT entitled to that model (default deny — PRD.md §10b), not
unlimited access. Callers resolving policies should look up the exact
model first, then fall back to a "*" wildcard policy if one exists, and
only then deny.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

BudgetPeriod = Literal["daily", "monthly"]
OnExceedAction = Literal["block", "warn"]


class RateLimitPolicy(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    model: str = Field(description='Model name, or "*" for all models')
    requests_per_minute: int = Field(gt=0)
    burst: int = Field(gt=0, description="Token bucket capacity")


class BudgetPolicy(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    model: str = Field(description='Model name, or "*" for all models')
    period: BudgetPeriod
    max_tokens: int | None = Field(default=None, gt=0)
    max_requests: int | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Unused in v1 — no paid providers ship yet. Kept so adding a "
            "paid provider later doesn't require a schema migration."
        ),
    )
    on_exceed: OnExceedAction = "block"
    alert_threshold_pct: int = Field(default=80, ge=1, le=100)
