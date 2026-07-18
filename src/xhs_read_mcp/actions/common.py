"""Shared page navigation and page-state classification helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from xhs_read_mcp.browser.page_contract import (
    ACCESS_CONTAINER_SELECTOR,
    INACCESSIBLE_TEXTS,
    LOGGED_IN_SELECTOR,
    QR_CODE_SELECTOR,
    RISK_CONTROL_TEXTS,
)
from xhs_read_mcp.errors import ErrorCode, XhsError


T = TypeVar("T")


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.25,
) -> T:
    """Retry an explicitly idempotent Playwright operation."""

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return await operation()
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(base_delay_seconds * (2**attempt))
    assert last_error is not None
    if isinstance(last_error, PlaywrightTimeoutError):
        raise XhsError(
            ErrorCode.TIMEOUT,
            "等待小红书页面响应超时。",
            retryable=True,
        ) from last_error
    raise XhsError(
        ErrorCode.BROWSER_ERROR,
        "浏览器页面操作失败。",
        retryable=True,
        details={"reason": type(last_error).__name__},
    ) from last_error


async def navigate(page: Page, url: str, timeout_seconds: float) -> None:
    async def operation() -> None:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=int(timeout_seconds * 1000),
        )

    await with_retries(operation, attempts=3, base_delay_seconds=0.5)


async def body_text(page: Page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except (PlaywrightError, PlaywrightTimeoutError):
        return ""


async def raise_for_page_problem(
    page: Page,
    *,
    check_note_access: bool = False,
    check_login_expired: bool = True,
) -> None:
    if check_login_expired:
        try:
            qr = page.locator(QR_CODE_SELECTOR).first
            qr_visible = await qr.count() > 0 and await qr.is_visible()
            logged_in = await page.locator(LOGGED_IN_SELECTOR).count() > 0
            if qr_visible and not logged_in:
                raise XhsError(
                    ErrorCode.LOGIN_EXPIRED,
                    "小红书登录状态已失效，请重新扫码登录。",
                    retryable=False,
                )
        except PlaywrightError:
            pass

    text = await body_text(page)
    for marker in RISK_CONTROL_TEXTS:
        if marker in text:
            raise XhsError(
                ErrorCode.RISK_CONTROL,
                f"小红书页面要求安全验证：{marker}",
                retryable=False,
            )

    if not check_note_access:
        return
    matched = next((marker for marker in INACCESSIBLE_TEXTS if marker in text), None)
    if matched:
        raise XhsError(
            ErrorCode.NOTE_UNAVAILABLE,
            f"笔记不可访问：{matched}",
            retryable=False,
            details={"reason": matched},
        )
    try:
        container = page.locator(ACCESS_CONTAINER_SELECTOR).first
        if await container.count():
            reason = (await container.inner_text(timeout=2000)).strip()
            if reason:
                raise XhsError(
                    ErrorCode.NOTE_UNAVAILABLE,
                    f"笔记不可访问：{reason}",
                    retryable=False,
                    details={"reason": reason},
                )
    except PlaywrightTimeoutError:
        return
