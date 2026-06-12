from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
import socket
import uuid
from typing import Any
from urllib import error, request

from .config import Settings
from .connectors.base import CollectedItem
from .connectors.bilibili import BilibiliConnector
from .connectors.subprocess import SubprocessRunner
from .connectors.xiaohongshu import XiaohongshuConnector
from .connectors.zhihu import ZhihuConnector
from .models.feed_item import FeedItem
from .push_service import (
    _build_reader_payload,
    _choose_excerpt,
    _clean_text,
    _format_clock,
    _source_label,
    _stats_text,
)
from .push_store import PushStore, SearchQueryCacheEntry


SEARCH_SOURCE_LIMIT = 8
SEARCH_HYDRATE_PARALLELISM = 6
SEARCH_SOURCE_ORDER = ("bilibili", "zhihu", "xiaohongshu")
SEARCH_QUERY_CACHE_TTL_SECONDS = 15 * 60
SEARCH_ITEM_CACHE_TTL_SECONDS = 12 * 60 * 60
SEARCH_QUERY_CACHE_LIMIT = 10
SEARCH_ITEM_CACHE_LIMIT = 240
SEARCH_REVIEW_LOCK_SECONDS = 5 * 60


def create_store(settings: Settings) -> PushStore:
    store = PushStore(settings.db_path)
    store.bootstrap()
    return store


def get_search_page_payload(
    *,
    settings: Settings,
    query: str,
    limit: int = SEARCH_SOURCE_LIMIT,
    force: bool = False,
) -> dict[str, Any]:
    keyword = _normalize_query(query)
    if not keyword:
        return _empty_search_payload()

    store = create_store(settings)
    store.prune_search_cache(
        max_queries=SEARCH_QUERY_CACHE_LIMIT,
        max_items=SEARCH_ITEM_CACHE_LIMIT,
    )

    cached = None if force else store.load_search_query_cache(query=keyword)
    if cached is not None and not _is_stale(cached.updated_at, SEARCH_QUERY_CACHE_TTL_SECONDS):
        return _finalize_search_payload(cached.payload, cached.review)

    previous = store.load_search_query_cache(query=keyword)
    items, source_counts, source_errors = collect_search_items(
        settings=settings,
        query=keyword,
        limit=limit,
        store=store,
    )

    if not items and previous is not None:
        fallback_payload = _clone_json(previous.payload)
        fallback_meta = fallback_payload.setdefault("meta", {})
        fallback_meta["cacheStatus"] = "stale"
        fallback_meta["sourceErrors"] = source_errors
        fallback_meta["statusText"] = _search_status_text(
            query=keyword,
            item_count=len(fallback_payload.get("items", [])),
            source_errors=source_errors,
            stale=True,
        )
        store.save_search_query_cache(
            query=keyword,
            payload=fallback_payload,
            review=previous.review,
        )
        return _finalize_search_payload(fallback_payload, previous.review)

    updated_at = _now_iso()
    raw_items = [_search_item_to_client_item(item, updated_at=updated_at) for item in items]
    raw_payload = {
        "page": "search",
        "items": raw_items,
        "meta": {
            "query": keyword,
            "itemCount": len(raw_items),
            "rawItemCount": len(raw_items),
            "sourceCounts": source_counts,
            "sourceErrors": source_errors,
            "cacheStatus": "refreshed" if force else "live",
            "statusText": _search_status_text(
                query=keyword,
                item_count=len(raw_items),
                source_errors=source_errors,
            ),
            "lastUpdated": _format_clock(updated_at),
            "lastUpdatedIso": updated_at,
            "itemHash": _search_item_hash(raw_items),
        },
    }
    review = _next_review_state(
        settings=settings,
        previous=previous,
        item_hash=str(raw_payload["meta"]["itemHash"]),
        force=force,
    )
    store.save_search_query_cache(
        query=keyword,
        payload=raw_payload,
        review=review,
    )
    store.prune_search_cache(
        max_queries=SEARCH_QUERY_CACHE_LIMIT,
        max_items=SEARCH_ITEM_CACHE_LIMIT,
    )
    return _finalize_search_payload(raw_payload, review)


