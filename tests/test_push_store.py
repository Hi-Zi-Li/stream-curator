from pathlib import Path

from stream_curator.push_store import PushCard, PushStore


def _card(item_uid: str, *, title: str) -> PushCard:
    return PushCard(
        item_uid=item_uid,
        source="zhihu",
        title=title,
        summary=f"{title} summary",
        reason=f"{title} reason",
        canonical_url=f"https://example.com/{item_uid}",
        author_name="tester",
        excerpt="excerpt",
        recommendation="must_read",
        tags=["AI"],
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
