import json
from collections.abc import Callable

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from xhs_read_mcp.actions import login as login_module
from xhs_read_mcp.actions.login import LoginAction
from xhs_read_mcp.browser.page_contract import (
    LOGIN_ENTRY_SELECTORS,
    LOGIN_DIAGNOSTICS_SCRIPT,
    LOGIN_STATE_SCRIPT,
    LOGIN_SURFACE_TRIGGER_SELECTORS,
    QR_CODE_SELECTORS,
)
from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.errors import ErrorCode, XhsError


class FakeLocator:
    def __init__(self, page: "FakeLoginPage", selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self) -> "FakeLocator":
        return self

    async def all(self) -> list["FakeLocator"]:
        return [self] if self.selector in self.page.visible else []

    async def count(self) -> int:
        return int(self.selector in self.page.visible)

    async def is_visible(self) -> bool:
        return self.selector in self.page.visible

    async def inner_text(self, *, timeout: int = 0) -> str:
        return self.page.visible[self.selector]

    async def click(self, *, timeout: int = 0) -> None:
        self.page.clicked.append(self.selector)
        if self.page.on_click is not None:
            self.page.on_click(self.selector)

    async def screenshot(self, *, type: str) -> bytes:
        assert type == "png"
        return self.page.qr_png


class FakeLoginPage:
    def __init__(
        self,
        *,
        initial_state: bool | None,
        visible: dict[str, str] | None = None,
        goto_error: Exception | None = None,
        evaluate_error: Exception | None = None,
        on_click: Callable[[str], None] | None = None,
    ) -> None:
        self.initial_state = initial_state
        self.visible = visible or {}
        self.goto_error = goto_error
        self.evaluate_error = evaluate_error
        self.on_click = on_click
        self.clicked: list[str] = []
        self.qr_png = b"png"
        self.failure_png = b"failure-page"
        self.url = "https://www.xiaohongshu.com/explore?ignored=secret"
        self.goto_count = 0

    async def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_count += 1
        assert wait_until == "domcontentloaded"
        if self.goto_error is not None:
            raise self.goto_error
        self.url = url

    async def evaluate(self, script: str):
        if self.evaluate_error is not None:
            raise self.evaluate_error
        if script == LOGIN_STATE_SCRIPT:
            return self.initial_state
        if script == LOGIN_DIAGNOSTICS_SCRIPT:
            return {
                "ordinary_login_candidate_count": 0,
                "shadow_login_candidate_count": 0,
                "open_shadow_root_count": 0,
                "candidates": [],
            }
        raise AssertionError("unexpected browser script")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def title(self) -> str:
        return "小红书 - 你的生活兴趣社区"

    async def screenshot(
        self,
        *,
        type: str,
        full_page: bool,
        mask: list[FakeLocator],
        mask_color: str,
    ) -> bytes:
        assert type == "png"
        assert not full_page
        assert mask_color == "#000000"
        return self.failure_png


@pytest.fixture(autouse=True)
def stable_page(monkeypatch):
    async def no_page_problem(_page, **_kwargs) -> None:
        return None

    monkeypatch.setattr(login_module, "raise_for_page_problem", no_page_problem)
    monkeypatch.setattr(login_module, "_LOGIN_POLL_SECONDS", 0.001)


async def test_loaded_anonymous_page_returns_not_logged_in() -> None:
    page = FakeLoginPage(initial_state=False)

    result = await LoginAction().check_login_status(page, timeout_seconds=0.1)

    assert result is False
    assert page.goto_count == 1


async def test_logged_in_initial_state_returns_true() -> None:
    page = FakeLoginPage(initial_state=True)

    assert await LoginAction().check_login_status(page, timeout_seconds=0.1)


async def test_navigation_timeout_has_timeout_code() -> None:
    page = FakeLoginPage(
        initial_state=None,
        goto_error=PlaywrightTimeoutError("navigation timed out"),
    )

    with pytest.raises(XhsError) as captured:
        await LoginAction().check_login_status(page, timeout_seconds=0.01)

    assert captured.value.code is ErrorCode.TIMEOUT
    assert page.goto_count == 1


