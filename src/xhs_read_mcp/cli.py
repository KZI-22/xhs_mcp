"""Command-line entry point for selecting an MCP transport."""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from starlette.responses import JSONResponse

from xhs_read_mcp import __version__
from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.logging import configure_logging
from xhs_read_mcp.server import create_server


class StaticBearerAuthMiddleware:
    """Small ASGI bearer-token boundary for explicitly exposed HTTP mode."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode("utf-8")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"authorization", b"")
            if not secrets.compare_digest(supplied, self.expected):
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhs-read-mcp",
        description="Local single-user read-only Xiaohongshu MCP server",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=None,
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--path", default=None)
    parser.add_argument("--auth-token", default=None)
    parser.add_argument("--allow-non-loopback", action="store_true", default=None)

    browser_mode = parser.add_mutually_exclusive_group()
    browser_mode.add_argument("--headless", action="store_true", dest="headless")
    browser_mode.add_argument("--headed", action="store_false", dest="headless")
    parser.set_defaults(headless=None)
    parser.add_argument(
        "--browser-channel",
        choices=("chrome", "chromium"),
        default=None,
        help="Use installed Google Chrome or Playwright bundled Chromium",
    )
    parser.add_argument(
        "--browser-path",
        type=Path,
        default=None,
        help="Path to a custom Google Chrome executable",
    )
    parser.add_argument("--auth-state-path", type=Path, default=None)
    parser.add_argument(
        "--log-level",
        choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
        default=None,
    )
    return parser


def config_from_args(args: argparse.Namespace) -> AppConfig:
    mapping = {
        "mcp_transport": args.transport,
        "mcp_host": args.host,
        "mcp_port": args.port,
        "mcp_path": args.path,
        "mcp_auth_token": args.auth_token,
        "mcp_allow_non_loopback": args.allow_non_loopback,
        "browser_headless": args.headless,
        "browser_channel": args.browser_channel,
        "browser_path": args.browser_path,
        "auth_state_path": args.auth_state_path,
        "log_level": args.log_level,
    }
    return AppConfig(**{key: value for key, value in mapping.items() if value is not None})


async def run_http(server, config: AppConfig) -> None:
    import uvicorn

    app: Any = server.streamable_http_app()
    if config.mcp_auth_token is not None:
        app = StaticBearerAuthMiddleware(
            app,
            config.mcp_auth_token.get_secret_value(),
        )
    uvicorn_config = uvicorn.Config(
        app,
        host=config.mcp_host,
        port=config.mcp_port,
        log_level=config.log_level.lower(),
    )
    await uvicorn.Server(uvicorn_config).serve()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = config_from_args(args)
    except ValidationError as exc:
        parser.error(str(exc))
    configure_logging(config.log_level)
    server = create_server(config)
    if config.mcp_transport == "stdio":
        server.run(transport="stdio")
    else:
        asyncio.run(run_http(server, config))


if __name__ == "__main__":
    main(sys.argv[1:])
