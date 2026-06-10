"""Unified feed item model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class FeedAuthor:
    id: str | None
    name: str
    profile_url: str | None = None


@dataclass(slots=True)
class FeedComment:
    author_name: str
    content: str
    like_count: int | None = None


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
    media: dict[str, int | bool | None]
    quality_flags: dict[str, bool]
    query_text: str | None = None
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
