from xhs_read_mcp.actions.comments import (
    MAX_STAGNANT_ATTEMPTS,
    CommentLoader,
    scroll_profile,
    should_skip_reply_button,
)
from xhs_read_mcp.models.public import (
    CommentLoadOptions,
    CommentStopReason,
    ScrollSpeed,
)


class ScriptedLoader(CommentLoader):
    def __init__(self, counts: list[int], options: CommentLoadOptions, *, end_at: int | None = None):
        super().__init__(object(), options)
        self.counts = counts
        self.index = 0
        self.end_at = end_at

    async def _scroll_to_comments_area(self) -> None:
        return None

    async def _has_no_comments(self) -> bool:
        return False

    async def _reached_end(self) -> bool:
        return self.end_at is not None and self.index >= self.end_at

    async def _click_more_replies(self) -> None:
        return None

    async def _parent_count(self) -> int:
        value = self.counts[min(self.index, len(self.counts) - 1)]
        self.index += 1
        return value

    async def _scroll_comments(self, *, large: bool) -> None:
        return None

    async def _wait_scroll_interval(self) -> None:
        return None


async def test_loader_stops_at_parent_comment_limit() -> None:
    loader = ScriptedLoader(
        [0, 50, 100], CommentLoadOptions(max_parent_comments=100)
    )

    outcome = await loader.load()

    assert outcome.stop_reason is CommentStopReason.MAX_PARENT_COMMENTS
    assert outcome.observed_parent_count == 100


async def test_loader_stops_after_stagnation() -> None:
    loader = ScriptedLoader(
        [1] * (MAX_STAGNANT_ATTEMPTS + 2),
        CommentLoadOptions(max_parent_comments=100),
    )

    outcome = await loader.load()

    assert outcome.stop_reason is CommentStopReason.STALLED
    assert outcome.observed_parent_count == 1


async def test_loader_recognizes_end_marker() -> None:
    loader = ScriptedLoader(
        [1, 2], CommentLoadOptions(max_parent_comments=100), end_at=1
    )

    outcome = await loader.load()

    assert outcome.stop_reason is CommentStopReason.END_REACHED


def test_reply_threshold_and_scroll_profiles() -> None:
    assert should_skip_reply_button("展开 11 条回复", 10)
    assert not should_skip_reply_button("展开 10 条回复", 10)
    assert not should_skip_reply_button("展开 999 条回复", None)
    assert scroll_profile(ScrollSpeed.SLOW, large=False) == (0.5, 1.2)
    assert scroll_profile(ScrollSpeed.FAST, large=True) == (1.8, 0.3)

