import pytest
from playwright.async_api import Error as PlaywrightError

from xhs_read_mcp.actions import search as search_module
from xhs_read_mcp.actions.search import SearchAction, build_search_result, is_note_payload
from xhs_read_mcp.browser.page_contract import (
    FilterTarget,
    SEARCH_EMPTY_RESULT_SCRIPT,
    SEARCH_FEEDS_SCRIPT,
)
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import SearchRequest, SortBy


def note_payload(*, note_id: str = "n1", token: str = "t1") -> dict:
    return {
        "id": note_id,
        "xsecToken": token,
        "modelType": "note",
        "index": 1,
        "noteCard": {
            "type": "normal",
            "displayTitle": "Title",
            "user": {"userId": "u1", "nickname": "Alice"},
            "interactInfo": {"likedCount": "1万"},
            "cover": {"url": "https://image"},
        },
    }


def test_search_result_filters_non_notes_and_preserves_metadata() -> None:
    request = SearchRequest(keyword="杭州", sort_by=SortBy.LATEST)

    result = build_search_result(
        [note_payload(), {"modelType": "hot_query", "title": "suggestion"}],
        request,
        duration_ms=12,
    )

    assert result.count == 1
    assert result.items[0].note_id == "n1"
    assert result.applied_filters.sort_by is SortBy.LATEST
    assert result.meta.raw_count == 2
    assert result.meta.skipped_non_note_items == 1
    assert result.meta.duration_ms == 12


def test_missing_token_is_returned_with_one_aggregated_warning() -> None:
    result = build_search_result(
        [note_payload(note_id="n1", token=""), note_payload(note_id="n2", token="")],
        SearchRequest(keyword="test"),
    )

    assert result.count == 2
    assert all(not item.detail_available for item in result.items)
    assert len(result.warnings) == 1
    assert result.warnings[0].details == {"count": 2}


def test_non_array_feeds_is_page_structure_error() -> None:
    with pytest.raises(XhsError) as captured:
        build_search_result({"unexpected": True}, SearchRequest(keyword="test"))

    assert captured.value.code is ErrorCode.PAGE_STRUCTURE_CHANGED


def test_note_payload_requires_note_card_object() -> None:
    assert is_note_payload(note_payload())
    assert not is_note_payload({"id": "n1", "noteCard": None})


class SequencedSearchPage:
    def __init__(
        self,
        payloads: list[object],
        *,
        explicitly_empty: bool = False,
        empty_check_failures: int = 0,
    ) -> None:
        self.payloads = iter(payloads)
        self.last_payload = payloads[-1]
        self.explicitly_empty = explicitly_empty
        self.empty_check_failures = empty_check_failures

    async def evaluate(self, script: str, arg=None):
        if script == SEARCH_FEEDS_SCRIPT:
            try:
                self.last_payload = next(self.payloads)
            except StopIteration:
                pass
            return self.last_payload
        if script == SEARCH_EMPTY_RESULT_SCRIPT:
            if self.empty_check_failures:
                self.empty_check_failures -= 1
                raise PlaywrightError("execution context was destroyed")
            return self.explicitly_empty
        raise AssertionError("unexpected browser script")


class FilterOption:
    def __init__(self, label: str) -> None:
        self.label = label
        self.clicked = False

    async def wait_for(self, **_kwargs) -> None:
        return None

    async def click(self) -> None:
        self.clicked = True


class FilterOptions:
    def __init__(self, labels: list[str]) -> None:
        self.items = [FilterOption(label) for label in labels]

    @property
    def first(self) -> FilterOption:
        return self.items[0]

    async def all_inner_texts(self) -> list[str]:
        return [item.label for item in self.items]

    def nth(self, index: int) -> FilterOption:
        return self.items[index]


class FilterGroup:
    def __init__(self, labels: list[str]) -> None:
        self.options = FilterOptions(labels)

    async def wait_for(self, **_kwargs) -> None:
        return None

    def locator(self, selector: str) -> FilterOptions:
        assert selector == "div.tags"
        return self.options


