from pathlib import Path

from xhs_read_mcp.cli import StaticBearerAuthMiddleware, build_parser, config_from_args


def test_cli_defaults_defer_to_settings(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XHS_AUTH_STATE_PATH", str(tmp_path / "state.json"))
    args = build_parser().parse_args([])

    config = config_from_args(args)

    assert config.mcp_transport == "stdio"
    assert not config.browser_headless
    assert config.auth_state_path == tmp_path / "state.json"


def test_cli_overrides_transport_and_browser_mode() -> None:
    args = build_parser().parse_args(
        ["--transport", "streamable-http", "--port", "9000", "--headed"]
    )

    config = config_from_args(args)

    assert config.mcp_transport == "streamable-http"
    assert config.mcp_port == 9000
    assert not config.browser_headless


async def test_bearer_middleware_rejects_missing_token() -> None:
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = StaticBearerAuthMiddleware(app, "secret")
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(
        {"type": "http", "method": "POST", "headers": []}, receive, send
    )

    assert not called
    assert sent[0]["status"] == 401


async def test_bearer_middleware_accepts_matching_token() -> None:
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    middleware = StaticBearerAuthMiddleware(app, "secret")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    await middleware(
        {
            "type": "http",
            "method": "POST",
            "headers": [(b"authorization", b"Bearer secret")],
        },
        receive,
        send,
    )

    assert called
