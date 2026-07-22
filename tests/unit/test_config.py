from pathlib import Path

import pytest
from pydantic import ValidationError

from xhs_read_mcp.config import AppConfig, is_loopback_host


def test_default_config_is_local_stdio() -> None:
    config = AppConfig(_env_file=None)

    assert config.mcp_transport == "stdio"
    assert config.mcp_host == "127.0.0.1"
    assert config.mcp_path == "/mcp"
    assert config.max_concurrent_operations == 2
    assert not config.browser_headless
    assert config.browser_path is None
    assert config.auth_state_path.name == "chrome-storage_state.json"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host)


def test_non_loopback_http_requires_explicit_permission() -> None:
    with pytest.raises(ValidationError, match="ALLOW_NON_LOOPBACK"):
        AppConfig(
            _env_file=None,
            mcp_transport="streamable-http",
            mcp_host="0.0.0.0",
        )


def test_non_loopback_http_requires_token() -> None:
    with pytest.raises(ValidationError, match="AUTH_TOKEN"):
        AppConfig(
            _env_file=None,
            mcp_transport="streamable-http",
            mcp_host="0.0.0.0",
            mcp_allow_non_loopback=True,
        )


def test_explicit_state_path_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    monkeypatch.setenv("XHS_AUTH_STATE_PATH", str(path))

    config = AppConfig(_env_file=None)

    assert config.auth_state_path == path


def test_browser_path_must_point_to_google_chrome() -> None:
    with pytest.raises(ValidationError, match="Google Chrome"):
        AppConfig(_env_file=None, browser_path=Path("msedge.exe"))

    chrome_path = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
    config = AppConfig(_env_file=None, browser_path=chrome_path)
    assert config.browser_path == chrome_path


def test_mcp_path_is_normalized() -> None:
    assert AppConfig(_env_file=None, mcp_path="custom/").mcp_path == "/custom"
