import pytest

from xhs_read_mcp.actions.common import raise_for_page_problem
from xhs_read_mcp.errors import ErrorCode, XhsError


class RiskRedirectPage:
    url = "https://www.xiaohongshu.com/website-login/error?redirectPath=%2Fexplore"


async def test_login_error_redirect_is_risk_control() -> None:
    with pytest.raises(XhsError) as captured:
        await raise_for_page_problem(RiskRedirectPage(), check_login_expired=False)

    assert captured.value.code is ErrorCode.RISK_CONTROL
    assert captured.value.details == {"page_path": "/website-login/error"}
