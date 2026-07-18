import asyncio
import json
from pathlib import Path

import pytest

from xhs_read_mcp.browser.auth_store import AuthStateStore
from xhs_read_mcp.errors import ErrorCode, XhsError


def sample_state(cookie_value: str = "value") -> dict:
    return {
        "cookies": [
            {
                "name": "session",
                "value": cookie_value,
                "domain": ".xiaohongshu.com",
                "path": "/",
            }
        ],
        "origins": [],
    }


async def test_save_load_and_idempotent_delete(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    store = AuthStateStore(path)

    assert await store.save(sample_state())
    assert not await store.save(sample_state())
    assert await store.load() == sample_state()
    assert await store.delete()
    assert not await store.delete()
    assert await store.load() is None


async def test_corrupt_state_is_not_silently_deleted(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    store = AuthStateStore(path)

    with pytest.raises(XhsError) as captured:
        await store.load()

    assert captured.value.code is ErrorCode.AUTH_STATE_ERROR
    assert path.exists()


async def test_concurrent_saves_leave_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = AuthStateStore(path)

    await asyncio.gather(*(store.save(sample_state(str(index))) for index in range(10)))

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cookies"][0]["value"] in {str(index) for index in range(10)}
    assert not list(path.parent.glob("*.tmp"))


async def test_invalid_state_is_rejected_before_writing(tmp_path: Path) -> None:
    store = AuthStateStore(tmp_path / "state.json")

    with pytest.raises(XhsError) as captured:
        await store.save({"cookies": "not-a-list"})

    assert captured.value.code is ErrorCode.AUTH_STATE_ERROR

