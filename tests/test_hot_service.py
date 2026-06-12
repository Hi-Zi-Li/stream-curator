from pathlib import Path

from stream_curator.config import Settings
from stream_curator.connectors.base import CollectedItem
from stream_curator.models.feed_item import FeedAuthor, FeedItem
from stream_curator.hot_service import (
    collect_hot_cards,
    create_store,
    get_hot_page_payload,
    refresh_hot_page_payload,
)
from stream_curator.push_store import PushCard


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        db_path=tmp_path / "hot.db",
        llm_chat_completions_url="https://example.com/v1/chat/completions",
        llm_api_key=None,
        llm_model="deepseek-v4-flash",
        llm_fallback_model="",
        llm_timeout_seconds=30,
        worker_poll_interval_seconds=30,
        bilibili_executable="bili",
        zhihu_executable="zhihu",
        xiaohongshu_executable="xhs",
    )


def _card(item_uid: str, *, title: str) -> PushCard:
    return PushCard(
        item_uid=item_uid,
        source="zhihu",
        title=title,
        summary=f"{title} summary",
        reason=f"{title} reason",
        canonical_url=f"https://example.com/{item_uid}",
        author_name="tester",
        excerpt=f"{title} excerpt",
        recommendation="worth_reading",
        tags=["AI"],
        reader_payload={
            "source": "zhihu",
            "entityType": "answer",
            "bodyText": f"{title} body",
            "transcriptText": "",
            "comments": [],
        },
    )


def _item(source: str, entity_type: str, source_item_id: str, title: str) -> FeedItem:
    canonical_url = f"https://example.com/{source}/{source_item_id}"
    return FeedItem(
        schema_version="1",
        item_uid=f"{source}:{entity_type}:{source_item_id}",
        source=source,
        entity_type=entity_type,
        source_item_id=source_item_id,
        canonical_url=canonical_url,
        collection_channel="hot",
        title=title,
        author=FeedAuthor(id="u1", name=f"{source}-author", profile_url=None),
        published_at=None,
        collected_at="2026-06-11T00:00:00+00:00",
        lang="zh-CN",
        excerpt_text=f"{title} excerpt",
        body_text=f"{title} body",
        transcript_text="" if source != "bilibili" else f"{title} subtitle",
        top_comments=[],
        topics=[source],
        engagement={
            "view_count": 100,
            "like_count": 20,
            "comment_count": 3,
            "share_count": 1,
            "favorite_count": 2,
            "voteup_count": 20,
            "coin_count": 1,
            "danmaku_count": 1,
        },
        media={
            "has_video": source == "bilibili",
            "duration_seconds": 120 if source == "bilibili" else None,
            "aid": 1 if source == "bilibili" else None,
            "cid": 2 if source == "bilibili" else None,
            "page_number": 1 if source == "bilibili" else None,
            "image_count": 1 if source != "bilibili" else None,
            "image_urls": [f"https://img.example.com/{source_item_id}.jpg"] if source != "bilibili" else [],
        },
        quality_flags={
            "has_transcript": source == "bilibili",
            "has_long_body": True,
            "is_recommendation": False,
            "is_from_following": False,
            "is_ad_suspected": False,
        },
    )


