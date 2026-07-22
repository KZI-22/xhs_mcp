import pytest
from playwright.async_api import async_playwright

from xhs_read_mcp.browser.page_contract import LOGIN_DIAGNOSTICS_SCRIPT


pytestmark = pytest.mark.browser


async def test_login_diagnostics_distinguish_dom_pseudo_and_shadow_candidates() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page()
        await page.set_content(
            """
            <style>.pseudo-login::before { content: "登录"; }</style>
            <button class="ordinary-login">登录</button>
            <div class="pseudo-login"></div>
            <login-shell></login-shell>
            <script>
                const host = document.querySelector("login-shell");
                host.attachShadow({mode: "open"}).innerHTML =
                    '<button class="shadow-login">登录</button>';
            </script>
            """
        )

        summary = await page.evaluate(LOGIN_DIAGNOSTICS_SCRIPT)

        assert summary["ordinary_login_candidate_count"] >= 2
        assert summary["shadow_login_candidate_count"] >= 1
        assert summary["open_shadow_root_count"] == 1
        assert summary["pseudo_login_rule_count"] == 1
        assert any(item["pseudo_before_has_login"] for item in summary["candidates"])
        assert any(item["in_shadow_dom"] for item in summary["candidates"])
        await browser.close()
