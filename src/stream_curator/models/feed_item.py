"""Unified feed item model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class FeedAuthor:
    id: str | None
    name: str
    profile_url: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedAuthor":
        return cls(
            id=str(payload.get("id")) if payload.get("id") is not None else None,
            name=str(payload.get("name", "")),
            profile_url=str(payload.get("profile_url")) if payload.get("profile_url") is not None else None,
        )


@dataclass(slots=True)
class FeedComment:
    author_name: str
    content: str
    like_count: int | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedComment":
        like_count = payload.get("like_count")
        return cls(
            author_name=str(payload.get("author_name", "")),
            content=str(payload.get("content", "")),
            like_count=int(like_count) if like_count is not None else None,
        )


@dataclass(slots=True)
class FeedItem:
    schema_version: str
    item_uid: str
    source: str
    entity_type: str
    source_item_id: str
    canonical_url: str
    collection_channel: str
    title: str
    author: FeedAuthor
    collected_at: str
    lang: str
    excerpt_text: str
    body_text: str
    transcript_text: str
    top_comments: list[FeedComment]
    topics: list[str]
    engagement: dict[str, int | None]
    media: dict[str, Any]
    quality_flags: dict[str, bool]
    query_text: str | None = None
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FeedItem":
        author_payload = payload.get("author", {})
        comments_payload = payload.get("top_comments", [])
        return cls(
            schema_version=str(payload.get("schema_version", "")),
            item_uid=str(payload.get("item_uid", "")),
            source=str(payload.get("source", "")),
            entity_type=str(payload.get("entity_type", "")),
            source_item_id=str(payload.get("source_item_id", "")),
            canonical_url=str(payload.get("canonical_url", "")),
            collection_channel=str(payload.get("collection_channel", "")),
            title=str(payload.get("title", "")),
            author=FeedAuthor.from_dict(author_payload if isinstance(author_payload, dict) else {}),
            collected_at=str(payload.get("collected_at", "")),
            lang=str(payload.get("lang", "")),
            excerpt_text=str(payload.get("excerpt_text", "")),
            body_text=str(payload.get("body_text", "")),
            transcript_text=str(payload.get("transcript_text", "")),
            top_comments=[
                FeedComment.from_dict(comment)
                for comment in comments_payload
                if isinstance(comment, dict)
            ],
            topics=[str(topic) for topic in payload.get("topics", []) if str(topic).strip()],
            engagement=payload.get("engagement", {}) if isinstance(payload.get("engagement"), dict) else {},
            media=payload.get("media", {}) if isinstance(payload.get("media"), dict) else {},
            quality_flags=payload.get("quality_flags", {}) if isinstance(payload.get("quality_flags"), dict) else {},
            query_text=str(payload.get("query_text")) if payload.get("query_text") is not None else None,
            published_at=str(payload.get("published_at")) if payload.get("published_at") is not None else None,
        )
