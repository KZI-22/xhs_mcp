"""Stable, transport-independent public models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from xhs_read_mcp.models.source import (
    SourceComment,
    SourceCover,
    SourceFeed,
    SourceFeedDetail,
    SourceInteractInfo,
    SourceUser,
)


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SortBy(StrEnum):
    RELEVANCE = "relevance"
    LATEST = "latest"
    MOST_LIKED = "most_liked"
    MOST_COMMENTED = "most_commented"
    MOST_COLLECTED = "most_collected"


class NoteTypeFilter(StrEnum):
    ANY = "any"
    VIDEO = "video"
    IMAGE = "image"


class PublishTime(StrEnum):
    ANY = "any"
    DAY = "day"
    WEEK = "week"
    HALF_YEAR = "half_year"


class SearchScope(StrEnum):
    ANY = "any"
    VIEWED = "viewed"
    UNVIEWED = "unviewed"
    FOLLOWING = "following"


class LocationFilter(StrEnum):
    ANY = "any"
    SAME_CITY = "same_city"
    NEARBY = "nearby"


class NoteType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    UNKNOWN = "unknown"


class CommentMode(StrEnum):
    NONE = "none"
    INITIAL = "initial"
    LOAD = "load"


class ScrollSpeed(StrEnum):
    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


class LoginSessionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


class CommentStopReason(StrEnum):
    DISABLED = "disabled"
    INITIAL_ONLY = "initial_only"
    END_REACHED = "end_reached"
    NO_COMMENTS = "no_comments"
    MAX_PARENT_COMMENTS = "max_parent_comments"
    STALLED = "stalled"
    TIMEOUT = "timeout"
    LOAD_ERROR = "load_error"
    CANCELLED = "cancelled"


class WarningInfo(PublicModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolMeta(PublicModel):
    duration_ms: int = Field(default=0, ge=0)


class UserInfo(PublicModel):
    user_id: str
    nickname: str
    avatar_url: str

    @classmethod
    def from_source(cls, source: SourceUser) -> "UserInfo":
        return cls(
            user_id=source.user_id,
            nickname=source.nickname or source.nick_name,
            avatar_url=source.avatar,
        )


class InteractionInfo(PublicModel):
    liked: bool
    liked_count: str
    shared_count: str
    comment_count: str
    collected_count: str
    collected: bool

    @classmethod
    def from_source(cls, source: SourceInteractInfo) -> "InteractionInfo":
        return cls(
            liked=source.liked,
            liked_count=str(source.liked_count),
            shared_count=str(source.shared_count),
            comment_count=str(source.comment_count),
            collected_count=str(source.collected_count),
            collected=source.collected,
        )


class ImageVariant(PublicModel):
    scene: str
    url: str


class CoverInfo(PublicModel):
    width: int
    height: int
    primary_url: str
    preview_url: str
    default_url: str
    file_id: str
    variants: list[ImageVariant]

    @classmethod
    def from_source(cls, source: SourceCover) -> "CoverInfo":
        return cls(
            width=source.width,
            height=source.height,
            primary_url=source.url,
            preview_url=source.url_pre,
            default_url=source.url_default,
            file_id=source.file_id,
            variants=[ImageVariant(scene=item.image_scene, url=item.url) for item in source.info_list],
        )


class VideoInfo(PublicModel):
    duration: int


def normalize_note_type(value: str) -> NoteType:
    normalized = value.strip().lower()
    if normalized in {"normal", "image", "images", "图文"}:
        return NoteType.IMAGE
    if normalized in {"video", "视频"}:
        return NoteType.VIDEO
    return NoteType.UNKNOWN


def normalize_timestamp(value: int, timezone: str) -> tuple[int, str]:
    """Return a millisecond timestamp and an ISO-8601 representation."""

    milliseconds = value if abs(value) >= 100_000_000_000 else value * 1000
    instant = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
    return milliseconds, instant.astimezone(ZoneInfo(timezone)).isoformat(timespec="seconds")


class SearchRequest(PublicModel):
    keyword: str = Field(min_length=1, max_length=200)
    sort_by: SortBy | None = None
    note_type: NoteTypeFilter | None = None
    publish_time: PublishTime | None = None
    search_scope: SearchScope | None = None
    location: LocationFilter | None = None

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("keyword cannot be empty")
        return value


class SearchNote(PublicModel):
    note_id: str
    xsec_token: str
    detail_available: bool
    model_type: str
    index: int
    note_type: NoteType
    title: str
    author: UserInfo
    interactions: InteractionInfo
    cover: CoverInfo
    video: VideoInfo | None = None

    @classmethod
    def from_source(cls, source: SourceFeed) -> "SearchNote":
        video = (
            VideoInfo(duration=source.note_card.video.capa.duration)
            if source.note_card.video
            else None
        )
        return cls(
            note_id=source.id,
            xsec_token=source.xsec_token,
            detail_available=bool(source.id and source.xsec_token),
            model_type=source.model_type,
            index=source.index,
            note_type=normalize_note_type(source.note_card.type),
            title=source.note_card.display_title,
            author=UserInfo.from_source(source.note_card.user),
            interactions=InteractionInfo.from_source(source.note_card.interact_info),
            cover=CoverInfo.from_source(source.note_card.cover),
            video=video,
        )


class AppliedSearchFilters(PublicModel):
    sort_by: SortBy | None = None
    note_type: NoteTypeFilter | None = None
    publish_time: PublishTime | None = None
    search_scope: SearchScope | None = None
    location: LocationFilter | None = None


class SearchMeta(PublicModel):
    source: Literal["initial_state"] = "initial_state"
    scope: Literal["initial_results"] = "initial_results"
    raw_count: int = Field(default=0, ge=0)
    skipped_non_note_items: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)


class SearchResult(PublicModel):
    keyword: str
    applied_filters: AppliedSearchFilters
    count: int = Field(ge=0)
    items: list[SearchNote]
    warnings: list[WarningInfo] = Field(default_factory=list)
    meta: SearchMeta


class DetailImageInfo(PublicModel):
    width: int
    height: int
    default_url: str
    preview_url: str
    live_photo: bool


class NoteDetail(PublicModel):
    note_id: str
    xsec_token: str
    title: str
    description: str
    note_type: NoteType
    published_at_ms: int
    published_at: str
    ip_location: str
    author: UserInfo
    interactions: InteractionInfo
    images: list[DetailImageInfo]

    @classmethod
    def from_source(cls, source: SourceFeedDetail, timezone: str) -> "NoteDetail":
        published_at_ms, published_at = normalize_timestamp(source.time, timezone)
        return cls(
            note_id=source.note_id,
            xsec_token=source.xsec_token,
            title=source.title,
            description=source.desc,
            note_type=normalize_note_type(source.type),
            published_at_ms=published_at_ms,
            published_at=published_at,
            ip_location=source.ip_location,
            author=UserInfo.from_source(source.user),
            interactions=InteractionInfo.from_source(source.interact_info),
            images=[
                DetailImageInfo(
                    width=image.width,
                    height=image.height,
                    default_url=image.url_default,
                    preview_url=image.url_pre,
                    live_photo=image.live_photo,
                )
                for image in source.image_list
            ],
        )


class Comment(PublicModel):
    comment_id: str
    note_id: str
    content: str
    like_count: str
    created_at_ms: int
    created_at: str
    ip_location: str
    liked: bool
    author: UserInfo
    sub_comment_count: str
    sub_comments: list[Comment]
    tags: list[str]

    @classmethod
    def from_source(cls, source: SourceComment, timezone: str) -> "Comment":
        created_at_ms, created_at = normalize_timestamp(source.create_time, timezone)
        return cls(
            comment_id=source.id,
            note_id=source.note_id,
            content=source.content,
            like_count=str(source.like_count),
            created_at_ms=created_at_ms,
            created_at=created_at,
            ip_location=source.ip_location,
            liked=source.liked,
            author=UserInfo.from_source(source.user_info),
            sub_comment_count=str(source.sub_comment_count),
            sub_comments=[cls.from_source(item, timezone) for item in source.sub_comments],
            tags=list(source.show_tags),
        )


class CommentLoadOptions(PublicModel):
    max_parent_comments: int = Field(default=100, ge=0, le=10_000)
    expand_replies: bool = False
    max_reply_count_to_expand: int | None = Field(default=10, ge=0)
    scroll_speed: ScrollSpeed = ScrollSpeed.NORMAL
    timeout_seconds: float = Field(default=300.0, gt=0, le=600)


class DetailRequest(PublicModel):
    note_id: str = Field(min_length=1, max_length=200)
    xsec_token: str = Field(min_length=1, max_length=4096, repr=False)
    comment_mode: CommentMode = CommentMode.INITIAL
    comment_options: CommentLoadOptions | None = None

    @field_validator("note_id", "xsec_token")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value cannot be empty")
        return value

    @model_validator(mode="after")
    def validate_comment_options(self) -> "DetailRequest":
        if self.comment_mode is not CommentMode.LOAD and self.comment_options is not None:
            raise ValueError("comment_options can only be used when comment_mode='load'")
        if self.comment_mode is CommentMode.LOAD and self.comment_options is None:
            self.comment_options = CommentLoadOptions()
        return self


class CommentsResult(PublicModel):
    mode: CommentMode
    items: list[Comment]
    cursor: str
    has_more: bool
    parent_comment_count: int = Field(ge=0)
    total_returned_count: int = Field(ge=0)
    partial: bool
    stop_reason: CommentStopReason
    warnings: list[WarningInfo] = Field(default_factory=list)


class NoteDetailResult(PublicModel):
    note_id: str
    detail: NoteDetail
    comments: CommentsResult
    warnings: list[WarningInfo] = Field(default_factory=list)
    meta: ToolMeta = Field(default_factory=ToolMeta)


class LoginStatusResult(PublicModel):
    is_logged_in: bool
    checked_at: str


class LoginSessionResult(PublicModel):
    login_id: str
    status: LoginSessionStatus
    created_at: str
    expires_at: str
    is_logged_in: bool = False
    qr_mime_type: str | None = None
    message: str = ""


class LogoutResult(PublicModel):
    cleared: bool
    message: str


def count_comments(items: list[Comment]) -> int:
    return sum(1 + count_comments(item.sub_comments) for item in items)


Comment.model_rebuild()

