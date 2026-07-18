import os

import pytest

from xhs_read_mcp.config import AppConfig
from xhs_read_mcp.service import XhsReadService


pytestmark = pytest.mark.live_xhs


def require_live_opt_in() -> None:
    if os.environ.get("XHS_RUN_LIVE_TESTS") != "1":
        pytest.skip("set XHS_RUN_LIVE_TESTS=1 to access the real Xiaohongshu website")


async def test_real_login_page_can_be_classified() -> None:
    require_live_opt_in()
    service = XhsReadService(AppConfig())
    await service.start()
    try:
        status = await service.check_login()
        assert isinstance(status.is_logged_in, bool)
    finally:
        await service.close()

