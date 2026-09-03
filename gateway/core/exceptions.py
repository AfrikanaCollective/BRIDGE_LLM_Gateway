"""Typed gateway error hierarchy.

See ARCHITECTURE.md §4.6. No handler in this codebase may return HTTP 200
with an error described in the body — that was a real bug in the
pre-gateway code (app.py returning 200 with "response": "Time out error").
Every failure mode here maps to a real status code and a JSON body shaped
like: {"error": {"type": "...", "message": "...", "request_id": "..."}}.
"""

from __future__ import annotations

from uuid import UUID


class GatewayError(Exception):
    """Base class for all typed gateway errors."""

    http_status: int = 500
    error_type: str = "gateway_error"

    def __init__(self, message: str, *, request_id: UUID | None = None):
        super().__init__(message)
        self.message = message
        self.request_id = request_id

    def to_body(self) -> dict:
        return {
            "error": {
                "type": self.error_type,
                "message": self.message,
                "request_id": str(self.request_id) if self.request_id else None,
            }
        }


class AuthenticationError(GatewayError):
    http_status = 401
    error_type = "authentication_error"


class RateLimitExceeded(GatewayError):
    http_status = 429
    error_type = "rate_limit_exceeded"

    def __init__(self, message: str, *, retry_after_seconds: float, request_id: UUID | None = None):
        super().__init__(message, request_id=request_id)
        self.retry_after_seconds = retry_after_seconds


class BudgetExceeded(GatewayError):
    http_status = 402
    error_type = "budget_exceeded"


class ModelNotEntitled(GatewayError):
    """Tenant has no RateLimitPolicy/BudgetPolicy for this model — default deny."""

    http_status = 403
    error_type = "model_not_entitled"


class AllProvidersUnavailable(GatewayError):
    http_status = 503
    error_type = "all_providers_unavailable"


class ProviderTimeout(GatewayError):
    http_status = 504
    error_type = "provider_timeout"


class PayloadTooLarge(GatewayError):
    http_status = 413
    error_type = "payload_too_large"
