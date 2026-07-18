"""Bounded UI-driven comment lazy loading."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from xhs_read_mcp.browser.page_contract import (
    COMMENT_CONTAINER_SELECTOR,
    COMMENT_END_SELECTOR,
    COMMENT_PARENT_SELECTOR,
    COMMENT_SHOW_MORE_SELECTOR,
    NO_COMMENTS_SELECTOR,
)
from xhs_read_mcp.models.public import (
    CommentLoadOptions,
    CommentStopReason,
    ScrollSpeed,
    WarningInfo,
)


DEFAULT_MAX_ATTEMPTS = 500
MAX_STAGNANT_ATTEMPTS = 20
MORE_BUTTON_INTERVAL = 3
MAX_MORE_BUTTONS_PER_SCAN = 3
REPLY_COUNT_PATTERN = re.compile(r"展开\s*(\d+)\s*条回复")


@dataclass(slots=True)
class CommentLoadOutcome:
    stop_reason: CommentStopReason
    observed_parent_count: int = 0
    warnings: list[WarningInfo] = field(default_factory=list)


def should_skip_reply_button(text: str, threshold: int | None) -> bool:
    if threshold is None:
        return False
    match = REPLY_COUNT_PATTERN.search(text)
    if not match:
        return False
    return int(match.group(1)) > threshold


def scroll_profile(speed: ScrollSpeed, *, large: bool) -> tuple[float, float]:
    ratio, interval = {
        ScrollSpeed.SLOW: (0.5, 1.2),
        ScrollSpeed.NORMAL: (0.7, 0.6),
        ScrollSpeed.FAST: (0.9, 0.3),
    }[speed]
    return (ratio * 2 if large else ratio), interval


class CommentLoader:
    def __init__(self, page: Page, options: CommentLoadOptions) -> None:
        self.page = page
        self.options = options

    async def load(self) -> CommentLoadOutcome:
        try:
            async with asyncio.timeout(self.options.timeout_seconds):
                return await self._load_bounded()
        except TimeoutError:
            return CommentLoadOutcome(
                stop_reason=CommentStopReason.TIMEOUT,
                observed_parent_count=await self._safe_parent_count(),
                warnings=[
                    WarningInfo(
                        code="COMMENT_LOAD_TIMEOUT",
                        message="评论主动加载达到超时限制，返回当前已加载内容。",
                    )
                ],
            )
        except asyncio.CancelledError:
            raise
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            return CommentLoadOutcome(
                stop_reason=CommentStopReason.LOAD_ERROR,
                observed_parent_count=await self._safe_parent_count(),
                warnings=[
                    WarningInfo(
                        code="COMMENT_LOAD_ERROR",
                        message="评论滚动加载失败，返回当前已加载内容。",
                        details={"reason": type(exc).__name__},
                    )
                ],
            )

    async def _load_bounded(self) -> CommentLoadOutcome:
        await self._scroll_to_comments_area()
        if await self._has_no_comments():
            return CommentLoadOutcome(CommentStopReason.NO_COMMENTS, 0)

        max_attempts = (
            self.options.max_parent_comments * 3
            if self.options.max_parent_comments > 0
            else DEFAULT_MAX_ATTEMPTS
        )
        last_count = -1
        stagnant = 0
        current_count = 0

        for attempt in range(max_attempts):
            if await self._reached_end():
                return CommentLoadOutcome(CommentStopReason.END_REACHED, current_count)

            if self.options.expand_replies and attempt % MORE_BUTTON_INTERVAL == 0:
                await self._click_more_replies()

            current_count = await self._parent_count()
            if (
                self.options.max_parent_comments > 0
                and current_count >= self.options.max_parent_comments
            ):
                return CommentLoadOutcome(
                    CommentStopReason.MAX_PARENT_COMMENTS, current_count
                )

            if current_count == last_count:
                stagnant += 1
            else:
                last_count = current_count
                stagnant = 0
            if stagnant >= MAX_STAGNANT_ATTEMPTS:
                return CommentLoadOutcome(CommentStopReason.STALLED, current_count)

            await self._scroll_comments(large=stagnant >= 5)
            await self._wait_scroll_interval()

        return CommentLoadOutcome(
            CommentStopReason.STALLED,
            current_count,
            warnings=[
                WarningInfo(
                    code="COMMENT_MAX_ATTEMPTS",
                    message="评论滚动达到最大尝试次数，返回当前已加载内容。",
                )
            ],
        )

    async def _scroll_to_comments_area(self) -> None:
        container = self.page.locator(COMMENT_CONTAINER_SELECTOR).first
        try:
            if await container.count():
                await container.scroll_into_view_if_needed(timeout=2000)
        except PlaywrightTimeoutError:
            pass
        await asyncio.sleep(0.5)
        await self._dispatch_wheel(100)

    async def _has_no_comments(self) -> bool:
        element = self.page.locator(NO_COMMENTS_SELECTOR).first
        if not await element.count():
            return False
        try:
            return "这是一片荒地" in (await element.inner_text(timeout=2000)).strip()
        except PlaywrightTimeoutError:
            return False

    async def _reached_end(self) -> bool:
        element = self.page.locator(COMMENT_END_SELECTOR).first
        if not await element.count():
            return False
        try:
            text = (await element.inner_text(timeout=2000)).strip().replace(" ", "").upper()
        except PlaywrightTimeoutError:
            return False
        return "THEEND" in text

    async def _click_more_replies(self) -> None:
        elements = await self.page.locator(COMMENT_SHOW_MORE_SELECTOR).all()
        clicked = 0
        for element in elements:
            if clicked >= MAX_MORE_BUTTONS_PER_SCAN:
                return
            try:
                if not await element.is_visible():
                    continue
                text = await element.inner_text()
                if should_skip_reply_button(
                    text, self.options.max_reply_count_to_expand
                ):
                    continue
                await element.scroll_into_view_if_needed(timeout=2000)
                await element.click()
                clicked += 1
                await asyncio.sleep(0.5)
            except (PlaywrightError, PlaywrightTimeoutError):
                continue

    async def _parent_count(self) -> int:
        return await self.page.locator(COMMENT_PARENT_SELECTOR).count()

    async def _safe_parent_count(self) -> int:
        try:
            return await self._parent_count()
        except PlaywrightError:
            return 0

    async def _scroll_comments(self, *, large: bool) -> None:
        comments = self.page.locator(COMMENT_PARENT_SELECTOR)
        count = await comments.count()
        if count:
            try:
                await comments.nth(count - 1).scroll_into_view_if_needed(timeout=2000)
            except PlaywrightTimeoutError:
                pass

        ratio, _ = scroll_profile(self.options.scroll_speed, large=large)
        viewport_height = await self.page.evaluate("() => window.innerHeight")
        delta = max(float(viewport_height) * ratio, 400.0)
        await self._dispatch_wheel(delta)
        await self.page.evaluate("(delta) => window.scrollBy(0, delta)", delta)

    async def _dispatch_wheel(self, delta: float) -> None:
        await self.page.evaluate(
            """(delta) => {
                const target = document.querySelector('.note-scroller')
                    || document.querySelector('.interaction-container')
                    || document.documentElement;
                target.dispatchEvent(new WheelEvent('wheel', {
                    deltaY: delta,
                    deltaMode: 0,
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            }""",
            delta,
        )

    async def _wait_scroll_interval(self) -> None:
        _, interval = scroll_profile(self.options.scroll_speed, large=False)
        await asyncio.sleep(interval)

