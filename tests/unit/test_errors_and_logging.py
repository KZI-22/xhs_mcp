import logging

from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.logging import SensitiveDataFilter, redact_text, redact_token


def test_error_details_are_redacted() -> None:
    error = XhsError(
        ErrorCode.BROWSER_ERROR,
        "browser failed",
        details={
            "xsec_token": "secret",
            "nested": {"cookie": "value"},
            "candidates": [{"tag": "input", "token": "nested-secret"}],
        },
    )

    assert error.to_dict()["details"] == {
        "xsec_token": "***",
        "nested": {"cookie": "***"},
        "candidates": [{"tag": "input", "token": "***"}],
    }


def test_text_redaction_masks_url_credentials_and_tokens() -> None:
    text = redact_text("proxy=http://user:pass@example.com xsec_token=abcdef")

    assert "user:pass" not in text
    assert "abcdef" not in text


def test_token_redaction_keeps_only_edges() -> None:
    assert redact_token("abcdefghijkl") == "abcd***ijkl"
    assert redact_token("short") == "***"


def test_logging_filter_replaces_message() -> None:
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "cookie=%s", ("secret",), None
    )

    assert SensitiveDataFilter().filter(record)
    assert record.getMessage() == "cookie=***"
