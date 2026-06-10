"""Minimal push backend for the desktop client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import uuid
from typing import Any

from .config import Settings
from .connectors.bilibili import BilibiliConnector
from .connectors.subprocess import SubprocessRunner
from .connectors.xiaohongshu import XiaohongshuConnector
from .connectors.zhihu import ZhihuConnector
from .push_llm import PushCandidate, PushLlmClient, PushSelectionResult
from .push_store import PushCard, PushPage, PushStore
from .worker_process import get_worker_process_status

PUSH_CARD_COUNT = 6
READY_CARD_TARGET = 18
SOURCE_LIMIT = 20
SELECT_LIMIT = 10
GENERATION_LOCK_SECONDS = 600


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
        if page is None:
            cache_status = "empty"

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
        previous_retry = _candidates_from_payload(active_store.load_retry_candidates())
        fresh_candidates, source_counts, source_errors = collect_push_candidates(
            settings=settings,
            source_limit=source_limit,
        )
        merged_candidates = previous_retry + fresh_candidates
        deduped_candidates = _dedupe_candidates(merged_candidates)
        active_item_uids = active_store.list_active_item_uids()
        final_candidates = [
            candidate
            for candidate in deduped_candidates
            if candidate.item_uid not in active_item_uids and candidate.canonical_url
        ]

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


def collect_push_candidates(
    *,
    settings: Settings,
    source_limit: int = SOURCE_LIMIT,
) -> tuple[list[PushCandidate], dict[str, int], dict[str, str]]:
    runner = SubprocessRunner()
    connectors = {
        "bilibili": BilibiliConnector(runner, executable=settings.bilibili_executable),
        "zhihu": ZhihuConnector(runner, executable=settings.zhihu_executable),
        "xiaohongshu": XiaohongshuConnector(runner, executable=settings.xiaohongshu_executable),
    }

    candidates: list[PushCandidate] = []
    source_counts: dict[str, int] = {}
    source_errors: dict[str, str] = {}
    for source_name, connector in connectors.items():
        try:
            collected = connector.collect_feed(limit=source_limit)
        except Exception as exc:
            source_counts[source_name] = 0
            source_errors[source_name] = _clean_text(exc)
            continue

        source_counts[source_name] = len(collected)
        for entry in collected:
            try:
                hydrated_entry = connector.hydrate_item(entry)
            except Exception:
                continue
            item = hydrated_entry.feed_item
            title = _clean_text(item.title) or f"{_source_label(item.source)}未命名内容"
            candidates.append(
                PushCandidate(
                    item_uid=item.item_uid,
                    source=item.source,
                    title=title,
                    author_name=_clean_text(item.author.name),
                    canonical_url=_clean_text(item.canonical_url),
                    excerpt=_choose_excerpt(item.to_dict()),
                    stats_text=_stats_text(item.source, item.to_dict()),
                )
            )
    return candidates, source_counts, source_errors


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
    return store.promote_next_ready_page(
        limit=limit,
        meta={
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "sourceCounts": meta.get("sourceCounts", {}),
            "sourceErrors": meta.get("sourceErrors", {}),
        },
    )


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
        "structured": {
            "sections": sections,
        },
    }


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
            )
        )
    return candidates


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


def _clean_text(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text
