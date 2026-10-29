"""Structured JSON logging for application events."""

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

_CONFIGURED = False
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}
_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_BASE_RECORD_FACTORY = logging.getLogRecordFactory()
_RECORD_FACTORY_INSTALLED = False


def set_request_id(request_id: str | None) -> Token[str | None]:
    """Bind a request ID to the current context and return a reset token."""
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous request ID after a request or job finishes."""
    _REQUEST_ID.reset(token)


def get_request_id() -> str | None:
    """Return the request ID bound to the current context, if any."""
    return _REQUEST_ID.get()


def _install_record_factory() -> None:
    global _RECORD_FACTORY_INSTALLED
    if _RECORD_FACTORY_INSTALLED:
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = _BASE_RECORD_FACTORY(*args, **kwargs)
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return record

    logging.setLogRecordFactory(factory)
    _RECORD_FACTORY_INSTALLED = True


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object, including structured extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Install one JSON handler on the root logger."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    _install_record_factory()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


_install_record_factory()
