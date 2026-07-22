import base64
from pathlib import Path

from mcp.types import ImageContent

from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import LogoutResult
from xhs_read_mcp.server import _error_result, _success_result, create_server


def config(tmp_path: Path, transport: str = "stdio") -> AppConfig:
    return AppConfig(
        _env_file=None,
        mcp_transport=transport,
        auth_state_path=tmp_path / "state.json",
    )


async def test_server_registers_exactly_the_seven_confirmed_tools(tmp_path: Path) -> None:
    server = create_server(config(tmp_path))

    tools = await server.list_tools()

    assert {tool.name for tool in tools} == {
        "xhs_check_login",
        "xhs_start_login",
        "xhs_get_login_status",
        "xhs_cancel_login",
        "xhs_logout",
        "xhs_search_notes",
        "xhs_get_note_detail",
    }
    by_name = {tool.name: tool for tool in tools}
    assert "force_restart" not in by_name["xhs_start_login"].inputSchema.get(
        "required", []
    )
    assert by_name["xhs_search_notes"].annotations.readOnlyHint
    assert by_name["xhs_logout"].annotations.destructiveHint


async def test_stdio_and_http_have_identical_tool_schemas(tmp_path: Path) -> None:
    stdio_tools = await create_server(config(tmp_path, "stdio")).list_tools()
    http_tools = await create_server(config(tmp_path, "streamable-http")).list_tools()

    stdio = {tool.name: tool.inputSchema for tool in stdio_tools}
    http = {tool.name: tool.inputSchema for tool in http_tools}
    assert stdio == http


def test_http_server_preserves_browser_login_sessions(tmp_path: Path) -> None:
    server = create_server(config(tmp_path, "streamable-http"))

    assert not server.settings.stateless_http


def test_success_result_has_structured_content_and_optional_image() -> None:
    result = _success_result(
        LogoutResult(cleared=True, message="done"),
        "done",
        image_png=b"png",
    )

    assert not result.isError
    assert result.structuredContent == {"cleared": True, "message": "done"}
    image = next(item for item in result.content if isinstance(item, ImageContent))
    assert base64.b64decode(image.data) == b"png"


def test_error_result_is_machine_readable_and_marked_as_error() -> None:
    result = _error_result(
        XhsError(ErrorCode.NOT_LOGGED_IN, "not logged in", retryable=False)
    )

    assert result.isError
    assert result.structuredContent["code"] == "NOT_LOGGED_IN"
    assert result.structuredContent["retryable"] is False
