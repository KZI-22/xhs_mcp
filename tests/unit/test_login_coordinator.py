import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from xhs_read_mcp.actions.login import LoginCoordinator
from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import LoginSessionStatus


class FakeBrowserManager:
    def __init__(self) -> None:
        self.persist_count = 0
        self.success_count = 0
        self.authentication_possible_count = 0

    @asynccontextmanager
    async def page(self):
        yield object()

    async def persist_state(self) -> bool:
        self.persist_count += 1
        return True

    def mark_operation_success(self) -> None:
        self.success_count += 1

    def mark_authentication_possible(self) -> None:
        self.authentication_possible_count += 1


class ControlledLoginAction:
    def __init__(self, *, initially_logged_in: bool = False) -> None:
        self.initially_logged_in = initially_logged_in
        self.result = asyncio.get_running_loop().create_future()
        self.prepare_count = 0

    async def check_login_status(self, page, timeout_seconds: float) -> bool:
        return self.initially_logged_in

    async def prepare_login(self, page, timeout_seconds: float):
        self.prepare_count += 1
        if self.prepare_count > 1:
            self.result = asyncio.get_running_loop().create_future()
        if self.initially_logged_in:
            return True, None
        return False, b"png"

    async def wait_for_login(
        self, page, timeout_seconds: float, poll_seconds: float = 0.5
    ):
        return await self.result


def make_coordinator(tmp_path: Path, action: ControlledLoginAction):
    config = AppConfig(
        _env_file=None,
        auth_state_path=tmp_path / "state.json",
        login_timeout_seconds=1,
    )
    browser = FakeBrowserManager()
    return LoginCoordinator(config, browser, action), browser


async def test_start_is_idempotent_while_session_is_pending(tmp_path: Path) -> None:
    action = ControlledLoginAction()
    coordinator, browser = make_coordinator(tmp_path, action)

    first = await coordinator.start()
    second = await coordinator.start()

    assert first.result.login_id == second.result.login_id
    assert first.result.status is LoginSessionStatus.PENDING
    assert first.qr_png == b"png"
    assert action.prepare_count == 1

    action.result.set_result(True)
    await coordinator._session.task
    status = await coordinator.get_status(first.result.login_id)
    assert status.status is LoginSessionStatus.SUCCEEDED
    assert browser.persist_count == 1
    assert browser.authentication_possible_count == 1


async def test_force_restart_cancels_old_session(tmp_path: Path) -> None:
    first_action = ControlledLoginAction()
    coordinator, _ = make_coordinator(tmp_path, first_action)
    first = await coordinator.start()

    second = await coordinator.start(force_restart=True)

    assert second.result.login_id != first.result.login_id
    assert second.result.status is LoginSessionStatus.PENDING
    await coordinator.cancel(second.result.login_id)


async def test_cancel_updates_session_status(tmp_path: Path) -> None:
    action = ControlledLoginAction()
    coordinator, _ = make_coordinator(tmp_path, action)
    started = await coordinator.start()

    result = await coordinator.cancel(started.result.login_id)

    assert result.status is LoginSessionStatus.CANCELLED


async def test_already_logged_in_finishes_without_qr(tmp_path: Path) -> None:
    action = ControlledLoginAction(initially_logged_in=True)
    coordinator, browser = make_coordinator(tmp_path, action)

    started = await coordinator.start()

    assert started.result.status is LoginSessionStatus.SUCCEEDED
    assert started.qr_png is None
    assert browser.persist_count == 1


async def test_unknown_login_id_has_stable_error(tmp_path: Path) -> None:
    action = ControlledLoginAction()
    coordinator, _ = make_coordinator(tmp_path, action)

    with pytest.raises(XhsError) as captured:
        await coordinator.get_status("missing")

    assert captured.value.code is ErrorCode.LOGIN_SESSION_NOT_FOUND


async def test_prepare_failure_does_not_leave_unconsumed_future(
    tmp_path: Path,
) -> None:
    action = ControlledLoginAction()

    async def fail_prepare(_page, _timeout_seconds: float):
        raise XhsError(ErrorCode.PAGE_STRUCTURE_CHANGED, "changed")

    action.prepare_login = fail_prepare
    coordinator, _ = make_coordinator(tmp_path, action)
    unhandled: list[dict] = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        with pytest.raises(XhsError) as captured:
            await coordinator.start()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert captured.value.code is ErrorCode.PAGE_STRUCTURE_CHANGED
    assert unhandled == []