def run_search_review(
    *,
    settings: Settings,
    query: str,
    limit: int = SEARCH_SOURCE_LIMIT,
    force: bool = False,
) -> dict[str, Any]:
    keyword = _normalize_query(query)
    if not keyword:
        return {"status": "idle"}

    store = create_store(settings)
    cached = store.load_search_query_cache(query=keyword)
    if cached is None or _is_stale(cached.updated_at, SEARCH_QUERY_CACHE_TTL_SECONDS):
        get_search_page_payload(settings=settings, query=keyword, limit=limit, force=force)
        cached = store.load_search_query_cache(query=keyword)
    if cached is None:
        return {"status": "failed", "message": "search_cache_missing"}

    payload = cached.payload if isinstance(cached.payload, dict) else {}
    item_hash = str(payload.get("meta", {}).get("itemHash", "")).strip()
    if not settings.llm_api_key:
        review = _disabled_review(item_hash=item_hash)
        store.save_search_query_review(query=keyword, review=review)
        return review

    existing_review = cached.review if isinstance(cached.review, dict) else {}
    if (
        not force
        and str(existing_review.get("status", "")).strip() == "completed"
        and str(existing_review.get("itemHash", "")).strip() == item_hash
    ):
        return existing_review

    owner = f"search-review-{uuid.uuid4().hex}"
    lock_name = _search_review_lock_name(keyword)
    if not store.try_acquire_lock(
        lock_name=lock_name,
        owner=owner,
        lease_seconds=SEARCH_REVIEW_LOCK_SECONDS,
    ):
        latest = store.load_search_query_cache(query=keyword)
        if latest is not None and isinstance(latest.review, dict):
            return latest.review
        return _running_review(item_hash=item_hash)

    try:
        store.save_search_query_review(query=keyword, review=_running_review(item_hash=item_hash))
        llm_payload = _request_search_review(
            settings=settings,
            payload=payload,
        )
        review = _normalize_review_payload(
            payload=llm_payload,
            raw_payload=payload,
            item_hash=item_hash,
        )
        store.save_search_query_review(query=keyword, review=review)
        return review
    except Exception as exc:
        review = _failed_review(
            item_hash=item_hash,
            message=_clean_text(exc) or "search_review_failed",
        )
        store.save_search_query_review(query=keyword, review=review)
        return review
    finally:
        store.release_lock(lock_name=lock_name, owner=owner)


def collect_search_items(
    *,
    settings: Settings,
    query: str,
    limit: int = SEARCH_SOURCE_LIMIT,
    store: PushStore | None = None,
) -> tuple[list[FeedItem], dict[str, int], dict[str, str]]:
    connector_specs = [
        ("bilibili", BilibiliConnector, settings.bilibili_executable),
        ("zhihu", ZhihuConnector, settings.zhihu_executable),
        ("xiaohongshu", XiaohongshuConnector, settings.xiaohongshu_executable),
    ]

    source_counts: dict[str, int] = {}
    source_errors: dict[str, str] = {}
    collected_jobs: list[tuple[int, str, type[Any], str, CollectedItem]] = []

    with ThreadPoolExecutor(max_workers=len(connector_specs)) as executor:
        future_map = {
            executor.submit(
                _collect_source_search,
                source_name=source_name,
                connector_cls=connector_cls,
                executable=executable,
                query=query,
                limit=limit,
            ): (source_name, connector_cls, executable)
            for source_name, connector_cls, executable in connector_specs
        }
        for future in as_completed(future_map):
            source_name, connector_cls, executable = future_map[future]
            try:
                collected = future.result()
            except Exception as exc:
                source_counts[source_name] = 0
                source_errors[source_name] = _clean_text(exc) or "search_failed"
                continue
            source_counts[source_name] = len(collected)
            base_index = len(collected_jobs)
            collected_jobs.extend(
                (base_index + index, source_name, connector_cls, executable, entry)
                for index, entry in enumerate(collected, start=1)
            )

    if not collected_jobs:
        return [], source_counts, source_errors

    cached_results: list[tuple[int, FeedItem]] = []
    hydrate_jobs: list[tuple[int, str, type[Any], str, CollectedItem]] = []
    for job_index, source_name, connector_cls, executable, entry in collected_jobs:
        cached_item = _load_cached_search_item(store=store, entry=entry)
        if cached_item is not None:
            cached_results.append((job_index, cached_item))
            continue
        hydrate_jobs.append((job_index, source_name, connector_cls, executable, entry))

    hydrated_results = list(cached_results)
    if hydrate_jobs:
        with ThreadPoolExecutor(max_workers=min(SEARCH_HYDRATE_PARALLELISM, len(hydrate_jobs))) as executor:
            future_map = {
                executor.submit(
                    _hydrate_search_item,
                    connector_cls=connector_cls,
                    executable=executable,
                    entry=entry,
                ): (job_index, source_name)
                for job_index, source_name, connector_cls, executable, entry in hydrate_jobs
            }
            for future in as_completed(future_map):
                job_index, source_name = future_map[future]
                try:
                    item = future.result()
                except Exception as exc:
                    source_errors.setdefault(source_name, _clean_text(exc) or "hydrate_failed")
                    continue
                if item is None:
                    continue
                if store is not None:
                    store.save_search_item_cache(item=item)
                hydrated_results.append((job_index, item))

    ordered = [item for _, item in sorted(hydrated_results, key=lambda entry: entry[0])]
    deduped = _dedupe_search_items(ordered)
    return _interleave_search_items(deduped), source_counts, source_errors


