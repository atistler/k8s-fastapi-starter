"""JSON logging formatter and Datadog log correlation."""

import json
import logging
import sys

from app.logging import JsonFormatter, configure_logging


def _record(**kwargs: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in kwargs.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_valid_json_with_core_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record()))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload


def test_formatter_includes_extra_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record(app_env="local")))

    assert payload["app_env"] == "local"


def test_formatter_includes_datadog_correlation_attrs() -> None:
    # ddtrace injects these dotted attributes onto the record when
    # DD_LOGS_INJECTION is enabled; the formatter must surface them.
    record = _record()
    setattr(record, "dd.trace_id", "123")
    setattr(record, "dd.span_id", "456")
    setattr(record, "dd.service", "k8s-fastapi-starter")
    setattr(record, "dd.env", "prod")
    setattr(record, "dd.version", "0.1.0")

    payload = json.loads(JsonFormatter().format(record))

    assert payload["dd.trace_id"] == "123"
    assert payload["dd.span_id"] == "456"
    assert payload["dd.service"] == "k8s-fastapi-starter"
    assert payload["dd.env"] == "prod"
    assert payload["dd.version"] == "0.1.0"


def test_formatter_omits_absent_or_zero_correlation_attrs() -> None:
    # When no span is active ddtrace sets ids to "0"; those are not useful noise.
    record = _record()
    setattr(record, "dd.trace_id", "0")

    payload = json.loads(JsonFormatter().format(record))

    assert "dd.trace_id" not in payload


def test_formatter_serialises_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="app.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exception"]


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG")
    configure_logging("DEBUG")

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.DEBUG
