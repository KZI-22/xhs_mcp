import pytest

from xhs_read_mcp.actions.comments import CommentLoadOutcome
from xhs_read_mcp.actions.feed_detail import build_detail_result
from xhs_read_mcp.errors import ErrorCode, XhsError
from xhs_read_mcp.models.public import (
    CommentLoadOptions,
    CommentMode,
    CommentStopReason,
    DetailRequest,
)


def detail_map(*, has_more: bool = True) -> dict:
    return {
        "n1": {
            "note": {
                "noteId": "n1",
                "xsecToken": "t1",
                "title": "Title",
                "desc": "Description",
                "type": "normal",
                "time": 1_700_000_000_000,
                "user": {"userId": "u1", "nickname": "Alice"},
                "interactInfo": {"likedCount": "10"},
                "imageList": [{"urlDefault": "https://image"}],
            },
            "comments": {
                "list": [
                    {
                        "id": "c1",
                        "noteId": "n1",
                        "content": "Comment",
                        "createTime": 1_700_000_000_000,
                        "userInfo": {"userId": "u2", "nickname": "Bob"},
                        "subComments": [
                            {
                                "id": "c2",
                                "createTime": 1_700_000_001_000,
                                "userInfo": {"userId": "u3", "nickname": "Carol"},
                            }
                        ],
                    }
                ],
                "cursor": "cursor",
                "hasMore": has_more,
            },
        }
    }


def test_initial_comments_are_marked_partial_when_has_more() -> None:
    result = build_detail_result(
        detail_map(),
        DetailRequest(note_id="n1", xsec_token="t1"),
        timezone="Asia/Shanghai",
    )

    assert result.detail.title == "Title"
    assert result.comments.parent_comment_count == 1
    assert result.comments.total_returned_count == 2
    assert result.comments.partial
    assert result.comments.stop_reason is CommentStopReason.INITIAL_ONLY


def test_none_mode_omits_comments_with_stable_shape() -> None:
    result = build_detail_result(
        detail_map(),
        DetailRequest(note_id="n1", xsec_token="t1", comment_mode=CommentMode.NONE),
        timezone="Asia/Shanghai",
    )

    assert result.comments.items == []
    assert result.comments.stop_reason is CommentStopReason.DISABLED
    assert not result.comments.partial


def test_load_outcome_is_reflected_in_result() -> None:
    request = DetailRequest(
        note_id="n1",
        xsec_token="t1",
        comment_mode=CommentMode.LOAD,
        comment_options=CommentLoadOptions(),
    )
    result = build_detail_result(
        detail_map(),
        request,
        timezone="Asia/Shanghai",
        load_outcome=CommentLoadOutcome(CommentStopReason.MAX_PARENT_COMMENTS, 100),
    )

    assert result.comments.partial
    assert result.comments.stop_reason is CommentStopReason.MAX_PARENT_COMMENTS


def test_missing_note_key_is_page_structure_error() -> None:
    with pytest.raises(XhsError) as captured:
        build_detail_result(
            {}, DetailRequest(note_id="n1", xsec_token="t1"), timezone="Asia/Shanghai"
        )

    assert captured.value.code is ErrorCode.PAGE_STRUCTURE_CHANGED

