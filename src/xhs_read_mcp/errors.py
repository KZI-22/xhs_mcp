"""Stable application errors exposed through the MCP adapter."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping


class ErrorCode(StrEnum):
    NOT_LOGGED_IN = "NOT_LOGGED_IN"
    LOGIN_EXPIRED = "LOGIN_EXPIRED"
    LOGIN_IN_PROGRESS = "LOGIN_IN_PROGRESS"
    LOGIN_SESSION_NOT_FOUND = "LOGIN_SESSION_NOT_FOUND"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOTE_UNAVAILABLE = "NOTE_UNAVAILABLE"
    PAGE_STRUCTURE_CHANGED = "PAGE_STRUCTURE_CHANGED"
    RISK_CONTROL = "RISK_CONTROL"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    BROWSER_ERROR = "BROWSER_ERROR"
    AUTH_STATE_ERROR = "AUTH_STATE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


SENSITIVE_DETAIL_KEYS = {
    "authorization",
    "cookie",
    "cookies",
    "password",
    "proxy",
    "qr",
    "qrcode",
    "storage_state",
    "token",
    "xsec_token",
}


def safe_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop known secret-bearing fields before an error crosses the API boundary."""

    if not details:
        return {}
    result: dict[str, Any] = {}
    for key, value in details.items():
        if key.lower() in SENSITIVE_DETAIL_KEYS:
            result[key] = "***"
        else:
            result[key] = _safe_detail_value(value)
    return result


def _safe_detail_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return safe_details(value)
    if isinstance(value, (list, tuple)):
        return [_safe_detail_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class XhsError(Exception):
    """A controlled error with a stable code and retry hint."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = safe_details(details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def invalid_argument(message: str, **details: Any) -> XhsError:
    return XhsError(ErrorCode.INVALID_ARGUMENT, message, details=details)
