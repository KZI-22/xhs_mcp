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
RISK_CONTROL_PATH_PREFIXES = ("/website-login/error",)

LOGGED_IN_SELECTORS = (
    ".main-container .user .link-wrapper .channel",
    "[class*='side-bar'] [class*='user'] a[href*='/user/profile/']",
    "nav [class*='user'] a[href*='/user/profile/']",
    "aside [class*='user'] a[href*='/user/profile/']",
)
LOGGED_IN_SELECTOR = ", ".join(LOGGED_IN_SELECTORS)

# Keep these fallbacks deliberately small and scoped to a login surface. Broad
# selectors such as every profile link or every canvas would also match feed
# content and can turn an anonymous page into a false positive.
LOGIN_ENTRY_SELECTORS = (
    "button:has-text('登录')",
    "[role='button']:has-text('登录')",
    "a:has-text('登录')",
    ".login-btn",
    "[class*='login-btn']",
)
LOGIN_ENTRY_TEXTS = ("登录", "登录/注册", "登录或注册")
LOGIN_SURFACE_TRIGGER_SELECTORS = (
    "#search-input[placeholder*='登录']",
    "input.search-input[placeholder*='登录']",
)
QR_LOGIN_ENTRY_SELECTORS = (
    "button:has-text('二维码登录')",
    "[role='tab']:has-text('二维码登录')",
    "button:has-text('扫码登录')",
    "[role='tab']:has-text('扫码登录')",
)
QR_LOGIN_ENTRY_TEXTS = ("二维码登录", "扫码登录")
ANONYMOUS_SELECTORS = (
    "#search-input[placeholder*='登录']",
    "input[placeholder*='登录探索更多内容']",
)
QR_CODE_SELECTORS = (
    ".login-container .qrcode-img",
    ".login-container [class*='qrcode'] img",
    ".login-container [class*='qrcode'] canvas",
    "[role='dialog'] [class*='qrcode'] img",
    "[role='dialog'] [class*='qrcode'] canvas",
    "[class*='login'] img[class*='qr']",
    "[class*='login'] canvas[class*='qr']",
)
QR_CODE_SELECTOR = ", ".join(QR_CODE_SELECTORS)

LOGIN_STATE_SCRIPT = """() => {
    const unwrap = (value) => {
        if (value && typeof value === "object") {
            if (Object.prototype.hasOwnProperty.call(value, "value")) return value.value;
            if (Object.prototype.hasOwnProperty.call(value, "_value")) return value._value;
        }
        return value;
    };
    const root = window.__INITIAL_STATE__;
    const user = unwrap(root && root.user);
    if (!user || typeof user !== "object") return null;
    for (const key of ["loggedIn", "isLoggedIn", "isLogin", "loginStatus"]) {
        const value = unwrap(user[key]);
        if (typeof value === "boolean") return value;
        if (value === 0 || value === 1) return Boolean(value);
        if (value === "true" || value === "false") return value === "true";
    }
    const info = unwrap(user.userInfo || user.user || user.profile);
    if (info && typeof info === "object") {
        for (const key of ["userId", "user_id", "redId", "red_id"]) {
            if (typeof info[key] === "string" && info[key].length > 0) return true;
        }
    }
    return null;
}"""

