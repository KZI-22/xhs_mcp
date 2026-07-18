"""Logging helpers that keep stdio stdout clean and redact common secrets."""

from __future__ import annotations

import logging
import re
import sys
from typing import Any


_URL_CREDENTIALS = re.compile(r"(?P<prefix>://)(?P<credentials>[^/@\s]+)@")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<key>xsec[_-]?token|authorization|cookie|password|proxy)"
    r"(?P<separator>\s*[:=]\s*)"
    r"(?P<value>[^,}\]\s]+)"
)


def redact_token(value: str, *, edge: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= edge * 2:
        return "***"
    return f"{value[:edge]}***{value[-edge:]}"


def redact_text(value: Any) -> str:
    text = str(value)
    text = _URL_CREDENTIALS.sub(r"\g<prefix>***:***@", text)
    return _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}***", text
    )


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure the package logger with a stderr-only handler."""

    logger = logging.getLogger("xhs_read_mcp")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger

