"""Background worker for keeping the push queue warm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import time

from .config import Settings
from .hot_service import ensure_hot_cache
from .push_service import (
    HYDRATED_POOL_TARGET,
    PUSH_CARD_COUNT,
    READY_CARD_TARGET,
    create_store,
    fill_ready_queue_once,
    fill_hydrated_pool_once,
    repair_cached_reader_payloads,
)


@dataclass(slots=True)
class WorkerCycleSummary:
    started_at: str
    finished_at: str
    duration_ms: int
    current_ready: bool
    ready_before: int
    ready_after: int
    enqueued_count: int
    retry_count: int
    provider: str
    model: str
    error: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def run_worker_once(*, settings: Settings) -> WorkerCycleSummary:
    started_at = _now_iso()
    started = time.perf_counter()
    store = create_store(settings)
    repair_cached_reader_payloads(settings=settings, store=store, ready_limit=READY_CARD_TARGET)
    ready_before = store.count_ready_cards()
    current_page = store.load_current_page()
    if current_page is None and ready_before >= PUSH_CARD_COUNT:
        current_page = store.promote_next_ready_page(
            limit=PUSH_CARD_COUNT,
            meta=store.load_last_fill_meta(),
        )

    result = None
    error_text: str | None = None
    try:
        should_fill = current_page is None or store.count_ready_cards() < READY_CARD_TARGET
        if should_fill:
            result = fill_ready_queue_once(settings=settings, store=store)
            error_text = result.error
        if store.count_hydrated_candidates() < HYDRATED_POOL_TARGET:
            fill_hydrated_pool_once(settings=settings, store=store)
        if current_page is None and store.count_ready_cards() >= PUSH_CARD_COUNT:
            current_page = store.promote_next_ready_page(
                limit=PUSH_CARD_COUNT,
                meta=store.load_last_fill_meta(),
            )
    except Exception as exc:
        error_text = str(exc)

    try:
        ensure_hot_cache(settings=settings, store=store, force=False)
    except Exception as exc:
        if not error_text:
            error_text = f"hot cache refresh failed: {exc}"

    store = create_store(settings)
    current_ready = store.load_current_page() is not None
    ready_after = store.count_ready_cards()
    finished_at = _now_iso()
    return WorkerCycleSummary(
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=int((time.perf_counter() - started) * 1000),
        current_ready=current_ready,
        ready_before=ready_before,
        ready_after=ready_after,
        enqueued_count=result.enqueued_count if result else 0,
        retry_count=result.retry_count if result else len(store.load_retry_candidates()),
        provider=result.provider if result else "none",
        model=result.model if result else "none",
        error=error_text,
    )


def run_worker_loop(
    *,
    settings: Settings,
    poll_seconds: int | None = None,
    max_cycles: int = 0,
) -> list[WorkerCycleSummary]:
    interval = max(5, poll_seconds or settings.worker_poll_interval_seconds)
    summaries: list[WorkerCycleSummary] = []
    cycle_index = 0
    while max_cycles <= 0 or cycle_index < max_cycles:
        summary = run_worker_once(settings=settings)
        summaries.append(summary)
        cycle_index += 1
        print(json.dumps(summary.to_dict(), ensure_ascii=False), flush=True)
        if max_cycles > 0 and cycle_index >= max_cycles:
            break
        sleep_seconds = interval
        if summary.current_ready and summary.ready_after < READY_CARD_TARGET:
            sleep_seconds = 1
        if not summary.current_ready:
            sleep_seconds = 1
        time.sleep(sleep_seconds)
    return summaries


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat(timespec="seconds")
