"""
Structured JSON logging.
Each log line is a single JSON object — plays well with Railway log viewer
and any log aggregator (Datadog, Grafana Loki, etc.).

Usage:
    from app.logging_config import setup_logging
    setup_logging()

    import logging
    logging.info("user_registered", extra={"user_id": 42, "email": "x@y.com"})
    # → {"ts":"2026-06-21T10:00:00Z","level":"INFO","logger":"root","msg":"user_registered","user_id":42,"email":"x@y.com"}
"""

import json
import logging
import time as _time

# Keys that belong to LogRecord internals — never forward them as extras
_STDLIB_KEYS: frozenset = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname",
    "filename", "module", "exc_info", "exc_text", "stack_info",
    "lineno", "funcName", "created", "msecs", "relativeCreated",
    "thread", "threadName", "processName", "process", "message",
    "taskName", "asctime",
})


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Forward any extra= kwargs the caller passed
        for key, val in record.__dict__.items():
            if key not in _STDLIB_KEYS and not key.startswith("_"):
                obj[key] = val
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Silence chatty third-party loggers that would flood the output
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
