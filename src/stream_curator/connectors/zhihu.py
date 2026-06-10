"""Zhihu subprocess-backed collector and hydrator."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any

from ..models.feed_item import FeedAuthor, FeedComment, FeedItem
from .base import BaseConnector, CollectedItem
from .subprocess import SubprocessRunner

HYDRATE_COMMENT_LIMIT = 5
HYDRATE_ANSWER_LIMIT = 3


class ZhihuConnector(BaseConnector):
    source = "zhihu"

    def __init__(self, runner: SubprocessRunner, executable: str = "zhihu"):
        self._runner = runner
        self._executable = executable

    def collect_feed(self, *, limit: int = 10) -> list[CollectedItem]:
        result = self._runner.run(
            [
                self._executable,
                "feed",
                "--limit",
                str(limit),
                "--json",
            ]
        )
        payload = result.json()
        items: list[CollectedItem] = []
        collected_at = _now_iso()
        for index, entry in enumerate(payload.get("data", []), start=1):
            if not isinstance(entry, dict):
                continue
            target = entry.get("target", {})
            if not isinstance(target, dict):
                continue
            entity_type, source_item_id, canonical_url = _resolve_feed_identity(target)
            if not source_item_id or not canonical_url:
                continue
            question = target.get("question", {}) if isinstance(target.get("question"), dict) else {}
            author = _resolve_feed_author(target, question)
            feed_item = FeedItem(
                schema_version="1",
                item_uid=f"zhihu:{entity_type}:{source_item_id}",
                source="zhihu",
                entity_type=entity_type,
                source_item_id=source_item_id,
                canonical_url=canonical_url,
                collection_channel="feed",
                title=_resolve_feed_title(target),
                author=FeedAuthor(
                    id=_string_or_none(author.get("id")),
                    name=str(author.get("name", "")).strip(),
                    profile_url=_string_or_none(author.get("url")),
                ),
                published_at=_resolve_feed_published_at(target, question),
                collected_at=collected_at,
                lang="zh-CN",
                excerpt_text=_resolve_feed_excerpt(target, question),
                body_text="",
                transcript_text="",
                top_comments=[],
                topics=_question_topics(question),
                engagement={
                    "view_count": _safe_int(target.get("visited_count")),
                    "like_count": _safe_int(target.get("voteup_count")),
                    "comment_count": _safe_int(target.get("comment_count")),
                    "share_count": None,
                    "favorite_count": _safe_int(target.get("favorite_count")),
                    "voteup_count": _safe_int(target.get("voteup_count")),
                    "coin_count": None,
                    "danmaku_count": None,
                },
                media={
                    "has_video": False,
                    "duration_seconds": None,
                    "image_count": None,
                },
                quality_flags={
                    "has_transcript": False,
                    "has_long_body": False,
                    "is_recommendation": True,
                    "is_from_following": False,
                    "is_ad_suspected": False,
                },
            )
            items.append(
                CollectedItem(rank_in_batch=index, raw_payload=entry, feed_item=feed_item)
            )
            if len(items) >= limit:
                break
        return items[:limit]

    def hydrate_item(self, item: CollectedItem) -> CollectedItem:
        if not item.feed_item.source_item_id:
            return item
        result = self._runner.run(
            [
                self._executable,
                "hydrate",
                item.feed_item.entity_type,
                item.feed_item.source_item_id,
                "--comment-limit",
                str(HYDRATE_COMMENT_LIMIT),
                "--answer-limit",
                str(HYDRATE_ANSWER_LIMIT),
                "--json",
            ]
        )
        payload = result.json()
        return CollectedItem(
            rank_in_batch=item.rank_in_batch,
            raw_payload=payload if isinstance(payload, dict) else item.raw_payload,
            feed_item=_map_hydrated_item(
                payload if isinstance(payload, dict) else {},
                fallback=item.feed_item,
            ),
        )


def _resolve_feed_identity(target: dict[str, Any]) -> tuple[str, str, str]:
    entity_type = str(target.get("type", "")).strip()
    source_item_id = str(target.get("id", "")).strip()
    if entity_type in {"answer", "question", "article"} and source_item_id:
        return (
            entity_type,
            source_item_id,
            _zhihu_web_url(entity_type, source_item_id, str(target.get("url", "")).strip()),
        )
    return "", "", ""


def _resolve_feed_title(target: dict[str, Any]) -> str:
    if str(target.get("type", "")).strip() == "answer":
        question = target.get("question", {})
        if isinstance(question, dict):
            return str(question.get("title", "") or question.get("name", "")).strip()
    return str(target.get("title", "") or target.get("name", "")).strip()


def _resolve_feed_excerpt(target: dict[str, Any], question: dict[str, Any]) -> str:
    excerpt = (
        target.get("excerpt_new")
        or target.get("excerpt")
        or target.get("preview_text")
        or target.get("content")
        or question.get("excerpt")
        or ""
    )
    return _strip_html(str(excerpt))[:1200]


def _resolve_feed_author(target: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    author = target.get("author", {})
    if isinstance(author, dict) and author:
        return author
    question_author = question.get("author", {})
    if isinstance(question_author, dict):
        return question_author
    return {}


def _resolve_feed_published_at(target: dict[str, Any], question: dict[str, Any]) -> str | None:
    return _ts_to_iso(target.get("created_time") or question.get("created"))


def _question_topics(question: dict[str, Any]) -> list[str]:
    topics = question.get("topics", [])
    if not isinstance(topics, list):
        return []
    return [
        str(topic.get("name", "")).strip()
        for topic in topics
        if isinstance(topic, dict) and str(topic.get("name", "")).strip()
    ]


def _map_hydrated_item(payload: dict[str, Any], *, fallback: FeedItem) -> FeedItem:
    entity_type = str(payload.get("entity_type", fallback.entity_type)).strip() or fallback.entity_type
    comments = payload.get("comments", []) if isinstance(payload.get("comments"), list) else []
    top_comments = [_map_comment(comment) for comment in comments if isinstance(comment, dict)]

    if entity_type == "question":
        question = payload.get("question", {}) if isinstance(payload.get("question"), dict) else {}
        answers = payload.get("answers", []) if isinstance(payload.get("answers"), list) else []
        detail = str(question.get("detail", "")).strip()
        excerpt = detail
        if not excerpt and answers:
            first_answer = answers[0] if isinstance(answers[0], dict) else {}
            excerpt = str(first_answer.get("excerpt", "")).strip()
        body = _combine_question_body(question=question, answers=answers)
        return FeedItem(
            schema_version=fallback.schema_version,
            item_uid=fallback.item_uid,
            source=fallback.source,
            entity_type=fallback.entity_type,
            source_item_id=fallback.source_item_id,
            canonical_url=str(question.get("url", "")).strip() or fallback.canonical_url,
            collection_channel=fallback.collection_channel,
            title=str(question.get("title", "")).strip() or fallback.title,
            author=fallback.author,
            published_at=fallback.published_at,
            collected_at=fallback.collected_at,
            lang=fallback.lang,
            excerpt_text=excerpt or fallback.excerpt_text,
            body_text=body,
            transcript_text="",
            top_comments=top_comments,
            topics=[
                str(topic).strip()
                for topic in question.get("topics", [])
                if str(topic).strip()
            ] or fallback.topics,
            engagement={
                "view_count": _safe_int(question.get("visit_count")) or fallback.engagement.get("view_count"),
                "like_count": fallback.engagement.get("like_count"),
                "comment_count": _safe_int(question.get("comment_count")) or fallback.engagement.get("comment_count"),
                "share_count": fallback.engagement.get("share_count"),
                "favorite_count": fallback.engagement.get("favorite_count"),
                "voteup_count": fallback.engagement.get("voteup_count"),
                "coin_count": None,
                "danmaku_count": None,
            },
            media=fallback.media,
            quality_flags={
                "has_transcript": False,
                "has_long_body": len(body) >= 240,
                "is_recommendation": fallback.quality_flags.get("is_recommendation", False),
                "is_from_following": fallback.quality_flags.get("is_from_following", False),
                "is_ad_suspected": fallback.quality_flags.get("is_ad_suspected", False),
            },
        )

    if entity_type == "answer":
        answer = payload.get("answer", {}) if isinstance(payload.get("answer"), dict) else {}
        author = answer.get("author", {}) if isinstance(answer.get("author"), dict) else {}
        body = str(answer.get("body", "")).strip()
        return FeedItem(
            schema_version=fallback.schema_version,
            item_uid=fallback.item_uid,
            source=fallback.source,
            entity_type=fallback.entity_type,
            source_item_id=fallback.source_item_id,
            canonical_url=str(answer.get("url", "")).strip() or fallback.canonical_url,
            collection_channel=fallback.collection_channel,
            title=str(answer.get("title", "")).strip() or fallback.title,
            author=FeedAuthor(
                id=_string_or_none(author.get("id")) or fallback.author.id,
                name=str(author.get("name", "")).strip() or fallback.author.name,
                profile_url=fallback.author.profile_url,
            ),
            published_at=fallback.published_at,
            collected_at=fallback.collected_at,
            lang=fallback.lang,
            excerpt_text=str(answer.get("excerpt", "")).strip() or fallback.excerpt_text,
            body_text=body,
            transcript_text="",
            top_comments=top_comments,
            topics=fallback.topics,
            engagement={
                "view_count": fallback.engagement.get("view_count"),
                "like_count": _safe_int(answer.get("voteup_count")) or fallback.engagement.get("like_count"),
                "comment_count": _safe_int(answer.get("comment_count")) or fallback.engagement.get("comment_count"),
                "share_count": fallback.engagement.get("share_count"),
                "favorite_count": fallback.engagement.get("favorite_count"),
                "voteup_count": _safe_int(answer.get("voteup_count")) or fallback.engagement.get("voteup_count"),
                "coin_count": None,
                "danmaku_count": None,
            },
            media=fallback.media,
            quality_flags={
                "has_transcript": False,
                "has_long_body": len(body) >= 240,
                "is_recommendation": fallback.quality_flags.get("is_recommendation", False),
                "is_from_following": fallback.quality_flags.get("is_from_following", False),
                "is_ad_suspected": fallback.quality_flags.get("is_ad_suspected", False),
            },
        )

    article = payload.get("article", {}) if isinstance(payload.get("article"), dict) else {}
    author = article.get("author", {}) if isinstance(article.get("author"), dict) else {}
    body = str(article.get("body", "")).strip()
    return FeedItem(
        schema_version=fallback.schema_version,
        item_uid=fallback.item_uid,
        source=fallback.source,
        entity_type=fallback.entity_type,
        source_item_id=fallback.source_item_id,
        canonical_url=str(article.get("url", "")).strip() or fallback.canonical_url,
        collection_channel=fallback.collection_channel,
        title=str(article.get("title", "")).strip() or fallback.title,
        author=FeedAuthor(
            id=_string_or_none(author.get("id")) or fallback.author.id,
            name=str(author.get("name", "")).strip() or fallback.author.name,
            profile_url=fallback.author.profile_url,
        ),
        published_at=fallback.published_at,
        collected_at=fallback.collected_at,
        lang=fallback.lang,
        excerpt_text=str(article.get("excerpt", "")).strip() or fallback.excerpt_text,
        body_text=body,
        transcript_text="",
        top_comments=top_comments,
        topics=fallback.topics,
        engagement={
            "view_count": fallback.engagement.get("view_count"),
            "like_count": _safe_int(article.get("voteup_count")) or fallback.engagement.get("like_count"),
            "comment_count": _safe_int(article.get("comment_count")) or fallback.engagement.get("comment_count"),
            "share_count": fallback.engagement.get("share_count"),
            "favorite_count": fallback.engagement.get("favorite_count"),
            "voteup_count": _safe_int(article.get("voteup_count")) or fallback.engagement.get("voteup_count"),
            "coin_count": None,
            "danmaku_count": None,
        },
        media=fallback.media,
        quality_flags={
            "has_transcript": False,
            "has_long_body": len(body) >= 240,
            "is_recommendation": fallback.quality_flags.get("is_recommendation", False),
            "is_from_following": fallback.quality_flags.get("is_from_following", False),
            "is_ad_suspected": fallback.quality_flags.get("is_ad_suspected", False),
        },
    )


def _combine_question_body(*, question: dict[str, Any], answers: list[Any]) -> str:
    parts: list[str] = []
    detail = str(question.get("detail", "")).strip()
    if detail:
        parts.append(detail)
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        author = answer.get("author", {}) if isinstance(answer.get("author"), dict) else {}
        author_name = str(author.get("name", "")).strip()
        body = str(answer.get("body", "") or answer.get("excerpt", "")).strip()
        if not body:
            continue
        if author_name:
            parts.append(f"{author_name}: {body}")
        else:
            parts.append(body)
    return "\n\n".join(parts).strip()


def _map_comment(comment: dict[str, Any]) -> FeedComment:
    author = comment.get("author", {}) if isinstance(comment.get("author"), dict) else {}
    return FeedComment(
        author_name=str((author.get("name") or "Anonymous")).strip(),
        content=str(comment.get("content", "")).strip(),
        like_count=_safe_int(comment.get("vote_count")),
    )


def _ts_to_iso(value: Any) -> str | None:
    timestamp = _safe_int(value)
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


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


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return " ".join(cleaned.split()).strip()


def _zhihu_web_url(entity_type: str, source_item_id: str, url: str) -> str:
    if entity_type == "answer":
        return f"https://www.zhihu.com/answer/{source_item_id}"
    if entity_type == "question":
        return f"https://www.zhihu.com/question/{source_item_id}"
    if entity_type == "article":
        if url.startswith("https://zhuanlan.zhihu.com/"):
            return url
        return f"https://zhuanlan.zhihu.com/p/{source_item_id}"
    return url


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
