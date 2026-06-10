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


CURRENT_PAGE_KEY = "current_page"
LAST_FILL_META_KEY = "last_fill_meta"
RETRY_CANDIDATES_KEY = "retry_candidates"


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PushPage:
    page_id: str
    updated_at: str
    cards: list[PushCard]
    meta: dict[str, Any]


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
                    enqueued_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ready_cards_queue
                    ON ready_cards (queue_id ASC);

                CREATE TABLE IF NOT EXISTS app_state (
                    state_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def load_current_page(self) -> PushPage | None:
        payload = self._load_state(CURRENT_PAGE_KEY)
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

    def save_current_page(self, *, cards: list[PushCard], meta: dict[str, Any] | None = None) -> PushPage:
        page = PushPage(
            page_id=f"page_{uuid.uuid4().hex}",
            updated_at=_now_iso(),
            cards=cards,
            meta=meta or {},
        )
        self._save_state(
            CURRENT_PAGE_KEY,
            {
                "page_id": page.page_id,
                "updated_at": page.updated_at,
                "cards": [card.to_dict() for card in cards],
                "meta": page.meta,
            },
        )
        return page

    def clear_current_page(self) -> None:
        self._delete_state(CURRENT_PAGE_KEY)

    def count_ready_cards(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM ready_cards").fetchone()
        return int(row["c"]) if row else 0

    def enqueue_ready_cards(self, cards: list[PushCard]) -> int:
        inserted = 0
        with self.connect() as conn:
            for card in cards:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO ready_cards (
                        item_uid, source, title, summary, reason, canonical_url,
                        author_name, excerpt, recommendation, tags_json, enqueued_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        _now_iso(),
                    ),
                )
                inserted += int(cursor.rowcount or 0)
        return inserted

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
        active.update(str(row["item_uid"]) for row in rows if str(row["item_uid"]).strip())
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
            rows = conn.execute(
                """
                SELECT *
                FROM ready_cards
                ORDER BY queue_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            if len(rows) < limit:
                return []
            queue_ids = [int(row["queue_id"]) for row in rows]
            conn.executemany("DELETE FROM ready_cards WHERE queue_id = ?", [(queue_id,) for queue_id in queue_ids])
        return [_row_to_card(row) for row in rows]

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
        )
        for card in cards_payload
        if isinstance(card, dict)
    ]


def _row_to_card(row: sqlite3.Row) -> PushCard:
    tags = _load_json(row["tags_json"])
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
    )


def _load_json(value: object) -> Any:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return None


def _lock_key(lock_name: str) -> str:
    return f"lock:{lock_name}"


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
