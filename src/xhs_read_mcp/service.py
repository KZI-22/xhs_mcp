"""Transport-independent orchestration for all read-only use cases."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError

from xhs_read_mcp.actions.feed_detail import FeedDetailAction
from xhs_read_mcp.actions.login import LoginAction, LoginCoordinator, LoginStart
from xhs_read_mcp.actions.search import SearchAction
from xhs_read_mcp.browser.auth_store import AuthStateStore
from xhs_read_mcp.browser.manager import BrowserManager
from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import (
    CommentMode,
    DetailRequest,
    LoginSessionResult,
    LoginStatusResult,
    LogoutResult,
    NoteDetailResult,
    SearchRequest,
    SearchResult,
    WarningInfo,
)


logger = logging.getLogger("xhs_read_mcp.service")
_MIN_CHECK_LOGIN_GRACE_SECONDS = 1.0
_MAX_CHECK_LOGIN_GRACE_SECONDS = 5.0


class XhsReadService:
    def __init__(
        self,
        config: AppConfig,
        *,
        auth_store: AuthStateStore | None = None,
        browser_manager: BrowserManager | None = None,
        login_action: LoginAction | None = None,
        login_coordinator: LoginCoordinator | None = None,
        search_action: SearchAction | None = None,
        detail_action: FeedDetailAction | None = None,
    ) -> None:
        self.config = config
        self.auth_store = auth_store or AuthStateStore(config.auth_state_path)
        self.browser_manager = browser_manager or BrowserManager(
            config, self.auth_store
        )
        self.login_action = login_action or LoginAction(config)
        self.login_coordinator = login_coordinator or LoginCoordinator(
            config, self.browser_manager, self.login_action
        )
        self.search_action = search_action or SearchAction()
        self.detail_action = detail_action or FeedDetailAction()

    async def start(self) -> None:
        await self.browser_manager.start()

    async def close(self) -> None:
        await self.login_coordinator.close()
        await self.browser_manager.close()

    async def check_login(self) -> LoginStatusResult:
        has_saved_state = await self.auth_store.exists()
        context_may_be_authenticated = getattr(
            self.browser_manager, "auth_state_may_be_present", True
        )
        if not has_saved_state and not context_may_be_authenticated:
            return LoginStatusResult(is_logged_in=False, checked_at=self._now_iso())

        async def operation() -> bool:
            async with self.browser_manager.page() as page:
                return await self.login_action.check_login_status(
                    page, self.config.status_timeout_seconds
                )

        task = asyncio.create_task(operation(), name="xhs-check-login")
        try:
            grace = min(
                _MAX_CHECK_LOGIN_GRACE_SECONDS,
                max(
                    _MIN_CHECK_LOGIN_GRACE_SECONDS,
                    self.config.status_timeout_seconds * 0.2,
                ),
            )
            done, _ = await asyncio.wait(
                {task}, timeout=self.config.status_timeout_seconds + grace
            )
            if task not in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise XhsError(
                    ErrorCode.TIMEOUT,
                    "检查登录状态超时。",
                    retryable=True,
                )
            is_logged_in = await task
            if is_logged_in:
                self.browser_manager.mark_operation_success()
                try:
                    await self.browser_manager.persist_state()
                except XhsError:
                    logger.warning(
                        "Could not persist refreshed state after login check"
                    )
            return LoginStatusResult(
                is_logged_in=is_logged_in,
                checked_at=self._now_iso(),
            )
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except TimeoutError as exc:
            raise XhsError(
                ErrorCode.TIMEOUT,
                "检查登录状态超时。",
                retryable=True,
            ) from exc
        except PlaywrightError as exc:
            raise self._browser_error(exc) from exc

    async def start_login(self, *, force_restart: bool = False) -> LoginStart:
        return await self.login_coordinator.start(force_restart=force_restart)

    async def get_login_status(self, login_id: str) -> LoginSessionResult:
        return await self.login_coordinator.get_status(login_id)

    async def cancel_login(self, login_id: str) -> LoginSessionResult:
        return await self.login_coordinator.cancel(login_id)

    async def logout(self) -> LogoutResult:
        await self.login_coordinator.cancel_active()
        deleted = await self.browser_manager.clear_auth_state()
        return LogoutResult(
            cleared=True,
            message=(
                "已清除本机登录状态；这不会主动吊销小红书服务器端 Cookie。"
                if deleted
                else "本机没有已保存的登录状态；浏览器上下文已重置。"
            ),
        )

    async def search_notes(self, request: SearchRequest) -> SearchResult:
        await self._require_login()
        try:
            async with asyncio.timeout(self.config.search_timeout_seconds):
                async with self.browser_manager.page() as page:
                    result = await self.search_action.search(
                        page, request, self.config.search_timeout_seconds
                    )
            self.browser_manager.mark_operation_success()
            warning = await self._persist_warning()
            if warning is not None:
                result.warnings.append(warning)
            return result
        except TimeoutError as exc:
            raise XhsError(
                ErrorCode.TIMEOUT,
                "搜索笔记超时。",
                retryable=True,
            ) from exc
        except PlaywrightError as exc:
            raise self._browser_error(exc) from exc

    async def get_note_detail(self, request: DetailRequest) -> NoteDetailResult:
        await self._require_login()
        overall_timeout = self.config.detail_timeout_seconds
        if (
            request.comment_mode is CommentMode.LOAD
            and request.comment_options is not None
        ):
            overall_timeout = min(
                max(overall_timeout, request.comment_options.timeout_seconds),
                self.config.max_comment_timeout_seconds,
            )
        try:
            async with asyncio.timeout(overall_timeout):
                async with self.browser_manager.page() as page:
                    result = await self.detail_action.get_detail(
                        page,
                        request,
                        timezone=self.config.timezone,
                        detail_timeout_seconds=self.config.detail_timeout_seconds,
                    )
            self.browser_manager.mark_operation_success()
            warning = await self._persist_warning()
            if warning is not None:
                result.warnings.append(warning)
            return result
        except TimeoutError as exc:
            raise XhsError(
                ErrorCode.TIMEOUT,
                "读取笔记详情超时。",
                retryable=True,
            ) from exc
        except PlaywrightError as exc:
            raise self._browser_error(exc) from exc

    async def _require_login(self) -> None:
        status = await self.check_login()
        if not status.is_logged_in:
            raise XhsError(
                ErrorCode.NOT_LOGGED_IN,
                "当前没有有效的小红书登录状态，请先调用 xhs_start_login。",
                retryable=False,
            )

    async def _persist_warning(self) -> WarningInfo | None:
        try:
            await self.browser_manager.persist_state()
            return None
        except XhsError as exc:
            logger.warning("Could not persist refreshed browser state")
            return WarningInfo(
                code="AUTH_STATE_SAVE_FAILED",
                message="本次读取成功，但刷新后的登录状态未能保存。",
                details={"error_code": exc.code.value},
            )

    def _now_iso(self) -> str:
        return (
            datetime.now(UTC)
            .astimezone(ZoneInfo(self.config.timezone))
            .isoformat(timespec="seconds")
        )

    @staticmethod
    def _browser_error(exc: Exception) -> XhsError:
        return XhsError(
            ErrorCode.BROWSER_ERROR,
            "浏览器页面操作失败。",
            retryable=True,
            details={"reason": type(exc).__name__},
        )