def _collect_source_search(
    *,
    source_name: str,
    connector_cls: type[Any],
    executable: str,
    query: str,
    limit: int,
) -> list[CollectedItem]:
    runner = SubprocessRunner()
    connector = connector_cls(runner, executable=executable)
    return connector.collect_search(query=query, limit=limit)


def _hydrate_search_item(
    *,
    connector_cls: type[Any],
    executable: str,
    entry: CollectedItem,
) -> FeedItem | None:
    runner = SubprocessRunner()
    connector = connector_cls(runner, executable=executable)
    hydrated = connector.hydrate_item(entry)
    item = hydrated.feed_item
    if not _search_item_is_usable(item):
        return None
    return item


def _load_cached_search_item(
    *,
    store: PushStore | None,
    entry: CollectedItem,
) -> FeedItem | None:
    if store is None:
        return None
    item_uid = str(entry.feed_item.item_uid).strip()
    if not item_uid:
        return None
    cached = store.load_search_item_cache(item_uid=item_uid)
    if cached is None or _is_stale(cached.updated_at, SEARCH_ITEM_CACHE_TTL_SECONDS):
        return None
    item = cached.item
    item.query_text = entry.feed_item.query_text
    return item if _search_item_is_usable(item) else None


def _search_item_is_usable(item: FeedItem) -> bool:
    if item.body_text.strip() or item.transcript_text.strip() or item.excerpt_text.strip():
        return True
    if item.top_comments:
        return True
    media = item.media if isinstance(item.media, dict) else {}
    return bool(media.get("image_count") or media.get("imageCount"))


def _dedupe_search_items(items: list[FeedItem]) -> list[FeedItem]:
    deduped: list[FeedItem] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (item.source, item.title, item.canonical_url)
        if key in seen or not item.canonical_url:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _interleave_search_items(items: list[FeedItem]) -> list[FeedItem]:
    by_source: dict[str, list[FeedItem]] = {source: [] for source in SEARCH_SOURCE_ORDER}
    remainder: list[FeedItem] = []
    for item in items:
        if item.source in by_source:
            by_source[item.source].append(item)
        else:
            remainder.append(item)

    interleaved: list[FeedItem] = []
    while any(by_source.values()):
        for source in SEARCH_SOURCE_ORDER:
            bucket = by_source[source]
            if bucket:
                interleaved.append(bucket.pop(0))
    interleaved.extend(remainder)
    return interleaved


def _search_item_to_client_item(item: FeedItem, *, updated_at: str) -> dict[str, Any]:
    excerpt = _clean_text(_choose_excerpt(item.to_dict()))
    summary = excerpt[:220] if excerpt else _clean_text(item.title)
    stats_text = _stats_text(item.source, item.to_dict())
    reason = stats_text or "搜索结果"
    sections: list[dict[str, str]] = []
    if excerpt:
        sections.append({"title": "摘录", "body": excerpt[:220]})

    return {
        "id": item.item_uid,
        "source": item.source,
        "sourceLabel": _source_label(item.source),
        "title": _clean_text(item.title),
        "summary": summary,
        "reason": reason,
        "labels": [_clean_text(topic) for topic in item.topics if _clean_text(topic)][:3],
        "recommendationLevel": "搜索",
        "recommendationKey": "search",
        "authorName": _clean_text(item.author.name),
        "updatedAt": _format_clock(updated_at),
        "updatedAtIso": updated_at,
        "canonicalUrl": _clean_text(item.canonical_url),
        "reader": _build_reader_payload(item),
        "structured": {
            "sections": sections,
        },
    }


