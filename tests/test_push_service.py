from pathlib import Path

from stream_curator.config import Settings
from stream_curator.connectors.base import CollectedItem
from stream_curator.models.feed_item import FeedAuthor, FeedComment, FeedItem
from stream_curator.push_llm import PushCandidate, PushCardDraft, PushSelectionResult
from stream_curator.push_service import (
    collect_push_candidates,
    create_store,
    fill_ready_queue_once,
    get_push_page_payload,
    refresh_push_page_payload,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        project_root=tmp_path,
        db_path=tmp_path / "push.db",
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


def _candidate(item_uid: str, *, title: str) -> PushCandidate:
    return PushCandidate(
        item_uid=item_uid,
        source="zhihu",
        title=title,
        author_name="tester",
        canonical_url=f"https://example.com/{item_uid}",
        excerpt=f"{title} excerpt",
        stats_text="赞同 12",
    )


def test_fill_ready_queue_only_enqueues_valid_cards_and_saves_retry(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = create_store(settings)
    first = _candidate("zhihu:answer:1", title="GPU training")
    second = _candidate("zhihu:answer:2", title="Agent workflow")

    monkeypatch.setattr(
        "stream_curator.push_service.collect_push_candidates",
        lambda settings, source_limit=20: ([first, second], {"zhihu": 2}, {}),
    )
    monkeypatch.setattr(
        "stream_curator.push_service.PushLlmClient.select_push_cards",
        lambda self, candidates, limit: PushSelectionResult(
            cards=[
                PushCardDraft(
                    item_uid=first.item_uid,
                    recommendation="must_read",
                    summary="这条回答拆解了 GPU 训练时最关键的瓶颈与取舍，适合快速补课。",
                    reason="训练问题讲得更透",
                    tags=["AI", "训练"],
                    is_valid=True,
                ),
                PushCardDraft(
                    item_uid=second.item_uid,
                    recommendation="worth_reading",
                    summary="Agent workflow",
                    reason="fallback",
                    tags=["AI"],
                    is_valid=False,
                ),
            ],
            provider="chat_completions",
            model="deepseek-v4-flash",
            used_fallback=False,
        ),
    )

    result = fill_ready_queue_once(settings=settings, store=store, select_limit=2)

    assert result.enqueued_count == 1
    assert result.retry_count == 1
    assert store.count_ready_cards() == 1
    assert [entry["item_uid"] for entry in store.load_retry_candidates()] == [second.item_uid]


def test_get_push_page_payload_promotes_from_ready_queue(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = create_store(settings)

    monkeypatch.setattr(
        "stream_curator.push_service.get_worker_process_status",
        lambda project_root: type("Status", (), {"running": False})(),
    )

    cards = []
    for index in range(6):
        cards.append(
            {
                "item_uid": f"zhihu:answer:{index}",
                "source": "zhihu",
                "title": f"title-{index}",
                "summary": f"summary-{index}",
                "reason": f"reason-{index}",
                "canonical_url": f"https://example.com/{index}",
                "author_name": "tester",
                "excerpt": "excerpt",
                "recommendation": "must_read",
                "tags": ["AI"],
            }
        )
    from stream_curator.push_store import PushCard

    store.enqueue_ready_cards([PushCard(**card) for card in cards])

    payload = get_push_page_payload(settings=settings, ensure_current=False, limit=6)

    assert len(payload["items"]) == 6
    assert payload["meta"]["cacheStatus"] == "promoted_ready_page"
    assert payload["meta"]["readyCount"] == 0


def test_refresh_keeps_current_page_when_no_ready_page(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = create_store(settings)

    monkeypatch.setattr(
        "stream_curator.push_service.get_worker_process_status",
        lambda project_root: type("Status", (), {"running": True})(),
    )

    from stream_curator.push_store import PushCard

    current = PushCard(
        item_uid="zhihu:answer:1",
        source="zhihu",
        title="current title",
        summary="current summary",
        reason="current reason",
        canonical_url="https://example.com/1",
        author_name="tester",
        excerpt="excerpt",
        recommendation="must_read",
        tags=["AI"],
    )
    store.save_current_page(cards=[current], meta={})

    payload = refresh_push_page_payload(settings=settings, limit=6)

    assert [item["id"] for item in payload["items"]] == ["zhihu:answer:1"]
    assert payload["meta"]["cacheStatus"] == "current_page"


def test_get_push_page_payload_can_fill_current_when_ensure_current(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    monkeypatch.setattr(
        "stream_curator.push_service.get_worker_process_status",
        lambda project_root: type("Status", (), {"running": False})(),
    )

    candidate = _candidate("zhihu:answer:1", title="GPU training")
    monkeypatch.setattr(
        "stream_curator.push_service.collect_push_candidates",
        lambda settings, source_limit=20: ([candidate], {"zhihu": 1}, {}),
    )
    monkeypatch.setattr(
        "stream_curator.push_service.PushLlmClient.select_push_cards",
        lambda self, candidates, limit: PushSelectionResult(
            cards=[
                PushCardDraft(
                    item_uid=candidate.item_uid,
                    recommendation="must_read",
                    summary="这条回答把训练资源、显存约束和调参思路放在一起讲清楚了。",
                    reason="适合快速补课",
                    tags=["AI", "训练"],
                    is_valid=True,
                )
            ],
            provider="chat_completions",
            model="deepseek-v4-flash",
            used_fallback=False,
        ),
    )

    payload = get_push_page_payload(settings=settings, ensure_current=True, limit=1)

    assert [item["id"] for item in payload["items"]] == [candidate.item_uid]
    assert payload["meta"]["cacheStatus"] == "filled_current_page"


def test_collect_push_candidates_uses_hydrated_text_for_llm_input(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    shallow = FeedItem(
        schema_version="1",
        item_uid="zhihu:answer:1",
        source="zhihu",
        entity_type="answer",
        source_item_id="1",
        canonical_url="https://example.com/answer/1",
        collection_channel="feed",
        title="Agent workflow",
        author=FeedAuthor(id="u1", name="tester", profile_url=None),
        collected_at="2026-06-10T00:00:00+00:00",
        lang="zh-CN",
        excerpt_text="shallow excerpt",
        body_text="",
        transcript_text="",
        top_comments=[],
        topics=[],
        engagement={
            "view_count": None,
            "like_count": None,
            "comment_count": None,
            "share_count": None,
            "favorite_count": None,
            "voteup_count": 12,
            "coin_count": None,
            "danmaku_count": None,
        },
        media={"has_video": False, "duration_seconds": None, "image_count": None},
        quality_flags={
            "has_transcript": False,
            "has_long_body": False,
            "is_recommendation": True,
            "is_from_following": False,
            "is_ad_suspected": False,
        },
    )
    hydrated = FeedItem(
        schema_version="1",
        item_uid=shallow.item_uid,
        source=shallow.source,
        entity_type=shallow.entity_type,
        source_item_id=shallow.source_item_id,
        canonical_url=shallow.canonical_url,
        collection_channel=shallow.collection_channel,
        title=shallow.title,
        author=shallow.author,
        collected_at=shallow.collected_at,
        lang=shallow.lang,
        excerpt_text="",
        body_text="full hydrated body",
        transcript_text="hydrated transcript",
        top_comments=[FeedComment(author_name="reader", content="useful comment", like_count=3)],
        topics=[],
        engagement=shallow.engagement,
        media=shallow.media,
        quality_flags={
            "has_transcript": True,
            "has_long_body": True,
            "is_recommendation": True,
            "is_from_following": False,
            "is_ad_suspected": False,
        },
        published_at=None,
    )

    class _HydratingConnector:
        def __init__(self, runner, executable="stub"):
            self.source = "zhihu"

        def collect_feed(self, **kwargs):
            return [CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=shallow)]

        def hydrate_item(self, item):
            return CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=hydrated)

    class _EmptyConnector:
        def __init__(self, runner, executable="stub"):
            self.source = "empty"

        def collect_feed(self, **kwargs):
            return []

        def hydrate_item(self, item):
            return item

    monkeypatch.setattr("stream_curator.push_service.BilibiliConnector", _EmptyConnector)
    monkeypatch.setattr("stream_curator.push_service.XiaohongshuConnector", _EmptyConnector)
    monkeypatch.setattr("stream_curator.push_service.ZhihuConnector", _HydratingConnector)

    candidates, counts, errors = collect_push_candidates(settings=settings, source_limit=1)

    assert counts == {"bilibili": 0, "zhihu": 1, "xiaohongshu": 0}
    assert errors == {}
    assert len(candidates) == 1
    assert candidates[0].excerpt.startswith("full hydrated body")
    assert "hydrated transcript" in candidates[0].excerpt
    assert "useful comment" in candidates[0].excerpt


def test_collect_push_candidates_drops_item_when_hydrate_fails(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    shallow = FeedItem(
        schema_version="1",
        item_uid="zhihu:answer:1",
        source="zhihu",
        entity_type="answer",
        source_item_id="1",
        canonical_url="https://example.com/answer/1",
        collection_channel="feed",
        title="Agent workflow",
        author=FeedAuthor(id="u1", name="tester", profile_url=None),
        collected_at="2026-06-10T00:00:00+00:00",
        lang="zh-CN",
        excerpt_text="shallow excerpt",
        body_text="",
        transcript_text="",
        top_comments=[],
        topics=[],
        engagement={
            "view_count": None,
            "like_count": None,
            "comment_count": None,
            "share_count": None,
            "favorite_count": None,
            "voteup_count": 12,
            "coin_count": None,
            "danmaku_count": None,
        },
        media={"has_video": False, "duration_seconds": None, "image_count": None},
        quality_flags={
            "has_transcript": False,
            "has_long_body": False,
            "is_recommendation": True,
            "is_from_following": False,
            "is_ad_suspected": False,
        },
    )

    class _FailingHydrateConnector:
        def __init__(self, runner, executable="stub"):
            self.source = "zhihu"

        def collect_feed(self, **kwargs):
            return [CollectedItem(rank_in_batch=1, raw_payload={}, feed_item=shallow)]

        def hydrate_item(self, item):
            raise RuntimeError("hydrate failed")

    class _EmptyConnector:
        def __init__(self, runner, executable="stub"):
            self.source = "empty"

        def collect_feed(self, **kwargs):
            return []

        def hydrate_item(self, item):
            return item

    monkeypatch.setattr("stream_curator.push_service.BilibiliConnector", _EmptyConnector)
    monkeypatch.setattr("stream_curator.push_service.XiaohongshuConnector", _EmptyConnector)
    monkeypatch.setattr("stream_curator.push_service.ZhihuConnector", _FailingHydrateConnector)

    candidates, counts, errors = collect_push_candidates(settings=settings, source_limit=1)

    assert counts == {"bilibili": 0, "zhihu": 1, "xiaohongshu": 0}
    assert errors == {}
    assert candidates == []
