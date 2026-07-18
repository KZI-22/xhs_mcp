from pathlib import Path

import pytest

from xhs_read_mcp.browser.auth_store import AuthStateStore
from xhs_read_mcp.browser.manager import BrowserManager
from xhs_read_mcp.config import AppConfig


@pytest.mark.browser
async def test_real_chromium_page_lifecycle_and_state_save(tmp_path: Path) -> None:
    config = AppConfig(
        _env_file=None,
        browser_headless=True,
        auth_state_path=tmp_path / "state.json",
    )
    store = AuthStateStore(config.auth_state_path)
    manager = BrowserManager(config, store)
    await manager.start()
    try:
        async with manager.page() as page:
            await page.set_content("<main><h1>ready</h1></main>")
            assert await page.locator("h1").inner_text() == "ready"
        assert manager.active_page_count == 0
        assert await manager.persist_state()
        assert config.auth_state_path.exists()
    finally:
        await manager.close()


@pytest.mark.browser
async def test_real_chromium_rebuilds_once_after_disconnect(tmp_path: Path) -> None:
    config = AppConfig(
        _env_file=None,
        browser_headless=True,
        auth_state_path=tmp_path / "state.json",
    )
    manager = BrowserManager(config, AuthStateStore(config.auth_state_path))
    await manager.start()
    try:
        original_browser = manager._browser
        assert original_browser is not None
        await original_browser.close()

        async with manager.page() as page:
            await page.set_content("<p>rebuilt</p>")
            assert await page.locator("p").inner_text() == "rebuilt"
        assert manager._browser is not original_browser
    finally:
        await manager.close(save_state=False)

