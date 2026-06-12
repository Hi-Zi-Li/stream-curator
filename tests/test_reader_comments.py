from pathlib import Path

import pytest

from stream_curator.config import Settings
from stream_curator.push_store import PushStore
from stream_curator.reader_comments import fetch_reader_comments_page


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


def test_bilibili_comments_cooldown_short_circuits(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = PushStore(settings.db_path)
    store.bootstrap()
    store.set_source_cooldown(source="bilibili", action="comments", seconds=60)
    calls = {"count": 0}

    class _Runner:
        def __init__(self, timeout_seconds=45):
            pass

        def run(self, command):
            calls["count"] += 1
            raise AssertionError("runner should not be called while cooldown is active")

    monkeypatch.setattr("stream_curator.reader_comments.SubprocessRunner", _Runner)

    with pytest.raises(RuntimeError, match="冷却中"):
        fetch_reader_comments_page(
            settings=settings,
            source="bilibili",
            entity_type="video",
            source_item_id="BV1test",
            canonical_url="",
            cursor="1",
            limit=10,
        )

    assert calls["count"] == 0


def test_bilibili_comments_412_sets_cooldown(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class _Runner:
        def __init__(self, timeout_seconds=45):
            pass

        def run(self, command):
            raise RuntimeError("412 Precondition Failed bilibili security control policy")

    monkeypatch.setattr("stream_curator.reader_comments.SubprocessRunner", _Runner)

    with pytest.raises(RuntimeError, match="触发风控"):
        fetch_reader_comments_page(
            settings=settings,
            source="bilibili",
            entity_type="video",
            source_item_id="BV1test",
            canonical_url="",
            cursor="1",
            limit=10,
        )

    store = PushStore(settings.db_path)
    store.bootstrap()
    assert store.has_source_cooldown(source="bilibili", action="comments") is True


def test_bilibili_comments_success_maps_payload(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class _Result:
        def json(self):
            return {
                "data": {
                    "comments": [
                        {
                            "author": {"name": "tester"},
                            "message": "hello world",
                            "like": 7,
                        }
                    ],
                    "has_more": True,
                    "cursor": "1",
                    "next_cursor": "2",
                }
            }

    class _Runner:
        def __init__(self, timeout_seconds=45):
            pass

        def run(self, command):
            return _Result()

    monkeypatch.setattr("stream_curator.reader_comments.SubprocessRunner", _Runner)

    payload = fetch_reader_comments_page(
        settings=settings,
        source="bilibili",
        entity_type="video",
        source_item_id="BV1test",
        canonical_url="",
        cursor="1",
        limit=10,
    )

    assert payload["source"] == "bilibili"
    assert payload["cursor"] == "1"
    assert payload["nextCursor"] == "2"
    assert payload["hasMore"] is True
    assert payload["comments"] == [
        {
            "authorName": "tester",
            "content": "hello world",
            "likeCount": 7,
        }
    ]


def test_zhihu_question_comments_fall_back_to_top_answer(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    commands: list[list[str]] = []

    class _Result:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _Runner:
        def __init__(self, timeout_seconds=45):
            pass

        def run(self, command):
            commands.append(command)
            if command[:2] == ["zhihu", "answers"]:
                return _Result(
                    {
                        "data": [
                            {
                                "id": "2030224773230360120",
                            }
                        ]
                    }
                )
            if command[:3] == ["zhihu", "comments", "answer"]:
                return _Result(
                    {
                        "comments": [
                            {
                                "author": {"name": "Bob"},
                                "content": "solid",
                                "vote_count": 3,
                            }
                        ],
                        "cursor": "0",
                        "next_cursor": "5",
                        "has_more": True,
                    }
                )
            raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("stream_curator.reader_comments.SubprocessRunner", _Runner)

    payload = fetch_reader_comments_page(
        settings=settings,
        source="zhihu",
        entity_type="question",
        source_item_id="661955118",
        canonical_url="https://www.zhihu.com/question/661955118",
        cursor="0",
        limit=5,
    )

    assert commands[0][:2] == ["zhihu", "answers"]
    assert commands[1][:3] == ["zhihu", "comments", "answer"]
    assert payload["entityType"] == "question"
    assert payload["nextCursor"] == "5"
    assert payload["hasMore"] is True
    assert payload["comments"] == [
        {
            "authorName": "Bob",
            "content": "solid",
            "likeCount": 3,
        }
    ]
