"""Bilibili subprocess-backed collector and hydrator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..models.feed_item import FeedAuthor, FeedComment, FeedItem
from .base import BaseConnector, CollectedItem
from .subprocess import SubprocessRunner

HYDRATE_COMMENT_LIMIT = 5


class BilibiliConnector(BaseConnector):
    source = "bilibili"

    def __init__(self, runner: SubprocessRunner, executable: str = "bili"):
        self._runner = runner
        self._executable = executable

    def collect_feed(self, *, limit: int = 20) -> list[CollectedItem]:
        result = self._runner.run(
            [
                self._executable,
                "recommend",
                "--max",
                str(limit),
                "--json",
            ]
        )
        payload = result.json()
        items = payload.get("data", {}).get("items", [])
        return [
            CollectedItem(
                rank_in_batch=index,
                raw_payload=entry,
                feed_item=_map_video_item(entry, collected_at=_now_iso(), channel="feed"),
            )
            for index, entry in enumerate(items, start=1)
            if isinstance(entry, dict) and entry.get("bvid")
        ]

    def hydrate_item(self, item: CollectedItem) -> CollectedItem:
        if not item.feed_item.source_item_id:
            return item
        result = self._runner.run(
            [
                self._executable,
                "hydrate",
                item.feed_item.source_item_id,
                "--comment-limit",
                str(HYDRATE_COMMENT_LIMIT),
                "--json",
            ]
        )
        payload = result.json().get("data", {})
        return CollectedItem(
            rank_in_batch=item.rank_in_batch,
            raw_payload=payload if isinstance(payload, dict) else item.raw_payload,
            feed_item=_map_hydrated_video_item(
                payload if isinstance(payload, dict) else {},
                fallback=item.feed_item,
            ),
        )


def _map_video_item(entry: dict[str, Any], *, collected_at: str, channel: str) -> FeedItem:
    bvid = str(entry.get("bvid", "")).strip()
    owner = entry.get("owner", {}) if isinstance(entry.get("owner"), dict) else {}
    stats = entry.get("stats", {}) if isinstance(entry.get("stats"), dict) else {}
    return FeedItem(
        schema_version="1",
        item_uid=f"bilibili:video:{bvid}",
        source="bilibili",
        entity_type="video",
        source_item_id=bvid,
        canonical_url=str(entry.get("url", "")).strip() or f"https://www.bilibili.com/video/{bvid}",
        collection_channel=channel,
        title=str(entry.get("title", "")).strip(),
        author=FeedAuthor(
            id=_string_or_none(owner.get("id")),
            name=str(owner.get("name", "")).strip(),
            profile_url=None,
        ),
        published_at=None,
        collected_at=collected_at,
        lang="zh-CN",
        excerpt_text=str(entry.get("description", "")).strip(),
        body_text="",
        transcript_text="",
        top_comments=[],
        topics=[],
        engagement={
            "view_count": _safe_int(stats.get("view")),
            "like_count": _safe_int(stats.get("like")),
            "comment_count": None,
            "share_count": _safe_int(stats.get("share")),
            "favorite_count": _safe_int(stats.get("favorite")),
            "voteup_count": None,
            "coin_count": _safe_int(stats.get("coin")),
            "danmaku_count": _safe_int(stats.get("danmaku")),
        },
        media={
            "has_video": True,
            "duration_seconds": _safe_int(entry.get("duration_seconds")),
            "image_count": None,
        },
        quality_flags={
            "has_transcript": False,
            "has_long_body": False,
            "is_recommendation": channel == "feed",
            "is_from_following": False,
            "is_ad_suspected": False,
        },
    )


def _map_hydrated_video_item(payload: dict[str, Any], *, fallback: FeedItem) -> FeedItem:
    video = payload.get("video", {}) if isinstance(payload.get("video"), dict) else {}
    subtitle = payload.get("subtitle", {}) if isinstance(payload.get("subtitle"), dict) else {}
    comments = payload.get("comments", []) if isinstance(payload.get("comments"), list) else []
    stats = video.get("stats", {}) if isinstance(video.get("stats"), dict) else {}
    owner = video.get("owner", {}) if isinstance(video.get("owner"), dict) else {}
    description = str(video.get("description", "")).strip()
    transcript = str(subtitle.get("text", "")).strip()
    top_comments = [_map_hydrated_comment(comment) for comment in comments if isinstance(comment, dict)]

    return FeedItem(
        schema_version=fallback.schema_version,
        item_uid=fallback.item_uid,
        source=fallback.source,
        entity_type=fallback.entity_type,
        source_item_id=fallback.source_item_id,
        canonical_url=str(video.get("url", "")).strip() or fallback.canonical_url,
        collection_channel=fallback.collection_channel,
        title=str(video.get("title", "")).strip() or fallback.title,
        author=FeedAuthor(
            id=_string_or_none(owner.get("id")) or fallback.author.id,
            name=str(owner.get("name", "")).strip() or fallback.author.name,
            profile_url=fallback.author.profile_url,
        ),
        published_at=fallback.published_at,
        collected_at=fallback.collected_at,
        lang=fallback.lang,
        excerpt_text=description or fallback.excerpt_text,
        body_text=description,
        transcript_text=transcript,
        top_comments=top_comments,
        topics=fallback.topics,
        engagement={
            "view_count": _safe_int(stats.get("view")) or fallback.engagement.get("view_count"),
            "like_count": _safe_int(stats.get("like")) or fallback.engagement.get("like_count"),
            "comment_count": len(top_comments) or fallback.engagement.get("comment_count"),
            "share_count": _safe_int(stats.get("share")) or fallback.engagement.get("share_count"),
            "favorite_count": _safe_int(stats.get("favorite")) or fallback.engagement.get("favorite_count"),
            "voteup_count": fallback.engagement.get("voteup_count"),
            "coin_count": _safe_int(stats.get("coin")) or fallback.engagement.get("coin_count"),
            "danmaku_count": _safe_int(stats.get("danmaku")) or fallback.engagement.get("danmaku_count"),
        },
        media={
            "has_video": True,
            "duration_seconds": _safe_int(video.get("duration_seconds")) or fallback.media.get("duration_seconds"),
            "image_count": fallback.media.get("image_count"),
        },
        quality_flags={
            "has_transcript": bool(transcript),
            "has_long_body": len(description) >= 160 or len(transcript) >= 240,
            "is_recommendation": fallback.quality_flags.get("is_recommendation", False),
            "is_from_following": fallback.quality_flags.get("is_from_following", False),
            "is_ad_suspected": fallback.quality_flags.get("is_ad_suspected", False),
        },
    )


def _map_hydrated_comment(comment: dict[str, Any]) -> FeedComment:
    author = comment.get("author", {}) if isinstance(comment.get("author"), dict) else {}
    return FeedComment(
        author_name=str(author.get("name", "")).strip() or "Anonymous",
        content=str(comment.get("message", "")).strip(),
        like_count=_safe_int(comment.get("like")),
    )


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