def test_get_hot_page_payload_builds_and_reuses_cache(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls = {"count": 0}

    monkeypatch.setattr(
        "stream_curator.hot_service.get_worker_process_status",
        lambda project_root: type("Status", (), {"running": False})(),
    )

    def _collect_hot_cards(**kwargs):
        calls["count"] += 1
        return (
            [_card("zhihu:answer:1", title="hot-card")],
            {"zhihu": 1},
            {},
        )

    monkeypatch.setattr("stream_curator.hot_service.collect_hot_cards", _collect_hot_cards)

    first = get_hot_page_payload(settings=settings, limit=6)
    second = get_hot_page_payload(settings=settings, limit=6)

    assert calls["count"] == 1
    assert first["page"] == "hot"
    assert first["meta"]["cacheStatus"] == "refreshed"
    assert second["meta"]["cacheStatus"] == "cached"
    assert first["items"][0]["title"] == "hot-card"
    assert second["items"][0]["title"] == "hot-card"


def test_refresh_hot_page_payload_forces_rebuild(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls = {"count": 0}

    monkeypatch.setattr(
        "stream_curator.hot_service.get_worker_process_status",
        lambda project_root: type("Status", (), {"running": True})(),
    )

    def _collect_hot_cards(**kwargs):
        calls["count"] += 1
        title = f"hot-card-{calls['count']}"
        return (
            [_card(f"zhihu:answer:{calls['count']}", title=title)],
            {"zhihu": 1},
            {},
        )

    monkeypatch.setattr("stream_curator.hot_service.collect_hot_cards", _collect_hot_cards)

    first = get_hot_page_payload(settings=settings, limit=6)
    refreshed = refresh_hot_page_payload(settings=settings, limit=6)

    assert calls["count"] == 2
    assert first["items"][0]["title"] == "hot-card-1"
    assert refreshed["items"][0]["title"] == "hot-card-2"
    assert refreshed["meta"]["cacheStatus"] == "refreshed"


def test_collect_hot_cards_hydrates_and_interleaves_sources(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = create_store(settings)

    class _BiliConnector:
        def __init__(self, runner, executable="bili"):
            self.source = "bilibili"

        def collect_hot(self, **kwargs):
            return [
                CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("bilibili", "video", "1", "b1")),
                CollectedItem(rank_in_batch=2, raw_payload={}, feed_item=_item("bilibili", "video", "2", "b2")),
            ]

        def hydrate_item(self, item):
            return item

    class _ZhihuConnector:
        def __init__(self, runner, executable="zhihu"):
            self.source = "zhihu"

        def collect_hot(self, **kwargs):
            return [
                CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("zhihu", "question", "1", "z1")),
                CollectedItem(rank_in_batch=2, raw_payload={}, feed_item=_item("zhihu", "question", "2", "z2")),
            ]

        def hydrate_item(self, item):
            return item

    class _XhsConnector:
        def __init__(self, runner, executable="xhs"):
            self.source = "xiaohongshu"

        def collect_hot(self, **kwargs):
            return [
                CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("xiaohongshu", "note", "1", "x1")),
                CollectedItem(rank_in_batch=2, raw_payload={}, feed_item=_item("xiaohongshu", "note", "2", "x2")),
            ]

        def hydrate_item(self, item):
            return item

    monkeypatch.setattr("stream_curator.hot_service.BilibiliConnector", _BiliConnector)
    monkeypatch.setattr("stream_curator.hot_service.ZhihuConnector", _ZhihuConnector)
    monkeypatch.setattr("stream_curator.hot_service.XiaohongshuConnector", _XhsConnector)

    cards, counts, errors = collect_hot_cards(settings=settings, store=store, source_limit=2)

    assert counts == {"bilibili": 2, "zhihu": 2, "xiaohongshu": 2}
    assert errors == {}
    assert [card.source for card in cards[:6]] == [
        "bilibili",
        "zhihu",
        "xiaohongshu",
        "bilibili",
        "zhihu",
        "xiaohongshu",
    ]
    assert cards[0].reader_payload["source"] == "bilibili"
    assert cards[1].reader_payload["entityType"] == "question"


