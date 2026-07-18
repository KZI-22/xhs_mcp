"""Login page operations and one-session QR login coordination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from xhs_read_mcp.actions.common import navigate, raise_for_page_problem
from xhs_read_mcp.browser.manager import BrowserManager
from xhs_read_mcp.browser.page_contract import (
    EXPLORE_URL,
    LOGGED_IN_SELECTOR,
    QR_CODE_SELECTOR,
)
from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import LoginSessionResult, LoginSessionStatus


class LoginPageAction(Protocol):
    async def check_login_status(self, page: Page, timeout_seconds: float) -> bool: ...

    async def prepare_login(
        self, page: Page, timeout_seconds: float
    ) -> tuple[bool, bytes | None]: ...

    async def wait_for_login(
        self, page: Page, timeout_seconds: float, poll_seconds: float = 0.5
    ) -> bool: ...


class LoginAction:
    async def check_login_status(self, page: Page, timeout_seconds: float) -> bool:
        await navigate(page, EXPLORE_URL, timeout_seconds)
        try:
            await page.wait_for_function(
                """([logged, qr]) =>
                    document.querySelector(logged) !== null ||
                    document.querySelector(qr) !== null
                """,
                arg=[LOGGED_IN_SELECTOR, QR_CODE_SELECTOR],
                timeout=int(timeout_seconds * 1000),
            )
        except PlaywrightTimeoutError:
            await raise_for_page_problem(page, check_login_expired=False)
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "无法识别小红书登录状态，页面结构可能已经变化。",
                retryable=False,
            )
        await raise_for_page_problem(page, check_login_expired=False)
        return await page.locator(LOGGED_IN_SELECTOR).count() > 0

    async def prepare_login(
        self, page: Page, timeout_seconds: float
    ) -> tuple[bool, bytes | None]:
        await navigate(page, EXPLORE_URL, timeout_seconds)
        if await page.locator(LOGGED_IN_SELECTOR).count() > 0:
            return True, None
        await raise_for_page_problem(page, check_login_expired=False)
        qr = page.locator(QR_CODE_SELECTOR).first
        try:
            await qr.wait_for(state="visible", timeout=int(timeout_seconds * 1000))
            image = await qr.screenshot(type="png")
        except PlaywrightTimeoutError as exc:
            await raise_for_page_problem(page, check_login_expired=False)
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "未找到登录二维码，页面结构可能已经变化。",
                retryable=False,
            ) from exc
        if not image:
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "登录二维码图片为空。",
                retryable=False,
            )
        return False, image

    async def wait_for_login(
        self, page: Page, timeout_seconds: float, poll_seconds: float = 0.5
    ) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            try:
                if await page.locator(LOGGED_IN_SELECTOR).count() > 0:
                    return True
            except PlaywrightError as exc:
                raise XhsError(
                    ErrorCode.BROWSER_ERROR,
                    "等待扫码时浏览器页面已不可用。",
                    retryable=True,
                ) from exc
            await asyncio.sleep(min(poll_seconds, max(0, deadline - loop.time())))
        return False


@dataclass(slots=True)
class LoginStart:
    result: LoginSessionResult
    qr_png: bytes | None


@dataclass(slots=True)
class _LoginSession:
    login_id: str
    created_at: datetime
    expires_at: datetime
    status: LoginSessionStatus = LoginSessionStatus.PENDING
    qr_png: bytes | None = None
    message: str = ""
    ready: asyncio.Future[None] | None = None
    task: asyncio.Task[None] | None = None


class LoginCoordinator:
    """Own the single background QR login session."""

    def __init__(
        self,
        config: AppConfig,
        browser_manager: BrowserManager,
        action: LoginPageAction | None = None,
    ) -> None:
        self.config = config
        self.browser_manager = browser_manager
        self.action = action or LoginAction()
        self._lock = asyncio.Lock()
        self._session: _LoginSession | None = None

    async def start(self, *, force_restart: bool = False) -> LoginStart:
        previous_task: asyncio.Task[None] | None = None
        async with self._lock:
            if self._session and self._session.status is LoginSessionStatus.PENDING:
                if not force_restart:
                    return self._start_snapshot(self._session)
                previous_task = self._session.task
                if previous_task is not None:
                    previous_task.cancel()

            now = datetime.now(UTC)
            session = _LoginSession(
                login_id=uuid4().hex,
                created_at=now,
                expires_at=now + timedelta(seconds=self.config.login_timeout_seconds),
                ready=asyncio.get_running_loop().create_future(),
            )
            session.task = asyncio.create_task(
                self._run_session(session),
                name=f"xhs-login-{session.login_id}",
            )
            session.task.add_done_callback(self._consume_task_result)
            self._session = session

        if previous_task is not None:
            await asyncio.gather(previous_task, return_exceptions=True)
        assert session.ready is not None
        await session.ready
        return self._start_snapshot(session)

    async def get_status(self, login_id: str) -> LoginSessionResult:
        async with self._lock:
            session = self._require_session(login_id)
            return self._snapshot(session)

    async def cancel(self, login_id: str) -> LoginSessionResult:
        async with self._lock:
            session = self._require_session(login_id)
            task = session.task
            if session.status is LoginSessionStatus.PENDING and task is not None:
                task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            return self._snapshot(session)

    async def cancel_active(self) -> None:
        async with self._lock:
            task = self._session.task if self._session else None
            if task is not None and not task.done():
                task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        await self.cancel_active()

    async def _run_session(self, session: _LoginSession) -> None:
        try:
            async with self.browser_manager.page() as page:
                logged_in, qr_png = await self.action.prepare_login(
                    page, self.config.status_timeout_seconds
                )
                if logged_in:
                    session.status = LoginSessionStatus.SUCCEEDED
                    session.message = "当前已经登录。"
                    self._set_ready(session)
                    return

                session.qr_png = qr_png
                session.message = "请使用小红书客户端扫描二维码。"
                self._set_ready(session)
                succeeded = await self.action.wait_for_login(
                    page, self.config.login_timeout_seconds
                )
                if not succeeded:
                    session.status = LoginSessionStatus.EXPIRED
                    session.message = "登录二维码已过期。"
                    return
                await self.browser_manager.persist_state()
                self.browser_manager.mark_operation_success()
                session.status = LoginSessionStatus.SUCCEEDED
                session.message = "扫码登录成功，登录状态已保存。"
        except asyncio.CancelledError:
            session.status = LoginSessionStatus.CANCELLED
            session.message = "扫码登录已取消。"
            self._set_ready(session)
            raise
        except XhsError as exc:
            session.status = LoginSessionStatus.FAILED
            session.message = exc.message
            if session.ready is not None and not session.ready.done():
                session.ready.set_exception(exc)
        except Exception as exc:
            session.status = LoginSessionStatus.FAILED
            session.message = "扫码登录任务发生内部错误。"
            error = XhsError(
                ErrorCode.INTERNAL_ERROR,
                session.message,
                details={"reason": type(exc).__name__},
            )
            if session.ready is not None and not session.ready.done():
                session.ready.set_exception(error)
        finally:
            self._set_ready(session)

    def _start_snapshot(self, session: _LoginSession) -> LoginStart:
        return LoginStart(result=self._snapshot(session), qr_png=session.qr_png)

    def _snapshot(self, session: _LoginSession) -> LoginSessionResult:
        timezone = ZoneInfo(self.config.timezone)
        return LoginSessionResult(
            login_id=session.login_id,
            status=session.status,
            created_at=session.created_at.astimezone(timezone).isoformat(timespec="seconds"),
            expires_at=session.expires_at.astimezone(timezone).isoformat(timespec="seconds"),
            is_logged_in=session.status is LoginSessionStatus.SUCCEEDED,
            qr_mime_type="image/png" if session.qr_png else None,
            message=session.message,
        )

    def _require_session(self, login_id: str) -> _LoginSession:
        if self._session is None or self._session.login_id != login_id:
            raise XhsError(
                ErrorCode.LOGIN_SESSION_NOT_FOUND,
                "没有找到指定的扫码登录会话。",
                retryable=False,
            )
        return self._session

    @staticmethod
    def _set_ready(session: _LoginSession) -> None:
        if session.ready is not None and not session.ready.done():
            session.ready.set_result(None)

    @staticmethod
    def _consume_task_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            pass
