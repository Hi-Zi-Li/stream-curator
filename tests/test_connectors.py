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


def test_bilibili_hot_uses_hot_channel() -> None:
    payload = {
        "data": {
            "items": [
                {
                    "bvid": "BV1hot1",
                    "title": "Hot AI roundup",
                    "description": "Trending now.",
                    "url": "https://www.bilibili.com/video/BV1hot1",
                    "duration_seconds": 210,
                    "owner": {"id": "99", "name": "hotter"},
                    "stats": {"view": 100, "like": 9, "coin": 1, "favorite": 2, "share": 3, "danmaku": 4},
                }
            ]
        }
    }
    runner = _FakeRunner([payload])
    connector = BilibiliConnector(runner, executable="bili")

    items = connector.collect_hot(limit=1)

    assert len(items) == 1
    assert runner.commands[0][:2] == ["bili", "hot"]
    assert items[0].feed_item.collection_channel == "hot"


def test_bilibili_search_uses_video_search_and_marks_query() -> None:
    payload = {
        "ok": True,
        "schema_version": "1",
        "data": [
            {
                "id": "BV1search1",
                "bvid": "BV1search1",
                "title": "AI infra tutorial",
                "author": "tester",
                "play": 321,
                "duration": "6:17",
            }
        ],
    }
    runner = _FakeRunner([payload])
    connector = BilibiliConnector(runner, executable="bili")

    items = connector.collect_search(query="AI", limit=1)

    assert runner.commands[0][:3] == ["bili", "search", "AI"]
    assert items[0].feed_item.collection_channel == "search"
    assert items[0].feed_item.query_text == "AI"
    assert items[0].feed_item.source_item_id == "BV1search1"


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


def test_zhihu_hot_maps_question_channel() -> None:
    payload = {
        "data": [
            {
                "question": {
                    "url": "https://www.zhihu.com/question/661955118",
                    "created": 1721302922,
                    "title": "How to evaluate this hot question?",
                    "type": "question",
                    "id": "661955118",
                    "topics": [{"name": "AI"}],
                    "creator": {"url_token": "alice", "name": "alice"},
                },
                "reaction": {
                    "pv": 11138660,
                    "follow_num": 2810,
                    "upvote_num": 126879,
                    "text": "1113 万热度",
                },
            }
        ]
    }
    runner = _FakeRunner([payload])
    connector = ZhihuConnector(runner, executable="zhihu")

    items = connector.collect_hot(limit=1)

    assert len(items) == 1
    assert runner.commands[0][:2] == ["zhihu", "hot"]
    assert items[0].feed_item.item_uid == "zhihu:question:661955118"
    assert items[0].feed_item.collection_channel == "hot"
    assert items[0].feed_item.excerpt_text == "1113 万热度"


def test_zhihu_search_filters_ads_and_maps_answer() -> None:
    payload = {
        "data": [
            {"type": "knowledge_ad", "object": {"title": "ad"}},
            {
                "type": "search_result",
                "object": {
                    "id": "2030224773230360120",
                    "type": "answer",
                    "title": "How to evaluate SkVM?",
                    "excerpt": "<p>Skill compiler discussion.</p>",
                    "url": "https://api.zhihu.com/answers/2030224773230360120",
                    "voteup_count": 321,
                    "comment_count": 12,
                    "favorites_count": 88,
                    "created_time": 1776823305,
                    "question": {
                        "id": "2030224013700678490",
                        "title": "How to evaluate SkVM?",
                    },
                    "author": {
                        "id": "alice",
                        "name": "alice",
                        "url": "https://api.zhihu.com/people/alice",
                    },
                },
            },
        ]
    }
    runner = _FakeRunner([payload])
    connector = ZhihuConnector(runner, executable="zhihu")

    items = connector.collect_search(query="SkVM", limit=1)

    assert runner.commands[0][:3] == ["zhihu", "search", "SkVM"]
    assert len(items) == 1
    assert items[0].feed_item.collection_channel == "search"
    assert items[0].feed_item.query_text == "SkVM"
    assert items[0].feed_item.item_uid == "zhihu:answer:2030224773230360120"


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


