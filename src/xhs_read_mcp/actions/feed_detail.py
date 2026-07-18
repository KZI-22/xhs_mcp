"""Note detail extraction and optional bounded comment loading."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
from pydantic import ValidationError

from xhs_read_mcp.actions.comments import CommentLoadOutcome, CommentLoader
from xhs_read_mcp.actions.common import navigate, raise_for_page_problem, with_retries
from xhs_read_mcp.browser.page_contract import (
    DETAIL_MAP_READY_SCRIPT,
    DETAIL_MAP_SCRIPT,
    make_detail_url,
)
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import (
    Comment,
    CommentMode,
    CommentStopReason,
    CommentsResult,
    DetailRequest,
    NoteDetail,
    NoteDetailResult,
    ToolMeta,
    count_comments,
)
from xhs_read_mcp.models.source import SourceFeedDetailEntry, unwrap_reactive_value


def build_detail_result(
    detail_map: Any,
    request: DetailRequest,
    *,
    timezone: str,
    load_outcome: CommentLoadOutcome | None = None,
    duration_ms: int = 0,
) -> NoteDetailResult:
    detail_map = unwrap_reactive_value(detail_map)
    if not isinstance(detail_map, dict):
        raise XhsError(
            ErrorCode.PAGE_STRUCTURE_CHANGED,
            "笔记详情状态不是对象，页面数据结构可能已经变化。",
            retryable=False,
        )
    raw_entry = detail_map.get(request.note_id)
    raw_entry = unwrap_reactive_value(raw_entry)
    if not isinstance(raw_entry, dict):
        raise XhsError(
            ErrorCode.PAGE_STRUCTURE_CHANGED,
            "详情状态中没有找到指定笔记。",
            retryable=False,
            details={"note_id": request.note_id},
        )
    try:
        source = SourceFeedDetailEntry.model_validate(raw_entry)
    except ValidationError as exc:
        raise XhsError(
            ErrorCode.PAGE_STRUCTURE_CHANGED,
            "无法解析笔记详情状态，页面数据结构可能已经变化。",
            retryable=False,
            details={"reason": type(exc).__name__},
        ) from exc

    if not source.note.note_id:
        source.note.note_id = request.note_id
    if not source.note.xsec_token:
        source.note.xsec_token = request.xsec_token
    detail = NoteDetail.from_source(source.note, timezone)

    if request.comment_mode is CommentMode.NONE:
        comments = CommentsResult(
            mode=CommentMode.NONE,
            items=[],
            cursor="",
            has_more=False,
            parent_comment_count=0,
            total_returned_count=0,
            partial=False,
            stop_reason=CommentStopReason.DISABLED,
        )
    else:
        items = [Comment.from_source(item, timezone) for item in source.comments.items]
        if request.comment_mode is CommentMode.INITIAL:
            if not items and not source.comments.has_more:
                stop_reason = CommentStopReason.NO_COMMENTS
                partial = False
            elif source.comments.has_more:
                stop_reason = CommentStopReason.INITIAL_ONLY
                partial = True
            else:
                stop_reason = CommentStopReason.END_REACHED
                partial = False
            warnings = []
        else:
            load_outcome = load_outcome or CommentLoadOutcome(
                CommentStopReason.LOAD_ERROR,
                warnings=[],
            )
            stop_reason = load_outcome.stop_reason
            partial = stop_reason not in {
                CommentStopReason.END_REACHED,
                CommentStopReason.NO_COMMENTS,
            }
            warnings = list(load_outcome.warnings)

        comments = CommentsResult(
            mode=request.comment_mode,
            items=items,
            cursor=source.comments.cursor,
            has_more=source.comments.has_more,
            parent_comment_count=len(items),
            total_returned_count=count_comments(items),
            partial=partial,
            stop_reason=stop_reason,
            warnings=warnings,
        )

    return NoteDetailResult(
        note_id=request.note_id,
        detail=detail,
        comments=comments,
        meta=ToolMeta(duration_ms=duration_ms),
    )


class FeedDetailAction:
    async def get_detail(
        self,
        page: Page,
        request: DetailRequest,
        *,
        timezone: str,
        detail_timeout_seconds: float,
    ) -> NoteDetailResult:
        started = perf_counter()
        await navigate(
            page,
            make_detail_url(request.note_id, request.xsec_token),
            detail_timeout_seconds,
        )
        await raise_for_page_problem(page, check_note_access=True)
        await self._wait_for_detail(page, detail_timeout_seconds)

        load_outcome: CommentLoadOutcome | None = None
        if request.comment_mode is CommentMode.LOAD:
            assert request.comment_options is not None
            load_outcome = await CommentLoader(page, request.comment_options).load()

        detail_map = await self._extract_detail_map(page)
        return build_detail_result(
            detail_map,
            request,
            timezone=timezone,
            load_outcome=load_outcome,
            duration_ms=max(0, int((perf_counter() - started) * 1000)),
        )

    async def _wait_for_detail(self, page: Page, timeout_seconds: float) -> None:
        try:
            await page.wait_for_function(
                DETAIL_MAP_READY_SCRIPT,
                timeout=int(timeout_seconds * 1000),
            )
        except PlaywrightTimeoutError as exc:
            await raise_for_page_problem(page, check_note_access=True)
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "详情页面没有出现 noteDetailMap，页面结构可能已经变化。",
                retryable=False,
            ) from exc

    async def _extract_detail_map(self, page: Page) -> Any:
        async def evaluate() -> Any:
            return await page.evaluate(DETAIL_MAP_SCRIPT)

        value = await with_retries(evaluate, attempts=3, base_delay_seconds=0.2)
        if value is None:
            await raise_for_page_problem(page, check_note_access=True)
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "无法读取笔记详情状态。",
                retryable=False,
            )
        return value

