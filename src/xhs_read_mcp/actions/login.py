"""Login page operations and one-session QR login coordination."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from xhs_read_mcp.actions.common import navigate, raise_for_page_problem
from xhs_read_mcp.browser.manager import BrowserManager
from xhs_read_mcp.browser.page_contract import (
    ANONYMOUS_SELECTORS,
    DEBUG_QR_MASK_SELECTORS,
    EXPLORE_URL,
    LOGIN_DIAGNOSTICS_SCRIPT,
    LOGGED_IN_SELECTORS,
    LOGIN_ENTRY_SELECTORS,
    LOGIN_ENTRY_TEXTS,
    LOGIN_STATE_SCRIPT,
    LOGIN_SURFACE_TRIGGER_SELECTORS,
    QR_CODE_SELECTORS,
    QR_LOGIN_ENTRY_SELECTORS,
    QR_LOGIN_ENTRY_TEXTS,
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


_LOGIN_POLL_SECONDS = 0.2
logger = logging.getLogger("xhs_read_mcp.login")


class _LoginPageState(StrEnum):
    LOGGED_IN = "logged_in"
    NOT_LOGGED_IN = "not_logged_in"
    UNKNOWN = "unknown"


async def _first_visible(page: Page, selectors: tuple[str, ...]) -> Locator | None:
    for selector in selectors:
        for locator in (await page.locator(selector).all())[:8]:
            if await locator.is_visible():
                return locator
    return None


async def _semantic_entry(
    page: Page,
    selectors: tuple[str, ...],
    accepted_texts: tuple[str, ...],
) -> Locator | None:
    for selector in selectors:
        for locator in (await page.locator(selector).all())[:8]:
            if not await locator.is_visible():
                continue
            try:
                text = "".join((await locator.inner_text(timeout=2000)).split())
            except PlaywrightTimeoutError:
                continue
            if text in accepted_texts:
                return locator
    return None


async def _page_diagnostics(page: Page, *, stage: str) -> dict[str, object]:
    details: dict[str, object] = {
        "stage": stage,
        "login_selectors_checked": len(LOGGED_IN_SELECTORS),
        "anonymous_selectors_checked": len(ANONYMOUS_SELECTORS),
        "qr_selectors_checked": len(QR_CODE_SELECTORS),
    }
    try:
        details["page_path"] = urlsplit(page.url).path or "/"
        details["title"] = (await asyncio.wait_for(page.title(), timeout=1.0))[:120]
    except (PlaywrightError, TimeoutError):
        details["page_unavailable"] = True
    try:
        summary = await asyncio.wait_for(
            page.evaluate(LOGIN_DIAGNOSTICS_SCRIPT), timeout=2.0
        )
        if isinstance(summary, dict):
            details["dom_summary"] = summary
    except (PlaywrightError, TimeoutError):
        details["dom_summary_unavailable"] = True
    return details


def _write_debug_artifact(
    directory: Path,
    artifact_id: str,
    screenshot: bytes,
    payload: dict[str, object],
    limit: int,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{artifact_id}.png").write_bytes(screenshot)
    (directory / f"{artifact_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    files = sorted(
        directory.glob("login-failure-*.*"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    for old_path in files[: max(0, len(files) - limit * 2)]:
        old_path.unlink(missing_ok=True)


class LoginAction:
    def __init__(self, config: AppConfig | None = None) -> None:
        self._debug_artifacts = bool(config and config.debug_artifacts)
        self._debug_artifacts_path = config.debug_artifacts_path if config else None
        self._debug_artifacts_limit = config.debug_artifacts_limit if config else 20

    async def _failure_details(self, page: Page, *, stage: str) -> dict[str, object]:
        details = await _page_diagnostics(page, stage=stage)
        if not self._debug_artifacts or self._debug_artifacts_path is None:
            return details
        artifact_id = await self._capture_failure_artifact(page, stage, details)
        if artifact_id is not None:
            details["debug_artifact_id"] = artifact_id
        return details

    async def _capture_failure_artifact(
        self,
        page: Page,
        stage: str,
        details: dict[str, object],
    ) -> str | None:
        assert self._debug_artifacts_path is not None
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        artifact_id = f"login-failure-{timestamp}-{stage}-{uuid4().hex[:8]}"
        try:
            masks: list[Locator] = []
            for selector in DEBUG_QR_MASK_SELECTORS:
                locator = page.locator(selector)
                if await locator.count() > 0:
                    masks.append(locator)
            screenshot = await page.screenshot(
                type="png",
                full_page=False,
                mask=masks,
                mask_color="#000000",
            )
            payload = {
                "artifact_id": artifact_id,
                "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "diagnostics": details,
            }
            await asyncio.to_thread(
                _write_debug_artifact,
                self._debug_artifacts_path,
                artifact_id,
                screenshot,
                payload,
                self._debug_artifacts_limit,
            )
        except (OSError, PlaywrightError, TypeError, ValueError) as exc:
            logger.warning(
                "Could not capture sanitized login failure artifact: %s",
                type(exc).__name__,
            )
            return None
        return artifact_id

    async def _classify(self, page: Page) -> _LoginPageState:
        await raise_for_page_problem(page, check_login_expired=False)
        initial_state = await page.evaluate(LOGIN_STATE_SCRIPT)
        if initial_state is True:
            return _LoginPageState.LOGGED_IN
        if await _first_visible(page, LOGGED_IN_SELECTORS) is not None:
            return _LoginPageState.LOGGED_IN
        if initial_state is False:
            return _LoginPageState.NOT_LOGGED_IN
        if await _first_visible(page, QR_CODE_SELECTORS) is not None:
            return _LoginPageState.NOT_LOGGED_IN
        if await _first_visible(page, ANONYMOUS_SELECTORS) is not None:
            return _LoginPageState.NOT_LOGGED_IN
        if (
            await _semantic_entry(page, LOGIN_ENTRY_SELECTORS, LOGIN_ENTRY_TEXTS)
            is not None
        ):
            return _LoginPageState.NOT_LOGGED_IN
        return _LoginPageState.UNKNOWN

    async def _wait_for_classification(
        self, page: Page, timeout_seconds: float
    ) -> _LoginPageState:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout_seconds)
        while True:
            state = await self._classify(page)
            if state is not _LoginPageState.UNKNOWN:
                return state
            remaining = deadline - loop.time()
            if remaining <= 0:
                return _LoginPageState.UNKNOWN
            await asyncio.sleep(min(_LOGIN_POLL_SECONDS, remaining))

    async def check_login_status(self, page: Page, timeout_seconds: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        await navigate(page, EXPLORE_URL, timeout_seconds, attempts=1)
        state = await self._wait_for_classification(
            page, max(0.0, deadline - loop.time())
        )
        if state is _LoginPageState.UNKNOWN:
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "页面已加载，但无法识别小红书登录状态。",
                retryable=False,
                details=await self._failure_details(page, stage="check_login"),
            )
        return state is _LoginPageState.LOGGED_IN

    async def prepare_login(
        self, page: Page, timeout_seconds: float
    ) -> tuple[bool, bytes | None]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        await navigate(page, EXPLORE_URL, timeout_seconds, attempts=1)
        state = await self._wait_for_classification(
            page, max(0.0, deadline - loop.time())
        )
        if state is _LoginPageState.LOGGED_IN:
            return True, None
        if state is _LoginPageState.UNKNOWN:
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "页面已加载，但无法识别匿名登录入口。",
                retryable=False,
                details=await self._failure_details(page, stage="find_login_entry"),
            )

        qr = await _first_visible(page, QR_CODE_SELECTORS)
        if qr is None:
            entry = await _semantic_entry(
                page, LOGIN_ENTRY_SELECTORS, LOGIN_ENTRY_TEXTS
            )
            if entry is None:
                entry = await _first_visible(page, LOGIN_SURFACE_TRIGGER_SELECTORS)
            if entry is None:
                raise XhsError(
                    ErrorCode.PAGE_STRUCTURE_CHANGED,
                    "未找到可验证的小红书登录入口。",
                    retryable=False,
                    details=await self._failure_details(page, stage="find_login_entry"),
                )
            try:
                await entry.click(
                    timeout=max(1, int(max(0.0, deadline - loop.time()) * 1000))
                )
            except PlaywrightTimeoutError as exc:
                await raise_for_page_problem(page, check_login_expired=False)
                raise XhsError(
                    ErrorCode.PAGE_STRUCTURE_CHANGED,
                    "登录入口存在，但未能打开登录界面。",
                    retryable=False,
                    details=await self._failure_details(
                        page, stage="open_login_surface"
                    ),
                ) from exc

        qr_mode_clicked = False
        while qr is None:
            await raise_for_page_problem(page, check_login_expired=False)
            if not qr_mode_clicked:
                qr_mode = await _semantic_entry(
                    page, QR_LOGIN_ENTRY_SELECTORS, QR_LOGIN_ENTRY_TEXTS
                )
                if qr_mode is not None:
                    await qr_mode.click(
                        timeout=max(1, int(max(0.0, deadline - loop.time()) * 1000))
                    )
                    qr_mode_clicked = True
            qr = await _first_visible(page, QR_CODE_SELECTORS)
            remaining = deadline - loop.time()
            if qr is not None or remaining <= 0:
                break
            await asyncio.sleep(min(_LOGIN_POLL_SECONDS, remaining))

        if qr is None:
            raise XhsError(
                ErrorCode.PAGE_STRUCTURE_CHANGED,
                "登录界面已触发，但未找到可扫描的二维码。",
                retryable=False,
                details=await self._failure_details(page, stage="find_qr_code"),
            )

        image = await qr.screenshot(type="png")
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
                if await self._classify(page) is _LoginPageState.LOGGED_IN:
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
    error: XhsError | None = None


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
        if session.error is not None:
            raise session.error
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
                    await self.browser_manager.persist_state()
                    self.browser_manager.mark_operation_success()
                    session.status = LoginSessionStatus.SUCCEEDED
                    session.message = "当前已经登录，登录状态已保存。"
                    self._set_ready(session)
                    return

                session.qr_png = qr_png
                session.message = "请使用小红书客户端扫描二维码。"
                self.browser_manager.mark_authentication_possible()
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
            session.error = exc
        except PlaywrightError as exc:
            session.status = LoginSessionStatus.FAILED
            session.message = "浏览器页面操作失败。"
            session.error = XhsError(
                ErrorCode.BROWSER_ERROR,
                session.message,
                retryable=True,
                details={"reason": type(exc).__name__},
            )
        except Exception as exc:
            session.status = LoginSessionStatus.FAILED
            session.message = "扫码登录任务发生内部错误。"
            session.error = XhsError(
                ErrorCode.INTERNAL_ERROR,
                session.message,
                details={"reason": type(exc).__name__},
            )
        finally:
            self._set_ready(session)

    def _start_snapshot(self, session: _LoginSession) -> LoginStart:
        return LoginStart(result=self._snapshot(session), qr_png=session.qr_png)

    def _snapshot(self, session: _LoginSession) -> LoginSessionResult:
        timezone = ZoneInfo(self.config.timezone)
        return LoginSessionResult(
            login_id=session.login_id,
            status=session.status,
            created_at=session.created_at.astimezone(timezone).isoformat(
                timespec="seconds"
            ),
            expires_at=session.expires_at.astimezone(timezone).isoformat(
                timespec="seconds"
            ),
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
