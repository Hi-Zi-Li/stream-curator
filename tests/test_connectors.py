from stream_curator.connectors.bilibili import BilibiliConnector
from stream_curator.connectors.subprocess import CommandResult
from stream_curator.connectors.xiaohongshu import XiaohongshuConnector, _compact_count_to_int
from stream_curator.connectors.zhihu import ZhihuConnector


class _FakeRunner:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> CommandResult:
        self.commands.append(command)
        payload = self.payloads[len(self.commands) - 1]
        import json

        return CommandResult(
            command=command,
            stdout=json.dumps(payload, ensure_ascii=False),
            stderr="",
            returncode=0,
        )


def test_xiaohongshu_compact_count_to_int() -> None:
    assert _compact_count_to_int("39") == 39
    assert _compact_count_to_int("2w+") == 20000
    assert _compact_count_to_int("") is None


def test_bilibili_feed_uses_recommend_and_marks_recommendation() -> None:
    payload = {
        "data": {
            "items": [
                {
                    "bvid": "BV1feed1",
                    "title": "AI infra weekly",
                    "description": "Dense infra roundup.",
                    "url": "https://www.bilibili.com/video/BV1feed1",
                    "duration_seconds": 321,
                    "owner": {"id": "42", "name": "tester"},
                    "stats": {
                        "view": 1200,
                        "danmaku": 12,
                        "like": 66,
                        "coin": 9,
                        "favorite": 18,
                        "share": 3,
                    },
                }
            ]
        }
    }
    runner = _FakeRunner([payload])
    connector = BilibiliConnector(runner, executable="bili")

    items = connector.collect_feed(limit=1)

    assert len(items) == 1
    assert runner.commands[0][:2] == ["bili", "recommend"]
    assert items[0].feed_item.collection_channel == "feed"
    assert items[0].feed_item.quality_flags["is_recommendation"] is True


def test_bilibili_hydrate_adds_transcript_and_comments() -> None:
    feed_payload = {
        "data": {
            "items": [
                {
                    "bvid": "BV1feed1",
                    "title": "AI infra weekly",
                    "description": "Dense infra roundup.",
                    "url": "https://www.bilibili.com/video/BV1feed1",
                    "duration_seconds": 321,
                    "owner": {"id": "42", "name": "tester"},
                    "stats": {"view": 1200, "like": 66, "coin": 9, "favorite": 18, "share": 3, "danmaku": 12},
                }
            ]
        }
    }
    hydrate_payload = {
        "data": {
            "mode": "hydrate",
            "video": {
                "bvid": "BV1feed1",
                "title": "AI infra weekly",
                "description": "Full description",
                "url": "https://www.bilibili.com/video/BV1feed1",
                "duration_seconds": 321,
                "owner": {"id": "42", "name": "tester"},
                "stats": {"view": 1200, "like": 66, "coin": 9, "favorite": 18, "share": 3, "danmaku": 12},
            },
            "subtitle": {"available": True, "text": "subtitle text"},
            "comments": [{"author": {"name": "Alice"}, "message": "great", "like": 5}],
            "related": [],
            "warnings": [],
        }
    }
    runner = _FakeRunner([feed_payload, hydrate_payload])
    connector = BilibiliConnector(runner, executable="bili")

    item = connector.collect_feed(limit=1)[0]
    hydrated = connector.hydrate_item(item)

    assert runner.commands[1][:2] == ["bili", "hydrate"]
    assert hydrated.feed_item.transcript_text == "subtitle text"
    assert hydrated.feed_item.top_comments[0].content == "great"


def test_zhihu_feed_maps_answer_and_marks_recommendation() -> None:
    feed_entry = {
        "type": "feed",
        "target": {
            "type": "answer",
            "id": "2030224773230360120",
            "url": "https://api.zhihu.com/answers/2030224773230360120",
            "created_time": 1776823305,
            "excerpt": "<p>Skill compiler discussion.</p>",
            "visited_count": 12345,
            "voteup_count": 321,
            "comment_count": 12,
            "favorite_count": 88,
            "author": {
                "id": "alice",
                "name": "alice",
                "url": "https://api.zhihu.com/people/alice",
            },
            "question": {
                "id": "2030224013700678490",
                "title": "How to evaluate SkVM?",
                "created": 1776823124,
            },
        },
    }
    payload = {
        "data": [
            dict(feed_entry),
            dict(feed_entry),
            dict(feed_entry),
        ]
    }
    runner = _FakeRunner([payload])
    connector = ZhihuConnector(runner, executable="zhihu")

    items = connector.collect_feed(limit=2)

    assert len(items) == 2
    assert runner.commands[0][:2] == ["zhihu", "feed"]
    assert items[0].feed_item.item_uid == "zhihu:answer:2030224773230360120"
    assert items[0].feed_item.canonical_url == "https://www.zhihu.com/answer/2030224773230360120"
    assert items[0].feed_item.title == "How to evaluate SkVM?"
    assert items[0].feed_item.excerpt_text == "Skill compiler discussion."
    assert items[0].feed_item.collection_channel == "feed"
    assert items[0].feed_item.quality_flags["is_recommendation"] is True


