"""Structured JSON logging with optional Datadog trace/log correlation.

A single JSON line is emitted per log record on stdout, which is what container
log collectors (including the Datadog Agent) expect. When ``DD_LOGS_INJECTION``
is enabled, ddtrace injects the ``dd.trace_id``/``dd.span_id``/``dd.service``/
``dd.env``/``dd.version`` attributes onto each :class:`logging.LogRecord`; this
formatter surfaces them so logs correlate with APM traces in Datadog. The
attributes are read defensively, so the formatter behaves identically whether or
not ddtrace is active (for example in local development and tests).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

# Correlation attributes ddtrace adds to log records (dotted names, so they are
# read with getattr rather than normal attribute access).
_DD_CORRELATION_ATTRS = (
    "dd.trace_id",
    "dd.span_id",
    "dd.service",
    "dd.env",
    "dd.version",
)

# Attributes present on every LogRecord; anything else is treated as a caller
# supplied ``extra=`` field and surfaced in the JSON payload.
_RESERVED_ATTRS = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render log records as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        # Any: a log payload holds heterogeneous JSON values (str, int, bool,
        # caller-supplied extras); json.dumps(default=str) serializes them all.
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(
                record.created, tz=dt.UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)

        # Datadog log/trace correlation fields (present only when ddtrace injects
        # them). Keep the dotted keys so they line up with Datadog's convention.
        for attr in _DD_CORRELATION_ATTRS:
            value = getattr(record, attr, None)
            if value not in (None, "", "0"):
                payload[attr] = value

        # Caller-provided ``extra=`` fields.
        for key, value in record.__dict__.items():
            if (
                key not in _RESERVED_ATTRS
                and key not in payload
                and not key.startswith("dd.")
            ):
                payload[key] = value

        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Install the JSON formatter on the root logger at ``level``.

    Idempotent: existing handlers are replaced so repeated calls (for example in
    tests that build the app more than once) do not emit duplicate lines.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())
