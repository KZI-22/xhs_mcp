"""Initial Xiaohongshu search result extraction."""

from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from pydantic import ValidationError

from xhs_read_mcp.actions.common import navigate, raise_for_page_problem, with_retries
from xhs_read_mcp.browser.page_contract import (
    FILTER_PANEL_SELECTOR,
    FILTER_TRIGGER_SELECTOR,
    FilterTarget,
    SEARCH_EMPTY_RESULT_SCRIPT,
    SEARCH_EMPTY_RESULT_TEXTS,
    SEARCH_FEEDS_READY_SCRIPT,
    SEARCH_FEEDS_SCRIPT,
    filter_targets,
    make_search_url,
)
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import (
    AppliedSearchFilters,
    SearchMeta,
    SearchNote,
    SearchRequest,
    SearchResult,
    WarningInfo,
)
from xhs_read_mcp.models.source import SourceFeed, unwrap_reactive_value


_SEARCH_POLL_SECONDS = 0.25
_SEARCH_STABLE_SECONDS = 0.5


def is_note_payload(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    note_card = value.get("noteCard", value.get("note_card"))
    return isinstance(note_card, dict)


def build_search_result(
    payload: Any,
    request: SearchRequest,
    *,
    duration_ms: int = 0,
) -> SearchResult:
    payload = unwrap_reactive_value(payload)
    if not isinstance(payload, list):
        raise XhsError(
            ErrorCode.PAGE_STRUCTURE_CHANGED,
            "搜索状态中的 feeds 不是数组，页面数据结构可能已经变化。",
            retryable=False,
        )

    notes: list[SearchNote] = []
    skipped = 0
    detail_unavailable = 0
    for raw_item in payload:
        if not is_note_payload(raw_item):
            skipped += 1
            continue
        try:
            source = SourceFeed.model_validate(raw_item)
        except ValidationError:
            skipped += 1
            continue
        note = SearchNote.from_source(source)
        if not note.detail_available:
            detail_unavailable += 1
        notes.append(note)

    warnings: list[WarningInfo] = []
    if detail_unavailable:
        warnings.append(
            WarningInfo(
                code="DETAIL_UNAVAILABLE",
                message=f"有 {detail_unavailable} 条笔记缺少 ID 或 xsec_token，无法读取详情。",
                details={"count": detail_unavailable},
            )
        )

    return SearchResult(
        keyword=request.keyword,
        applied_filters=AppliedSearchFilters(
            sort_by=request.sort_by,
            note_type=request.note_type,
            publish_time=request.publish_time,
            search_scope=request.search_scope,
            location=request.location,
        ),
        count=len(notes),
        items=notes,
        warnings=warnings,
        meta=SearchMeta(
            raw_count=len(payload),
            skipped_non_note_items=skipped,
            duration_ms=duration_ms,
        ),
    )


class SearchAction:
    async def search(
        self,
        page: Page,
        request: SearchRequest,
        timeout_seconds: float,
    ) -> SearchResult:
        started = perf_counter()
        await navigate(page, make_search_url(request.keyword), timeout_seconds)
        await self._wait_for_feeds(page, timeout_seconds)

        targets = filter_targets(request)
        if targets:
            await self._apply_filters(page, targets, timeout_seconds)

        payload = await self._stable_feeds(page, timeout_seconds)
        return build_search_result(
            payload,
            request,
            duration_ms=max(0, int((perf_counter() - started) * 1000)),
        )

    async def _wait_for_feeds(self, page: Page, timeout_seconds: float) -> None:
        try:
            await page.wait_for_function(
                SEARCH_FEEDS_READY_SCRIPT,
                timeout=int(timeout_seconds * 1000),
            )
        except PlaywrightTimeoutError as exc:
            await raise_for_page_problem(page)
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "搜索页面没有出现 feeds 状态，页面结构可能已经变化。",
                retryable=False,
            ) from exc

    async def _find_filter_option(
        self,
        page: Page,
        target: FilterTarget,
        timeout_seconds: float,
    ) -> Locator:
        group = page.locator(
            f"{FILTER_PANEL_SELECTOR} "
            f"div.filters:nth-child({target.group_index})"
        )
        await group.wait_for(
            state="visible",
            timeout=int(timeout_seconds * 1000),
        )

        options = group.locator("div.tags")
        await options.first.wait_for(
            state="visible",
            timeout=int(timeout_seconds * 1000),
        )
        labels = [" ".join(text.split()) for text in await options.all_inner_texts()]
        expected_label = " ".join(target.label.split())
        for index, label in enumerate(labels):
            if label == expected_label:
                return options.nth(index)

        raise XhsError(
            ErrorCode.PAGE_STRUCTURE_CHANGED,
            "搜索筛选项文案已经变化。",
            retryable=False,
            details={
                "expected_label": target.label,
                "group_index": target.group_index,
                "available_labels": labels,
            },
        )

    async def _apply_filters(
        self,
        page: Page,
        targets: list[FilterTarget],
        timeout_seconds: float,
    ) -> None:
        try:
            await page.locator(FILTER_TRIGGER_SELECTOR).hover(
                timeout=int(timeout_seconds * 1000)
            )
            await page.locator(FILTER_PANEL_SELECTOR).wait_for(
                state="visible", timeout=int(timeout_seconds * 1000)
            )
            for target in targets:
                locator = await self._find_filter_option(
                    page,
                    target,
                    timeout_seconds,
                )
                await locator.click()
            await self._wait_for_feeds(page, timeout_seconds)
        except XhsError:
            raise
        except PlaywrightTimeoutError as exc:
            await raise_for_page_problem(page)
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "无法操作搜索筛选面板，页面结构可能已经变化。",
                retryable=False,
            ) from exc
        except PlaywrightError as exc:
            raise XhsError(
                ErrorCode.BROWSER_ERROR,
                "搜索筛选操作失败。",
                retryable=True,
                details={"reason": type(exc).__name__},
            ) from exc

    async def _stable_feeds(self, page: Page, timeout_seconds: float) -> list[Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        previous: str | None = None
        stable_since: float | None = None

        async def explicitly_empty() -> bool:
            return bool(
                await page.evaluate(
                    SEARCH_EMPTY_RESULT_SCRIPT,
                    list(SEARCH_EMPTY_RESULT_TEXTS),
                )
            )

        while loop.time() < deadline:
            async def evaluate() -> Any:
                return await page.evaluate(SEARCH_FEEDS_SCRIPT)

            payload = await with_retries(evaluate, attempts=3, base_delay_seconds=0.2)
            payload = unwrap_reactive_value(payload)
            if isinstance(payload, list):
                signature = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                now = loop.time()
                if signature != previous:
                    previous = signature
                    stable_since = now
                elif stable_since is not None and now - stable_since >= _SEARCH_STABLE_SECONDS:
                    if payload:
                        return payload
                    await raise_for_page_problem(page)
                    if await with_retries(
                        explicitly_empty,
                        attempts=3,
                        base_delay_seconds=0.2,
                    ):
                        return []
            else:
                await raise_for_page_problem(page)
            await asyncio.sleep(_SEARCH_POLL_SECONDS)

        await raise_for_page_problem(page)
        raise XhsError(
            ErrorCode.TIMEOUT,
            "等待搜索结果加载完成超时；页面只返回了未完成的空 feeds。",
            retryable=True,
        )
