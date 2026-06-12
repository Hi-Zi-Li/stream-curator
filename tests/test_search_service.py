from pathlib import Path

from stream_curator.config import Settings
from stream_curator.connectors.base import CollectedItem
from stream_curator.models.feed_item import FeedAuthor, FeedComment, FeedItem
from stream_curator.search_service import collect_search_items, create_store, get_search_page_payload, run_search_review


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        db_path=tmp_path / "search.db",
        llm_chat_completions_url="https://example.com/v1/chat/completions",
        llm_api_key="test-key",
        llm_model="deepseek-v4-flash",
        llm_fallback_model="",
        llm_timeout_seconds=30,
        worker_poll_interval_seconds=30,
        bilibili_executable="bili",
        zhihu_executable="zhihu",
        xiaohongshu_executable="xhs",
    )


def _item(source: str, entity_type: str, source_item_id: str, title: str, *, query: str) -> FeedItem:
    canonical_url = f"https://example.com/{source}/{source_item_id}"
    return FeedItem(
        schema_version="1",
        item_uid=f"{source}:{entity_type}:{source_item_id}",
        source=source,
        entity_type=entity_type,
        source_item_id=source_item_id,
        canonical_url=canonical_url,
        collection_channel="search",
        title=title,
        author=FeedAuthor(id="u1", name=f"{source}-author", profile_url=None),
        published_at="2026-06-12T00:00:00+00:00",
        collected_at="2026-06-12T00:00:00+00:00",
        lang="zh-CN",
        excerpt_text=f"{title} excerpt",
        body_text=f"{title} body",
        transcript_text="" if source != "bilibili" else f"{title} subtitle",
        top_comments=[FeedComment(author_name="reader", content="useful", like_count=3)],
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
            "page_number": 1,
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
        query_text=query,
    )


def test_collect_search_items_hydrates_and_interleaves(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class _BiliConnector:
        def __init__(self, runner, executable="bili"):
            self.source = "bilibili"

        def collect_search(self, **kwargs):
            return [
                CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("bilibili", "video", "1", "b1", query="AI")),
                CollectedItem(rank_in_batch=2, raw_payload={}, feed_item=_item("bilibili", "video", "2", "b2", query="AI")),
            ]

        def hydrate_item(self, item):
            return item

    class _ZhihuConnector:
        def __init__(self, runner, executable="zhihu"):
            self.source = "zhihu"

        def collect_search(self, **kwargs):
            return [
                CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("zhihu", "answer", "1", "z1", query="AI")),
                CollectedItem(rank_in_batch=2, raw_payload={}, feed_item=_item("zhihu", "answer", "2", "z2", query="AI")),
            ]

        def hydrate_item(self, item):
            return item

    class _XhsConnector:
        def __init__(self, runner, executable="xhs"):
            self.source = "xiaohongshu"

        def collect_search(self, **kwargs):
            return [
                CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("xiaohongshu", "note", "1", "x1", query="AI")),
                CollectedItem(rank_in_batch=2, raw_payload={}, feed_item=_item("xiaohongshu", "note", "2", "x2", query="AI")),
            ]

        def hydrate_item(self, item):
            return item

    monkeypatch.setattr("stream_curator.search_service.BilibiliConnector", _BiliConnector)
    monkeypatch.setattr("stream_curator.search_service.ZhihuConnector", _ZhihuConnector)
    monkeypatch.setattr("stream_curator.search_service.XiaohongshuConnector", _XhsConnector)

    items, counts, errors = collect_search_items(settings=settings, query="AI", limit=2)

    assert counts == {"bilibili": 2, "zhihu": 2, "xiaohongshu": 2}
    assert errors == {}
    assert [item.source for item in items[:6]] == [
        "bilibili",
        "zhihu",
        "xiaohongshu",
        "bilibili",
        "zhihu",
        "xiaohongshu",
    ]


