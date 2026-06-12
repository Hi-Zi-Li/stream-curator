"""Minimal push backend for the desktop client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .config import Settings
from .connectors.base import CollectedItem
from .connectors.bilibili import BilibiliConnector
from .connectors.subprocess import SubprocessRunner
from .connectors.xiaohongshu import XiaohongshuConnector
from .connectors.zhihu import ZhihuConnector
from .models.feed_item import FeedAuthor, FeedComment, FeedItem
from .push_llm import PushCandidate, PushLlmClient, PushSelectionResult
from .push_store import PushCard, PushPage, PushStore
from .worker_process import get_worker_process_status

PUSH_CARD_COUNT = 6
READY_CARD_TARGET = 24
HYDRATED_POOL_TARGET = 100
HYDRATED_SELECT_BATCH_SIZE = 24
SOURCE_LIMIT = 20
SELECT_LIMIT = 10
GENERATION_LOCK_SECONDS = 600
HYDRATE_PARALLELISM = 6
XHS_FEED_COOLDOWN_SECONDS = 60


@dataclass(slots=True)
class FillReadyQueueResult:
    created: bool
    candidate_count: int
    selected_count: int
    enqueued_count: int
    retry_count: int
    ready_count: int
    source_counts: dict[str, int]
    source_errors: dict[str, str]
    provider: str
    model: str
    used_fallback: bool
    error: str | None


def create_store(settings: Settings) -> PushStore:
    store = PushStore(settings.db_path)
    store.bootstrap()
    return store


def get_push_page_payload(
    *,
    settings: Settings,
    ensure_current: bool = True,
    limit: int = PUSH_CARD_COUNT,
) -> dict[str, Any]:
    store = create_store(settings)
    page = store.load_current_page()
    if page is not None and not _page_has_reader_payload(page):
        store.clear_current_page()
        page = None
    cache_status = "current_page" if page else "empty"

    if page is None:
        page = _promote_ready_page(store=store, limit=limit)
        if page is not None:
            cache_status = "promoted_ready_page"
        elif ensure_current:
            _fill_ready_queue_until(
                settings=settings,
                store=store,
                minimum_ready_cards=limit,
                max_rounds=3,
            )
            page = store.load_current_page()
            if page is None:
                page = _promote_ready_page(store=store, limit=limit)
            cache_status = "filled_current_page" if page else "empty"
    if page is not None:
        page = _backfill_page_reader_media(settings=settings, store=store, page=page)

    worker_status = get_worker_process_status(project_root=settings.project_root)
    return _build_page_payload(
        page=page,
        ready_count=store.count_ready_cards(),
        retry_count=len(store.load_retry_candidates()),
        worker_running=worker_status.running,
        cache_status=cache_status,
        limit=limit,
    )


def refresh_push_page_payload(
    *,
    settings: Settings,
    limit: int = PUSH_CARD_COUNT,
) -> dict[str, Any]:
    store = create_store(settings)
    page = _promote_ready_page(store=store, limit=limit)
    cache_status = "refreshed_ready_page" if page else "current_page"
    if page is None:
        page = store.load_current_page()
        if page is not None and not _page_has_reader_payload(page):
            store.clear_current_page()
            page = None
        if page is None:
            cache_status = "empty"
    if page is not None:
        page = _backfill_page_reader_media(settings=settings, store=store, page=page)

    worker_status = get_worker_process_status(project_root=settings.project_root)
    return _build_page_payload(
        page=page,
        ready_count=store.count_ready_cards(),
        retry_count=len(store.load_retry_candidates()),
        worker_running=worker_status.running,
        cache_status=cache_status,
        limit=limit,
    )


def fill_ready_queue_once(
    *,
    settings: Settings,
    store: PushStore | None = None,
    source_limit: int = SOURCE_LIMIT,
    select_limit: int = SELECT_LIMIT,
) -> FillReadyQueueResult:
    active_store = store or create_store(settings)
    owner = f"push-generator-{uuid.uuid4().hex}"
    if not active_store.try_acquire_lock(
        lock_name="push_generation",
        owner=owner,
        lease_seconds=GENERATION_LOCK_SECONDS,
    ):
        return FillReadyQueueResult(
            created=False,
            candidate_count=0,
            selected_count=0,
            enqueued_count=0,
            retry_count=len(active_store.load_retry_candidates()),
            ready_count=active_store.count_ready_cards(),
            source_counts={},
            source_errors={"lock": "busy"},
            provider="busy",
            model="busy",
            used_fallback=False,
            error="busy",
        )

    try:
        source_counts: dict[str, int] = {}
        source_errors: dict[str, str] = {}
        previous_retry = _candidates_from_payload(active_store.load_retry_candidates())
        pool_candidates = active_store.pop_hydrated_candidates(limit=HYDRATED_SELECT_BATCH_SIZE)
        final_candidates = _filter_ready_candidates(
            store=active_store,
            candidates=previous_retry + pool_candidates,
        )

        if not final_candidates:
            _, source_counts, source_errors = fill_hydrated_pool_once(
                settings=settings,
                store=active_store,
                source_limit=source_limit,
            )
            pool_candidates = active_store.pop_hydrated_candidates(limit=HYDRATED_SELECT_BATCH_SIZE)
            final_candidates = _filter_ready_candidates(
                store=active_store,
                candidates=previous_retry + pool_candidates,
            )

        if not final_candidates:
            active_store.save_retry_candidates([])
            active_store.save_last_fill_meta(
                {
                    "provider": "none",
                    "model": "none",
                    "usedFallback": False,
                    "candidateCount": 0,
                    "selectedCount": 0,
                    "validCount": 0,
                    "retryCount": 0,
                    "sourceCounts": source_counts,
                    "sourceErrors": source_errors,
                }
            )
            return FillReadyQueueResult(
                created=False,
                candidate_count=0,
                selected_count=0,
                enqueued_count=0,
                retry_count=0,
                ready_count=active_store.count_ready_cards(),
                source_counts=source_counts,
                source_errors=source_errors,
                provider="none",
                model="none",
                used_fallback=False,
                error="no_candidates",
            )

        llm_client = PushLlmClient(
            api_url=settings.llm_chat_completions_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            fallback_model=settings.llm_fallback_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        selection = llm_client.select_push_cards(
            candidates=final_candidates,
            limit=min(select_limit, len(final_candidates)),
        )
        valid_cards, retry_candidates = _split_selection(
            candidates=final_candidates,
            selection=selection,
        )
        selected_uids = {draft.item_uid for draft in selection.cards}
        recycled_candidates = [
            candidate
            for candidate in final_candidates
            if candidate.item_uid not in selected_uids
        ]
        active_store.enqueue_hydrated_candidates(recycled_candidates)
        enqueued_count = active_store.enqueue_ready_cards(valid_cards)
        active_store.save_retry_candidates([_candidate_to_payload(candidate) for candidate in retry_candidates])
        active_store.save_last_fill_meta(
            {
                "provider": selection.provider,
                "model": selection.model,
                "usedFallback": selection.used_fallback,
                "candidateCount": len(final_candidates),
                "selectedCount": len(selection.cards),
                "validCount": len(valid_cards),
                "retryCount": len(retry_candidates),
                "sourceCounts": source_counts,
                "sourceErrors": source_errors,
            }
        )
        return FillReadyQueueResult(
            created=enqueued_count > 0,
            candidate_count=len(final_candidates),
            selected_count=len(selection.cards),
            enqueued_count=enqueued_count,
            retry_count=len(retry_candidates),
            ready_count=active_store.count_ready_cards(),
            source_counts=source_counts,
            source_errors=source_errors,
            provider=selection.provider,
            model=selection.model,
            used_fallback=selection.used_fallback,
            error=None,
        )
    finally:
        active_store.release_lock(lock_name="push_generation", owner=owner)


def fill_hydrated_pool_once(
    *,
    settings: Settings,
    store: PushStore,
    source_limit: int = SOURCE_LIMIT,
) -> tuple[int, dict[str, int], dict[str, str]]:
    remaining_slots = max(0, HYDRATED_POOL_TARGET - store.count_hydrated_candidates())
    if remaining_slots <= 0:
        return 0, {}, {}
    fresh_candidates, source_counts, source_errors = collect_push_candidates(
        settings=settings,
        source_limit=source_limit,
        store=store,
    )
    deduped_candidates = _dedupe_candidates(fresh_candidates)
    active_item_uids = store.list_active_item_uids()
    final_candidates = [
        candidate
        for candidate in deduped_candidates
        if candidate.item_uid not in active_item_uids and candidate.canonical_url
    ]
    inserted = store.enqueue_hydrated_candidates(final_candidates[:remaining_slots])
    return inserted, source_counts, source_errors


def _filter_ready_candidates(*, store: PushStore, candidates: list[PushCandidate]) -> list[PushCandidate]:
    deduped_candidates = _dedupe_candidates(candidates)
    active_item_uids = store.list_active_item_uids()
    return [
        candidate
        for candidate in deduped_candidates
        if candidate.item_uid not in active_item_uids and candidate.canonical_url
    ]


def collect_push_candidates(
    *,
    settings: Settings,
    source_limit: int = SOURCE_LIMIT,
    store: PushStore | None = None,
) -> tuple[list[PushCandidate], dict[str, int], dict[str, str]]:
    connector_specs = [
        ("bilibili", BilibiliConnector, settings.bilibili_executable),
        ("zhihu", ZhihuConnector, settings.zhihu_executable),
        ("xiaohongshu", XiaohongshuConnector, settings.xiaohongshu_executable),
    ]
    candidates: list[PushCandidate] = []
    source_counts: dict[str, int] = {}
    source_errors: dict[str, str] = {}
    hydrate_jobs: list[tuple[str, type[Any], str, CollectedItem]] = []
    for source_name, connector_cls, executable in connector_specs:
        source_name, collected, source_count, source_error = _collect_source_items(
            source_name=source_name,
            connector_cls=connector_cls,
            executable=executable,
            source_limit=source_limit,
            store=store,
        )
        source_counts[source_name] = source_count
        if source_error:
            source_errors[source_name] = source_error
            continue
        hydrate_jobs.extend(
            (source_name, connector_cls, executable, entry)
            for entry in collected
        )

    if not hydrate_jobs:
        return candidates, source_counts, source_errors

    with ThreadPoolExecutor(max_workers=min(HYDRATE_PARALLELISM, len(hydrate_jobs))) as executor:
        futures = [
            executor.submit(
                _hydrate_candidate_entry,
                source_name=source_name,
                connector_cls=connector_cls,
                executable=executable,
                entry=entry,
            )
            for source_name, connector_cls, executable, entry in hydrate_jobs
        ]
        for future in as_completed(futures):
            candidate = future.result()
            if candidate is not None:
                candidates.append(candidate)
    return candidates, source_counts, source_errors


def _collect_source_items(
    *,
    source_name: str,
    connector_cls: type[Any],
    executable: str,
    source_limit: int,
    store: PushStore | None = None,
) -> tuple[str, list[CollectedItem], int, str | None]:
    if _should_skip_source_feed(store=store, source_name=source_name):
        return source_name, [], 0, None
    runner = SubprocessRunner()
    connector = connector_cls(runner, executable=executable)
    try:
        collected = connector.collect_feed(limit=source_limit)
    except Exception as exc:
        error_text = _clean_text(exc)
        if _mark_source_feed_cooldown(
            store=store,
            source_name=source_name,
            error_text=error_text,
        ):
            return source_name, [], 0, None
        return source_name, [], 0, error_text
    _clear_source_feed_cooldown(store=store, source_name=source_name)
    return source_name, collected, len(collected), None


def _should_skip_source_feed(*, store: PushStore | None, source_name: str) -> bool:
    if store is None:
        return False
    return store.has_source_cooldown(source=source_name, action="feed")


def _mark_source_feed_cooldown(
    *,
    store: PushStore | None,
    source_name: str,
    error_text: str,
) -> bool:
    if store is None or not _is_xhs_transient_feed_error(source_name=source_name, error_text=error_text):
        return False
    store.set_source_cooldown(
        source=source_name,
        action="feed",
        seconds=XHS_FEED_COOLDOWN_SECONDS,
    )
    return True


def _clear_source_feed_cooldown(*, store: PushStore | None, source_name: str) -> None:
    if store is None:
        return
    store.clear_source_cooldown(source=source_name, action="feed")


def _is_xhs_transient_feed_error(*, source_name: str, error_text: str) -> bool:
    if source_name != "xiaohongshu":
        return False
    lowered = error_text.lower()
    needles = (
        "captcha triggered",
        "cooling down",
        "risk control",
        "verify",
        "验证码",
        "风控",
    )
    return any(needle in lowered or needle in error_text for needle in needles)


def _hydrate_candidate_entry(
    *,
    source_name: str,
    connector_cls: type[Any],
    executable: str,
    entry: CollectedItem,
) -> PushCandidate | None:
    runner = SubprocessRunner()
    connector = connector_cls(runner, executable=executable)
    try:
        hydrated_entry = connector.hydrate_item(entry)
    except Exception:
        return None
    item = hydrated_entry.feed_item
    title = _clean_text(item.title) or f"{_source_label(item.source)}未命名内容"
    return PushCandidate(
        item_uid=item.item_uid,
        source=item.source,
        title=title,
        author_name=_clean_text(item.author.name),
        canonical_url=_clean_text(item.canonical_url),
        excerpt=_choose_excerpt(item.to_dict()),
        stats_text=_stats_text(item.source, item.to_dict()),
        reader_payload=_build_reader_payload(item),
    )


def _fill_ready_queue_until(
    *,
    settings: Settings,
    store: PushStore,
    minimum_ready_cards: int,
    max_rounds: int,
) -> list[FillReadyQueueResult]:
    results: list[FillReadyQueueResult] = []
    rounds = max(1, max_rounds)
    for _ in range(rounds):
        before = store.count_ready_cards()
        if before >= minimum_ready_cards:
            break
        result = fill_ready_queue_once(settings=settings, store=store)
        results.append(result)
        after = store.count_ready_cards()
        if after <= before:
            break
    return results


def _promote_ready_page(*, store: PushStore, limit: int) -> PushPage | None:
    meta = store.load_last_fill_meta()
    for _ in range(3):
        page = store.promote_next_ready_page(
            limit=limit,
            meta={
                "provider": meta.get("provider"),
                "model": meta.get("model"),
                "sourceCounts": meta.get("sourceCounts", {}),
                "sourceErrors": meta.get("sourceErrors", {}),
            },
        )
        if page is None:
            return None
        if _page_has_reader_payload(page):
            return page
        store.clear_current_page()
    return None


def _build_page_payload(
    *,
    page: PushPage | None,
    ready_count: int,
    retry_count: int,
    worker_running: bool,
    cache_status: str,
    limit: int,
) -> dict[str, Any]:
    cards = list(page.cards) if page else []
    items = [_card_to_client_item(card, updated_at=page.updated_at if page else None) for card in cards[: max(1, limit)]]
    meta = page.meta if page else {}
    last_updated_iso = page.updated_at if page else None
    return {
        "page": "push",
        "items": items,
        "meta": {
            "pageId": page.page_id if page else None,
            "lastUpdated": _format_clock(last_updated_iso),
            "lastUpdatedIso": last_updated_iso,
            "pushedCount": len(items),
            "readyCount": ready_count,
            "retryCount": retry_count,
            "cacheStatus": cache_status,
            "workerRunning": worker_running,
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "sourceCounts": meta.get("sourceCounts", {}),
            "sourceErrors": meta.get("sourceErrors", {}),
            "statusText": _status_text(
                cache_status=cache_status,
                item_count=len(items),
                ready_count=ready_count,
            ),
        },
    }


def _backfill_page_reader_media(
    *,
    settings: Settings,
    store: PushStore,
    page: PushPage,
) -> PushPage:
    changed = False
    for card in page.cards:
        if not _needs_reader_media_backfill(card):
            continue
        payload = _rehydrate_card_reader_payload(settings=settings, card=card)
        if not payload:
            continue
        card.reader_payload = payload
        changed = True
    if changed:
        store.replace_current_page(page)
    return page


def repair_cached_reader_payloads(
    *,
    settings: Settings,
    store: PushStore,
    ready_limit: int = READY_CARD_TARGET,
) -> None:
    current_page = store.load_current_page()
    if current_page is not None:
        _backfill_page_reader_media(settings=settings, store=store, page=current_page)

    for card in store.peek_ready_cards(limit=max(0, ready_limit)):
        if not _needs_reader_media_backfill(card):
            continue
        try:
            payload = _rehydrate_card_reader_payload(settings=settings, card=card)
        except Exception:
            continue
        if not payload:
            continue
        card.reader_payload = payload
        store.replace_ready_card(card)


def _card_to_client_item(card: PushCard, *, updated_at: str | None) -> dict[str, Any]:
    level_map = {
        "must_read": ("必读", "must-read"),
        "worth_reading": ("值得看", "worth-reading"),
        "later": ("稍后看", "later"),
    }
    recommendation_label, recommendation_key = level_map.get(
        card.recommendation,
        ("值得看", "worth-reading"),
    )

    sections: list[dict[str, str]] = []
    if card.excerpt:
        sections.append({"title": "摘录", "body": card.excerpt[:180]})

    return {
        "id": card.item_uid,
        "source": card.source,
        "sourceLabel": _source_label(card.source),
        "title": card.title,
        "summary": card.summary,
        "reason": card.reason,
        "labels": card.tags[:3],
        "recommendationLevel": recommendation_label,
        "recommendationKey": recommendation_key,
        "authorName": card.author_name,
        "updatedAt": _format_clock(updated_at),
        "updatedAtIso": updated_at,
        "canonicalUrl": card.canonical_url,
        "reader": card.reader_payload,
        "structured": {
            "sections": sections,
        },
    }


def _needs_reader_media_backfill(card: PushCard) -> bool:
    if card.source not in {"zhihu", "xiaohongshu"}:
        return False
    payload = card.reader_payload if isinstance(card.reader_payload, dict) else {}
    if card.source == "zhihu":
        if _needs_zhihu_question_reader_backfill(payload):
            return True
        content_blocks = payload.get("contentBlocks")
        body_text = str(payload.get("bodyText", "")).strip()
        excerpt_text = str(payload.get("excerptText", "")).strip()
        if (not isinstance(content_blocks, list) or not content_blocks) and not body_text and not excerpt_text:
            return True
    media = payload.get("media", {}) if isinstance(payload.get("media"), dict) else {}
    image_count = _safe_int(media.get("imageCount")) or 0
    images = media.get("images")
    return image_count > 0 and (not isinstance(images, list) or not images)


def _needs_zhihu_question_reader_backfill(payload: dict[str, Any]) -> bool:
    if str(payload.get("entityType", "")).strip() != "question":
        return False

    answers = payload.get("questionAnswers")
    if not isinstance(answers, list) or not answers:
        return True

    default_answer_id = str(payload.get("defaultAnswerId", "")).strip()
    if not default_answer_id:
        return True

    body_text = str(payload.get("bodyText", "")).strip()
    comments = payload.get("comments", [])
    return not body_text or not isinstance(comments, list) or len(comments) == 0


def _rehydrate_card_reader_payload(*, settings: Settings, card: PushCard) -> dict[str, Any] | None:
    connector = _reader_backfill_connector(settings=settings, source=card.source)
    if connector is None:
        return None

    payload = card.reader_payload if isinstance(card.reader_payload, dict) else {}
    media = payload.get("media", {}) if isinstance(payload.get("media"), dict) else {}
    engagement = payload.get("engagement", {}) if isinstance(payload.get("engagement"), dict) else {}
    comments = payload.get("comments", []) if isinstance(payload.get("comments"), list) else []
    fallback_item = FeedItem(
        schema_version="1",
        item_uid=card.item_uid,
        source=card.source,
        entity_type=str(payload.get("entityType", "")).strip() or _entity_type_from_item_uid(card.item_uid),
        source_item_id=str(payload.get("sourceItemId", "")).strip() or _source_item_id_from_item_uid(card.item_uid),
        canonical_url=card.canonical_url,
        collection_channel="feed",
        title=card.title,
        author=FeedAuthor(id=None, name=str(payload.get("authorName", "")).strip() or card.author_name),
        collected_at=datetime.now(tz=UTC).isoformat(),
        lang="zh-CN",
        excerpt_text=str(payload.get("excerptText", "")).strip(),
        body_text=str(payload.get("bodyText", "")).strip(),
        transcript_text=str(payload.get("transcriptText", "")).strip(),
        top_comments=[
            FeedComment(
                author_name=str(comment.get("authorName", "")).strip() or "Anonymous",
                content=str(comment.get("content", "")).strip(),
                like_count=_safe_int(comment.get("likeCount")),
            )
            for comment in comments
            if isinstance(comment, dict)
        ],
        topics=[str(topic).strip() for topic in payload.get("topics", []) if str(topic).strip()]
        if isinstance(payload.get("topics"), list)
        else [],
        engagement={
            "view_count": _safe_int(engagement.get("view_count")),
            "like_count": _safe_int(engagement.get("like_count")),
            "comment_count": _safe_int(engagement.get("comment_count")),
            "share_count": _safe_int(engagement.get("share_count")),
            "favorite_count": _safe_int(engagement.get("favorite_count")),
            "voteup_count": _safe_int(engagement.get("voteup_count")),
            "coin_count": _safe_int(engagement.get("coin_count")),
            "danmaku_count": _safe_int(engagement.get("danmaku_count")),
        },
        media={
            "has_video": bool(media.get("hasVideo")),
            "duration_seconds": _safe_int(media.get("durationSeconds")),
            "aid": _safe_int(media.get("aid")),
            "cid": _safe_int(media.get("cid")),
            "page_number": _safe_int(media.get("pageNumber")) or 1,
            "image_count": _safe_int(media.get("imageCount")),
            "image_urls": _media_urls(media.get("images")),
            "content_blocks": _reader_content_blocks(payload.get("contentBlocks")),
        },
        quality_flags={},
        query_text=None,
        published_at=str(payload.get("publishedAt", "")).strip() or None,
    )
    hydrated = connector.hydrate_item(
        CollectedItem(rank_in_batch=None, raw_payload={}, feed_item=fallback_item)
    )
    return _build_reader_payload(hydrated.feed_item)


def _split_selection(
    *,
    candidates: list[PushCandidate],
    selection: PushSelectionResult,
) -> tuple[list[PushCard], list[PushCandidate]]:
    candidate_by_uid = {candidate.item_uid: candidate for candidate in candidates}
    cards: list[PushCard] = []
    retry_candidates: list[PushCandidate] = []
    seen_retry: set[str] = set()
    for draft in selection.cards:
        candidate = candidate_by_uid.get(draft.item_uid)
        if candidate is None:
            continue
        if draft.is_valid:
            cards.append(
                PushCard(
                    item_uid=candidate.item_uid,
                    source=candidate.source,
                    title=candidate.title,
                    summary=_clean_text(draft.summary),
                    reason=_clean_text(draft.reason),
                    canonical_url=candidate.canonical_url,
                    author_name=candidate.author_name,
                    excerpt=_clean_text(candidate.excerpt),
                    recommendation=draft.recommendation,
                    tags=[_clean_text(tag) for tag in draft.tags if _clean_text(tag)][:3],
                    reader_payload=candidate.reader_payload,
                )
            )
            continue
        if candidate.item_uid not in seen_retry:
            retry_candidates.append(candidate)
            seen_retry.add(candidate.item_uid)
    return cards, retry_candidates


def _candidate_to_payload(candidate: PushCandidate) -> dict[str, Any]:
    return {
        "item_uid": candidate.item_uid,
        "source": candidate.source,
        "title": candidate.title,
        "author_name": candidate.author_name,
        "canonical_url": candidate.canonical_url,
        "excerpt": candidate.excerpt,
        "stats_text": candidate.stats_text,
        "reader_payload": candidate.reader_payload,
    }


def _candidates_from_payload(payload: list[dict[str, Any]]) -> list[PushCandidate]:
    candidates: list[PushCandidate] = []
    for entry in payload:
        item_uid = _clean_text(entry.get("item_uid"))
        canonical_url = _clean_text(entry.get("canonical_url"))
        if not item_uid or not canonical_url:
            continue
        candidates.append(
            PushCandidate(
                item_uid=item_uid,
                source=_clean_text(entry.get("source")),
                title=_clean_text(entry.get("title")),
                author_name=_clean_text(entry.get("author_name")),
                canonical_url=canonical_url,
                excerpt=_clean_text(entry.get("excerpt")),
                stats_text=_clean_text(entry.get("stats_text")),
                reader_payload=entry.get("reader_payload", {})
                if isinstance(entry.get("reader_payload"), dict)
                else {},
            )
        )
    return candidates


def _build_reader_payload(item: FeedItem) -> dict[str, Any]:
    return {
        "source": item.source,
        "entityType": item.entity_type,
        "sourceItemId": item.source_item_id,
        "canonicalUrl": item.canonical_url,
        "title": item.title,
        "authorName": item.author.name,
        "publishedAt": item.published_at,
        "topics": [topic for topic in item.topics if topic][:8],
        "statsText": _stats_text(item.source, item.to_dict()),
        "excerptText": _reader_text(item.excerpt_text),
        "bodyText": _reader_text(item.body_text),
        "transcriptText": _reader_text(item.transcript_text),
        "contentBlocks": _reader_content_blocks(item.media.get("content_blocks")),
        "questionDetailBlocks": _reader_content_blocks(item.media.get("question_detail_blocks")),
        "questionAnswers": _reader_question_answers(item.media.get("answer_sections")),
        "defaultAnswerId": str(item.media.get("default_answer_id", "")).strip(),
        "commentSourceAnswerId": str(item.media.get("comment_answer_id", "")).strip(),
        "comments": [_comment_to_reader_payload(comment) for comment in item.top_comments[:5]],
        "media": {
            "hasVideo": bool(item.media.get("has_video")),
            "durationSeconds": _safe_int(item.media.get("duration_seconds")),
            "aid": _safe_int(item.media.get("aid")),
            "cid": _safe_int(item.media.get("cid")),
            "pageNumber": _safe_int(item.media.get("page_number")) or 1,
            "imageCount": _safe_int(item.media.get("image_count")),
            "images": _media_urls(item.media.get("image_urls")),
        },
        "engagement": {
            key: _safe_int(value)
            for key, value in item.engagement.items()
        },
    }


def _comment_to_reader_payload(comment: FeedComment) -> dict[str, Any]:
    return {
        "authorName": comment.author_name,
        "content": _reader_text(comment.content),
        "likeCount": _safe_int(comment.like_count),
    }


def _media_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    urls: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in urls:
            urls.append(text)
    return urls


def _reader_content_blocks(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        block_type = str(entry.get("type", "")).strip()
        if block_type == "text":
            text = _reader_text(entry.get("text"))
            if text:
                blocks.append({"type": "text", "text": text})
            continue
        if block_type == "image":
            url = str(entry.get("url", "")).strip()
            if url:
                blocks.append({"type": "image", "url": url})
    return blocks


def _reader_question_answers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    answers: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        answer_id = str(entry.get("answer_id", "")).strip()
        heading = _reader_text(entry.get("heading"))
        if not answer_id and not heading:
            continue
        answers.append(
            {
                "answerId": answer_id,
                "heading": heading,
                "authorName": _reader_text(entry.get("author_name")),
                "bodyText": _reader_text(entry.get("body")),
                "excerptText": _reader_text(entry.get("excerpt")),
                "contentBlocks": _reader_content_blocks(entry.get("content_blocks")),
                "commentCount": _safe_int(entry.get("comment_count")),
                "likeCount": _safe_int(entry.get("like_count")),
                "canonicalUrl": str(entry.get("canonical_url", "")).strip(),
            }
        )
    return answers


def _reader_backfill_connector(*, settings: Settings, source: str) -> ZhihuConnector | XiaohongshuConnector | None:
    runner = SubprocessRunner()
    if source == "zhihu":
        return ZhihuConnector(runner, executable=settings.zhihu_executable)
    if source == "xiaohongshu":
        return XiaohongshuConnector(runner, executable=settings.xiaohongshu_executable)
    return None


def _entity_type_from_item_uid(item_uid: str) -> str:
    parts = str(item_uid).split(":")
    return parts[1] if len(parts) >= 3 else ""


def _source_item_id_from_item_uid(item_uid: str) -> str:
    parts = str(item_uid).split(":")
    return ":".join(parts[2:]) if len(parts) >= 3 else ""


def _page_has_reader_payload(page: PushPage) -> bool:
    return all(_card_has_current_reader_payload(card) for card in page.cards)


def _card_has_current_reader_payload(card: PushCard) -> bool:
    payload = card.reader_payload if isinstance(card.reader_payload, dict) else {}
    if not payload:
        return False
    if card.source != "bilibili":
        return True

    media = payload.get("media", {}) if isinstance(payload.get("media"), dict) else {}
    return _safe_int(media.get("cid")) is not None


def _dedupe_candidates(candidates: list[PushCandidate]) -> list[PushCandidate]:
    deduped: list[PushCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        fingerprint = hashlib.sha256(
            f"{candidate.source}|{candidate.title}|{candidate.canonical_url}".encode("utf-8")
        ).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(candidate)
    return deduped


def _choose_excerpt(item: dict[str, Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for key in ["body_text", "transcript_text", "excerpt_text"]:
        text = _clean_text(item.get(key))
        if not text or text in seen:
            continue
        parts.append(text)
        seen.add(text)

    comments = item.get("top_comments", [])
    if isinstance(comments, list):
        comment_snippets: list[str] = []
        for comment in comments[:3]:
            if not isinstance(comment, dict):
                continue
            content = _clean_text(comment.get("content"))
            if content:
                comment_snippets.append(content[:80])
        if comment_snippets:
            parts.append("评论：" + " / ".join(comment_snippets))

    return "\n\n".join(parts)[:560]


def _stats_text(source: str, item: dict[str, Any]) -> str:
    engagement = item.get("engagement", {})
    if not isinstance(engagement, dict):
        return ""

    label_maps = {
        "bilibili": [
            ("view_count", "播放"),
            ("like_count", "点赞"),
            ("danmaku_count", "弹幕"),
            ("favorite_count", "收藏"),
            ("share_count", "分享"),
        ],
        "zhihu": [
            ("view_count", "浏览"),
            ("voteup_count", "赞同"),
            ("comment_count", "评论"),
            ("favorite_count", "收藏"),
        ],
        "xiaohongshu": [
            ("like_count", "点赞"),
            ("comment_count", "评论"),
            ("favorite_count", "收藏"),
            ("share_count", "分享"),
        ],
    }
    parts: list[str] = []
    for key, label in label_maps.get(source, []):
        value = engagement.get(key)
        if isinstance(value, int) and value > 0:
            parts.append(f"{label} {value}")
    return " / ".join(parts[:4])


def _status_text(*, cache_status: str, item_count: int, ready_count: int) -> str:
    if item_count <= 0:
        return "后台正在准备第一组推送。"
    if cache_status == "refreshed_ready_page":
        return "已切换到下一组推送。"
    if cache_status == "filled_current_page":
        return "已生成当前推送，后台继续补缓存。"
    if cache_status == "promoted_ready_page":
        return "已从本地缓存载入推送。"
    if ready_count <= 0:
        return "当前页已就绪，后台正在补充新卡片。"
    return "当前页已就绪。"


def _format_clock(iso_text: str | None) -> str:
    if not iso_text:
        return "--:--"
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return "--:--"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone().strftime("%H:%M")


def _source_label(source: str) -> str:
    return {
        "bilibili": "B站",
        "zhihu": "知乎",
        "xiaohongshu": "小红书",
    }.get(source, source)


def _reader_text(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _safe_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text
