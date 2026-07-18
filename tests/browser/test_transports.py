import asyncio
import os
import socket
import sys
from pathlib import Path

import pytest
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


pytestmark = pytest.mark.browser
EXPECTED_TOOLS = {
    "xhs_check_login",
    "xhs_start_login",
    "xhs_get_login_status",
    "xhs_cancel_login",
    "xhs_logout",
    "xhs_search_notes",
    "xhs_get_note_detail",
}


def subprocess_env(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    source_path = str(Path(__file__).resolve().parents[2] / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        os.pathsep.join([source_path, existing]) if existing else source_path
    )
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["XHS_AUTH_STATE_PATH"] = str(tmp_path / "state.json")
    return environment


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


async def wait_for_port(port: int, process: asyncio.subprocess.Process) -> None:
    for _ in range(100):
        if process.returncode is not None:
            stderr = await process.stderr.read() if process.stderr else b""
            raise AssertionError(
                f"HTTP MCP exited early: {stderr.decode('utf-8', errors='replace')}"
            )
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise AssertionError("HTTP MCP did not start within 10 seconds")


async def list_tool_names(session: ClientSession) -> set[str]:
    await session.initialize()
    result = await session.list_tools()
    return {tool.name for tool in result.tools}


async def test_stdio_transport_completes_real_mcp_handshake(tmp_path: Path) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "xhs_read_mcp", "--transport", "stdio"],
        env=subprocess_env(tmp_path),
        cwd=Path(__file__).resolve().parents[2],
    )
    with (tmp_path / "stdio.stderr.log").open("w+", encoding="utf-8") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                assert await list_tool_names(session) == EXPECTED_TOOLS


async def test_streamable_http_completes_real_mcp_handshake(tmp_path: Path) -> None:
    port = free_port()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "xhs_read_mcp",
        "--transport",
        "streamable-http",
        "--port",
        str(port),
        cwd=Path(__file__).resolve().parents[2],
        env=subprocess_env(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await wait_for_port(port, process)
        async with httpx.AsyncClient(trust_env=False) as http_client:
            async with streamable_http_client(
                f"http://127.0.0.1:{port}/mcp",
                http_client=http_client,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    assert await list_tool_names(session) == EXPECTED_TOOLS
    finally:
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
