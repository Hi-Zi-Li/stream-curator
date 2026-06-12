"""Reader comment pagination helpers for the desktop client."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .connectors.subprocess import SubprocessRunner
from .push_store import PushStore


BILIBILI_COMMENTS_COOLDOWN_SECONDS = 180


def fetch_reader_comments_page(
    *,
    settings: Settings,
    source: str,
    entity_type: str,
    source_item_id: str,
    canonical_url: str,
    cursor: str,
    limit: int,
) -> dict[str, Any]:
    runner = SubprocessRunner(timeout_seconds=45)
    normalized_limit = max(1, int(limit or 10))
    normalized_cursor = str(cursor or "").strip()

    if source == "bilibili":
        return _fetch_bilibili_comments(
            runner=runner,
            settings=settings,
            source_item_id=source_item_id,
            canonical_url=canonical_url,
            cursor=normalized_cursor,
            limit=normalized_limit,
        )
    if source == "zhihu":
        return _fetch_zhihu_comments(
            runner=runner,
            settings=settings,
            entity_type=entity_type,
            source_item_id=source_item_id,
            cursor=normalized_cursor,
            limit=normalized_limit,
        )
    if source == "xiaohongshu":
        return _fetch_xiaohongshu_comments(
            runner=runner,
            settings=settings,
            source_item_id=source_item_id,
            canonical_url=canonical_url,
            cursor=normalized_cursor,
            limit=normalized_limit,
        )

    return _empty_payload(
        source=source,
        entity_type=entity_type,
        cursor=normalized_cursor,
        limit=normalized_limit,
        message="unsupported_source",
    )


def _fetch_bilibili_comments(
    *,
    runner: SubprocessRunner,
    settings: Settings,
    source_item_id: str,
    canonical_url: str,
    cursor: str,
    limit: int,
) -> dict[str, Any]:
    store = _comments_store(settings)
    if store.has_source_cooldown(source="bilibili", action="comments"):
        raise RuntimeError("B站评论接口冷却中，请稍后再试。")

    page = _safe_positive_int(cursor) or 1
    target = source_item_id or canonical_url
    try:
        raw_payload = runner.run(
            [
                settings.bilibili_executable,
                "comments",
                target,
                "--page",
                str(page),
                "--limit",
                str(limit),
                "--json",
            ]
        ).json()
    except Exception as exc:
        error_text = str(exc)
        if _is_bilibili_comments_risk_control(error_text):
            store.set_source_cooldown(
                source="bilibili",
                action="comments",
                seconds=BILIBILI_COMMENTS_COOLDOWN_SECONDS,
            )
            raise RuntimeError("B站评论接口触发风控，请稍后再试。") from exc
        raise

    store.clear_source_cooldown(source="bilibili", action="comments")
    payload = _unwrap_structured_payload(raw_payload)

    comments = payload.get("comments", []) if isinstance(payload.get("comments"), list) else []
    has_more = bool(payload.get("has_more"))
    return {
        "source": "bilibili",
        "entityType": "video",
        "cursor": str(payload.get("cursor") or page),
        "nextCursor": str(payload.get("next_cursor") or "") if has_more else "",
        "hasMore": has_more,
        "pageSize": limit,
        "message": "",
        "comments": [_map_bilibili_comment(comment) for comment in comments if isinstance(comment, dict)],
    }


def _fetch_zhihu_comments(
    *,
    runner: SubprocessRunner,
    settings: Settings,
    entity_type: str,
    source_item_id: str,
    cursor: str,
    limit: int,
) -> dict[str, Any]:
    if entity_type == "question":
        answer_ids = _resolve_zhihu_question_comment_targets(
            runner=runner,
            settings=settings,
            question_id=source_item_id,
        )
        if not answer_ids:
            return {
                "source": "zhihu",
                "entityType": "question",
                "cursor": cursor,
                "nextCursor": "",
                "hasMore": False,
                "pageSize": limit,
                "message": "该问题暂无可用回答评论。",
                "comments": [],
            }
        last_payload: dict[str, Any] | None = None
        for answer_id in answer_ids:
            payload = _fetch_zhihu_comments(
                runner=runner,
                settings=settings,
                entity_type="answer",
                source_item_id=answer_id,
                cursor=cursor,
                limit=limit,
            )
            payload["entityType"] = "question"
            comments = payload.get("comments", [])
            last_payload = payload
            if isinstance(comments, list) and comments:
                return payload
        return last_payload or {
            "source": "zhihu",
            "entityType": "question",
            "cursor": cursor,
            "nextCursor": "",
            "hasMore": False,
            "pageSize": limit,
            "message": "",
            "comments": [],
        }

    raw_payload = runner.run(
        [
            settings.zhihu_executable,
            "comments",
            entity_type,
            source_item_id,
            "--offset",
            cursor,
            "--limit",
            str(limit),
            "--json",
        ]
    ).json()
    payload = _unwrap_structured_payload(raw_payload)
    comments = payload.get("comments", []) if isinstance(payload.get("comments"), list) else []
    return {
        "source": "zhihu",
        "entityType": entity_type,
        "cursor": str(payload.get("cursor", cursor)),
        "nextCursor": str(payload.get("next_cursor", "")),
        "hasMore": bool(payload.get("has_more")),
        "pageSize": limit,
        "message": str(payload.get("warning", "")),
        "comments": [_map_zhihu_comment(comment) for comment in comments if isinstance(comment, dict)],
    }


def _resolve_zhihu_question_comment_targets(
    *,
    runner: SubprocessRunner,
    settings: Settings,
    question_id: str,
) -> list[str]:
    payload = runner.run(
        [
            settings.zhihu_executable,
            "answers",
            question_id,
            "--limit",
            "3",
            "--json",
        ]
    ).json()
    answers = payload.get("data", []) if isinstance(payload, dict) else []
    if not isinstance(answers, list) or not answers:
        return []
    preferred: list[str] = []
    fallback: list[str] = []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        answer_id = str(answer.get("id", "")).strip()
        if not answer_id:
            continue
        if (_safe_int(answer.get("comment_count")) or 0) > 0:
            preferred.append(answer_id)
        else:
            fallback.append(answer_id)
    return preferred or fallback


def _fetch_xiaohongshu_comments(
    *,
    runner: SubprocessRunner,
    settings: Settings,
    source_item_id: str,
    canonical_url: str,
    cursor: str,
    limit: int,
) -> dict[str, Any]:
    target = canonical_url or source_item_id
    command = [settings.xiaohongshu_executable, "comments", target, "--json"]
    if cursor:
        command.extend(["--cursor", cursor])
    raw_payload = runner.run(command).json()
    payload = _unwrap_structured_payload(raw_payload)
    comments = payload.get("comments", []) if isinstance(payload.get("comments"), list) else []
    has_more = bool(payload.get("has_more"))
    next_cursor = str(payload.get("cursor", "")).strip()
    return {
        "source": "xiaohongshu",
        "entityType": "note",
        "cursor": cursor,
        "nextCursor": next_cursor if has_more else "",
        "hasMore": has_more,
        "pageSize": limit,
        "message": "",
        "comments": [
            _map_xiaohongshu_comment(comment)
            for comment in comments[:limit]
            if isinstance(comment, dict)
        ],
    }


def _map_bilibili_comment(comment: dict[str, Any]) -> dict[str, Any]:
    author = comment.get("author", {}) if isinstance(comment.get("author"), dict) else {}
    return {
        "authorName": str(author.get("name", "")).strip() or "Anonymous",
        "content": str(comment.get("message", "")).strip(),
        "likeCount": _safe_int(comment.get("like")),
    }


def _map_zhihu_comment(comment: dict[str, Any]) -> dict[str, Any]:
    author = comment.get("author", {}) if isinstance(comment.get("author"), dict) else {}
    return {
        "authorName": str(author.get("name", "")).strip() or "Anonymous",
        "content": str(comment.get("content", "")).strip(),
        "likeCount": _safe_int(comment.get("vote_count")),
    }


def _map_xiaohongshu_comment(comment: dict[str, Any]) -> dict[str, Any]:
    user_info = comment.get("user_info", {}) if isinstance(comment.get("user_info"), dict) else {}
    return {
        "authorName": str(user_info.get("nickname", "")).strip() or "Anonymous",
        "content": str(comment.get("content", "")).strip(),
        "likeCount": _safe_int(comment.get("like_count")),
    }


def _empty_payload(
    *,
    source: str,
    entity_type: str,
    cursor: str,
    limit: int,
    message: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "entityType": entity_type,
        "cursor": cursor,
        "nextCursor": "",
        "hasMore": False,
        "pageSize": limit,
        "message": message,
        "comments": [],
    }


def _comments_store(settings: Settings) -> PushStore:
    store = PushStore(settings.db_path)
    store.bootstrap()
    return store


def _is_bilibili_comments_risk_control(error_text: str) -> bool:
    lowered = str(error_text or "").lower()
    needles = (
        "412",
        "precondition failed",
        "security control policy",
        "风控",
    )
    return any(needle in lowered or needle in error_text for needle in needles)


def _unwrap_structured_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_positive_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
