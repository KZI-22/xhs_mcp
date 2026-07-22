import pytest

from xhs_read_mcp.actions.common import raise_for_page_problem
from xhs_read_mcp.errors import ErrorCode, XhsError


class RiskRedirectPage:
    url = "https://www.xiaohongshu.com/website-login/error?redirectPath=%2Fexplore"


class BodyLocator:
    async def inner_text(self, *, timeout: int) -> str:
        return "手机号 验证码 获取验证码 登录"


class NormalVerificationCodePage:
    url = "https://www.xiaohongshu.com/explore"

    def locator(self, selector: str) -> BodyLocator:
        assert selector == "body"
        return BodyLocator()


async def test_login_error_redirect_is_risk_control() -> None:
    with pytest.raises(XhsError) as captured:
        await raise_for_page_problem(RiskRedirectPage(), check_login_expired=False)

    assert captured.value.code is ErrorCode.RISK_CONTROL
    assert captured.value.details == {"page_path": "/website-login/error"}


async def test_normal_verification_code_text_is_not_risk_control() -> None:
    await raise_for_page_problem(
        NormalVerificationCodePage(),
        check_login_expired=False,
    )
