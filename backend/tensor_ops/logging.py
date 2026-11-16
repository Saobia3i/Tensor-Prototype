"""Structured JSON logging with correlation IDs and redaction. Document text never goes to logs."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone

correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)

_SENSITIVE_KEYS = re.compile(r"(pass(word)?|secret|token|authorization|api[_-]?key|cookie|text|content|prompt|body)", re.I)
_SECRET_PATTERNS = [
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]+"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def redact_secrets(text: str) -> str:
    """Remove credential-shaped strings without truncating (used before model inference)."""
    for p in _SECRET_PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text


def redact_value(v: object) -> object:
    if isinstance(v, str):
        for p in _SECRET_PATTERNS:
            v = p.sub("[REDACTED]", v)
        return v if len(v) <= 500 else v[:500] + "…[truncated]"
    if isinstance(v, dict):
        return {k: ("[REDACTED]" if _SENSITIVE_KEYS.search(str(k)) else redact_value(x)) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [redact_value(x) for x in v[:50]]
    return v


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_value(record.getMessage()),
            "correlation_id": correlation_id.get(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            data.update(redact_value(extra))  # type: ignore[arg-type]
        if record.exc_info:
            data["exc"] = redact_value(self.formatException(record.exc_info))
        return json.dumps(data, default=str)


def configure_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)


def log(logger: logging.Logger, msg: str, **fields: object) -> None:
    logger.info(msg, extra={"fields": fields})
