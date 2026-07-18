"""Centralized Xiaohongshu page URLs, selectors, labels, and state paths."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlencode

from xhs_read_mcp.models.public import (
    LocationFilter,
    NoteTypeFilter,
    PublishTime,
    SearchRequest,
    SearchScope,
    SortBy,
)


EXPLORE_URL = "https://www.xiaohongshu.com/explore"
SEARCH_URL = "https://www.xiaohongshu.com/search_result"
DETAIL_URL_PREFIX = "https://www.xiaohongshu.com/explore"

LOGGED_IN_SELECTOR = ".main-container .user .link-wrapper .channel"
QR_CODE_SELECTOR = ".login-container .qrcode-img"
FILTER_TRIGGER_SELECTOR = "div.filter"
FILTER_PANEL_SELECTOR = "div.filter-panel"
NO_COMMENTS_SELECTOR = ".no-comments-text"
COMMENT_CONTAINER_SELECTOR = ".comments-container"
COMMENT_PARENT_SELECTOR = ".parent-comment"
COMMENT_SHOW_MORE_SELECTOR = ".show-more"
COMMENT_END_SELECTOR = ".end-container"
COMMENT_SCROLL_TARGETS = (
    ".note-scroller",
    ".interaction-container",
    "html",
)

ACCESS_CONTAINER_SELECTOR = (
    ".access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper"
)
INACCESSIBLE_TEXTS = (
    "当前笔记暂时无法浏览",
    "该内容因违规已被删除",
    "该笔记已被删除",
    "内容不存在",
    "笔记不存在",
    "已失效",
    "私密笔记",
    "仅作者可见",
    "因用户设置，你无法查看",
    "因违规无法查看",
)
RISK_CONTROL_TEXTS = (
    "访问频繁",
    "操作频繁",
    "安全验证",
    "请完成验证",
    "IP存在风险",
    "网络环境存在风险",
    "安全限制",
    "验证码",
)
SEARCH_EMPTY_RESULT_TEXTS = (
    "没有找到相关结果",
    "没有相关搜索结果",
    "暂无搜索结果",
    "暂时没有相关内容",
    "换个关键词试试",
)


@dataclass(frozen=True, slots=True)
class FilterTarget:
    group_index: int
    tag_index: int
    label: str

    @property
    def selector(self) -> str:
        return (
            "div.filter-panel "
            f"div.filters:nth-child({self.group_index}) "
            f"div.tags:nth-child({self.tag_index})"
        )


SORT_TARGETS = {
    SortBy.RELEVANCE: FilterTarget(1, 1, "综合"),
    SortBy.LATEST: FilterTarget(1, 2, "最新"),
    SortBy.MOST_LIKED: FilterTarget(1, 3, "最多点赞"),
    SortBy.MOST_COMMENTED: FilterTarget(1, 4, "最多评论"),
    SortBy.MOST_COLLECTED: FilterTarget(1, 5, "最多收藏"),
}
NOTE_TYPE_TARGETS = {
    NoteTypeFilter.ANY: FilterTarget(2, 1, "不限"),
    NoteTypeFilter.VIDEO: FilterTarget(2, 2, "视频"),
    NoteTypeFilter.IMAGE: FilterTarget(2, 3, "图文"),
}
PUBLISH_TIME_TARGETS = {
    PublishTime.ANY: FilterTarget(3, 1, "不限"),
    PublishTime.DAY: FilterTarget(3, 2, "一天内"),
    PublishTime.WEEK: FilterTarget(3, 3, "一周内"),
    PublishTime.HALF_YEAR: FilterTarget(3, 4, "半年内"),
}
SEARCH_SCOPE_TARGETS = {
    SearchScope.ANY: FilterTarget(4, 1, "不限"),
    SearchScope.VIEWED: FilterTarget(4, 2, "已看过"),
    SearchScope.UNVIEWED: FilterTarget(4, 3, "未看过"),
    SearchScope.FOLLOWING: FilterTarget(4, 4, "已关注"),
}
LOCATION_TARGETS = {
    LocationFilter.ANY: FilterTarget(5, 1, "不限"),
    LocationFilter.SAME_CITY: FilterTarget(5, 2, "同城"),
    LocationFilter.NEARBY: FilterTarget(5, 3, "附近"),
}


def make_search_url(keyword: str) -> str:
    return f"{SEARCH_URL}?{urlencode({'keyword': keyword, 'source': 'web_explore_feed'})}"


def make_detail_url(note_id: str, xsec_token: str) -> str:
    encoded_id = quote(note_id, safe="")
    query = urlencode({"xsec_token": xsec_token, "xsec_source": "pc_feed"})
    return f"{DETAIL_URL_PREFIX}/{encoded_id}?{query}"


def filter_targets(request: SearchRequest) -> list[FilterTarget]:
    result: list[FilterTarget] = []
    pairs = (
        (request.sort_by, SORT_TARGETS),
        (request.note_type, NOTE_TYPE_TARGETS),
        (request.publish_time, PUBLISH_TIME_TARGETS),
        (request.search_scope, SEARCH_SCOPE_TARGETS),
        (request.location, LOCATION_TARGETS),
    )
    for value, mapping in pairs:
        if value is not None:
            result.append(mapping[value])
    return result


SEARCH_FEEDS_SCRIPT = """() => {
    const root = window.__INITIAL_STATE__;
    if (!root || !root.search || root.search.feeds === undefined) return null;
    const feeds = root.search.feeds;
    const data = feeds && feeds.value !== undefined
        ? feeds.value
        : (feeds && feeds._value !== undefined ? feeds._value : feeds);
    return data == null ? null : JSON.parse(JSON.stringify(data));
}"""

SEARCH_FEEDS_READY_SCRIPT = """() => {
    const root = window.__INITIAL_STATE__;
    return !!(root && root.search && root.search.feeds !== undefined);
}"""

SEARCH_EMPTY_RESULT_SCRIPT = """(markers) => {
    const text = document.body ? document.body.innerText : "";
    return markers.some((marker) => text.includes(marker));
}"""

DETAIL_MAP_SCRIPT = """() => {
    const root = window.__INITIAL_STATE__;
    const detailMap = root && root.note && root.note.noteDetailMap;
    return detailMap == null ? null : JSON.parse(JSON.stringify(detailMap));
}"""

DETAIL_MAP_READY_SCRIPT = """() => {
    const root = window.__INITIAL_STATE__;
    return !!(root && root.note && root.note.noteDetailMap);
}"""