def test_collect_hot_cards_keeps_shallow_card_when_hydrate_fails(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = create_store(settings)

    class _BiliConnector:
        def __init__(self, runner, executable="bili"):
            self.source = "bilibili"

        def collect_hot(self, **kwargs):
            return []

        def hydrate_item(self, item):
            return item

    class _ZhihuConnector:
        def __init__(self, runner, executable="zhihu"):
            self.source = "zhihu"

        def collect_hot(self, **kwargs):
            return [
                CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("zhihu", "question", "1", "z1")),
            ]

        def hydrate_item(self, item):
            raise RuntimeError("access denied")

    class _XhsConnector:
        def __init__(self, runner, executable="xhs"):
            self.source = "xiaohongshu"

        def collect_hot(self, **kwargs):
            return []

        def hydrate_item(self, item):
            return item

    monkeypatch.setattr("stream_curator.hot_service.BilibiliConnector", _BiliConnector)
    monkeypatch.setattr("stream_curator.hot_service.ZhihuConnector", _ZhihuConnector)
    monkeypatch.setattr("stream_curator.hot_service.XiaohongshuConnector", _XhsConnector)

    cards, counts, errors = collect_hot_cards(settings=settings, store=store, source_limit=1)

    assert counts == {"bilibili": 0, "zhihu": 1, "xiaohongshu": 0}
    assert errors == {}
    assert len(cards) == 1
    assert cards[0].source == "zhihu"
    assert cards[0].reader_payload["entityType"] == "question"


def test_get_hot_page_payload_repairs_sparse_zhihu_reader_payload(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = create_store(settings)
    store.save_hot_page(
        cards=[
            PushCard(
                item_uid="zhihu:question:1",
                source="zhihu",
                title="hot-question",
                summary="hot-question summary",
                reason="hot-question reason",
                canonical_url="https://www.zhihu.com/question/1",
                author_name="tester",
                excerpt="hot-question excerpt",
                recommendation="worth_reading",
                tags=["AI"],
                reader_payload={
                    "source": "zhihu",
                    "entityType": "question",
                    "sourceItemId": "1",
                    "canonicalUrl": "https://www.zhihu.com/question/1",
                    "title": "hot-question",
                    "authorName": "tester",
                    "publishedAt": None,
                    "topics": ["AI"],
                    "statsText": "",
                    "excerptText": "",
                    "bodyText": "legacy body",
                    "transcriptText": "",
                    "contentBlocks": [],
                    "questionAnswers": [],
                    "defaultAnswerId": "",
                    "comments": [{"authorName": "Old", "content": "legacy", "likeCount": 1}],
                    "media": {},
                    "engagement": {},
                },
            )
        ],
        meta={"sourceCounts": {"zhihu": 1}, "sourceErrors": {}, "itemCount": 1},
    )
    monkeypatch.setattr(
        "stream_curator.hot_service.get_worker_process_status",
        lambda project_root: type("Status", (), {"running": False})(),
    )
    monkeypatch.setattr(
        "stream_curator.hot_service._rehydrate_card_reader_payload",
        lambda **kwargs: {
            "source": "zhihu",
            "entityType": "question",
            "sourceItemId": "1",
            "canonicalUrl": "https://www.zhihu.com/question/1",
            "title": "hot-question",
            "authorName": "tester",
            "publishedAt": None,
            "topics": ["AI"],
            "statsText": "",
            "excerptText": "excerpt",
            "bodyText": "full body",
            "transcriptText": "",
            "contentBlocks": [],
            "questionAnswers": [
                {
                    "answerId": "a1",
                    "heading": "回答 1 · Bob",
                    "authorName": "Bob",
                    "bodyText": "answer body",
                    "excerptText": "answer excerpt",
                    "contentBlocks": [{"type": "text", "text": "answer body"}],
                    "commentCount": 3,
                    "likeCount": 5,
                    "canonicalUrl": "https://www.zhihu.com/answer/a1",
                }
            ],
            "defaultAnswerId": "a1",
            "comments": [{"authorName": "Bob", "content": "solid", "likeCount": 3}],
            "media": {},
            "engagement": {},
        },
    )

    payload = get_hot_page_payload(settings=settings, limit=6)

    assert payload["items"][0]["reader"]["bodyText"] == "full body"
    assert payload["items"][0]["reader"]["defaultAnswerId"] == "a1"
    assert payload["items"][0]["reader"]["questionAnswers"][0]["answerId"] == "a1"
    assert payload["items"][0]["reader"]["comments"][0]["content"] == "solid"
