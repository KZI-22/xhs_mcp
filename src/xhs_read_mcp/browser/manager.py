"""Shared Playwright/Google Chrome lifecycle for the local single-user service."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    PlaywrightContextManager,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

from xhs_read_mcp.browser.auth_store import AuthStateStore
from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.errors import ErrorCode, XhsError


logger = logging.getLogger("xhs_read_mcp.browser")
PlaywrightFactory = Callable[[], PlaywrightContextManager]
GOOGLE_CHROME_CHANNEL = "chrome"


def build_proxy_settings(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy_url}

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    server = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))
    result = {"server": server}
    if parsed.username is not None:
        result["username"] = unquote(parsed.username)
    if parsed.password is not None:
        result["password"] = unquote(parsed.password)
    return result


class BrowserManager:
    """Own one Google Chrome and one authenticated context; lease a page per operation."""

    def __init__(
        self,
        config: AppConfig,
        auth_store: AuthStateStore,
        *,
        playwright_factory: PlaywrightFactory = async_playwright,
    ) -> None:
        self.config = config
        self.auth_store = auth_store
        self._playwright_factory = playwright_factory
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(config.max_concurrent_operations)
        self._active_pages: set[Page] = set()
        self._disconnected = False
        self._rebuild_attempted = False
        self._persist_on_close = False
        self._auth_state_may_be_present = False

    @property
    def active_page_count(self) -> int:
        return len(self._active_pages)

    @property
    def is_started(self) -> bool:
        return self._context is not None and self._browser_is_connected()

    @property
    def auth_state_may_be_present(self) -> bool:
        """Whether the current context may hold a login not represented by a file."""

        return self._auth_state_may_be_present

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self.is_started:
                return
            await self._start_locked()

    async def _start_locked(self) -> None:
        try:
            if self._playwright is None:
                self._playwright = await self._playwright_factory().start()

            launch_options: dict[str, Any] = {
                "headless": self.config.browser_headless,
            }
            if self.config.browser_path is not None:
                launch_options["executable_path"] = str(self.config.browser_path)
            else:
                launch_options["channel"] = GOOGLE_CHROME_CHANNEL
            proxy = build_proxy_settings(
                self.config.proxy.get_secret_value() if self.config.proxy else None
            )
            if proxy is not None:
                launch_options["proxy"] = proxy

            self._browser = await self._playwright.chromium.launch(**launch_options)
            self._browser.on("disconnected", self._on_disconnected)
            state = await self.auth_store.load()
            context_options: dict[str, Any] = {
                "locale": "zh-CN",
                "timezone_id": self.config.timezone,
                "viewport": {"width": 1440, "height": 900},
            }
            if state is not None:
                context_options["storage_state"] = state
            self._persist_on_close = state is not None
            self._auth_state_may_be_present = state is not None
            self._context = await self._browser.new_context(**context_options)
            self._disconnected = False
        except XhsError:
            await self._close_browser_locked()
            raise
        except (PlaywrightError, OSError) as exc:
            await self._close_browser_locked()
            raise XhsError(
                ErrorCode.BROWSER_ERROR,
                "无法启动 Google Chrome，请确认已安装 Chrome 或配置了 Chrome 浏览器路径。",
                retryable=False,
                details={"reason": type(exc).__name__},
            ) from exc

    async def ensure_started(self) -> None:
        if self.is_started:
            return
        async with self._lifecycle_lock:
            if self.is_started:
                return
            if self._disconnected:
                if self._rebuild_attempted:
                    raise XhsError(
                        ErrorCode.BROWSER_ERROR,
                        "Google Chrome 已断开，自动重建失败。",
                        retryable=True,
                    )
                self._rebuild_attempted = True
            await self._close_browser_locked()
            await self._start_locked()

    def mark_operation_success(self) -> None:
        self._rebuild_attempted = False

    def mark_authentication_possible(self) -> None:
        """Keep login checks on-page while a QR session can mutate the context."""

        self._auth_state_may_be_present = True

    @asynccontextmanager
    async def page(self) -> AsyncIterator[Page]:
        """Lease an isolated Page while respecting the configured concurrency cap."""

        async with self._semaphore:
            await self.ensure_started()
            page: Page | None = None
            try:
                async with self._lifecycle_lock:
                    if self._context is None:
                        raise XhsError(
                            ErrorCode.BROWSER_ERROR,
                            "浏览器登录上下文不可用。",
                            retryable=True,
                        )
                    page = await self._context.new_page()
                    self._active_pages.add(page)
                yield page
            finally:
                if page is not None:
                    self._active_pages.discard(page)
                    try:
                        await page.close()
                    except PlaywrightError:
                        pass

    async def persist_state(self) -> bool:
        async with self._persist_lock:
            async with self._lifecycle_lock:
                if self._context is None:
                    raise XhsError(
                        ErrorCode.BROWSER_ERROR,
                        "没有可保存的浏览器登录上下文。",
                        retryable=True,
                    )
                try:
                    state = await self._context.storage_state(indexed_db=True)
                except PlaywrightError as exc:
                    raise XhsError(
                        ErrorCode.AUTH_STATE_ERROR,
                        "无法从浏览器读取登录状态。",
                        retryable=True,
                        details={"reason": type(exc).__name__},
                    ) from exc
                changed = await self.auth_store.save(state)
                self._persist_on_close = True
                self._auth_state_may_be_present = True
                return changed

    async def clear_auth_state(self) -> bool:
        """Cancel current pages, delete persisted state, and create a blank context."""

        async with self._lifecycle_lock:
            for page in tuple(self._active_pages):
                try:
                    await page.close()
                except PlaywrightError:
                    pass
            self._active_pages.clear()
            if self._context is not None:
                try:
                    await self._context.close()
                except PlaywrightError:
                    pass
                self._context = None
            deleted = await self.auth_store.delete()
            self._persist_on_close = False
            self._auth_state_may_be_present = False
            if not self._browser_is_connected():
                await self._close_browser_locked()
                await self._start_locked()
            elif self._browser is not None:
                self._context = await self._browser.new_context(
                    locale="zh-CN",
                    timezone_id=self.config.timezone,
                    viewport={"width": 1440, "height": 900},
                )
            self._rebuild_attempted = False
            return deleted

    async def close(self, *, save_state: bool = True) -> None:
        async with self._lifecycle_lock:
            if (
                save_state
                and self._persist_on_close
                and self._context is not None
                and self._browser_is_connected()
            ):
                try:
                    state = await self._context.storage_state(indexed_db=True)
                    await self.auth_store.save(state)
                except (PlaywrightError, XhsError):
                    logger.warning("Could not persist browser state during shutdown")
            await self._close_browser_locked()
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except PlaywrightError:
                    pass
                self._playwright = None

    async def _close_browser_locked(self) -> None:
        self._active_pages.clear()
        if self._context is not None:
            try:
                await self._context.close()
            except PlaywrightError:
                pass
            self._context = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except PlaywrightError:
                pass
            self._browser = None

    def _browser_is_connected(self) -> bool:
        return bool(
            self._browser is not None
            and not self._disconnected
            and self._browser.is_connected()
        )

    def _on_disconnected(self, *_: Any) -> None:
        self._disconnected = True
        self._context = None
