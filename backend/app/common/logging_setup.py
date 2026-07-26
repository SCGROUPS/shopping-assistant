"""Making log records say what happened, including the parts passed as `extra`.

There was no logging configuration at all. Python's fallback handler prints the
formatted message and nothing else, so every `logger.warning(..., extra={...})`
in this codebase threw its structured fields away. The first production run of
the translation worker emitted several hundred lines reading exactly
`translation.failed`, with the job id, the classification and the provider's
error message all attached to the record and none of them visible. Diagnosing it
meant guessing.

JSON lines rather than prose because these go to Log Analytics, where a line is
queryable only if it has fields.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

# Everything the logging module puts on a record by itself. Anything else came
# from an `extra=`, and is the part worth reading.
_BUILTIN = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _BUILTIN or key.startswith("_"):
                continue
            payload[key] = value if isinstance(value, str | int | float | bool | None) else repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger, replacing any earlier one.

    Idempotent: the CLI and the API both call it, and a job that ran both would
    otherwise log everything twice.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # These are conversational at INFO and say nothing we do not already log.
    for noisy in ("httpx", "httpcore", "azure.core.pipeline.policies.http_logging_policy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