# This script deliberately returns structure and booleans, never full text,
# attribute values, image sources, canvas pixels, or serialized HTML.
LOGIN_DIAGNOSTICS_SCRIPT = """() => {
    const result = {
        ready_state: document.readyState,
        element_count: 0,
        open_shadow_root_count: 0,
        custom_element_count: 0,
        ordinary_login_candidate_count: 0,
        shadow_login_candidate_count: 0,
        qr_candidate_count: 0,
        pseudo_login_rule_count: 0,
        inaccessible_stylesheet_count: 0,
        body_has_login_text: false,
        risk_signals: {
            security_limit: false,
            ip_risk: false,
            verification: false
        },
        candidates: []
    };
    const bodyText = document.body && document.body.innerText
        ? document.body.innerText : "";
    result.body_has_login_text = bodyText.includes("登录");
    result.risk_signals.security_limit = bodyText.includes("安全限制");
    result.risk_signals.ip_risk = bodyText.includes("IP存在风险")
        || bodyText.includes("IP 存在风险");
    result.risk_signals.verification = bodyText.includes("安全验证")
        || bodyText.includes("验证码");
    const cleanToken = (value) => String(value || "")
        .replace(/[^a-zA-Z0-9_-]/g, "")
        .slice(0, 40);
    const classTokens = (element) => Array.from(element.classList || [])
        .map(cleanToken).filter(Boolean).slice(0, 4);
    const pseudoFlag = (element, pseudo) => {
        const content = getComputedStyle(element, pseudo).content || "";
        return content !== "none" && content !== "normal" && content !== '""';
    };
    const pseudoHasLogin = (element, pseudo) => {
        const content = getComputedStyle(element, pseudo).content || "";
        return content.includes("登录");
    };
    const scanRules = (rules) => {
        for (const rule of Array.from(rules || [])) {
            if (rule.cssRules) scanRules(rule.cssRules);
            const selector = String(rule.selectorText || "");
            const content = String(rule.style && rule.style.content || "");
            if ((selector.includes("::before") || selector.includes("::after"))
                    && content.includes("登录")) {
                result.pseudo_login_rule_count += 1;
            }
        }
    };
    for (const sheet of Array.from(document.styleSheets || [])) {
        try {
            scanRules(sheet.cssRules);
        } catch (_) {
            result.inaccessible_stylesheet_count += 1;
        }
    }
    const visible = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden"
            && Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
    };
    const textSignal = (element) => {
        const rawText = String(element.innerText || element.textContent || "")
            .replace(/\\s+/g, "");
        const role = String(element.getAttribute("role") || "");
        const interactive = ["button", "a", "input"].includes(element.localName)
            || ["button", "tab", "link"].includes(role);
        const text = rawText.length <= 40 || interactive ? rawText.slice(0, 80) : "";
        const aria = String(element.getAttribute("aria-label") || "");
        const title = String(element.getAttribute("title") || "");
        const placeholder = String(element.getAttribute("placeholder") || "");
        const combined = `${text}|${aria}|${title}|${placeholder}`;
        if (text === "登录" || text === "登录/注册" || text === "登录或注册") {
            return "exact_login";
        }
        if (combined.includes("二维码登录")) return "qr_login";
        if (combined.includes("扫码登录")) return "scan_login";
        if (combined.includes("登录")) return "contains_login";
        return "none";
    };
    const qrSignal = (element) => {
        const structural = [
            element.tagName,
            element.id,
            element.className,
            element.getAttribute("role"),
            element.getAttribute("aria-label"),
            element.getAttribute("title")
        ].join(" ").toLowerCase();
        return structural.includes("qrcode") || structural.includes("qr-code")
            || structural.includes("二维码");
    };
    const visitRoot = (root, inShadow, shadowHostTag) => {
        const elements = Array.from(root.querySelectorAll("*"));
        for (const element of elements.slice(0, 5000)) {
            result.element_count += 1;
            if (String(element.localName || "").includes("-")) {
                result.custom_element_count += 1;
            }
            const signal = textSignal(element);
            const structuralLogin = [
                element.id,
                element.className,
                element.getAttribute("role"),
                element.getAttribute("aria-label"),
                element.getAttribute("title")
            ].join(" ").toLowerCase().includes("login");
            const inspectPseudo = signal !== "none" || structuralLogin;
            const beforeHasLogin = inspectPseudo
                ? pseudoHasLogin(element, "::before") : false;
            const afterHasLogin = inspectPseudo
                ? pseudoHasLogin(element, "::after") : false;
            const isLoginCandidate = signal !== "none" || beforeHasLogin || afterHasLogin;
            const isQrCandidate = qrSignal(element);
            if (isLoginCandidate) {
                if (inShadow) result.shadow_login_candidate_count += 1;
                else result.ordinary_login_candidate_count += 1;
            }
            if (isQrCandidate) result.qr_candidate_count += 1;
            if ((isLoginCandidate || isQrCandidate) && result.candidates.length < 24) {
                const role = cleanToken(element.getAttribute("role"));
                result.candidates.push({
                    tag: cleanToken(String(element.localName || "unknown")),
                    role: role || null,
                    class_tokens: classTokens(element),
                    text_signal: signal,
                    visible: visible(element),
                    in_shadow_dom: inShadow,
                    shadow_host_tag: shadowHostTag || null,
                    pseudo_before_present: inspectPseudo
                        ? pseudoFlag(element, "::before") : false,
                    pseudo_after_present: inspectPseudo
                        ? pseudoFlag(element, "::after") : false,
                    pseudo_before_has_login: beforeHasLogin,
                    pseudo_after_has_login: afterHasLogin,
                    qr_signal: isQrCandidate
                });
            }
            if (element.shadowRoot) {
                result.open_shadow_root_count += 1;
                visitRoot(element.shadowRoot, true, cleanToken(element.localName));
            }
        }
    };
    visitRoot(document, false, null);
    return result;
}"""

DEBUG_QR_MASK_SELECTORS = (
    "[role='dialog'] img",
    "[role='dialog'] canvas",
    "[class*='login'] img",
    "[class*='login'] canvas",
    "[class*='qrcode']",
    "[class*='qr-code']",
)
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
    return (
        f"{SEARCH_URL}?{urlencode({'keyword': keyword, 'source': 'web_explore_feed'})}"
    )


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
