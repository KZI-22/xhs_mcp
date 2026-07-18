from pydantic import ValidationError

from xhs_read_mcp.models.public import (
    Comment,
    CommentLoadOptions,
    CommentMode,
    DetailRequest,
    NoteType,
    SearchNote,
    SearchRequest,
    UserInfo,
    count_comments,
    normalize_timestamp,
)
from xhs_read_mcp.models.source import SourceComment, SourceFeed, SourceUser, unwrap_reactive_value


def test_user_accepts_both_nickname_spellings() -> None:
    source = SourceUser.model_validate(
        {"userId": "u1", "nickName": "Alice", "avatar": "https://avatar"}
    )

    assert UserInfo.from_source(source).nickname == "Alice"


def test_search_note_normalizes_source_fields_without_losing_count_text() -> None:
    source = SourceFeed.model_validate(
        {
            "id": "note-1",
            "xsecToken": "token-1",
            "modelType": "note",
            "index": 2,
            "noteCard": {
                "type": "normal",
                "displayTitle": "Title",
                "user": {"userId": "u1", "nickname": "Alice"},
                "interactInfo": {"likedCount": "1.2万", "commentCount": 42},
                "cover": {"width": 1080, "height": 1440, "url": "https://image"},
            },
        }
    )

    note = SearchNote.from_source(source)

    assert note.note_type is NoteType.IMAGE
    assert note.detail_available
    assert note.interactions.liked_count == "1.2万"
    assert note.interactions.comment_count == "42"


def test_timestamp_is_normalized_to_milliseconds_and_shanghai_iso() -> None:
    milliseconds, value = normalize_timestamp(1_700_000_000, "Asia/Shanghai")

    assert milliseconds == 1_700_000_000_000
    assert value.endswith("+08:00")


def test_recursive_comments_are_preserved_and_counted() -> None:
    source = SourceComment.model_validate(
        {
            "id": "parent",
            "createTime": 1_700_000_000_000,
            "userInfo": {"userId": "u1", "nickname": "Parent"},
            "subComments": [
                {
                    "id": "child",
                    "createTime": 1_700_000_001_000,
                    "userInfo": {"userId": "u2", "nickName": "Child"},
                }
            ],
        }
    )

    comment = Comment.from_source(source, "Asia/Shanghai")

    assert comment.sub_comments[0].author.nickname == "Child"
    assert count_comments([comment]) == 2


def test_search_keyword_is_trimmed_and_empty_keyword_is_rejected() -> None:
    assert SearchRequest(keyword="  杭州  ").keyword == "杭州"

    try:
        SearchRequest(keyword="   ")
    except ValidationError:
        pass
    else:
        raise AssertionError("empty keyword should fail validation")


def test_load_mode_populates_default_comment_options() -> None:
    request = DetailRequest(note_id="n1", xsec_token="t1", comment_mode=CommentMode.LOAD)

    assert request.comment_options == CommentLoadOptions()
    assert request.comment_options.max_parent_comments == 100


def test_comment_options_are_rejected_outside_load_mode() -> None:
    try:
        DetailRequest(
            note_id="n1",
            xsec_token="t1",
            comment_mode=CommentMode.INITIAL,
            comment_options=CommentLoadOptions(),
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("unused comment options should fail validation")


def test_reactive_values_are_unwrapped() -> None:
    assert unwrap_reactive_value({"value": [1]}) == [1]
    assert unwrap_reactive_value({"_value": [2]}) == [2]
    assert unwrap_reactive_value([3]) == [3]

