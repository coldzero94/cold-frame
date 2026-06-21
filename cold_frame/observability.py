"""Structured stdlib-logging JSON to stderr with content redaction (I16).

Core uses the stdlib ``logging`` module (NOT structlog — keeps core deps = pydantic+numpy).
``redact_filter`` masks every denylisted field so note content / source raw text / secrets
never reach a log sink. Only ids/hashes/tasks/counters/is_local/token-counts are emitted.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Final

# Fields whose VALUES must never be logged (I16). Substring match on the key.
REDACT_DENYLIST: Final[tuple[str, ...]] = (
    "content",
    "text",
    "user",
    "payload",
    "raw",
    "span",
)
REDACTED: Final[str] = "[REDACTED]"

# Standard LogRecord attributes we never echo into the JSON `extra` blob.
_RESERVED: Final[frozenset[str]] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }
)


def _is_denylisted(key: str) -> bool:
    lowered = key.lower()
    return any(bad in lowered for bad in REDACT_DENYLIST)


class RedactFilter(logging.Filter):
    """Masks denylisted ``extra`` fields on the record before formatting (I16)."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(vars(record)):
            if key not in _RESERVED and _is_denylisted(key):
                setattr(record, key, REDACTED)
        return True


class JsonFormatter(logging.Formatter):
    """Renders each record as a single-line JSON object (structured logging)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key in _RESERVED or key == "message":
                continue
            payload[key] = REDACTED if _is_denylisted(key) else value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str, ensure_ascii=False)


# Module-level filter instance reused across loggers.
redact_filter: Final[RedactFilter] = RedactFilter()


def get_logger(name: str, *, unsafe_trace: bool = False) -> logging.Logger:
    """Return a configured JSON logger writing to stderr (I16).

    ``unsafe_trace=True`` is the ONLY content path (off by default, ``--unsafe-trace``).
    """
    logger = logging.getLogger(name)
    if not getattr(logger, "_cold_frame_configured", False):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        if not unsafe_trace:
            handler.addFilter(redact_filter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._cold_frame_configured = True  # type: ignore[attr-defined]
    return logger
