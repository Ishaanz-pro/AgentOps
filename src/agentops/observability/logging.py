"""JSON structured logging with secret redaction.

Every log line carries request_id/trace_id from the active trace. A
redaction filter scrubs Gemini API keys, demo bearer tokens and
``Authorization`` headers from messages and extra fields before emission.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from agentops.observability.tracing import get_trace

_REDACT_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),         # Google API keys (AI Studio format)
    re.compile(r"\bAQ\.[0-9A-Za-z_\-]{20,}"),       # Google API keys (newer "AQ." format)
    re.compile(r"\btok-[a-z0-9]+\b"),               # demo bearer tokens
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+"),  # Authorization headers
]
_STD_ATTRS = set(logging.LogRecord("x", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pat in _REDACT_PATTERNS:
            value = pat.sub("[REDACTED]", value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        for key in list(record.__dict__):
            if key not in _STD_ATTRS:
                record.__dict__[key] = redact(record.__dict__[key])
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        trace = get_trace()
        if trace:
            payload["request_id"] = trace.request_id
            payload["trace_id"] = trace.trace_id
        for key, val in record.__dict__.items():
            if key not in _STD_ATTRS:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info)[-500:])
        return json.dumps(payload, default=str)


class _LiveStdoutHandler(logging.StreamHandler):
    """StreamHandler that always writes to the *current* sys.stdout (survives stream swaps)."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:  # ignore assignments from StreamHandler.__init__
        pass


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = _LiveStdoutHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    # Quieten chatty libraries; their payloads can include request bodies.
    for noisy in ("httpx", "httpcore", "google_genai", "google.adk"):
        logging.getLogger(noisy).setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
