"""Durable usage record model.

See ARCHITECTURE.md §4.3. One UsageRecord is written per completed or
failed request, asynchronously, off the hot path. This is the audit trail
that answers "what did tenant X consume" without grepping logs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

UsageStatus = Literal[
    "success",
    "provider_error",
    "timeout",
    "rate_limited",
    "budget_exceeded",
    "all_providers_down",
]


class UsageRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    request_id: UUID
    tenant_id: UUID
    api_key_id: UUID
    model: str
    """The model the TENANT requested — this is what rate-limit/budget
    policies are keyed on (gateway/models/policy.py), even if a different
    model actually served the request (see served_model)."""
    served_model: str | None = None
    """The model that actually generated the response, set on success —
    may differ from `model` when a failover backend runs a different model
    (ARCHITECTURE.md §6.5). None when no backend produced output (rejected
    pre-dispatch, or every backend failed)."""
    backend_id: str | None = None
    status: UsageStatus
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    failover_attempts: int = 0
    budget_period_key: str
    """The budget period this request was billed against (e.g. "2026-09"),
    fixed at request start. Prevents a request spanning a period boundary
    from leaking usage across periods or double-counting — see
    ARCHITECTURE.md §12(b)."""
    created_at: datetime
