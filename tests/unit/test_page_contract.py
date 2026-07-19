from urllib.parse import parse_qs, urlparse

from xhs_read_mcp.browser.page_contract import (
    RISK_CONTROL_TEXTS,
    RISK_CONTROL_PATH_PREFIXES,
    filter_targets,
    make_detail_url,
    make_search_url,
)
from xhs_read_mcp.models.public import (
    LocationFilter,
    NoteTypeFilter,
    PublishTime,
    SearchRequest,
    SearchScope,
    SortBy,
)


def test_search_url_encodes_keyword_and_source() -> None:
    parsed = urlparse(make_search_url("杭州 三天"))

    assert parsed.path == "/search_result"
    assert parse_qs(parsed.query) == {
        "keyword": ["杭州 三天"],
        "source": ["web_explore_feed"],
    }


def test_detail_url_encodes_id_and_token() -> None:
    parsed = urlparse(make_detail_url("id/with/slash", "token+value"))

    assert parsed.path.endswith("/id%2Fwith%2Fslash")
    assert parse_qs(parsed.query) == {
        "xsec_token": ["token+value"],
        "xsec_source": ["pc_feed"],
    }


def test_all_filter_groups_map_to_expected_dom_positions() -> None:
    request = SearchRequest(
        keyword="test",
        sort_by=SortBy.MOST_COLLECTED,
        note_type=NoteTypeFilter.IMAGE,
        publish_time=PublishTime.HALF_YEAR,
        search_scope=SearchScope.FOLLOWING,
        location=LocationFilter.NEARBY,
    )

    targets = filter_targets(request)

    assert [(item.group_index, item.tag_index, item.label) for item in targets] == [
        (1, 5, "最多收藏"),
        (2, 3, "图文"),
        (3, 4, "半年内"),
        (4, 4, "已关注"),
        (5, 3, "附近"),
    ]


def test_ip_risk_redirect_text_is_classified_as_risk_control() -> None:
    assert "IP存在风险" in RISK_CONTROL_TEXTS
    assert "安全限制" in RISK_CONTROL_TEXTS
    assert "/website-login/error" in RISK_CONTROL_PATH_PREFIXES
