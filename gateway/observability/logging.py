"""Structured JSON logging for the gateway.

Replaces the emoji-decorated free-text log lines in the pre-gateway
app.py — those are fine for a human tailing one process, but not for
aggregating across replicas or alerting on. `app.py`'s FastAPI-app-shell
code keeps its existing logging style (CLAUDE.md "Working conventions");
this module only applies to `gateway/`.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from gateway.config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
