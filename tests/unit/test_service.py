import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError

from xhs_read_mcp.actions.login import LoginStart
from xhs_read_mcp.actions.search import build_search_result
from xhs_read_mcp import service as service_module
from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import (
    LoginSessionResult,
    LoginSessionStatus,
    SearchRequest,
)
from xhs_read_mcp.service import XhsReadService


class FakeBrowserManager:
    def __init__(self) -> None:
        self.persist_count = 0
        self.clear_count = 0
        self.fail_persist = False
        self.auth_state_may_be_present = True
        self.page_count = 0
        self.page_cleanup_count = 0

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @asynccontextmanager
    async def page(self):
        self.page_count += 1
        try:
            yield object()
        finally:
            self.page_cleanup_count += 1

    async def persist_state(self) -> bool:
        self.persist_count += 1
        if self.fail_persist:
            raise XhsError(ErrorCode.AUTH_STATE_ERROR, "failed")
        return True

    async def clear_auth_state(self) -> bool:
        self.clear_count += 1
        return True

    def mark_operation_success(self) -> None:
        return None


class FakeLoginAction:
    def __init__(self, logged_in: bool) -> None:
        self.logged_in = logged_in
        self.calls = 0

    async def check_login_status(self, page, timeout_seconds: float) -> bool:
        self.calls += 1
        return self.logged_in


class FakeLoginCoordinator:
    def __init__(self) -> None:
        self.cancelled = False

    async def start(self, *, force_restart: bool = False) -> LoginStart:
        result = LoginSessionResult(
            login_id="id",
            status=LoginSessionStatus.PENDING,
            created_at="2026-01-01T00:00:00+08:00",
            expires_at="2026-01-01T00:04:00+08:00",
        )
        return LoginStart(result, b"png")

    async def get_status(self, login_id: str) -> LoginSessionResult:
        return (await self.start()).result

    async def cancel(self, login_id: str) -> LoginSessionResult:
        self.cancelled = True
        result = (await self.start()).result
        result.status = LoginSessionStatus.CANCELLED
        return result

    async def cancel_active(self) -> None:
        self.cancelled = True

    async def close(self) -> None:
        return None


class FakeSearchAction:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, page, request: SearchRequest, timeout_seconds: float):
        self.calls += 1
        return build_search_result([], request)


def make_service(tmp_path: Path, *, logged_in: bool):
    config = AppConfig(_env_file=None, auth_state_path=tmp_path / "state.json")
    browser = FakeBrowserManager()
    coordinator = FakeLoginCoordinator()
    search = FakeSearchAction()
    service = XhsReadService(
        config,
        browser_manager=browser,
        login_action=FakeLoginAction(logged_in),
        login_coordinator=coordinator,
        search_action=search,
    )
    return service, browser, coordinator, search


async def test_search_requires_valid_login(tmp_path: Path) -> None:
    service, _, _, search = make_service(tmp_path, logged_in=False)

    with pytest.raises(XhsError) as captured:
        await service.search_notes(SearchRequest(keyword="test"))

    assert captured.value.code is ErrorCode.NOT_LOGGED_IN
    assert search.calls == 0


async def test_successful_search_persists_refreshed_state(tmp_path: Path) -> None:
    service, browser, _, search = make_service(tmp_path, logged_in=True)

    result = await service.search_notes(SearchRequest(keyword="test"))

    assert result.count == 0
    assert search.calls == 1
    assert browser.persist_count == 2  # login check and completed search


async def test_persist_failure_is_a_warning_not_a_failed_read(tmp_path: Path) -> None:
    service, browser, _, _ = make_service(tmp_path, logged_in=True)
    browser.fail_persist = True

    result = await service.search_notes(SearchRequest(keyword="test"))

    assert result.warnings[-1].code == "AUTH_STATE_SAVE_FAILED"


async def test_logout_cancels_login_and_resets_browser_context(tmp_path: Path) -> None:
    service, browser, coordinator, _ = make_service(tmp_path, logged_in=True)

    result = await service.logout()

    assert result.cleared
    assert coordinator.cancelled
    assert browser.clear_count == 1


async def test_check_login_without_saved_or_in_memory_state_is_fast(
    tmp_path: Path,
) -> None:
    service, browser, _, _ = make_service(tmp_path, logged_in=True)
    browser.auth_state_may_be_present = False

    result = await asyncio.wait_for(service.check_login(), timeout=0.1)

    assert not result.is_logged_in
    assert browser.page_count == 0
    assert service.login_action.calls == 0


async def test_outer_watchdog_does_not_mask_specific_action_error(
    tmp_path: Path,
) -> None:
    service, _, _, _ = make_service(tmp_path, logged_in=False)
    service.config.status_timeout_seconds = 0.01

    async def delayed_structure_error(_page, _timeout_seconds: float) -> bool:
        await asyncio.sleep(0.02)
        raise XhsError(ErrorCode.PAGE_STRUCTURE_CHANGED, "changed")

    service.login_action.check_login_status = delayed_structure_error

    with pytest.raises(XhsError) as captured:
        await service.check_login()

    assert captured.value.code is ErrorCode.PAGE_STRUCTURE_CHANGED


async def test_check_login_timeout_consumes_cancelled_task_and_cleans_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, browser, _, _ = make_service(tmp_path, logged_in=False)
    service.config.status_timeout_seconds = 0.01
    monkeypatch.setattr(service_module, "_MIN_CHECK_LOGIN_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(service_module, "_MAX_CHECK_LOGIN_GRACE_SECONDS", 0.0)
    cancelled = asyncio.Event()
    unhandled: list[dict] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

    async def wait_until_cancelled(_page, _timeout_seconds: float) -> bool:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    service.login_action.check_login_status = wait_until_cancelled
    try:
        with pytest.raises(XhsError) as captured:
            await service.check_login()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert captured.value.code is ErrorCode.TIMEOUT
    assert cancelled.is_set()
    assert browser.page_cleanup_count == 1
    assert unhandled == []


async def test_check_login_maps_closed_page_to_browser_error(tmp_path: Path) -> None:
    service, _, _, _ = make_service(tmp_path, logged_in=False)

    async def closed_page(_page, _timeout_seconds: float) -> bool:
        raise PlaywrightError("page closed")

    service.login_action.check_login_status = closed_page

    with pytest.raises(XhsError) as captured:
        await service.check_login()

    assert captured.value.code is ErrorCode.BROWSER_ERROR
