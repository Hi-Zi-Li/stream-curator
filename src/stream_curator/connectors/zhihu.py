"""Zhihu subprocess-backed collector and hydrator."""

from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import re
from typing import Any

from ..models.feed_item import FeedAuthor, FeedComment, FeedItem
from .base import BaseConnector, CollectedItem
from .subprocess import SubprocessRunner

HYDRATE_COMMENT_LIMIT = 5
HYDRATE_ANSWER_LIMIT = 5


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

    def collect_hot(self, *, limit: int = 10) -> list[CollectedItem]:
        result = self._runner.run(
            [
                self._executable,
                "hot",
                "--limit",
                str(limit),
                "--answers",
                "0",
                "--json",
            ]
        )
        payload = result.json()
        items: list[CollectedItem] = []
        collected_at = _now_iso()
        for index, entry in enumerate(payload.get("data", []), start=1):
            if not isinstance(entry, dict):
                continue
            question = entry.get("question", {})
            if not isinstance(question, dict):
                continue
            source_item_id = str(question.get("id", "")).strip()
            canonical_url = str(question.get("url", "")).strip() or _zhihu_web_url("question", source_item_id, "")
            if not source_item_id or not canonical_url:
                continue
            creator = question.get("creator", {}) if isinstance(question.get("creator"), dict) else {}
            reaction = entry.get("reaction", {}) if isinstance(entry.get("reaction"), dict) else {}
            excerpt = str(entry.get("detail_text") or reaction.get("text") or "").strip()
            feed_item = FeedItem(
                schema_version="1",
                item_uid=f"zhihu:question:{source_item_id}",
                source="zhihu",
                entity_type="question",
                source_item_id=source_item_id,
                canonical_url=canonical_url,
                collection_channel="hot",
                title=str(question.get("title", "") or question.get("name", "")).strip(),
                author=FeedAuthor(
                    id=_string_or_none(creator.get("url_token")),
                    name=str(creator.get("name", "")).strip(),
                    profile_url=None,
                ),
                published_at=_ts_to_iso(question.get("created")),
                collected_at=collected_at,
                lang="zh-CN",
                excerpt_text=excerpt,
                body_text="",
                transcript_text="",
                top_comments=[],
                topics=_question_topics(question),
                engagement={
                    "view_count": _safe_int(reaction.get("pv")),
                    "like_count": _safe_int(reaction.get("upvote_num")),
                    "comment_count": None,
                    "share_count": None,
                    "favorite_count": _safe_int(reaction.get("follow_num")),
                    "voteup_count": _safe_int(reaction.get("upvote_num")),
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
                    "is_recommendation": False,
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

    def collect_search(self, *, query: str, limit: int = 10) -> list[CollectedItem]:
        keyword = str(query or "").strip()
        if not keyword:
            return []
        requested_limit = max(limit * 4, limit)
        result = self._runner.run(
            [
                self._executable,
                "search",
                keyword,
                "-l",
                str(requested_limit),
                "-a",
                "1",
                "--json",
            ]
        )
        payload = result.json()
        entries = payload.get("data", []) if isinstance(payload.get("data"), list) else []
        items: list[CollectedItem] = []
        collected_at = _now_iso()
        for index, entry in enumerate(entries, start=1):
            feed_item = _map_search_entry(entry, collected_at=collected_at, query=keyword)
            if feed_item is None:
                continue
            items.append(
                CollectedItem(rank_in_batch=index, raw_payload=entry, feed_item=feed_item)
            )
            if len(items) >= limit:
                break
        return items

    def hydrate_item(self, item: CollectedItem) -> CollectedItem:
        if not item.feed_item.source_item_id:
            return item
        payload: dict[str, Any] | None = None
        mapped_item = item
        hydrate_error: Exception | None = None
        try:
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
            raw_payload = result.json()
            payload = raw_payload if isinstance(raw_payload, dict) else {}
            mapped_item = CollectedItem(
                rank_in_batch=item.rank_in_batch,
                raw_payload=payload or item.raw_payload,
                feed_item=_map_hydrated_item(
                    payload,
                    fallback=item.feed_item,
                ),
            )
        except Exception as exc:
            hydrate_error = exc

        if item.feed_item.entity_type == "question":
            enriched_feed_item = self._enrich_question_from_answers(mapped_item.feed_item)
            if _question_reader_is_usable(enriched_feed_item):
                return CollectedItem(
                    rank_in_batch=item.rank_in_batch,
                    raw_payload=payload if payload is not None else item.raw_payload,
                    feed_item=enriched_feed_item,
                )
            if hydrate_error is not None:
                raise hydrate_error
            return CollectedItem(
                rank_in_batch=item.rank_in_batch,
                raw_payload=payload if payload is not None else mapped_item.raw_payload,
                feed_item=enriched_feed_item,
            )

        if hydrate_error is not None:
            raise hydrate_error
        return mapped_item

    def _enrich_question_from_answers(self, item: FeedItem) -> FeedItem:
        try:
            payload = self._runner.run(
                [
                    self._executable,
                    "answers",
                    item.source_item_id,
                    "--limit",
                    str(HYDRATE_ANSWER_LIMIT),
                    "--json",
                ]
            ).json()
        except Exception:
            return item

        answers = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(answers, list) or not answers:
            return item

        first_answer = answers[0] if isinstance(answers[0], dict) else {}
        body = item.body_text.strip() or _combine_question_answer_fallback_body(answers)
        excerpt = item.excerpt_text.strip() or _answer_fallback_excerpt(first_answer)
        comment_answer_id, fallback_comments = self._question_answer_comments(answers)
        top_comments = item.top_comments or fallback_comments
        content_blocks = item.media.get("content_blocks") or _question_answer_content_blocks(answers)
        answer_sections = item.media.get("answer_sections") or _question_answer_sections(answers)
        default_answer_id = _question_default_answer_id(answer_sections)

        return FeedItem(
            schema_version=item.schema_version,
            item_uid=item.item_uid,
            source=item.source,
            entity_type=item.entity_type,
            source_item_id=item.source_item_id,
            canonical_url=item.canonical_url,
            collection_channel=item.collection_channel,
            title=item.title,
            author=item.author,
            published_at=item.published_at,
            collected_at=item.collected_at,
            lang=item.lang,
            excerpt_text=excerpt or item.excerpt_text,
            body_text=body,
            transcript_text=item.transcript_text,
            top_comments=top_comments,
            topics=item.topics,
            engagement=item.engagement,
            media={
                **item.media,
                "content_blocks": content_blocks,
                "answer_sections": answer_sections,
                "default_answer_id": default_answer_id,
                "comment_answer_id": comment_answer_id or item.media.get("comment_answer_id"),
            },
            quality_flags={
                **item.quality_flags,
                "has_long_body": len(body) >= 240,
            },
            query_text=item.query_text,
        )

    def _question_answer_comments(self, answers: list[Any]) -> tuple[str, list[FeedComment]]:
        candidates = _comment_candidate_answers(answers)
        for answer in candidates:
            answer_id = str(answer.get("id", "")).strip()
            if not answer_id:
                continue
            try:
                payload = self._runner.run(
                    [
                        self._executable,
                        "comments",
                        "answer",
                        answer_id,
                        "--limit",
                        str(HYDRATE_COMMENT_LIMIT),
                        "--json",
                    ]
                ).json()
            except Exception:
                continue

            comments = payload.get("comments", []) if isinstance(payload, dict) else []
            if not isinstance(comments, list) or not comments:
                continue
            return answer_id, [_map_comment(comment) for comment in comments if isinstance(comment, dict)]
        return "", []


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


def _map_search_entry(
    entry: dict[str, Any],
    *,
    collected_at: str,
    query: str,
) -> FeedItem | None:
    if str(entry.get("type", "")).strip() != "search_result":
        return None
    target = entry.get("object", {}) if isinstance(entry.get("object"), dict) else {}
    entity_type = str(target.get("type", "")).strip()
    source_item_id = str(target.get("id", "")).strip()
    canonical_url = _zhihu_web_url(entity_type, source_item_id, str(target.get("url", "")).strip())
    if entity_type not in {"answer", "question", "article"} or not source_item_id or not canonical_url:
        return None

    author = target.get("author", {}) if isinstance(target.get("author"), dict) else {}
    question = target.get("question", {}) if isinstance(target.get("question"), dict) else {}
    title = _resolve_search_title(target=target, question=question, entity_type=entity_type)
    excerpt = _resolve_search_excerpt(target)
    thumbnail_info = target.get("thumbnail_info", {}) if isinstance(target.get("thumbnail_info"), dict) else {}

    return FeedItem(
        schema_version="1",
        item_uid=f"zhihu:{entity_type}:{source_item_id}",
        source="zhihu",
        entity_type=entity_type,
        source_item_id=source_item_id,
        canonical_url=canonical_url,
        collection_channel="search",
        title=title,
        author=FeedAuthor(
            id=_string_or_none(author.get("id")) or _string_or_none(author.get("url_token")),
            name=str(author.get("name", "")).strip(),
            profile_url=_string_or_none(author.get("url")),
        ),
        published_at=_ts_to_iso(target.get("created_time") or target.get("updated_time")),
        collected_at=collected_at,
        lang="zh-CN",
        excerpt_text=excerpt,
        body_text="",
        transcript_text="",
        top_comments=[],
        topics=_question_topics(question) if entity_type == "question" else [],
        engagement={
            "view_count": _safe_int(target.get("visits_count")) or _safe_int(target.get("visited_count")),
            "like_count": _safe_int(target.get("voteup_count")),
            "comment_count": _safe_int(target.get("comment_count")),
            "share_count": None,
            "favorite_count": _safe_int(target.get("favorites_count")) or _safe_int(target.get("favorite_count")),
            "voteup_count": _safe_int(target.get("voteup_count")),
            "coin_count": None,
            "danmaku_count": None,
        },
        media={
            "has_video": False,
            "duration_seconds": None,
            "image_count": _safe_int(thumbnail_info.get("total_count")) or _safe_int(thumbnail_info.get("count")),
        },
        quality_flags={
            "has_transcript": False,
            "has_long_body": len(excerpt) >= 180,
            "is_recommendation": False,
            "is_from_following": False,
            "is_ad_suspected": False,
        },
        query_text=query,
    )


def _resolve_feed_title(target: dict[str, Any]) -> str:
    if str(target.get("type", "")).strip() == "answer":
        question = target.get("question", {})
        if isinstance(question, dict):
            return str(question.get("title", "") or question.get("name", "")).strip()
    return str(target.get("title", "") or target.get("name", "")).strip()


def _resolve_search_title(*, target: dict[str, Any], question: dict[str, Any], entity_type: str) -> str:
    if entity_type == "answer":
        return (
            str(question.get("title", "") or question.get("name", "")).strip()
            or str(target.get("title", "") or target.get("name", "")).strip()
        )
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


def _resolve_search_excerpt(target: dict[str, Any]) -> str:
    excerpt = (
        target.get("excerpt")
        or target.get("description")
        or target.get("content")
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
        question_detail_blocks = _normalize_content_blocks(question.get("content_blocks"))
        answer_sections = _question_answer_sections(answers)
        content_blocks = _combine_question_content_blocks(question=question, answers=answers)
        image_urls = _merge_image_urls(
            question.get("images"),
            *[
                answer.get("images")
                for answer in answers
                if isinstance(answer, dict)
            ],
            fallback.media.get("image_urls"),
        )
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
            query_text=fallback.query_text,
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
            media={
                **fallback.media,
                "image_count": len(image_urls) or fallback.media.get("image_count"),
                "image_urls": image_urls,
                "content_blocks": content_blocks or fallback.media.get("content_blocks"),
                "question_detail_blocks": question_detail_blocks or fallback.media.get("question_detail_blocks"),
                "answer_sections": answer_sections or fallback.media.get("answer_sections"),
                "default_answer_id": _question_default_answer_id(answer_sections) or fallback.media.get("default_answer_id"),
                "comment_answer_id": _question_comment_answer_id(answer_sections, top_comments) or fallback.media.get("comment_answer_id"),
            },
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
        content_blocks = _normalize_content_blocks(answer.get("content_blocks"))
        image_urls = _merge_image_urls(answer.get("images"), fallback.media.get("image_urls"))
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
            query_text=fallback.query_text,
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
            media={
                **fallback.media,
                "image_count": len(image_urls) or fallback.media.get("image_count"),
                "image_urls": image_urls,
                "content_blocks": content_blocks or fallback.media.get("content_blocks"),
            },
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
    content_blocks = _normalize_content_blocks(article.get("content_blocks"))
    image_urls = _merge_image_urls(article.get("images"), fallback.media.get("image_urls"))
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
        query_text=fallback.query_text,
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
        media={
            **fallback.media,
            "image_count": len(image_urls) or fallback.media.get("image_count"),
            "image_urls": image_urls,
            "content_blocks": content_blocks or fallback.media.get("content_blocks"),
        },
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


def _combine_question_answer_fallback_body(answers: list[Any]) -> str:
    parts: list[str] = []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        author = answer.get("author", {}) if isinstance(answer.get("author"), dict) else {}
        author_name = str(author.get("name", "")).strip()
        body = _html_to_text(answer.get("content") or answer.get("excerpt") or "")
        if not body:
            continue
        if author_name:
            parts.append(f"{author_name}:\n{body}")
        else:
            parts.append(body)
    return "\n\n".join(parts).strip()


def _combine_question_content_blocks(*, question: dict[str, Any], answers: list[Any]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    question_blocks = _normalize_content_blocks(question.get("content_blocks"))
    if question_blocks:
        blocks.extend(question_blocks)

    answer_blocks = _question_answer_content_blocks(answers)
    if answer_blocks:
        if blocks:
            blocks.append({"type": "text", "text": "------"})
        blocks.extend(answer_blocks)
    return blocks


def _question_answer_content_blocks(answers: list[Any]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for section in _question_answer_sections(answers):
        blocks.append({"type": "text", "text": str(section.get("heading", "")).strip()})
        for block in section.get("content_blocks", []):
            if isinstance(block, dict):
                blocks.append(block)
    return blocks


def _question_answer_sections(answers: list[Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for index, answer in enumerate(answers, start=1):
        if not isinstance(answer, dict):
            continue
        author = answer.get("author", {}) if isinstance(answer.get("author"), dict) else {}
        author_name = str(author.get("name", "")).strip()
        heading = f"回答 {index}"
        if author_name:
            heading = f"{heading} · {author_name}"

        normalized_blocks = _normalize_content_blocks(answer.get("content_blocks"))
        body = _html_to_text(answer.get("body") or answer.get("content") or answer.get("excerpt") or "")
        if not normalized_blocks and body:
            normalized_blocks = [{"type": "text", "text": body}]

        sections.append(
            {
                "answer_id": str(answer.get("id", "")).strip(),
                "author_name": author_name,
                "heading": heading,
                "body": body,
                "excerpt": _html_to_text(answer.get("excerpt") or answer.get("content") or answer.get("body") or "")[:800],
                "content_blocks": normalized_blocks,
                "comment_count": _safe_int(answer.get("comment_count")),
                "like_count": _safe_int(answer.get("voteup_count")),
                "canonical_url": _zhihu_web_url("answer", str(answer.get("id", "")).strip(), str(answer.get("url", "")).strip()),
            }
        )
    return sections


def _question_default_answer_id(answer_sections: list[dict[str, Any]]) -> str:
    for section in answer_sections:
        answer_id = str(section.get("answer_id", "")).strip()
        if answer_id:
            return answer_id
    return ""


def _question_comment_answer_id(answer_sections: list[dict[str, Any]], comments: list[FeedComment]) -> str:
    if not comments:
        return ""
    return _question_default_answer_id(answer_sections)


def _answer_fallback_excerpt(answer: dict[str, Any]) -> str:
    return _html_to_text(answer.get("excerpt") or answer.get("content") or "")[:1200]


def _question_reader_is_usable(item: FeedItem) -> bool:
    return bool(item.body_text.strip() or item.excerpt_text.strip() or item.top_comments)


def _comment_candidate_answers(answers: list[Any]) -> list[dict[str, Any]]:
    normalized = [answer for answer in answers if isinstance(answer, dict)]
    preferred = [
        answer for answer in normalized
        if (_safe_int(answer.get("comment_count")) or 0) > 0
    ]
    return preferred or normalized


def _merge_image_urls(*groups: Any) -> list[str]:
    urls: list[str] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        for item in group:
            text = str(item or "").strip()
            if text and text not in urls:
                urls.append(text)
    return urls


def _normalize_content_blocks(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        block_type = str(entry.get("type", "")).strip()
        if block_type == "text":
            text = str(entry.get("text", "")).strip()
            if text:
                blocks.append({"type": "text", "text": text})
            continue
        if block_type == "image":
            url = str(entry.get("url", "")).strip()
            if url:
                blocks.append({"type": "image", "url": url})
    return blocks


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


def _html_to_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    replacements = (
        (r"(?i)<br\s*/?>", "\n"),
        (r"(?i)</p\s*>", "\n\n"),
        (r"(?i)</div\s*>", "\n\n"),
        (r"(?i)</li\s*>", "\n"),
        (r"(?i)<li[^>]*>", "• "),
        (r"(?i)<hr[^>]*>", "\n\n"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    collapsed: list[str] = []
    last_blank = True
    for line in lines:
        if not line:
            if not last_blank:
                collapsed.append("")
            last_blank = True
            continue
        collapsed.append(line)
        last_blank = False
    return "\n".join(collapsed).strip()


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
