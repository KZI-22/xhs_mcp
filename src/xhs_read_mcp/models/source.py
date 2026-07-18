"""Permissive models for Xiaohongshu's browser-side JSON state."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


CountValue = str | int | float


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class SourceUser(SourceModel):
    user_id: str = Field(default="", validation_alias=AliasChoices("userId", "user_id"))
    nickname: str = ""
    nick_name: str = Field(default="", validation_alias=AliasChoices("nickName", "nick_name"))
    avatar: str = ""


class SourceInteractInfo(SourceModel):
    liked: bool = False
    liked_count: CountValue = Field(
        default="0", validation_alias=AliasChoices("likedCount", "liked_count")
    )
    shared_count: CountValue = Field(
        default="0", validation_alias=AliasChoices("sharedCount", "shared_count")
    )
    comment_count: CountValue = Field(
        default="0", validation_alias=AliasChoices("commentCount", "comment_count")
    )
    collected_count: CountValue = Field(
        default="0", validation_alias=AliasChoices("collectedCount", "collected_count")
    )
    collected: bool = False


class SourceImageInfo(SourceModel):
    image_scene: str = Field(
        default="", validation_alias=AliasChoices("imageScene", "image_scene")
    )
    url: str = ""


class SourceCover(SourceModel):
    width: int = 0
    height: int = 0
    url: str = ""
    file_id: str = Field(default="", validation_alias=AliasChoices("fileId", "file_id"))
    url_pre: str = Field(default="", validation_alias=AliasChoices("urlPre", "url_pre"))
    url_default: str = Field(
        default="", validation_alias=AliasChoices("urlDefault", "url_default")
    )
    info_list: list[SourceImageInfo] = Field(
        default_factory=list, validation_alias=AliasChoices("infoList", "info_list")
    )


class SourceVideoCapability(SourceModel):
    duration: int = 0


class SourceVideo(SourceModel):
    capa: SourceVideoCapability = Field(default_factory=SourceVideoCapability)


class SourceNoteCard(SourceModel):
    type: str = ""
    display_title: str = Field(
        default="", validation_alias=AliasChoices("displayTitle", "display_title")
    )
    user: SourceUser = Field(default_factory=SourceUser)
    interact_info: SourceInteractInfo = Field(
        default_factory=SourceInteractInfo,
        validation_alias=AliasChoices("interactInfo", "interact_info"),
    )
    cover: SourceCover = Field(default_factory=SourceCover)
    video: SourceVideo | None = None


class SourceFeed(SourceModel):
    xsec_token: str = Field(
        default="", validation_alias=AliasChoices("xsecToken", "xsec_token")
    )
    id: str = ""
    model_type: str = Field(
        default="", validation_alias=AliasChoices("modelType", "model_type")
    )
    note_card: SourceNoteCard = Field(
        default_factory=SourceNoteCard,
        validation_alias=AliasChoices("noteCard", "note_card"),
    )
    index: int = 0


class SourceDetailImage(SourceModel):
    width: int = 0
    height: int = 0
    url_default: str = Field(
        default="", validation_alias=AliasChoices("urlDefault", "url_default")
    )
    url_pre: str = Field(default="", validation_alias=AliasChoices("urlPre", "url_pre"))
    live_photo: bool = Field(
        default=False, validation_alias=AliasChoices("livePhoto", "live_photo")
    )


class SourceFeedDetail(SourceModel):
    note_id: str = Field(default="", validation_alias=AliasChoices("noteId", "note_id"))
    xsec_token: str = Field(
        default="", validation_alias=AliasChoices("xsecToken", "xsec_token")
    )
    title: str = ""
    desc: str = ""
    type: str = ""
    time: int = 0
    ip_location: str = Field(
        default="", validation_alias=AliasChoices("ipLocation", "ip_location")
    )
    user: SourceUser = Field(default_factory=SourceUser)
    interact_info: SourceInteractInfo = Field(
        default_factory=SourceInteractInfo,
        validation_alias=AliasChoices("interactInfo", "interact_info"),
    )
    image_list: list[SourceDetailImage] = Field(
        default_factory=list, validation_alias=AliasChoices("imageList", "image_list")
    )


class SourceComment(SourceModel):
    id: str = ""
    note_id: str = Field(default="", validation_alias=AliasChoices("noteId", "note_id"))
    content: str = ""
    like_count: CountValue = Field(
        default="0", validation_alias=AliasChoices("likeCount", "like_count")
    )
    create_time: int = Field(
        default=0, validation_alias=AliasChoices("createTime", "create_time")
    )
    ip_location: str = Field(
        default="", validation_alias=AliasChoices("ipLocation", "ip_location")
    )
    liked: bool = False
    user_info: SourceUser = Field(
        default_factory=SourceUser,
        validation_alias=AliasChoices("userInfo", "user_info"),
    )
    sub_comment_count: CountValue = Field(
        default="0",
        validation_alias=AliasChoices("subCommentCount", "sub_comment_count"),
    )
    sub_comments: list[SourceComment] = Field(
        default_factory=list,
        validation_alias=AliasChoices("subComments", "sub_comments"),
    )
    show_tags: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("showTags", "show_tags")
    )


class SourceCommentList(SourceModel):
    items: list[SourceComment] = Field(
        default_factory=list, validation_alias=AliasChoices("list", "items")
    )
    cursor: str = ""
    has_more: bool = Field(
        default=False, validation_alias=AliasChoices("hasMore", "has_more")
    )


class SourceFeedDetailEntry(SourceModel):
    note: SourceFeedDetail = Field(default_factory=SourceFeedDetail)
    comments: SourceCommentList = Field(default_factory=SourceCommentList)


def unwrap_reactive_value(value: Any) -> Any:
    """Unwrap common Vue/Pinia reactive JSON wrappers."""

    if isinstance(value, dict):
        if "value" in value:
            return value["value"]
        if "_value" in value:
            return value["_value"]
    return value