def _empty_search_payload() -> dict[str, Any]:
    return {
        "page": "search",
        "items": [],
        "meta": {
            "query": "",
            "itemCount": 0,
            "rawItemCount": 0,
            "sourceCounts": {},
            "sourceErrors": {},
            "cacheStatus": "empty",
            "reviewStatus": "idle",
            "statusText": "输入关键词开始搜索。",
        },
        "review": {
            "status": "idle",
            "summary": "",
            "groups": [],
            "keptItemUids": [],
            "droppedItemUids": [],
        },
    }


def _next_review_state(
    *,
    settings: Settings,
    previous: SearchQueryCacheEntry | None,
    item_hash: str,
    force: bool,
) -> dict[str, Any]:
    if not settings.llm_api_key:
        return _disabled_review(item_hash=item_hash)

    if previous is not None and isinstance(previous.review, dict):
        previous_review = previous.review
        if (
            not force
            and str(previous_review.get("itemHash", "")).strip() == item_hash
            and str(previous_review.get("status", "")).strip() in {"running", "completed"}
        ):
            return previous_review

    return {
        "status": "pending",
        "summary": "",
        "groups": [],
        "keptItemUids": [],
        "droppedItemUids": [],
        "updatedAtIso": _now_iso(),
        "updatedAt": _format_clock(_now_iso()),
        "itemHash": item_hash,
    }


def _running_review(*, item_hash: str) -> dict[str, Any]:
    now_iso = _now_iso()
    return {
        "status": "running",
        "summary": "",
        "groups": [],
        "keptItemUids": [],
        "droppedItemUids": [],
        "updatedAtIso": now_iso,
        "updatedAt": _format_clock(now_iso),
        "itemHash": item_hash,
    }


def _disabled_review(*, item_hash: str) -> dict[str, Any]:
    now_iso = _now_iso()
    return {
        "status": "disabled",
        "summary": "",
        "groups": [],
        "keptItemUids": [],
        "droppedItemUids": [],
        "updatedAtIso": now_iso,
        "updatedAt": _format_clock(now_iso),
        "itemHash": item_hash,
        "message": "llm_disabled",
    }


def _failed_review(*, item_hash: str, message: str) -> dict[str, Any]:
    now_iso = _now_iso()
    return {
        "status": "failed",
        "summary": "",
        "groups": [],
        "keptItemUids": [],
        "droppedItemUids": [],
        "updatedAtIso": now_iso,
        "updatedAt": _format_clock(now_iso),
        "itemHash": item_hash,
        "message": message[:160],
    }