def test_zhihu_question_hydrate_falls_back_to_answers_and_comments() -> None:
    hot_payload = {
        "data": [
            {
                "question": {
                    "url": "https://www.zhihu.com/question/661955118",
                    "created": 1721302922,
                    "title": "How to evaluate this hot question?",
                    "type": "question",
                    "id": "661955118",
                    "topics": [{"name": "AI"}],
                    "creator": {"url_token": "alice", "name": "alice"},
                },
                "reaction": {"pv": 11138660, "follow_num": 2810, "upvote_num": 126879},
            }
        ]
    }
    answers_payload = {
        "data": [
            {
                "id": "2030224773230360120",
                "excerpt": "<p>Skill compiler discussion.</p>",
                "content": "<p>First paragraph.</p><p>Second paragraph.</p>",
                "author": {"name": "alice"},
            }
        ]
    }
    comments_payload = {
        "comments": [
            {"author": {"name": "Bob"}, "content": "solid", "vote_count": 3},
        ]
    }

    class _Runner:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def run(self, command: list[str]) -> CommandResult:
            self.commands.append(command)
            import json

            if command[:2] == ["zhihu", "hot"]:
                payload = hot_payload
            elif command[:2] == ["zhihu", "hydrate"]:
                raise RuntimeError("Command failed (1): zhihu hydrate question 661955118")
            elif command[:2] == ["zhihu", "answers"]:
                payload = answers_payload
            elif command[:3] == ["zhihu", "comments", "answer"]:
                payload = comments_payload
            else:
                raise AssertionError(f"unexpected command: {command}")
            return CommandResult(
                command=command,
                stdout=json.dumps(payload, ensure_ascii=False),
                stderr="",
                returncode=0,
            )

    runner = _Runner()
    connector = ZhihuConnector(runner, executable="zhihu")

    item = connector.collect_hot(limit=1)[0]
    hydrated = connector.hydrate_item(item)

    assert hydrated.feed_item.body_text == "alice:\nFirst paragraph.\n\nSecond paragraph."
    assert hydrated.feed_item.top_comments[0].content == "solid"
    assert hydrated.feed_item.media["content_blocks"][0]["text"] == "回答 1 · alice"
    assert hydrated.feed_item.media["content_blocks"][1]["text"] == "First paragraph.\n\nSecond paragraph."
    assert runner.commands[1][:2] == ["zhihu", "hydrate"]
    assert runner.commands[2][:2] == ["zhihu", "answers"]
    assert runner.commands[3][:3] == ["zhihu", "comments", "answer"]


def test_zhihu_question_hydrate_includes_multiple_answers_in_content_blocks() -> None:
    hot_payload = {
        "data": [
            {
                "question": {
                    "url": "https://www.zhihu.com/question/661955118",
                    "created": 1721302922,
                    "title": "How to evaluate this hot question?",
                    "type": "question",
                    "id": "661955118",
                    "topics": [{"name": "AI"}],
                    "creator": {"url_token": "alice", "name": "alice"},
                },
                "reaction": {"pv": 11138660, "follow_num": 2810, "upvote_num": 126879},
            }
        ]
    }
    hydrate_payload = {
        "mode": "hydrate",
        "entity_type": "question",
        "question": {
            "id": "661955118",
            "title": "How to evaluate this hot question?",
            "detail": "question detail",
            "visit_count": 123,
            "comment_count": 4,
            "url": "https://www.zhihu.com/question/661955118",
            "images": [],
            "content_blocks": [{"type": "text", "text": "question detail"}],
            "topics": ["AI"],
        },
        "answers": [
            {
                "id": "a1",
                "body": "first answer body",
                "excerpt": "first answer body",
                "author": {"name": "alice"},
                "content_blocks": [{"type": "text", "text": "first answer body"}],
                "images": [],
            },
            {
                "id": "a2",
                "body": "second answer body",
                "excerpt": "second answer body",
                "author": {"name": "bob"},
                "content_blocks": [{"type": "text", "text": "second answer body"}],
                "images": [],
            },
        ],
        "comments": [],
        "warnings": [],
    }
    runner = _FakeRunner([hot_payload, hydrate_payload])
    connector = ZhihuConnector(runner, executable="zhihu")

    item = connector.collect_hot(limit=1)[0]
    hydrated = connector.hydrate_item(item)
    blocks = hydrated.feed_item.media["content_blocks"]

    assert blocks[0]["text"] == "question detail"
    assert blocks[1]["text"] == "------"
    assert blocks[2]["text"] == "回答 1 · alice"
    assert blocks[3]["text"] == "first answer body"
    assert blocks[4]["text"] == "回答 2 · bob"
    assert blocks[5]["text"] == "second answer body"


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


def test_xiaohongshu_hot_uses_hot_channel() -> None:
    payload = {
        "data": {
            "items": [
                _xhs_note("1", "A"),
                _xhs_note("2", "B"),
            ]
        }
    }
    runner = _FakeRunner([payload])
    connector = XiaohongshuConnector(runner, executable="xhs")

    items = connector.collect_hot(limit=1)

    assert [item.feed_item.source_item_id for item in items] == ["1"]
    assert runner.commands[0][:2] == ["xhs", "hot"]
    assert all(item.feed_item.collection_channel == "hot" for item in items)


def test_xiaohongshu_search_uses_keyword_search() -> None:
    payload = {
        "data": {
            "items": [
                _xhs_note("1", "AI note"),
                {"model_type": "hot_query"},
            ]
        }
    }
    runner = _FakeRunner([payload])
    connector = XiaohongshuConnector(runner, executable="xhs")

    items = connector.collect_search(query="AI", limit=1)

    assert runner.commands[0][:3] == ["xhs", "search", "AI"]
    assert len(items) == 1
    assert items[0].feed_item.collection_channel == "search"
    assert items[0].feed_item.query_text == "AI"


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
