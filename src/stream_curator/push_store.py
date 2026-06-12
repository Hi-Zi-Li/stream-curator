"""Minimal SQLite storage for the current page, ready queue, and retry candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import ctypes
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .models.feed_item import FeedItem
from .push_llm import PushCandidate


CURRENT_PAGE_KEY = "current_page"
HOT_PAGE_KEY = "hot_page"
LAST_FILL_META_KEY = "last_fill_meta"
RETRY_CANDIDATES_KEY = "retry_candidates"
READY_PAGE_LOOKAHEAD_MULTIPLIER = 4


@dataclass(slots=True)
class PushCard:
    item_uid: str
    source: str
    title: str
    summary: str
    reason: str
    canonical_url: str
    author_name: str
    excerpt: str
    recommendation: str
    tags: list[str]
    reader_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PushPage:
    page_id: str
    updated_at: str
    cards: list[PushCard]
    meta: dict[str, Any]


@dataclass(slots=True)
class CachedFeedItem:
    item: FeedItem
    updated_at: str


@dataclass(slots=True)
class SearchQueryCacheEntry:
    query: str
    payload: dict[str, Any]
    review: dict[str, Any]
    updated_at: str
    last_accessed_at: str


class PushStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path

    def bootstrap(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS ready_cards (
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
                    reader_payload_json TEXT NOT NULL DEFAULT '{}',
                    enqueued_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ready_cards_queue
                    ON ready_cards (queue_id ASC);

                CREATE TABLE IF NOT EXISTS hydrated_candidates (
                    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_uid TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author_name TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    stats_text TEXT NOT NULL,
                    reader_payload_json TEXT NOT NULL DEFAULT '{}',
                    enqueued_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hydrated_candidates_queue
                    ON hydrated_candidates (queue_id ASC);

                CREATE TABLE IF NOT EXISTS app_state (
                    state_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS search_item_cache (
                    item_uid TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    source_item_id TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_search_item_cache_access
                    ON search_item_cache (last_accessed_at ASC);

                CREATE TABLE IF NOT EXISTS search_query_cache (
                    normalized_query TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    review_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_search_query_cache_access
                    ON search_query_cache (last_accessed_at ASC);
                """
            )
            _ensure_column(
                conn,
                table_name="ready_cards",
                column_name="reader_payload_json",
                column_def="TEXT NOT NULL DEFAULT '{}'",
            )
            conn.execute(
                """
                DELETE FROM ready_cards
                WHERE reader_payload_json IS NULL
                   OR TRIM(reader_payload_json) = ''
                   OR TRIM(reader_payload_json) = '{}'
                """
            )
            conn.execute(
                """
                DELETE FROM hydrated_candidates
                WHERE reader_payload_json IS NULL
                   OR TRIM(reader_payload_json) = ''
                   OR TRIM(reader_payload_json) = '{}'
                """
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def load_current_page(self) -> PushPage | None:
        return self._load_page_state(CURRENT_PAGE_KEY)

    def save_current_page(self, *, cards: list[PushCard], meta: dict[str, Any] | None = None) -> PushPage:
        return self._save_page_state(CURRENT_PAGE_KEY, cards=cards, meta=meta)

    def replace_current_page(self, page: PushPage) -> None:
        self._replace_page_state(CURRENT_PAGE_KEY, page)

    def clear_current_page(self) -> None:
        self._delete_state(CURRENT_PAGE_KEY)

    def load_hot_page(self) -> PushPage | None:
        return self._load_page_state(HOT_PAGE_KEY)

    def save_hot_page(self, *, cards: list[PushCard], meta: dict[str, Any] | None = None) -> PushPage:
        return self._save_page_state(HOT_PAGE_KEY, cards=cards, meta=meta)

    def replace_hot_page(self, page: PushPage) -> None:
        self._replace_page_state(HOT_PAGE_KEY, page)

    def clear_hot_page(self) -> None:
        self._delete_state(HOT_PAGE_KEY)

    def load_search_item_cache(self, *, item_uid: str) -> CachedFeedItem | None:
        if not str(item_uid).strip():
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT item_json, updated_at
                FROM search_item_cache
                WHERE item_uid = ?
                """,
                (item_uid,),
            ).fetchone()
            if row is None:
                return None
            touched_at = _now_iso()
            conn.execute(
                """
                UPDATE search_item_cache
                SET last_accessed_at = ?
                WHERE item_uid = ?
                """,
                (touched_at, item_uid),
            )
        payload = _load_json(row["item_json"])
        if not isinstance(payload, dict):
            return None
        return CachedFeedItem(
            item=FeedItem.from_dict(payload),
            updated_at=str(row["updated_at"]),
        )

    def save_search_item_cache(self, *, item: FeedItem) -> None:
        if not item.item_uid.strip():
            return
        touched_at = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO search_item_cache (
                    item_uid, source, entity_type, source_item_id, canonical_url,
                    item_json, updated_at, last_accessed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_uid) DO UPDATE SET
                    source = excluded.source,
                    entity_type = excluded.entity_type,
                    source_item_id = excluded.source_item_id,
                    canonical_url = excluded.canonical_url,
                    item_json = excluded.item_json,
                    updated_at = excluded.updated_at,
                    last_accessed_at = excluded.last_accessed_at
                """,
                (
                    item.item_uid,
                    item.source,
                    item.entity_type,
                    item.source_item_id,
                    item.canonical_url,
                    json.dumps(item.to_dict(), ensure_ascii=False),
                    touched_at,
                    touched_at,
                ),
            )

    def load_search_query_cache(self, *, query: str) -> SearchQueryCacheEntry | None:
        normalized_query = _normalize_search_query(query)
        if not normalized_query:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json, review_json, updated_at, last_accessed_at
                FROM search_query_cache
                WHERE normalized_query = ?
                """,
                (normalized_query,),
            ).fetchone()
            if row is None:
                return None
            touched_at = _now_iso()
            conn.execute(
                """
                UPDATE search_query_cache
                SET last_accessed_at = ?
                WHERE normalized_query = ?
                """,
                (touched_at, normalized_query),
            )
        payload = _load_json(row["payload_json"])
        review = _load_json(row["review_json"])
        if not isinstance(payload, dict):
            return None
        return SearchQueryCacheEntry(
            query=normalized_query,
            payload=payload,
            review=review if isinstance(review, dict) else {},
            updated_at=str(row["updated_at"]),
            last_accessed_at=str(row["last_accessed_at"]),
        )

    def save_search_query_cache(
        self,
        *,
        query: str,
        payload: dict[str, Any],
        review: dict[str, Any] | None = None,
    ) -> None:
        normalized_query = _normalize_search_query(query)
        if not normalized_query:
            return
        touched_at = _now_iso()
        review_payload = review if isinstance(review, dict) else {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO search_query_cache (
                    normalized_query, payload_json, review_json, updated_at, last_accessed_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(normalized_query) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    review_json = excluded.review_json,
                    updated_at = excluded.updated_at,
                    last_accessed_at = excluded.last_accessed_at
                """,
                (
                    normalized_query,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(review_payload, ensure_ascii=False),
                    touched_at,
                    touched_at,
                ),
            )

    def save_search_query_review(self, *, query: str, review: dict[str, Any]) -> None:
        normalized_query = _normalize_search_query(query)
        if not normalized_query:
            return
        touched_at = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE search_query_cache
                SET review_json = ?,
                    last_accessed_at = ?
                WHERE normalized_query = ?
                """,
                (json.dumps(review, ensure_ascii=False), touched_at, normalized_query),
            )

    def prune_search_cache(self, *, max_queries: int, max_items: int) -> None:
        with self.connect() as conn:
            if max_queries > 0:
                conn.execute(
                    """
                    DELETE FROM search_query_cache
                    WHERE normalized_query IN (
                        SELECT normalized_query
                        FROM search_query_cache
                        ORDER BY last_accessed_at DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (max_queries,),
                )
            if max_items > 0:
                conn.execute(
                    """
                    DELETE FROM search_item_cache
                    WHERE item_uid IN (
                        SELECT item_uid
                        FROM search_item_cache
                        ORDER BY last_accessed_at DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (max_items,),
                )

    def count_ready_cards(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM ready_cards").fetchone()
        return int(row["c"]) if row else 0

    def count_hydrated_candidates(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM hydrated_candidates").fetchone()
        return int(row["c"]) if row else 0

    def enqueue_ready_cards(self, cards: list[PushCard]) -> int:
        inserted = 0
        with self.connect() as conn:
            for card in cards:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO ready_cards (
                        item_uid, source, title, summary, reason, canonical_url,
                        author_name, excerpt, recommendation, tags_json, reader_payload_json, enqueued_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card.item_uid,
                        card.source,
                        card.title,
                        card.summary,
                        card.reason,
                        card.canonical_url,
                        card.author_name,
                        card.excerpt,
                        card.recommendation,
                        json.dumps(card.tags, ensure_ascii=False),
                        json.dumps(card.reader_payload, ensure_ascii=False),
                        _now_iso(),
                    ),
                )
                inserted += int(cursor.rowcount or 0)
        return inserted

    def enqueue_hydrated_candidates(self, candidates: list[PushCandidate]) -> int:
        inserted = 0
        with self.connect() as conn:
            for candidate in candidates:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO hydrated_candidates (
                        item_uid, source, title, author_name, canonical_url,
                        excerpt, stats_text, reader_payload_json, enqueued_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.item_uid,
                        candidate.source,
                        candidate.title,
                        candidate.author_name,
                        candidate.canonical_url,
                        candidate.excerpt,
                        candidate.stats_text,
                        json.dumps(candidate.reader_payload, ensure_ascii=False),
                        _now_iso(),
                    ),
                )
                inserted += int(cursor.rowcount or 0)
        return inserted

    def peek_ready_cards(self, *, limit: int) -> list[PushCard]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM ready_cards
                ORDER BY queue_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_card(row) for row in rows]

    def peek_hydrated_candidates(self, *, limit: int) -> list[PushCandidate]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM hydrated_candidates
                ORDER BY queue_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_candidate(row) for row in rows]

    def replace_ready_card(self, card: PushCard) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE ready_cards
                SET source = ?,
                    title = ?,
                    summary = ?,
                    reason = ?,
                    canonical_url = ?,
                    author_name = ?,
                    excerpt = ?,
                    recommendation = ?,
                    tags_json = ?,
                    reader_payload_json = ?
                WHERE item_uid = ?
                """,
                (
                    card.source,
                    card.title,
                    card.summary,
                    card.reason,
                    card.canonical_url,
                    card.author_name,
                    card.excerpt,
                    card.recommendation,
                    json.dumps(card.tags, ensure_ascii=False),
                    json.dumps(card.reader_payload, ensure_ascii=False),
                    card.item_uid,
                ),
            )

    def replace_hydrated_candidate(self, candidate: PushCandidate) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE hydrated_candidates
                SET source = ?,
                    title = ?,
                    author_name = ?,
                    canonical_url = ?,
                    excerpt = ?,
                    stats_text = ?,
                    reader_payload_json = ?
                WHERE item_uid = ?
                """,
                (
                    candidate.source,
                    candidate.title,
                    candidate.author_name,
                    candidate.canonical_url,
                    candidate.excerpt,
                    candidate.stats_text,
                    json.dumps(candidate.reader_payload, ensure_ascii=False),
                    candidate.item_uid,
                ),
            )

    def promote_next_ready_page(self, *, limit: int, meta: dict[str, Any] | None = None) -> PushPage | None:
        cards = self._pop_next_ready_cards(limit=limit)
        if len(cards) < limit:
            return None
        return self.save_current_page(cards=cards, meta=meta or {})

    def list_active_item_uids(self) -> set[str]:
        active: set[str] = set()
        current = self.load_current_page()
        if current is not None:
            active.update(card.item_uid for card in current.cards)
        with self.connect() as conn:
            rows = conn.execute("SELECT item_uid FROM ready_cards").fetchall()
            hydrated_rows = conn.execute("SELECT item_uid FROM hydrated_candidates").fetchall()
        active.update(str(row["item_uid"]) for row in rows if str(row["item_uid"]).strip())
        active.update(str(row["item_uid"]) for row in hydrated_rows if str(row["item_uid"]).strip())
        return active

    def load_retry_candidates(self) -> list[dict[str, Any]]:
        payload = self._load_state(RETRY_CANDIDATES_KEY)
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if isinstance(entry, dict)]

    def save_retry_candidates(self, candidates: list[dict[str, Any]]) -> None:
        clean: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            item_uid = str(candidate.get("item_uid", "")).strip()
            if not item_uid or item_uid in seen:
                continue
            seen.add(item_uid)
            clean.append(candidate)
        self._save_state(RETRY_CANDIDATES_KEY, clean)

    def load_last_fill_meta(self) -> dict[str, Any]:
        payload = self._load_state(LAST_FILL_META_KEY)
        return payload if isinstance(payload, dict) else {}

    def save_last_fill_meta(self, meta: dict[str, Any]) -> None:
        self._save_state(LAST_FILL_META_KEY, meta)

    def has_source_cooldown(self, *, source: str, action: str) -> bool:
        state_key = _source_cooldown_key(source, action)
        payload = self._load_state(state_key)
        if not isinstance(payload, dict):
            return False
        until = str(payload.get("until", "")).strip()
        if until and until > _now_iso():
            return True
        self._delete_state(state_key)
        return False

    def set_source_cooldown(self, *, source: str, action: str, seconds: int) -> None:
        self._save_state(
            _source_cooldown_key(source, action),
            {"until": _iso_after_seconds(seconds)},
        )

    def clear_source_cooldown(self, *, source: str, action: str) -> None:
        self._delete_state(_source_cooldown_key(source, action))

    def try_acquire_lock(self, *, lock_name: str, owner: str, lease_seconds: int) -> bool:
        now_iso = _now_iso()
        expires_at = _iso_after_seconds(lease_seconds)
        state_key = _lock_key(lock_name)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value_json FROM app_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
            current = _load_json(row["value_json"]) if row else {}
            current_owner = str(current.get("owner", "")).strip()
            current_expires_at = str(current.get("expires_at", "")).strip()
            current_owner_pid = _safe_int(current.get("owner_pid"))
            lock_live = (
                current_owner
                and current_expires_at
                and current_expires_at > now_iso
                and current_owner != owner
                and _pid_is_running(current_owner_pid)
            )
            if lock_live:
                return False
            payload = {
                "owner": owner,
                "owner_pid": os.getpid(),
                "expires_at": expires_at,
            }
            conn.execute(
                """
                INSERT INTO app_state (state_key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (state_key, json.dumps(payload, ensure_ascii=False), now_iso),
            )
        return True

    def release_lock(self, *, lock_name: str, owner: str) -> None:
        state_key = _lock_key(lock_name)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM app_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
            if row is None:
                return
            payload = _load_json(row["value_json"])
            if str(payload.get("owner", "")).strip() != owner:
                return
            conn.execute("DELETE FROM app_state WHERE state_key = ?", (state_key,))

    def _pop_next_ready_cards(self, *, limit: int) -> list[PushCard]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lookahead = max(limit, limit * READY_PAGE_LOOKAHEAD_MULTIPLIER)
            rows = conn.execute(
                """
                SELECT *
                FROM ready_cards
                ORDER BY queue_id ASC
                LIMIT ?
                """,
                (lookahead,),
            ).fetchall()
            selected_rows = _select_ready_page_rows(rows, limit=limit)
            if len(selected_rows) < limit:
                return []
            queue_ids = [int(row["queue_id"]) for row in selected_rows]
            conn.executemany("DELETE FROM ready_cards WHERE queue_id = ?", [(queue_id,) for queue_id in queue_ids])
        return [_row_to_card(row) for row in selected_rows]

    def pop_hydrated_candidates(self, *, limit: int) -> list[PushCandidate]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT *
                FROM hydrated_candidates
                ORDER BY queue_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            if not rows:
                return []
            queue_ids = [int(row["queue_id"]) for row in rows]
            conn.executemany(
                "DELETE FROM hydrated_candidates WHERE queue_id = ?",
                [(queue_id,) for queue_id in queue_ids],
            )
        return [_row_to_candidate(row) for row in rows]

    def _load_state(self, state_key: str) -> Any:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM app_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
        if row is None:
            return None
        return _load_json(row["value_json"])

    def _save_state(self, state_key: str, payload: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state (state_key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (state_key, json.dumps(payload, ensure_ascii=False), _now_iso()),
            )

    def _delete_state(self, state_key: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM app_state WHERE state_key = ?", (state_key,))

    def _load_page_state(self, state_key: str) -> PushPage | None:
        payload = self._load_state(state_key)
        if not isinstance(payload, dict):
            return None
        cards_payload = payload.get("cards")
        if not isinstance(cards_payload, list) or not cards_payload:
            return None
        return PushPage(
            page_id=str(payload.get("page_id", "")) or f"page_{uuid.uuid4().hex}",
            updated_at=str(payload.get("updated_at", "")) or _now_iso(),
            cards=_cards_from_payload(cards_payload),
            meta=payload.get("meta", {}) if isinstance(payload.get("meta"), dict) else {},
        )

    def _save_page_state(
        self,
        state_key: str,
        *,
        cards: list[PushCard],
        meta: dict[str, Any] | None = None,
    ) -> PushPage:
        page = PushPage(
            page_id=f"page_{uuid.uuid4().hex}",
            updated_at=_now_iso(),
            cards=cards,
            meta=meta or {},
        )
        self._replace_page_state(state_key, page)
        return page

    def _replace_page_state(self, state_key: str, page: PushPage) -> None:
        self._save_state(
            state_key,
            {
                "page_id": page.page_id,
                "updated_at": page.updated_at,
                "cards": [card.to_dict() for card in page.cards],
                "meta": page.meta,
            },
        )


def _cards_from_payload(cards_payload: list[dict[str, Any]]) -> list[PushCard]:
    return [
        PushCard(
            item_uid=str(card.get("item_uid", "")),
            source=str(card.get("source", "")),
            title=str(card.get("title", "")),
            summary=str(card.get("summary", "")),
            reason=str(card.get("reason", "")),
            canonical_url=str(card.get("canonical_url", "")),
            author_name=str(card.get("author_name", "")),
            excerpt=str(card.get("excerpt", "")),
            recommendation=str(card.get("recommendation", "worth_reading")),
            tags=[str(tag) for tag in card.get("tags", []) if str(tag).strip()],
            reader_payload=card.get("reader_payload", {}) if isinstance(card.get("reader_payload"), dict) else {},
        )
        for card in cards_payload
        if isinstance(card, dict)
    ]


def _row_to_card(row: sqlite3.Row) -> PushCard:
    tags = _load_json(row["tags_json"])
    reader_payload = _load_json(row["reader_payload_json"])
    return PushCard(
        item_uid=str(row["item_uid"]),
        source=str(row["source"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        reason=str(row["reason"]),
        canonical_url=str(row["canonical_url"]),
        author_name=str(row["author_name"]),
        excerpt=str(row["excerpt"]),
        recommendation=str(row["recommendation"]),
        tags=[str(tag) for tag in tags] if isinstance(tags, list) else [],
        reader_payload=reader_payload if isinstance(reader_payload, dict) else {},
    )


def _row_to_candidate(row: sqlite3.Row) -> PushCandidate:
    reader_payload = _load_json(row["reader_payload_json"])
    return PushCandidate(
        item_uid=str(row["item_uid"]),
        source=str(row["source"]),
        title=str(row["title"]),
        author_name=str(row["author_name"]),
        canonical_url=str(row["canonical_url"]),
        excerpt=str(row["excerpt"]),
        stats_text=str(row["stats_text"]),
        reader_payload=reader_payload if isinstance(reader_payload, dict) else {},
    )


def _ensure_column(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    column_def: str,
) -> None:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {str(row["name"]) for row in rows}
    if column_name in existing:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def _load_json(value: object) -> Any:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return None


def _lock_key(lock_name: str) -> str:
    return f"lock:{lock_name}"


def _source_cooldown_key(source: str, action: str) -> str:
    return f"cooldown:{source}:{action}"


def _normalize_search_query(query: str) -> str:
    return str(query or "").strip().casefold()


def _select_ready_page_rows(rows: list[sqlite3.Row], *, limit: int) -> list[sqlite3.Row]:
    if len(rows) < limit:
        return []

    selected = list(rows[:limit])
    if len(_row_sources(selected)) >= 2:
        return selected

    primary_source = _row_source(selected[0])
    if not primary_source:
        return selected

    replacement = next(
        (row for row in rows[limit:] if _row_source(row) and _row_source(row) != primary_source),
        None,
    )
    if replacement is None:
        return selected

    for index in range(len(selected) - 1, -1, -1):
        if _row_source(selected[index]) == primary_source:
            selected[index] = replacement
            break
    return sorted(selected, key=lambda row: int(row["queue_id"]))


def _row_sources(rows: list[sqlite3.Row]) -> set[str]:
    return {_row_source(row) for row in rows if _row_source(row)}


def _row_source(row: sqlite3.Row) -> str:
    return str(row["source"]).strip()


def _pid_is_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        still_active = 259
        access = process_query_limited_information | synchronize

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        open_process.restype = ctypes.c_void_p

        get_exit_code_process = kernel32.GetExitCodeProcess
        get_exit_code_process.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_exit_code_process.restype = ctypes.c_int

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int

        handle = open_process(access, 0, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_uint32()
            if not get_exit_code_process(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            close_handle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _safe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_after_seconds(seconds: int) -> str:
    target = datetime.now(tz=UTC)
    if seconds > 0:
        from datetime import timedelta

        target = target + timedelta(seconds=seconds)
    return target.replace(microsecond=0).isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat(timespec="seconds")