def _finalize_search_payload(raw_payload: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    payload = _clone_json(raw_payload)
    items = payload.get("items", [])
    if not isinstance(items, list):
        items = []
        payload["items"] = items

    meta = payload.get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        payload["meta"] = meta

    final_items = items
    review_state = review if isinstance(review, dict) else {}
    review_status = str(review_state.get("status", "")).strip() or "pending"
    if review_status == "completed":
        filtered_items = _apply_review_order(items=items, review=review_state)
        if filtered_items:
            final_items = filtered_items
            payload["items"] = final_items

    raw_count = len(items)
    meta["rawItemCount"] = raw_count
    meta["itemCount"] = len(final_items)
    meta["reviewStatus"] = review_status
    payload["review"] = _public_review_payload(review_state, raw_count=raw_count, shown_count=len(final_items))
    return payload


def _apply_review_order(*, items: list[dict[str, Any]], review: dict[str, Any]) -> list[dict[str, Any]]:
    kept_item_uids = review.get("keptItemUids")
    if not isinstance(kept_item_uids, list):
        return items
    item_by_uid = {
        str(item.get("id", "")).strip(): item
        for item in items
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item_uid in kept_item_uids:
        key = str(item_uid).strip()
        if not key or key in seen:
            continue
        item = item_by_uid.get(key)
        if item is None:
            continue
        ordered.append(item)
        seen.add(key)
    return ordered or items


def _public_review_payload(review: dict[str, Any], *, raw_count: int, shown_count: int) -> dict[str, Any]:
    status = str(review.get("status", "")).strip() or "pending"
    payload = {
        "status": status,
        "summary": _clean_text(review.get("summary")),
        "groups": [],
        "keptItemUids": [str(item_uid) for item_uid in review.get("keptItemUids", []) if str(item_uid).strip()],
        "droppedItemUids": [str(item_uid) for item_uid in review.get("droppedItemUids", []) if str(item_uid).strip()],
        "updatedAt": str(review.get("updatedAt", "")),
        "updatedAtIso": str(review.get("updatedAtIso", "")),
        "message": _clean_text(review.get("message")),
        "keptItemCount": shown_count,
        "rawItemCount": raw_count,
    }
    groups = review.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            payload["groups"].append(
                {
                    "title": _clean_text(group.get("title"))[:20],
                    "summary": _clean_text(group.get("summary"))[:240],
                    "itemUids": [
                        str(item_uid)
                        for item_uid in group.get("itemUids", [])
                        if str(item_uid).strip()
                    ],
                }
            )
    return payload


def _request_search_review(
    *,
    settings: Settings,
    payload: dict[str, Any],
) -> dict[str, Any]:
    req_payload = {
        "model": settings.llm_model,
        "temperature": 0.1,
        "max_tokens": 2400,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _search_review_system_prompt()},
            {"role": "user", "content": _search_review_user_prompt(payload)},
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
    timeout_seconds = max(settings.llm_timeout_seconds, 60)
    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (TimeoutError, socket.timeout, error.URLError, error.HTTPError) as exc:
        raise RuntimeError(f"search_review_request_failed: {exc}") from exc

    content = _extract_content(raw)
    return _parse_json_object(content)


def _normalize_review_payload(
    *,
    payload: dict[str, Any],
    raw_payload: dict[str, Any],
    item_hash: str,
) -> dict[str, Any]:
    items = raw_payload.get("items", [])
    valid_item_uids = [
        str(item.get("id", "")).strip()
        for item in items
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    ]
    valid_item_uid_set = set(valid_item_uids)

    summary = _clean_text(payload.get("summary"))
    groups_payload = payload.get("groups", [])
    groups: list[dict[str, Any]] = []
    used_group_item_uids: set[str] = set()
    if isinstance(groups_payload, list):
        for group in groups_payload[:5]:
            if not isinstance(group, dict):
                continue
            title = _clean_text(group.get("title"))[:20]
            group_summary = _clean_text(group.get("summary"))[:240]
            item_uids = [
                str(item_uid).strip()
                for item_uid in group.get("item_uids", group.get("itemUids", []))
                if str(item_uid).strip() in valid_item_uid_set
            ]
            if not title or not group_summary or not item_uids:
                continue
            groups.append(
                {
                    "title": title,
                    "summary": group_summary,
                    "itemUids": item_uids,
                }
            )
            used_group_item_uids.update(item_uids)

    kept_item_uids_raw = payload.get("kept_item_uids", payload.get("keptItemUids", []))
    kept_item_uids: list[str] = []
    if isinstance(kept_item_uids_raw, list):
        for item_uid in kept_item_uids_raw:
            key = str(item_uid).strip()
            if key and key in valid_item_uid_set and key not in kept_item_uids:
                kept_item_uids.append(key)
    if not kept_item_uids and used_group_item_uids:
        kept_item_uids = [item_uid for item_uid in valid_item_uids if item_uid in used_group_item_uids]
    if not kept_item_uids:
        kept_item_uids = valid_item_uids

    dropped_item_uids_raw = payload.get("dropped_item_uids", payload.get("droppedItemUids", []))
    dropped_item_uids: list[str] = []
    if isinstance(dropped_item_uids_raw, list):
        for item_uid in dropped_item_uids_raw:
            key = str(item_uid).strip()
            if key and key in valid_item_uid_set and key not in kept_item_uids and key not in dropped_item_uids:
                dropped_item_uids.append(key)

    now_iso = _now_iso()
    return {
        "status": "completed",
        "summary": summary[:720],
        "groups": groups,
        "keptItemUids": kept_item_uids,
        "droppedItemUids": dropped_item_uids,
        "updatedAtIso": now_iso,
        "updatedAt": _format_clock(now_iso),
        "itemHash": item_hash,
        "message": "",
    }


def _search_review_system_prompt() -> str:
    return (
        "你是中文搜索结果编辑。"
        " 你的任务是整理同一关键词下的多源内容，保留高信息密度、强相关、不过时的条目，剔除噪音和弱相关结果。"
        " 语气必须像编辑写给人看的搜索整理稿，直接、具体、自然。"
        " 不要出现“以下是”“根据提供内容”“作为AI”“我认为”“可以看到”等 AI 口癖。"
        " 只返回 JSON，不要解释，不要 markdown。"
        ' JSON 结构固定为 {"summary":"","groups":[{"title":"","summary":"","item_uids":[""]}],"kept_item_uids":[""],"dropped_item_uids":[""]}。'
        " summary 用中文长摘要，120 到 260 字，先给当前搜索主题的结论，再概括主要分支。"
        " groups 返回 2 到 5 组，每组 title 为 4 到 12 个字，summary 为 30 到 120 个字，item_uids 只能引用候选里的 item_uid。"
        " kept_item_uids 按建议阅读顺序输出，至少保留 1 条。"
        " dropped_item_uids 只放你明确判断为过时、弱相关、信息量太低或明显跑题的条目。"
        " 不要编造事实，不要引用候选中不存在的 item_uid。"
    )


def _search_review_user_prompt(payload: dict[str, Any]) -> str:
    meta = payload.get("meta", {})
    query = _clean_text(meta.get("query"))
    items = payload.get("items", [])
    blocks = [f"搜索关键词：{query}", "候选内容如下："]
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        reader = item.get("reader", {})
        reader = reader if isinstance(reader, dict) else {}
        blocks.append(
            "\n".join(
                [
                    f"[{index}] item_uid={_clean_text(item.get('id'))}",
                    f"source={_clean_text(item.get('sourceLabel') or item.get('source'))}",
                    f"title={_clean_text(item.get('title'))}",
                    f"author={_clean_text(item.get('authorName'))}",
                    f"published_at={_clean_text(reader.get('publishedAt'))}",
                    f"topics={', '.join(str(topic) for topic in reader.get('topics', []) if str(topic).strip())}",
                    f"signals={_clean_text(reader.get('statsText') or item.get('reason'))}",
                    f"content={_review_content_text(item=item, reader=reader)}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _review_content_text(*, item: dict[str, Any], reader: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("excerptText", "bodyText", "transcriptText"):
        text = _clean_text(reader.get(key))
        if text:
            parts.append(text)
    comments = reader.get("comments", [])
    if isinstance(comments, list) and comments:
        comment_parts: list[str] = []
        for comment in comments[:4]:
            if not isinstance(comment, dict):
                continue
            comment_text = _clean_text(comment.get("content"))
            if comment_text:
                comment_parts.append(comment_text)
        if comment_parts:
            parts.append("评论：" + " / ".join(comment_parts))
    if not parts:
        parts.append(_clean_text(item.get("summary")))
    text = "\n".join(part for part in parts if part)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:1800]


def _extract_content(raw: dict[str, Any]) -> str:
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("search_review_response_shape_invalid") from exc

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "".join(parts).strip()
    return str(content).strip()


def _parse_json_object(text: str) -> dict[str, Any]:
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
            raise RuntimeError("search_review_json_invalid") from None
        payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, dict):
        raise RuntimeError("search_review_not_object")
    return payload


def _search_status_text(
    *,
    query: str,
    item_count: int,
    source_errors: dict[str, str],
    stale: bool = False,
) -> str:
    if item_count <= 0:
        if source_errors:
            return f"“{query}” 暂无可用结果。"
        return f"“{query}” 没有命中结果。"
    if stale:
        return f"“{query}” 当前显示上一轮缓存结果。"
    return f"已搜索 “{query}”，共 {item_count} 条结果。"


def _search_item_hash(items: list[dict[str, Any]]) -> str:
    keys = []
    for item in items:
        if not isinstance(item, dict):
            continue
        keys.append(
            "|".join(
                [
                    _clean_text(item.get("id")),
                    _clean_text(item.get("title")),
                    _clean_text(item.get("canonicalUrl")),
                ]
            )
        )
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()


def _search_review_lock_name(query: str) -> str:
    return f"search_review:{_normalize_query(query)}"


def _normalize_query(query: str) -> str:
    return str(query or "").strip()


def _clone_json(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _is_stale(iso_text: str, ttl_seconds: int) -> bool:
    try:
        parsed = datetime.fromisoformat(str(iso_text))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed < datetime.now(tz=UTC) - timedelta(seconds=ttl_seconds)


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat(timespec="seconds")
