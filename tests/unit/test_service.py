from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from xhs_read_mcp.actions.login import LoginStart
from xhs_read_mcp.actions.search import build_search_result
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

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @asynccontextmanager
    async def page(self):
        yield object()

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

    async def check_login_status(self, page, timeout_seconds: float) -> bool:
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

