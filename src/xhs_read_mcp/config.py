"""Application configuration with safe local defaults."""

from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    """Return a per-user data directory without requiring a platform helper."""

    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "xhs-read-mcp"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "xhs-read-mcp"
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "xhs-read-mcp"


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower().strip("[]")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


class AppConfig(BaseSettings):
    """Validated settings shared by both MCP transports."""

    model_config = SettingsConfigDict(
        env_prefix="XHS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    mcp_transport: Literal["stdio", "streamable-http"] = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8765, ge=1, le=65535)
    mcp_path: str = "/mcp"
    mcp_auth_token: SecretStr | None = None
    mcp_allow_non_loopback: bool = False
    mcp_allowed_hosts: str = ""
    mcp_allowed_origins: str = ""

    browser_headless: bool = False
    browser_channel: Literal["chrome", "chromium"] = "chrome"
    browser_path: Path | None = None
    proxy: SecretStr | None = None

    auth_state_path: Path = Field(
        default_factory=lambda: default_data_dir() / "chrome-storage_state.json"
    )
    debug_artifacts: bool = False
    debug_artifacts_path: Path = Field(
        default_factory=lambda: default_data_dir() / "debug-artifacts"
    )
    debug_artifacts_limit: int = Field(default=20, ge=1, le=500)

    login_timeout_seconds: float = Field(default=240.0, gt=0, le=1800)
    status_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    search_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    detail_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    comment_timeout_seconds: float = Field(default=300.0, gt=0, le=600)
    max_comment_timeout_seconds: float = Field(default=600.0, gt=0, le=1800)

    max_concurrent_operations: int = Field(default=2, ge=1, le=10)
    timezone: str = "Asia/Shanghai"
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"

    @field_validator("mcp_path")
    @classmethod
    def normalize_mcp_path(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return "/mcp"
        if not value.startswith("/"):
            value = f"/{value}"
        return value.rstrip("/") or "/"

    @field_validator("mcp_host")
    @classmethod
    def normalize_host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("MCP host cannot be empty")
        return value

    @field_validator("browser_path")
    @classmethod
    def require_google_chrome_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if "chrome" not in value.name.casefold():
            raise ValueError(
                "XHS_BROWSER_PATH must point to a Google Chrome executable"
            )
        return value

    @model_validator(mode="after")
    def validate_security_boundary(self) -> "AppConfig":
        if self.comment_timeout_seconds > self.max_comment_timeout_seconds:
            raise ValueError(
                "XHS_COMMENT_TIMEOUT_SECONDS cannot exceed XHS_MAX_COMMENT_TIMEOUT_SECONDS"
            )
        if self.mcp_transport != "streamable-http":
            return self
        if is_loopback_host(self.mcp_host):
            return self
        if not self.mcp_allow_non_loopback:
            raise ValueError(
                "non-loopback MCP binding requires XHS_MCP_ALLOW_NON_LOOPBACK=true"
            )
        token = self.mcp_auth_token.get_secret_value() if self.mcp_auth_token else ""
        if not token.strip():
            raise ValueError("non-loopback MCP binding requires XHS_MCP_AUTH_TOKEN")
        return self