def test_get_search_page_payload_builds_reader_items(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class _BiliConnector:
        def __init__(self, runner, executable="bili"):
            self.source = "bilibili"

        def collect_search(self, **kwargs):
            return [CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("bilibili", "video", "1", "b1", query="AI"))]

        def hydrate_item(self, item):
            return item

    class _ZhihuConnector:
        def __init__(self, runner, executable="zhihu"):
            self.source = "zhihu"

        def collect_search(self, **kwargs):
            return [CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("zhihu", "answer", "1", "z1", query="AI"))]

        def hydrate_item(self, item):
            return item

    class _XhsConnector:
        def __init__(self, runner, executable="xhs"):
            self.source = "xiaohongshu"

        def collect_search(self, **kwargs):
            return []

        def hydrate_item(self, item):
            return item

    monkeypatch.setattr("stream_curator.search_service.BilibiliConnector", _BiliConnector)
    monkeypatch.setattr("stream_curator.search_service.ZhihuConnector", _ZhihuConnector)
    monkeypatch.setattr("stream_curator.search_service.XiaohongshuConnector", _XhsConnector)

    payload = get_search_page_payload(settings=settings, query="AI", limit=1)

    assert payload["page"] == "search"
    assert payload["meta"]["query"] == "AI"
    assert payload["meta"]["itemCount"] == 2
    assert payload["items"][0]["reader"]["source"] in {"bilibili", "zhihu"}
    assert payload["items"][0]["recommendationLevel"] == "搜索"


def test_get_search_page_payload_handles_empty_query(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    payload = get_search_page_payload(settings=settings, query="   ", limit=1)

    assert payload["page"] == "search"
    assert payload["items"] == []
    assert payload["meta"]["statusText"] == "输入关键词开始搜索。"


def test_collect_search_items_reuses_hydrate_cache(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = create_store(settings)
    hydrate_calls = {"count": 0}

    class _BiliConnector:
        def __init__(self, runner, executable="bili"):
            self.source = "bilibili"

        def collect_search(self, **kwargs):
            return [CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("bilibili", "video", "1", "b1", query="AI"))]

        def hydrate_item(self, item):
            hydrate_calls["count"] += 1
            return item

    class _EmptyConnector:
        def __init__(self, runner, executable="noop"):
            self.source = "noop"

        def collect_search(self, **kwargs):
            return []

        def hydrate_item(self, item):
            return item

    monkeypatch.setattr("stream_curator.search_service.BilibiliConnector", _BiliConnector)
    monkeypatch.setattr("stream_curator.search_service.ZhihuConnector", _EmptyConnector)
    monkeypatch.setattr("stream_curator.search_service.XiaohongshuConnector", _EmptyConnector)

    first_items, _, _ = collect_search_items(settings=settings, query="AI", limit=1, store=store)
    second_items, _, _ = collect_search_items(settings=settings, query="AI", limit=1, store=store)

    assert len(first_items) == 1
    assert len(second_items) == 1
    assert hydrate_calls["count"] == 1


def test_run_search_review_filters_cached_search_results(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class _BiliConnector:
        def __init__(self, runner, executable="bili"):
            self.source = "bilibili"

        def collect_search(self, **kwargs):
            return [
                CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=_item("bilibili", "video", "1", "b1", query="AI")),
                CollectedItem(rank_in_batch=2, raw_payload={}, feed_item=_item("bilibili", "video", "2", "b2", query="AI")),
            ]

        def hydrate_item(self, item):
            return item

    class _EmptyConnector:
        def __init__(self, runner, executable="noop"):
            self.source = "noop"

        def collect_search(self, **kwargs):
            return []

        def hydrate_item(self, item):
            return item

    monkeypatch.setattr("stream_curator.search_service.BilibiliConnector", _BiliConnector)
    monkeypatch.setattr("stream_curator.search_service.ZhihuConnector", _EmptyConnector)
    monkeypatch.setattr("stream_curator.search_service.XiaohongshuConnector", _EmptyConnector)
    monkeypatch.setattr(
        "stream_curator.search_service._request_search_review",
        lambda **kwargs: {
            "summary": "结果主要集中在两个视频，其中第二条信息量更高，也更贴近查询主题。",
            "groups": [
                {
                    "title": "重点结果",
                    "summary": "第二条内容更完整，适合优先查看。",
                    "item_uids": ["bilibili:video:2"],
                }
            ],
            "kept_item_uids": ["bilibili:video:2"],
            "dropped_item_uids": ["bilibili:video:1"],
        },
    )

    initial_payload = get_search_page_payload(settings=settings, query="AI", limit=2)
    review = run_search_review(settings=settings, query="AI", limit=2)
    final_payload = get_search_page_payload(settings=settings, query="AI", limit=2)

    assert initial_payload["review"]["status"] == "pending"
    assert review["status"] == "completed"
    assert final_payload["review"]["status"] == "completed"
    assert final_payload["review"]["groups"][0]["title"] == "重点结果"
    assert [item["id"] for item in final_payload["items"]] == ["bilibili:video:2"]
