"""Base interfaces for source connectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import FeedItem


@dataclass(slots=True)
class CollectedItem:
    rank_in_batch: int | None
    raw_payload: dict[str, Any]
    feed_item: FeedItem


class BaseConnector:
    source: str

    def collect_feed(self, **kwargs: Any) -> list[CollectedItem]:
        raise NotImplementedError

    def hydrate_item(self, item: CollectedItem) -> CollectedItem:
        return item
