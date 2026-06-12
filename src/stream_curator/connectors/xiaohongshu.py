"""Xiaohongshu subprocess-backed collector and hydrator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..models.feed_item import FeedAuthor, FeedComment, FeedItem
from .base import BaseConnector, CollectedItem
from .subprocess import SubprocessRunner

HYDRATE_COMMENT_LIMIT = 5


class XiaohongshuConnector(BaseConnector):
    source = "xiaohongshu"

    def __init__(self, runner: SubprocessRunner, executable: str = "xhs"):
        self._runner = runner
        self._executable = executable

    def collect_feed(self, *, limit: int = 20) -> list[CollectedItem]:
        result = self._runner.run(
            [
                self._executable,
                "feed",
                "--json",
            ]
        )
        payload = result.json()
        items = payload.get("data", {}).get("items", [])
        collected = [
            CollectedItem(
                rank_in_batch=index,
                raw_payload=entry,
                feed_item=_map_note_item(entry, collected_at=_now_iso(), channel="feed"),
            )
            for index, entry in enumerate(items, start=1)
            if _is_note_item(entry)
        ]
        return collected[:limit]

    def collect_hot(self, *, limit: int = 10) -> list[CollectedItem]:
        result = self._runner.run(
            [
                self._executable,
                "hot",
                "--json",
            ]
        )
        payload = result.json()
        items = payload.get("data", {}).get("items", [])
        collected = [
            CollectedItem(
                rank_in_batch=index,
                raw_payload=entry,
                feed_item=_map_note_item(entry, collected_at=_now_iso(), channel="hot"),
            )
            for index, entry in enumerate(items, start=1)
            if _is_note_item(entry)
        ]
        return collected[:limit]

    def collect_search(self, *, query: str, limit: int = 10) -> list[CollectedItem]:
        keyword = str(query or "").strip()
        if not keyword:
            return []
        result = self._runner.run(
            [
                self._executable,
                "search",
                keyword,
                "--page",
                "1",
                "--json",
            ]
        )
        payload = result.json()
        items = payload.get("data", {}).get("items", [])
        collected = [
            CollectedItem(
                rank_in_batch=index,
                raw_payload=entry,
                feed_item=_map_note_item(entry, collected_at=_now_iso(), channel="search", query=keyword),
            )
            for index, entry in enumerate(items, start=1)
            if _is_note_item(entry)
        ]
        return collected[:limit]

    def hydrate_item(self, item: CollectedItem) -> CollectedItem:
        note_ref = item.feed_item.canonical_url or item.feed_item.source_item_id
        if not note_ref:
            return item
        result = self._runner.run(
            [
                self._executable,
                "hydrate",
                note_ref,
                "--comment-limit",
                str(HYDRATE_COMMENT_LIMIT),
                "--json",
            ]
        )
        payload = result.json().get("data", {})
        return CollectedItem(
            rank_in_batch=item.rank_in_batch,
            raw_payload=payload if isinstance(payload, dict) else item.raw_payload,
            feed_item=_map_hydrated_note_item(
                payload if isinstance(payload, dict) else {},
                fallback=item.feed_item,
            ),
        )


def _is_note_item(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("model_type") == "note"
        and isinstance(entry.get("note_card"), dict)
    )


def _map_note_item(
    entry: dict[str, Any],
    *,
    collected_at: str,
    channel: str,
    query: str | None = None,
) -> FeedItem:
    note_id = str(entry.get("id", "")).strip()
    note_card = entry.get("note_card", {}) if isinstance(entry.get("note_card"), dict) else {}
    user = note_card.get("user", {}) if isinstance(note_card.get("user"), dict) else {}
    interact = (
        note_card.get("interact_info", {})
        if isinstance(note_card.get("interact_info"), dict)
        else {}
    )
    cover = note_card.get("cover", {}) if isinstance(note_card.get("cover"), dict) else {}
    image_list = note_card.get("image_list", []) if isinstance(note_card.get("image_list"), list) else []
    has_video = note_card.get("type") == "video"
    duration_seconds = _safe_int(
        ((note_card.get("video") or {}).get("capa") or {}).get("duration")
        if isinstance(note_card.get("video"), dict)
        else None
    )
    return FeedItem(
        schema_version="1",
        item_uid=f"xiaohongshu:note:{note_id}",
        source="xiaohongshu",
        entity_type="note",
        source_item_id=note_id,
        canonical_url=_canonical_note_url(note_id, str(entry.get("xsec_token", "")).strip()),
        collection_channel=channel,
        query_text=query,
        title=str(note_card.get("display_title", "")).strip(),
        author=FeedAuthor(
            id=_string_or_none(user.get("user_id")),
            name=str(user.get("nickname", "") or user.get("nick_name", "")).strip(),
            profile_url=None,
        ),
        published_at=None,
        collected_at=collected_at,
        lang="zh-CN",
        excerpt_text="",
        body_text="",
        transcript_text="",
        top_comments=[],
        topics=[],
        engagement={
            "view_count": None,
            "like_count": _compact_count_to_int(interact.get("liked_count")),
            "comment_count": _compact_count_to_int(interact.get("comment_count")),
            "share_count": _compact_count_to_int(interact.get("shared_count")),
            "favorite_count": _compact_count_to_int(interact.get("collected_count")),
            "voteup_count": None,
            "coin_count": None,
            "danmaku_count": None,
        },
        media={
            "has_video": has_video,
            "duration_seconds": duration_seconds,
            "image_count": len(image_list) if image_list else (_safe_int(cover.get("image_count")) or 1),
            "image_urls": _extract_image_urls(image_list),
        },
        quality_flags={
            "has_transcript": False,
            "has_long_body": False,
            "is_recommendation": channel == "feed",
            "is_from_following": False,
            "is_ad_suspected": False,
        },
    )


def _map_hydrated_note_item(payload: dict[str, Any], *, fallback: FeedItem) -> FeedItem:
    note = payload.get("note", {}) if isinstance(payload.get("note"), dict) else {}
    comments = payload.get("comments", []) if isinstance(payload.get("comments"), list) else []
    author = note.get("author", {}) if isinstance(note.get("author"), dict) else {}
    body = str(note.get("body", "")).strip()
    title = str(note.get("title", "")).strip()
    tags = note.get("tags", []) if isinstance(note.get("tags"), list) else []
    image_urls = _normalize_image_urls(note.get("images"))
    top_comments = [_map_comment(comment) for comment in comments if isinstance(comment, dict)]

    return FeedItem(
        schema_version=fallback.schema_version,
        item_uid=fallback.item_uid,
        source=fallback.source,
        entity_type=fallback.entity_type,
        source_item_id=fallback.source_item_id,
        canonical_url=str(note.get("url", "")).strip() or fallback.canonical_url,
        collection_channel=fallback.collection_channel,
        query_text=fallback.query_text,
        title=title or fallback.title,
        author=FeedAuthor(
            id=_string_or_none(author.get("id")) or fallback.author.id,
            name=str(author.get("name", "")).strip() or fallback.author.name,
            profile_url=fallback.author.profile_url,
        ),
        published_at=fallback.published_at,
        collected_at=fallback.collected_at,
        lang=fallback.lang,
        excerpt_text=body[:320] if body else fallback.excerpt_text,
        body_text=body,
        transcript_text="",
        top_comments=top_comments,
        topics=[str(tag).strip() for tag in tags if str(tag).strip()],
        engagement={
            "view_count": fallback.engagement.get("view_count"),
            "like_count": _safe_int(note.get("liked_count")) or fallback.engagement.get("like_count"),
            "comment_count": _safe_int(note.get("comment_count")) or fallback.engagement.get("comment_count"),
            "share_count": _safe_int(note.get("share_count")) or fallback.engagement.get("share_count"),
            "favorite_count": _safe_int(note.get("collected_count")) or fallback.engagement.get("favorite_count"),
            "voteup_count": fallback.engagement.get("voteup_count"),
            "coin_count": fallback.engagement.get("coin_count"),
            "danmaku_count": fallback.engagement.get("danmaku_count"),
        },
        media={
            "has_video": str(note.get("note_type", "")).strip() == "video" or bool(fallback.media.get("has_video")),
            "duration_seconds": fallback.media.get("duration_seconds"),
            "image_count": _safe_int(note.get("image_count")) or len(image_urls) or fallback.media.get("image_count"),
            "image_urls": image_urls or _normalize_image_urls(fallback.media.get("image_urls")),
        },
        quality_flags={
            "has_transcript": False,
            "has_long_body": len(body) >= 160,
            "is_recommendation": fallback.quality_flags.get("is_recommendation", False),
            "is_from_following": fallback.quality_flags.get("is_from_following", False),
            "is_ad_suspected": fallback.quality_flags.get("is_ad_suspected", False),
        },
    )


def _map_comment(comment: dict[str, Any]) -> FeedComment:
    return FeedComment(
        author_name=str(comment.get("nickname", "")).strip() or "Anonymous",
        content=str(comment.get("content", "")).strip(),
        like_count=_safe_int(comment.get("like_count")),
    )


def _canonical_note_url(note_id: str, xsec_token: str) -> str:
    if xsec_token:
        return (
            f"https://www.xiaohongshu.com/explore/{note_id}"
            f"?xsec_token={xsec_token}&xsec_source=pc_search"
        )
    return f"https://www.xiaohongshu.com/explore/{note_id}"


def _extract_image_urls(image_list: list[Any]) -> list[str]:
    urls: list[str] = []
    for image in image_list:
        if not isinstance(image, dict):
            continue
        url = _first_non_empty(
            image.get("url_default"),
            image.get("url_pre"),
            image.get("url"),
        )
        if not url:
            info_list = image.get("info_list", [])
            if isinstance(info_list, list):
                for info in info_list:
                    if not isinstance(info, dict):
                        continue
                    url = _first_non_empty(info.get("url"))
                    if url:
                        break
        normalized = _normalize_media_url(url)
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def _normalize_image_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    urls: list[str] = []
    for item in value:
        normalized = _normalize_media_url(item)
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_media_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://"):
        return "https://" + text[len("http://"):]
    return text


def _compact_count_to_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("w+", "w").replace("W+", "W")
    if normalized.endswith(("w", "W")):
        try:
            return int(float(normalized[:-1]) * 10000)
        except ValueError:
            return None
    try:
        return int(normalized)
    except ValueError:
        return None


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
