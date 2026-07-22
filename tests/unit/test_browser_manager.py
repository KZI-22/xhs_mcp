import asyncio
from pathlib import Path

from xhs_read_mcp.browser.auth_store import AuthStateStore
from xhs_read_mcp.browser.manager import (
    GOOGLE_CHROME_CHANNEL,
    BrowserManager,
    build_proxy_settings,
)
from xhs_read_mcp.config import AppConfig


class FakePage:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, state: dict | None = None) -> None:
        self.state = state or {"cookies": [], "origins": []}
        self.pages: list[FakePage] = []
        self.closed = False

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    async def storage_state(self, *, indexed_db: bool = False) -> dict:
        assert indexed_db
        return self.state

    async def close(self) -> None:
        self.closed = True
        for page in self.pages:
            await page.close()


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []
        self.connected = True
        self.handlers = {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def is_connected(self) -> bool:
        return self.connected

    async def new_context(self, **kwargs) -> FakeContext:
        context = FakeContext(kwargs.get("storage_state"))
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.connected = False


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.launch_options = None

    async def launch(self, **kwargs) -> FakeBrowser:
        self.launch_options = kwargs
        self.browser.connected = True
        return self.browser


class FakePlaywright:
    def __init__(self) -> None:
        self.browser = FakeBrowser()
        self.chromium = FakeChromium(self.browser)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakePlaywrightManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


def make_manager(tmp_path: Path, *, max_operations: int = 2):
    fake = FakePlaywright()
    config = AppConfig(
        _env_file=None,
        auth_state_path=tmp_path / "state.json",
        max_concurrent_operations=max_operations,
    )
    store = AuthStateStore(config.auth_state_path)
    manager = BrowserManager(
        config,
        store,
        playwright_factory=lambda: FakePlaywrightManager(fake),
    )
    return manager, store, fake


async def test_page_lease_closes_page_and_persists_state(tmp_path: Path) -> None:
    manager, store, fake = make_manager(tmp_path)
    await manager.start()

    async with manager.page() as page:
        assert manager.active_page_count == 1
        fake.browser.contexts[0].state = {
            "cookies": [{"name": "session", "value": "value"}],
            "origins": [],
        }

    assert page.closed
    assert manager.active_page_count == 0
    assert await manager.persist_state()
    assert (await store.load())["cookies"][0]["name"] == "session"
    await manager.close()


async def test_default_launch_uses_google_chrome_channel(tmp_path: Path) -> None:
    manager, _, fake = make_manager(tmp_path)

    await manager.start()

    assert fake.chromium.launch_options == {
        "headless": False,
        "channel": GOOGLE_CHROME_CHANNEL,
    }
    await manager.close(save_state=False)


async def test_bundled_chromium_launch_omits_branded_channel(tmp_path: Path) -> None:
    fake = FakePlaywright()
    config = AppConfig(
        _env_file=None,
        auth_state_path=tmp_path / "state.json",
        browser_channel="chromium",
        browser_headless=True,
    )
    manager = BrowserManager(
        config,
        AuthStateStore(config.auth_state_path),
        playwright_factory=lambda: FakePlaywrightManager(fake),
    )

    await manager.start()

    assert fake.chromium.launch_options == {"headless": True}
    await manager.close(save_state=False)


async def test_custom_google_chrome_path_replaces_channel(tmp_path: Path) -> None:
    fake = FakePlaywright()
    chrome_path = tmp_path / "chrome.exe"
    config = AppConfig(
        _env_file=None,
        auth_state_path=tmp_path / "state.json",
        browser_path=chrome_path,
    )
    manager = BrowserManager(
        config,
        AuthStateStore(config.auth_state_path),
        playwright_factory=lambda: FakePlaywrightManager(fake),
    )

    await manager.start()

    assert fake.chromium.launch_options == {
        "headless": False,
        "executable_path": str(chrome_path),
    }
    await manager.close(save_state=False)


async def test_clear_auth_state_replaces_context_and_deletes_file(tmp_path: Path) -> None:
    manager, store, fake = make_manager(tmp_path)
    await manager.start()
    fake.browser.contexts[0].state = {
        "cookies": [{"name": "session", "value": "value"}],
        "origins": [],
    }
    await manager.persist_state()

    assert await manager.clear_auth_state()
    assert len(fake.browser.contexts) == 2
    assert await store.load() is None
    await manager.close()
    assert await store.load() is None



async def test_page_lease_enforces_concurrency_limit(tmp_path: Path) -> None:
    manager, _, _ = make_manager(tmp_path, max_operations=2)
    await manager.start()
    entered = 0
    maximum = 0
    release = asyncio.Event()

    async def worker() -> None:
        nonlocal entered, maximum
        async with manager.page():
            entered += 1
            maximum = max(maximum, entered)
            await release.wait()
            entered -= 1

    tasks = [asyncio.create_task(worker()) for _ in range(3)]
    for _ in range(20):
        if entered == 2:
            break
        await asyncio.sleep(0)
    assert entered == 2
    assert maximum == 2
    release.set()
    await asyncio.gather(*tasks)
    await manager.close(save_state=False)


async def test_cancelled_page_lease_still_closes_page(tmp_path: Path) -> None:
    manager, _, _ = make_manager(tmp_path)
    await manager.start()
    entered = asyncio.Event()

    async def worker() -> None:
        async with manager.page():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await entered.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert manager.active_page_count == 0
    assert manager._browser.contexts[0].pages[0].closed
    await manager.close(save_state=False)


def test_proxy_credentials_are_split_from_server_url() -> None:
    settings = build_proxy_settings("http://user:p%40ss@example.com:8080")

    assert settings == {
        "server": "http://example.com:8080",
        "username": "user",
        "password": "p@ss",
    }