async def test_loaded_unknown_page_has_structure_changed_code() -> None:
    page = FakeLoginPage(initial_state=None)

    with pytest.raises(XhsError) as captured:
        await LoginAction().check_login_status(page, timeout_seconds=0.01)

    assert captured.value.code is ErrorCode.PAGE_STRUCTURE_CHANGED
    assert captured.value.details["stage"] == "check_login"
    assert captured.value.details["page_path"] == "/explore"


async def test_browser_failure_is_not_misclassified_as_structure_change() -> None:
    page = FakeLoginPage(
        initial_state=None,
        evaluate_error=PlaywrightError("page closed"),
    )

    with pytest.raises(PlaywrightError):
        await LoginAction().check_login_status(page, timeout_seconds=0.01)


async def test_login_entry_click_reveals_scannable_qr() -> None:
    entry_selector = LOGIN_ENTRY_SELECTORS[0]
    qr_selector = QR_CODE_SELECTORS[0]
    page: FakeLoginPage

    def reveal_qr(selector: str) -> None:
        if selector == entry_selector:
            page.visible[qr_selector] = ""

    page = FakeLoginPage(
        initial_state=False,
        visible={entry_selector: "登录"},
        on_click=reveal_qr,
    )

    logged_in, qr_png = await LoginAction().prepare_login(page, timeout_seconds=0.1)

    assert not logged_in
    assert qr_png == b"png"
    assert page.clicked == [entry_selector]


async def test_login_placeholder_input_click_reveals_scannable_qr() -> None:
    entry_selector = LOGIN_SURFACE_TRIGGER_SELECTORS[0]
    qr_selector = QR_CODE_SELECTORS[0]
    page: FakeLoginPage

    def reveal_qr(selector: str) -> None:
        if selector == entry_selector:
            page.visible[qr_selector] = ""

    page = FakeLoginPage(
        initial_state=False,
        visible={entry_selector: ""},
        on_click=reveal_qr,
    )

    logged_in, qr_png = await LoginAction().prepare_login(page, timeout_seconds=0.1)

    assert not logged_in
    assert qr_png == b"png"
    assert page.clicked == [entry_selector]


async def test_missing_login_entry_has_structure_changed_code() -> None:
    page = FakeLoginPage(initial_state=False)

    with pytest.raises(XhsError) as captured:
        await LoginAction().prepare_login(page, timeout_seconds=0.01)

    assert captured.value.code is ErrorCode.PAGE_STRUCTURE_CHANGED
    assert captured.value.details["stage"] == "find_login_entry"


async def test_triggered_login_surface_without_qr_has_structure_changed_code() -> None:
    entry_selector = LOGIN_ENTRY_SELECTORS[0]
    page = FakeLoginPage(
        initial_state=False,
        visible={entry_selector: "登录"},
    )

    with pytest.raises(XhsError) as captured:
        await LoginAction().prepare_login(page, timeout_seconds=0.01)

    assert captured.value.code is ErrorCode.PAGE_STRUCTURE_CHANGED
    assert captured.value.details["stage"] == "find_qr_code"


async def test_risk_control_error_is_preserved(monkeypatch) -> None:
    async def risk_control(_page, **_kwargs) -> None:
        raise XhsError(ErrorCode.RISK_CONTROL, "安全验证")

    monkeypatch.setattr(login_module, "raise_for_page_problem", risk_control)
    page = FakeLoginPage(initial_state=False)

    with pytest.raises(XhsError) as captured:
        await LoginAction().check_login_status(page, timeout_seconds=0.01)

    assert captured.value.code is ErrorCode.RISK_CONTROL


async def test_failure_writes_screenshot_and_sanitized_dom_summary(
    tmp_path,
) -> None:
    config = AppConfig(
        _env_file=None,
        debug_artifacts=True,
        debug_artifacts_path=tmp_path,
    )
    page = FakeLoginPage(initial_state=False)

    with pytest.raises(XhsError) as captured:
        await LoginAction(config).prepare_login(page, timeout_seconds=0.01)

    artifact_id = captured.value.details["debug_artifact_id"]
    screenshot = tmp_path / f"{artifact_id}.png"
    diagnostic = tmp_path / f"{artifact_id}.json"
    assert screenshot.read_bytes() == b"failure-page"
    payload = json.loads(diagnostic.read_text(encoding="utf-8"))
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["diagnostics"]["dom_summary"]["candidates"] == []
    assert "ignored=secret" not in serialized
    assert "storage_state" not in serialized
