import sqlite3
from pathlib import Path

from stream_curator.push_store import PushCard, PushStore


def _card(item_uid: str, *, title: str, source: str = "zhihu") -> PushCard:
    return PushCard(
        item_uid=item_uid,
        source=source,
        title=title,
        summary=f"{title} summary",
        reason=f"{title} reason",
        canonical_url=f"https://example.com/{item_uid}",
        author_name="tester",
        excerpt="excerpt",
        recommendation="must_read",
        tags=["AI"],
        reader_payload={
            "source": "zhihu",
            "entityType": "answer",
            "bodyText": f"{title} full body",
            "transcriptText": "",
            "comments": [],
        },
    )


def test_push_store_current_ready_and_promote(tmp_path: Path) -> None:
    store = PushStore(tmp_path / "push.db")
    store.bootstrap()

    current = store.save_current_page(cards=[_card("a", title="current")], meta={"provider": "llm"})
    inserted = store.enqueue_ready_cards(
        [
            _card("b", title="ready-1"),
            _card("c", title="ready-2"),
        ]
    )

    assert inserted == 2
    assert store.load_current_page().page_id == current.page_id
    assert store.count_ready_cards() == 2
    assert store.list_active_item_uids() == {"a", "b", "c"}

    promoted = store.promote_next_ready_page(limit=2, meta={"provider": "llm"})

    assert promoted is not None
    assert [card.item_uid for card in promoted.cards] == ["b", "c"]
    assert store.load_current_page().page_id == promoted.page_id
    assert store.count_ready_cards() == 0
    assert promoted.cards[0].reader_payload["bodyText"] == "ready-1 full body"


def test_push_store_retry_candidates_are_deduped(tmp_path: Path) -> None:
    store = PushStore(tmp_path / "push.db")
    store.bootstrap()

    store.save_retry_candidates(
        [
            {"item_uid": "zhihu:1", "canonical_url": "https://example.com/1"},
            {"item_uid": "zhihu:1", "canonical_url": "https://example.com/1"},
            {"item_uid": "zhihu:2", "canonical_url": "https://example.com/2"},
        ]
    )

    retry = store.load_retry_candidates()

    assert [entry["item_uid"] for entry in retry] == ["zhihu:1", "zhihu:2"]


def test_push_store_reclaims_dead_process_lock(tmp_path: Path, monkeypatch) -> None:
    store = PushStore(tmp_path / "push.db")
    store.bootstrap()

    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO app_state (state_key, value_json, updated_at)
            VALUES (?, ?, ?)
            """,
            (
                "lock:push_generation",
                '{"owner":"dead-owner","owner_pid":99999,"expires_at":"2999-01-01T00:00:00+00:00"}',
                "2026-01-01T00:00:00+00:00",
            ),
        )

    monkeypatch.setattr("stream_curator.push_store._pid_is_running", lambda pid: False)

    acquired = store.try_acquire_lock(lock_name="push_generation", owner="new-owner", lease_seconds=60)

    assert acquired is True


def test_push_store_bootstrap_adds_reader_payload_column_to_old_db(tmp_path: Path) -> None:
    db_path = tmp_path / "push.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE ready_cards (
                queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_uid TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                reason TEXT NOT NULL,
                canonical_url TEXT NOT NULL,
                author_name TEXT NOT NULL,
                excerpt TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                enqueued_at TEXT NOT NULL
            );

            CREATE TABLE app_state (
                state_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO ready_cards (
                item_uid, source, title, summary, reason, canonical_url,
                author_name, excerpt, recommendation, tags_json, enqueued_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old:1",
                "zhihu",
                "old title",
                "old summary",
                "old reason",
                "https://example.com/old",
                "tester",
                "old excerpt",
                "must_read",
                '["AI"]',
                "2026-06-10T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    store = PushStore(db_path)
    store.bootstrap()

    assert store.count_ready_cards() == 0
    migrated = store.enqueue_ready_cards([_card("legacy", title="legacy")])
    promoted = store.promote_next_ready_page(limit=1)

    assert migrated == 1
    assert promoted is not None
    assert promoted.cards[0].reader_payload["bodyText"] == "legacy full body"


def test_push_store_promote_next_ready_page_mixes_second_source_when_available(tmp_path: Path) -> None:
    store = PushStore(tmp_path / "push.db")
    store.bootstrap()

    inserted = store.enqueue_ready_cards(
        [
            _card("b1", title="b1", source="bilibili"),
            _card("b2", title="b2", source="bilibili"),
            _card("b3", title="b3", source="bilibili"),
            _card("b4", title="b4", source="bilibili"),
            _card("b5", title="b5", source="bilibili"),
            _card("b6", title="b6", source="bilibili"),
            _card("z1", title="z1", source="zhihu"),
        ]
    )

    assert inserted == 7

    promoted = store.promote_next_ready_page(limit=6)

    assert promoted is not None
    assert [card.item_uid for card in promoted.cards] == ["b1", "b2", "b3", "b4", "b5", "z1"]
    assert {card.source for card in promoted.cards} == {"bilibili", "zhihu"}
    assert [card.item_uid for card in store.peek_ready_cards(limit=10)] == ["b6"]


def test_push_store_promote_next_ready_page_keeps_existing_mix_order(tmp_path: Path) -> None:
    store = PushStore(tmp_path / "push.db")
    store.bootstrap()

    inserted = store.enqueue_ready_cards(
        [
            _card("b1", title="b1", source="bilibili"),
            _card("z1", title="z1", source="zhihu"),
            _card("b2", title="b2", source="bilibili"),
            _card("b3", title="b3", source="bilibili"),
            _card("b4", title="b4", source="bilibili"),
            _card("b5", title="b5", source="bilibili"),
            _card("x1", title="x1", source="xiaohongshu"),
        ]
    )

    assert inserted == 7

    promoted = store.promote_next_ready_page(limit=6)

    assert promoted is not None
    assert [card.item_uid for card in promoted.cards] == ["b1", "z1", "b2", "b3", "b4", "b5"]
    assert [card.item_uid for card in store.peek_ready_cards(limit=10)] == ["x1"]
