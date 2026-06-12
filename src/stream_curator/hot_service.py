"""Minimal hot-page backend for the desktop client."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
import json
import re
import socket
from typing import Any
from urllib import error, request
import uuid

from .config import Settings
from .connectors.base import CollectedItem
from .connectors.bilibili import BilibiliConnector
from .connectors.subprocess import SubprocessRunner
from .connectors.xiaohongshu import XiaohongshuConnector
from .connectors.zhihu import ZhihuConnector
from .push_service import (
    _build_reader_payload,
    _card_to_client_item,
    _choose_excerpt,
    _clean_text,
    _is_xhs_transient_feed_error,
    _rehydrate_card_reader_payload,
    _source_label,
    _stats_text,
)
from .push_store import PushCard, PushPage, PushStore
from .worker_process import get_worker_process_status


HOT_CARD_COUNT = 15
HOT_SOURCE_LIMIT = 10
HOT_CACHE_TTL_SECONDS = 3600
HOT_HYDRATE_PARALLELISM = 6
HOT_GENERATION_LOCK_SECONDS = 600
HOT_SOURCE_ORDER = ("bilibili", "zhihu", "xiaohongshu")
HOT_XHS_COOLDOWN_SECONDS = 60
HOT_SUMMARY_INPUT_LIMIT = 1600


def create_store(settings: Settings) -> PushStore:
    store = PushStore(settings.db_path)
    store.bootstrap()
    return store


def get_hot_page_payload(
    *,
    settings: Settings,
    limit: int = HOT_CARD_COUNT,
) -> dict[str, Any]:
    store = create_store(settings)
    page = store.load_hot_page()
    cache_status = "cached"
    if _is_hot_page_stale(page):
        rebuilt = ensure_hot_cache(settings=settings, store=store, force=False)
        if rebuilt is not None:
            page = rebuilt
            cache_status = "refreshed"
        elif page is None:
            cache_status = "empty"
        else:
            cache_status = "stale"
    page = _repair_hot_page_reader_payloads(settings=settings, store=store, page=page)

    worker_status = get_worker_process_status(project_root=settings.project_root)
    return _build_hot_payload(
        page=page,
        cache_status=cache_status,
        worker_running=worker_status.running,
        limit=limit,
    )


def refresh_hot_page_payload(
    *,
    settings: Settings,
    limit: int = HOT_CARD_COUNT,
) -> dict[str, Any]:
    store = create_store(settings)
    previous = store.load_hot_page()
    page = ensure_hot_cache(settings=settings, store=store, force=True)
    cache_status = "refreshed"
    if page is None:
        page = previous
        cache_status = "stale" if page is not None else "empty"
    page = _repair_hot_page_reader_payloads(settings=settings, store=store, page=page)

    worker_status = get_worker_process_status(project_root=settings.project_root)
    return _build_hot_payload(
        page=page,
        cache_status=cache_status,
        worker_running=worker_status.running,
        limit=limit,
    )


def ensure_hot_cache(
    *,
    settings: Settings,
    store: PushStore | None = None,
    force: bool = False,
) -> PushPage | None:
    active_store = store or create_store(settings)
    cached_page = active_store.load_hot_page()
    if not force and not _is_hot_page_stale(cached_page):
        return cached_page

    owner = f"hot-generator-{uuid.uuid4().hex}"
    if not active_store.try_acquire_lock(
        lock_name="hot_generation",
        owner=owner,
        lease_seconds=HOT_GENERATION_LOCK_SECONDS,
    ):
        return active_store.load_hot_page()

    try:
        cached_page = active_store.load_hot_page()
        if not force and not _is_hot_page_stale(cached_page):
            return cached_page

        cards, source_counts, source_errors = collect_hot_cards(
            settings=settings,
            store=active_store,
            source_limit=HOT_SOURCE_LIMIT,
            card_limit=HOT_CARD_COUNT,
        )
        if not cards:
            return cached_page
        cards = _summarize_hot_cards(settings=settings, cards=cards)

        return active_store.save_hot_page(
            cards=cards,
            meta={
                "sourceCounts": source_counts,
                "sourceErrors": source_errors,
                "itemCount": len(cards),
            },
        )
    finally:
        active_store.release_lock(lock_name="hot_generation", owner=owner)


def collect_hot_cards(
    *,
    settings: Settings,
    store: PushStore | None = None,
    source_limit: int = HOT_SOURCE_LIMIT,
    card_limit: int = HOT_CARD_COUNT,
) -> tuple[list[PushCard], dict[str, int], dict[str, str]]:
    connector_specs = [
        ("bilibili", BilibiliConnector, settings.bilibili_executable),
        ("zhihu", ZhihuConnector, settings.zhihu_executable),
        ("xiaohongshu", XiaohongshuConnector, settings.xiaohongshu_executable),
    ]
    source_counts: dict[str, int] = {}
    source_errors: dict[str, str] = {}
    jobs: list[tuple[int, str, type[Any], str, CollectedItem]] = []

    for source_name, connector_cls, executable in connector_specs:
        if _should_skip_hot_source(store=store, source_name=source_name):
            source_counts[source_name] = 0
            continue
        runner = SubprocessRunner()
        connector = connector_cls(runner, executable=executable)
        try:
            collected = connector.collect_hot(limit=source_limit)
        except Exception as exc:
            error_text = _clean_text(exc)
            source_counts[source_name] = 0
            if _mark_hot_source_cooldown(
                store=store,
                source_name=source_name,
                error_text=error_text,
            ):
                continue
            source_errors[source_name] = error_text
            continue

        _clear_hot_source_cooldown(store=store, source_name=source_name)
        source_counts[source_name] = len(collected)
        base_index = len(jobs)
        jobs.extend(
            (index, source_name, connector_cls, executable, entry)
            for index, entry in enumerate(collected, start=base_index)
        )

    if not jobs:
        return [], source_counts, source_errors

    drafts: list[tuple[int, PushCard]] = []
    with ThreadPoolExecutor(max_workers=min(HOT_HYDRATE_PARALLELISM, len(jobs))) as executor:
        futures = [
            executor.submit(
                _hydrate_hot_card,
                index=index,
                source_name=source_name,
                connector_cls=connector_cls,
                executable=executable,
                entry=entry,
            )
            for index, source_name, connector_cls, executable, entry in jobs
        ]
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                continue
            drafts.append(result)

    ordered_cards = [card for _, card in sorted(drafts, key=lambda entry: entry[0])]
    deduped_cards = _dedupe_hot_cards(ordered_cards)
    interleaved_cards = _interleave_hot_cards(deduped_cards)
    return interleaved_cards[: max(1, card_limit)], source_counts, source_errors


def _repair_hot_page_reader_payloads(
    *,
    settings: Settings,
    store: PushStore,
    page: PushPage | None,
) -> PushPage | None:
    if page is None:
        return None

    changed = False
    for card in page.cards:
        if not _needs_hot_reader_backfill(card):
            continue
        try:
            payload = _rehydrate_card_reader_payload(settings=settings, card=card)
        except Exception:
            continue
        if not isinstance(payload, dict) or not payload:
            continue
        card.reader_payload = payload
        changed = True

    if not changed:
        return page

    repaired_page = PushPage(
        page_id=page.page_id,
        updated_at=page.updated_at,
        cards=page.cards,
        meta=page.meta,
    )
    store.replace_hot_page(repaired_page)
    return repaired_page


def _needs_hot_reader_backfill(card: PushCard) -> bool:
    if card.source != "zhihu":
        return False
    payload = card.reader_payload if isinstance(card.reader_payload, dict) else {}
    if str(payload.get("entityType", "")).strip() != "question":
        return False
    answers = payload.get("questionAnswers")
    if not isinstance(answers, list) or not answers:
        return True
    default_answer_id = str(payload.get("defaultAnswerId", "")).strip()
    if not default_answer_id:
        return True
    body = str(payload.get("bodyText", "")).strip()
    comments = payload.get("comments", [])
    return not body or not isinstance(comments, list) or len(comments) == 0


def _hydrate_hot_card(
    *,
    index: int,
    source_name: str,
    connector_cls: type[Any],
    executable: str,
    entry: CollectedItem,
) -> tuple[int, PushCard] | None:
    runner = SubprocessRunner()
    connector = connector_cls(runner, executable=executable)
    try:
        hydrated_entry = connector.hydrate_item(entry)
    except Exception:
        hydrated_entry = entry

    item = hydrated_entry.feed_item
    excerpt = _clean_text(_choose_excerpt(item.to_dict()))
    summary = _hot_summary(item=item, excerpt=excerpt)
    if not summary:
        return None

    stats_text = _stats_text(item.source, item.to_dict())
    reason = f"热门第{entry.rank_in_batch or '?'}位"
    if stats_text:
        reason = f"{reason} · {stats_text}"

    return (
        index,
        PushCard(
            item_uid=item.item_uid,
            source=source_name,
            title=_clean_text(item.title) or f"{_source_label(source_name)} 热门内容",
            summary=summary,
            reason=reason,
            canonical_url=_clean_text(item.canonical_url),
            author_name=_clean_text(item.author.name),
            excerpt=excerpt[:320],
            recommendation="worth_reading",
            tags=[_clean_text(topic) for topic in item.topics if _clean_text(topic)][:3],
            reader_payload=_build_reader_payload(item),
        ),
    )


def _hot_summary(*, item: Any, excerpt: str) -> str:
    parts = [excerpt, _clean_text(item.excerpt_text), _clean_text(item.title)]
    for part in parts:
        if not part:
            continue
        if len(part) <= 180:
            return part
        return f"{part[:177]}..."
    return ""


def _summarize_hot_cards(*, settings: Settings, cards: list[PushCard]) -> list[PushCard]:
    if not cards or not settings.llm_api_key:
        return cards

    summaries = _request_hot_summaries(settings=settings, cards=cards)
    if not summaries:
        return cards

    for card in cards:
        summary = _normalize_hot_summary(
            summaries.get(card.item_uid),
            card=card,
        )
        if summary:
            card.summary = summary
    return cards


def _request_hot_summaries(*, settings: Settings, cards: list[PushCard]) -> dict[str, str]:
    models: list[str] = []
    for model_name in (settings.llm_model, settings.llm_fallback_model):
        cleaned = str(model_name or "").strip()
        if cleaned and cleaned not in models:
            models.append(cleaned)

    if not models:
        return {}

    last_error: Exception | None = None
    for model_name in models:
        req_payload = {
            "model": model_name,
            "temperature": 0.1,
            "max_tokens": 2200,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _hot_summary_system_prompt()},
                {"role": "user", "content": _hot_summary_user_prompt(cards)},
            ],
        }
        req = request.Request(
            settings.llm_chat_completions_url,
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "stream-curator/0.1",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=max(settings.llm_timeout_seconds, 60)) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            payload = _parse_hot_summary_payload(_extract_hot_summary_content(raw))
            items = payload.get("items")
            if not isinstance(items, list):
                continue
            summaries: dict[str, str] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_uid = _clean_text(item.get("item_uid") or item.get("itemUid"))
                summary = _clean_text(item.get("summary"))
                if item_uid and summary:
                    summaries[item_uid] = summary
            if summaries:
                return summaries
        except (TimeoutError, socket.timeout, error.URLError, error.HTTPError, RuntimeError, ValueError) as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise RuntimeError(f"hot_summary_request_failed: {last_error}") from last_error
    return {}


def _hot_summary_system_prompt() -> str:
    return (
        "你是中文信息编辑，只负责给热门流里的每一条内容写摘要。"
        " 不要筛选，不要淘汰，不要排序解释，输入里有几条就输出几条。"
        " 只返回 JSON，不要 markdown，不要额外说明。"
        ' JSON 结构固定为 {"items":[{"item_uid":"","summary":""}]}。'
        " summary 必须是中文，30 到 90 字，直接概括信息点，不要复述标题，不要出现“这条内容”“该内容”“以下是”“根据提供内容”等口癖。"
        " 不要编造输入里没有的信息。"
    )


def _hot_summary_user_prompt(cards: list[PushCard]) -> str:
    blocks = ["请逐条总结以下热门内容："]
    for index, card in enumerate(cards, start=1):
        reader = card.reader_payload if isinstance(card.reader_payload, dict) else {}
        blocks.append(
            "\n".join(
                [
                    f"[{index}] item_uid={card.item_uid}",
                    f"source={card.source}",
                    f"title={_clean_text(card.title)}",
                    f"author={_clean_text(card.author_name)}",
                    f"signals={_clean_text(card.reason)}",
                    f"content={_hot_summary_content_text(card=card, reader=reader)}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _hot_summary_content_text(*, card: PushCard, reader: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("excerptText", "bodyText", "transcriptText"):
        text = _clean_text(reader.get(key))
        if text:
            parts.append(text)

    answers = reader.get("questionAnswers")
    if isinstance(answers, list) and answers:
        answer_blocks: list[str] = []
        for answer in answers[:3]:
            if not isinstance(answer, dict):
                continue
            author_name = _clean_text(answer.get("authorName"))
            body_text = _clean_text(answer.get("bodyText") or answer.get("excerptText"))
            if not body_text:
                continue
            if author_name:
                answer_blocks.append(f"{author_name}: {body_text}")
            else:
                answer_blocks.append(body_text)
        if answer_blocks:
            parts.append("回答：" + " / ".join(answer_blocks))

    comments = reader.get("comments")
    if isinstance(comments, list) and comments:
        comment_parts: list[str] = []
        for comment in comments[:3]:
            if not isinstance(comment, dict):
                continue
            comment_text = _clean_text(comment.get("content"))
            if comment_text:
                comment_parts.append(comment_text)
        if comment_parts:
            parts.append("评论：" + " / ".join(comment_parts))

    if not parts:
        parts.append(_clean_text(card.excerpt))
    text = "\n".join(part for part in parts if part)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:HOT_SUMMARY_INPUT_LIMIT]


def _extract_hot_summary_content(raw: dict[str, Any]) -> str:
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("hot_summary_response_shape_invalid") from exc

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "".join(parts).strip()
    return str(content).strip()


def _parse_hot_summary_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("hot_summary_json_invalid") from None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("hot_summary_json_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("hot_summary_payload_not_object")
    return payload


def _normalize_hot_summary(summary: object, *, card: PushCard) -> str:
    text = _clean_text(summary)
    if not text or len(text) < 16:
        return ""
    title = _clean_text(card.title)
    excerpt = _clean_text(card.excerpt)
    if title and text == title:
        return ""
    if excerpt and text == excerpt:
        return ""
    return text[:120]


def _dedupe_hot_cards(cards: list[PushCard]) -> list[PushCard]:
    deduped: list[PushCard] = []
    seen: set[tuple[str, str, str]] = set()
    for card in cards:
        key = (card.source, card.title, card.canonical_url)
        if key in seen or not card.canonical_url:
            continue
        seen.add(key)
        deduped.append(card)
    return deduped


def _interleave_hot_cards(cards: list[PushCard]) -> list[PushCard]:
    by_source: dict[str, list[PushCard]] = {source: [] for source in HOT_SOURCE_ORDER}
    remainder: list[PushCard] = []
    for card in cards:
        if card.source in by_source:
            by_source[card.source].append(card)
        else:
            remainder.append(card)

    interleaved: list[PushCard] = []
    while any(by_source.values()):
        for source in HOT_SOURCE_ORDER:
            bucket = by_source[source]
            if bucket:
                interleaved.append(bucket.pop(0))
    interleaved.extend(remainder)
    return interleaved


def _should_skip_hot_source(*, store: PushStore | None, source_name: str) -> bool:
    if store is None:
        return False
    return store.has_source_cooldown(source=source_name, action="hot")


def _mark_hot_source_cooldown(
    *,
    store: PushStore | None,
    source_name: str,
    error_text: str,
) -> bool:
    if store is None or not _is_xhs_transient_feed_error(source_name=source_name, error_text=error_text):
        return False
    store.set_source_cooldown(
        source=source_name,
        action="hot",
        seconds=HOT_XHS_COOLDOWN_SECONDS,
    )
    return True


def _clear_hot_source_cooldown(*, store: PushStore | None, source_name: str) -> None:
    if store is None:
        return
    store.clear_source_cooldown(source=source_name, action="hot")


def _is_hot_page_stale(page: PushPage | None) -> bool:
    if page is None:
        return True
    try:
        updated_at = datetime.fromisoformat(page.updated_at)
    except ValueError:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at < datetime.now(tz=UTC) - timedelta(seconds=HOT_CACHE_TTL_SECONDS)


def _build_hot_payload(
    *,
    page: PushPage | None,
    cache_status: str,
    worker_running: bool,
    limit: int,
) -> dict[str, Any]:
    cards = list(page.cards) if page else []
    items = [_hot_card_to_client_item(card, updated_at=page.updated_at if page else None) for card in cards[: max(1, limit)]]
    meta = page.meta if page else {}
    return {
        "page": "hot",
        "items": items,
        "meta": {
            "pageId": page.page_id if page else None,
            "lastUpdated": _format_clock(page.updated_at if page else None),
            "lastUpdatedIso": page.updated_at if page else None,
            "itemCount": len(cards),
            "cacheStatus": cache_status,
            "workerRunning": worker_running,
            "sourceCounts": meta.get("sourceCounts", {}),
            "sourceErrors": meta.get("sourceErrors", {}),
            "statusText": _hot_status_text(cache_status=cache_status, item_count=len(cards)),
        },
    }


def _hot_card_to_client_item(card: PushCard, *, updated_at: str | None) -> dict[str, Any]:
    item = _card_to_client_item(card, updated_at=updated_at)
    item["recommendationLevel"] = "热门"
    item["recommendationKey"] = "worth-reading"
    return item


def _hot_status_text(*, cache_status: str, item_count: int) -> str:
    if item_count <= 0:
        return "正在准备热门内容..."
    if cache_status == "refreshed":
        return "已刷新热门缓存。"
    if cache_status == "stale":
        return "热门刷新失败，显示上一版缓存。"
    return "已加载热门缓存。"


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