def test_zhihu_hydrate_adds_body_and_comments() -> None:
    feed_payload = {
        "data": [
            {
                "type": "feed",
                "target": {
                    "type": "answer",
                    "id": "2030224773230360120",
                    "url": "https://api.zhihu.com/answers/2030224773230360120",
                    "created_time": 1776823305,
                    "excerpt": "<p>Skill compiler discussion.</p>",
                    "visited_count": 12345,
                    "voteup_count": 321,
                    "comment_count": 12,
                    "favorite_count": 88,
                    "author": {"id": "alice", "name": "alice"},
                    "question": {"id": "2030224013700678490", "title": "How to evaluate SkVM?"},
                },
            }
        ]
    }
    hydrate_payload = {
        "mode": "hydrate",
        "entity_type": "answer",
        "answer": {
            "id": "2030224773230360120",
            "title": "How to evaluate SkVM?",
            "body": "Long answer body",
            "excerpt": "Skill compiler discussion.",
            "author": {"id": "alice", "name": "alice"},
            "voteup_count": 321,
            "comment_count": 12,
            "url": "https://www.zhihu.com/answer/2030224773230360120",
        },
        "comments": [{"author": {"name": "Bob"}, "content": "solid", "vote_count": 3}],
        "warnings": [],
    }
    runner = _FakeRunner([feed_payload, hydrate_payload])
    connector = ZhihuConnector(runner, executable="zhihu")

    item = connector.collect_feed(limit=1)[0]
    hydrated = connector.hydrate_item(item)

    assert runner.commands[1][:2] == ["zhihu", "hydrate"]
    assert hydrated.feed_item.body_text == "Long answer body"
    assert hydrated.feed_item.top_comments[0].content == "solid"


def test_xiaohongshu_feed_respects_limit_and_marks_recommendation() -> None:
    payload = {
        "data": {
            "items": [
                _xhs_note("1", "A"),
                _xhs_note("2", "B"),
                _xhs_note("3", "C"),
            ]
        }
    }
    runner = _FakeRunner([payload])
    connector = XiaohongshuConnector(runner, executable="xhs")

    items = connector.collect_feed(limit=2)

    assert [item.feed_item.source_item_id for item in items] == ["1", "2"]
    assert runner.commands[0][:2] == ["xhs", "feed"]
    assert all(item.feed_item.quality_flags["is_recommendation"] for item in items)


def test_xiaohongshu_hydrate_adds_body_and_comments() -> None:
    feed_payload = {
        "data": {
            "items": [_xhs_note("1", "A")]
        }
    }
    hydrate_payload = {
        "data": {
            "mode": "hydrate",
            "note": {
                "id": "1",
                "url": "https://www.xiaohongshu.com/explore/1",
                "title": "A",
                "body": "full note body",
                "author": {"id": "user-1", "name": "tester"},
                "note_type": "image",
                "liked_count": 1,
                "collected_count": 4,
                "comment_count": 2,
                "share_count": 3,
                "tags": ["AI"],
                "image_count": 1,
            },
            "comments": [{"nickname": "reader", "content": "nice", "like_count": 2}],
            "warnings": [],
        }
    }
    runner = _FakeRunner([feed_payload, hydrate_payload])
    connector = XiaohongshuConnector(runner, executable="xhs")

    item = connector.collect_feed(limit=1)[0]
    hydrated = connector.hydrate_item(item)

    assert runner.commands[1][:2] == ["xhs", "hydrate"]
    assert hydrated.feed_item.body_text == "full note body"
    assert hydrated.feed_item.top_comments[0].content == "nice"


def _xhs_note(note_id: str, title: str) -> dict:
    return {
        "id": note_id,
        "model_type": "note",
        "xsec_token": "",
        "note_card": {
            "display_title": title,
            "type": "normal",
            "user": {
                "user_id": f"user-{note_id}",
                "nickname": "tester",
            },
            "interact_info": {
                "liked_count": "1",
                "comment_count": "2",
                "shared_count": "3",
                "collected_count": "4",
            },
            "image_list": [],
            "cover": {},
        },
    }
