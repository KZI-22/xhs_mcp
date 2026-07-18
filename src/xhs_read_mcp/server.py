"""FastMCP adapter shared by stdio and Streamable HTTP transports."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, ValidationError

from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.errors import ErrorCode, XhsError, invalid_argument
from xhs_read_mcp.models.public import (
    CommentLoadOptions,
    CommentMode,
    DetailRequest,
    LocationFilter,
    NoteTypeFilter,
    PublishTime,
    SearchRequest,
    SearchScope,
    SortBy,
)
from xhs_read_mcp.service import XhsReadService


logger = logging.getLogger("xhs_read_mcp.server")
T = TypeVar("T", bound=BaseModel)
ServiceFactory = Callable[[AppConfig], XhsReadService]


@dataclass(slots=True)
class AppContext:
    service: XhsReadService


def _success_result(
    model: BaseModel,
    summary: str,
    *,
    image_png: bytes | None = None,
) -> CallToolResult:
    structured = model.model_dump(mode="json")
    content: list[TextContent | ImageContent] = [
        TextContent(type="text", text=summary)
    ]
    if image_png is not None:
        content.append(
            ImageContent(
                type="image",
                data=base64.b64encode(image_png).decode("ascii"),
                mimeType="image/png",
            )
        )
    return CallToolResult(
        content=content,
        structuredContent=structured,
        isError=False,
    )


def _error_result(error: XhsError) -> CallToolResult:
    payload = error.to_dict()
    return CallToolResult(
        content=[TextContent(type="text", text=error.message)],
        structuredContent=payload,
        isError=True,
    )


async def _execute(
    operation: Callable[[], Awaitable[T]],
    summary: Callable[[T], str],
) -> CallToolResult:
    try:
        result = await operation()
        return _success_result(result, summary(result))
    except asyncio.CancelledError:
        raise
    except XhsError as exc:
        return _error_result(exc)
    except ValidationError as exc:
        return _error_result(
            XhsError(
                ErrorCode.INVALID_ARGUMENT,
                "工具参数无效。",
                retryable=False,
                details={"errors": exc.error_count()},
            )
        )
    except Exception as exc:
        logger.error("Unhandled tool error: %s", type(exc).__name__)
        return _error_result(
            XhsError(
                ErrorCode.INTERNAL_ERROR,
                "工具执行时发生内部错误。",
                retryable=False,
                details={"reason": type(exc).__name__},
            )
        )


def _service(context: Context) -> XhsReadService:
    request_context = context.request_context
    if request_context is None:
        raise XhsError(ErrorCode.INTERNAL_ERROR, "MCP 请求上下文不可用。")
    app_context = request_context.lifespan_context
    if not isinstance(app_context, AppContext):
        raise XhsError(ErrorCode.INTERNAL_ERROR, "MCP 服务生命周期上下文不可用。")
    return app_context.service


async def _progress(context: Context, value: float, message: str) -> None:
    try:
        await context.report_progress(progress=value, total=1.0, message=message)
    except Exception:
        # Progress is optional and must never make the read operation fail.
        return


def _transport_security(config: AppConfig) -> TransportSecuritySettings:
    host = config.mcp_host
    port = config.mcp_port
    allowed_hosts = [f"{host}:{port}"]
    allowed_origins = [f"http://{host}:{port}", f"https://{host}:{port}"]
    if host in {"127.0.0.1", "localhost", "::1"}:
        allowed_hosts.extend(
            [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
        )
        allowed_origins.extend(
            [
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
                f"http://[::1]:{port}",
            ]
        )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sorted(set(allowed_hosts)),
        allowed_origins=sorted(set(allowed_origins)),
    )


def create_server(
    config: AppConfig,
    *,
    service_factory: ServiceFactory = XhsReadService,
) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[AppContext]:
        service = service_factory(config)
        await service.start()
        try:
            yield AppContext(service=service)
        finally:
            await service.close()

    mcp = FastMCP(
        name="xhs-read-mcp",
        instructions=(
            "本地单用户、只读的小红书 MCP。先检查登录；未登录时扫码；"
            "搜索结果中的 note_id 和 xsec_token 必须成对用于详情工具。"
        ),
        host=config.mcp_host,
        port=config.mcp_port,
        streamable_http_path=config.mcp_path,
        stateless_http=True,
        json_response=False,
        log_level=config.log_level,
        lifespan=lifespan,
        transport_security=_transport_security(config),
    )

    @mcp.tool(
        name="xhs_check_login",
        description="检查当前保存的小红书网页登录状态是否有效。",
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def check_login(ctx: Context) -> CallToolResult:
        return await _execute(
            lambda: _service(ctx).check_login(),
            lambda result: "当前已登录。" if result.is_logged_in else "当前未登录。",
        )

    @mcp.tool(
        name="xhs_start_login",
        description="创建或复用一个二维码扫码登录会话，并返回二维码图片。",
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def start_login(ctx: Context, force_restart: bool = False) -> CallToolResult:
        try:
            started = await _service(ctx).start_login(force_restart=force_restart)
            return _success_result(
                started.result,
                started.result.message,
                image_png=started.qr_png,
            )
        except asyncio.CancelledError:
            raise
        except XhsError as exc:
            return _error_result(exc)
        except Exception as exc:
            logger.error("Unhandled login start error: %s", type(exc).__name__)
            return _error_result(
                XhsError(
                    ErrorCode.INTERNAL_ERROR,
                    "启动扫码登录时发生内部错误。",
                    details={"reason": type(exc).__name__},
                )
            )

    @mcp.tool(
        name="xhs_get_login_status",
        description="根据 login_id 查询二维码扫码登录会话状态。",
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def get_login_status(login_id: str, ctx: Context) -> CallToolResult:
        async def operation():
            if not login_id.strip():
                raise invalid_argument("login_id 不能为空。")
            return await _service(ctx).get_login_status(login_id.strip())

        return await _execute(operation, lambda result: result.message)

    @mcp.tool(
        name="xhs_cancel_login",
        description="取消指定的二维码扫码登录会话。",
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def cancel_login(login_id: str, ctx: Context) -> CallToolResult:
        async def operation():
            if not login_id.strip():
                raise invalid_argument("login_id 不能为空。")
            return await _service(ctx).cancel_login(login_id.strip())

        return await _execute(operation, lambda result: result.message)

    @mcp.tool(
        name="xhs_logout",
        description="清除本机保存的小红书登录状态并重置浏览器上下文。",
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def logout(ctx: Context) -> CallToolResult:
        return await _execute(
            lambda: _service(ctx).logout(),
            lambda result: result.message,
        )

    @mcp.tool(
        name="xhs_search_notes",
        description="按关键词和可选网页筛选项读取搜索页首次加载的笔记结果；不滚动、不分页。",
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def search_notes(
        keyword: str,
        ctx: Context,
        sort_by: SortBy | None = None,
        note_type: NoteTypeFilter | None = None,
        publish_time: PublishTime | None = None,
        search_scope: SearchScope | None = None,
        location: LocationFilter | None = None,
    ) -> CallToolResult:
        async def operation():
            request = SearchRequest(
                keyword=keyword,
                sort_by=sort_by,
                note_type=note_type,
                publish_time=publish_time,
                search_scope=search_scope,
                location=location,
            )
            return await _service(ctx).search_notes(request)

        return await _execute(
            operation,
            lambda result: f"找到 {result.count} 条初始笔记结果。",
        )

    @mcp.tool(
        name="xhs_get_note_detail",
        description="使用同一搜索结果的 note_id 和 xsec_token 读取笔记详情及可选评论。",
        structured_output=False,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def get_note_detail(
        note_id: str,
        xsec_token: str,
        ctx: Context,
        comment_mode: CommentMode = CommentMode.INITIAL,
        comment_options: CommentLoadOptions | None = None,
    ) -> CallToolResult:
        async def operation():
            request = DetailRequest(
                note_id=note_id,
                xsec_token=xsec_token,
                comment_mode=comment_mode,
                comment_options=comment_options,
            )
            await _progress(ctx, 0.0, "正在打开笔记详情。")
            result = await _service(ctx).get_note_detail(request)
            await _progress(ctx, 1.0, "笔记详情读取完成。")
            return result

        return await _execute(
            operation,
            lambda result: (
                f"已读取笔记详情，返回 {result.comments.parent_comment_count} 条父评论。"
            ),
        )

    return mcp