class FilterPage:
    def __init__(self, labels: list[str]) -> None:
        self.group = FilterGroup(labels)
        self.requested_selectors: list[str] = []

    def locator(self, selector: str) -> FilterGroup:
        self.requested_selectors.append(selector)
        return self.group


@pytest.fixture
def ignore_page_problem(monkeypatch):
    async def no_problem(_page) -> None:
        return None

    monkeypatch.setattr(search_module, "raise_for_page_problem", no_problem)


async def test_filter_option_is_selected_by_label_when_order_changes() -> None:
    page = FilterPage(["综合", "最新", "最多收藏", "最多评论", "最多点赞"])
    target = FilterTarget(group_index=1, tag_index=3, label="最多点赞")

    option = await SearchAction()._find_filter_option(page, target, 1)
    await option.click()

    assert page.requested_selectors == ["div.filter-panel div.filters:nth-child(1)"]
    assert page.group.options.items[4].clicked
    assert not page.group.options.items[2].clicked


async def test_missing_filter_label_reports_available_options() -> None:
    page = FilterPage(["综合", "最新", "最多收藏"])
    target = FilterTarget(group_index=1, tag_index=3, label="最多点赞")

    with pytest.raises(XhsError) as captured:
        await SearchAction()._find_filter_option(page, target, 1)

    assert captured.value.code is ErrorCode.PAGE_STRUCTURE_CHANGED
    assert captured.value.retryable is False
    assert captured.value.details == {
        "expected_label": "最多点赞",
        "group_index": 1,
        "available_labels": ["综合", "最新", "最多收藏"],
    }


async def test_stable_feeds_waits_for_initial_empty_state_to_fill(
    monkeypatch,
    ignore_page_problem,
) -> None:
    monkeypatch.setattr(search_module, "_SEARCH_POLL_SECONDS", 0)
    monkeypatch.setattr(search_module, "_SEARCH_STABLE_SECONDS", 0)
    expected = [note_payload()]
    page = SequencedSearchPage([[], [], expected, expected])

    result = await SearchAction()._stable_feeds(page, timeout_seconds=1)

    assert result == expected


async def test_stable_feeds_accepts_explicit_empty_result(
    monkeypatch,
    ignore_page_problem,
) -> None:
    monkeypatch.setattr(search_module, "_SEARCH_POLL_SECONDS", 0)
    monkeypatch.setattr(search_module, "_SEARCH_STABLE_SECONDS", 0)
    page = SequencedSearchPage([[], []], explicitly_empty=True)

    result = await SearchAction()._stable_feeds(page, timeout_seconds=1)

    assert result == []


async def test_empty_result_check_retries_during_navigation(
    monkeypatch,
    ignore_page_problem,
) -> None:
    monkeypatch.setattr(search_module, "_SEARCH_POLL_SECONDS", 0)
    monkeypatch.setattr(search_module, "_SEARCH_STABLE_SECONDS", 0)
    page = SequencedSearchPage(
        [[], []],
        explicitly_empty=True,
        empty_check_failures=1,
    )

    result = await SearchAction()._stable_feeds(page, timeout_seconds=1)

    assert result == []


async def test_stable_feeds_classifies_redirected_problem_page(monkeypatch) -> None:
    monkeypatch.setattr(search_module, "_SEARCH_POLL_SECONDS", 0)
    page = SequencedSearchPage([None])

    async def raise_risk_control(_page) -> None:
        raise XhsError(ErrorCode.RISK_CONTROL, "IP存在风险")

    monkeypatch.setattr(search_module, "raise_for_page_problem", raise_risk_control)

    with pytest.raises(XhsError) as captured:
        await SearchAction()._stable_feeds(page, timeout_seconds=1)

    assert captured.value.code is ErrorCode.RISK_CONTROL
