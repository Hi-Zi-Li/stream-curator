"""LLM selection for push cards."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import socket
from typing import Any
from urllib import error, request


@dataclass(slots=True)
class PushCandidate:
    item_uid: str
    source: str
    title: str
    author_name: str
    canonical_url: str
    excerpt: str
    stats_text: str


@dataclass(slots=True)
class PushCardDraft:
    item_uid: str
    recommendation: str
    summary: str
    reason: str
    tags: list[str]
    is_valid: bool


@dataclass(slots=True)
class PushSelectionResult:
    cards: list[PushCardDraft]
    provider: str
    model: str
    used_fallback: bool


class PushLlmClient:
    def __init__(
        self,
        *,
        api_url: str,
        api_key: str | None,
        model: str,
        fallback_model: str | None,
        timeout_seconds: int,
    ) -> None:
        self._api_url = api_url
        self._api_key = api_key
        self._model = model
        self._fallback_model = fallback_model
        self._timeout_seconds = timeout_seconds

    def select_push_cards(
        self,
        *,
        candidates: list[PushCandidate],
        limit: int,
    ) -> PushSelectionResult:
        if not candidates:
            return PushSelectionResult(cards=[], provider="none", model="none", used_fallback=True)

        if not self._api_key:
            return PushSelectionResult(
                cards=_fallback_drafts(candidates=candidates, limit=limit),
                provider="fallback",
                model="fallback",
                used_fallback=True,
            )

        tried_models: list[str] = []
        for model_name in [self._model, self._fallback_model]:
            if not model_name or model_name in tried_models:
                continue
            tried_models.append(model_name)
            try:
                payload = self._request(
                    model=model_name,
                    timeout_seconds=self._timeout_seconds,
                    system_prompt=_system_prompt(limit=limit),
                    user_prompt=_user_prompt(candidates),
                )
                drafts = _normalize_payload(payload, candidates=candidates, limit=limit)
                if drafts:
                    drafts = _top_up_drafts(drafts=drafts, candidates=candidates, limit=limit)
                    return PushSelectionResult(
                        cards=drafts,
                        provider="chat_completions",
                        model=model_name,
                        used_fallback=False,
                    )
            except Exception:
                continue

        return PushSelectionResult(
            cards=_fallback_drafts(candidates=candidates, limit=limit),
            provider="fallback",
            model="fallback",
            used_fallback=True,
        )

    def _request(
        self,
        *,
        model: str,
        timeout_seconds: int,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        req_payload = {
            "model": model,
            "temperature": 0.1,
            "max_tokens": 1800,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        req = request.Request(
            self._api_url,
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "stream-curator/0.1",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (TimeoutError, socket.timeout, error.URLError, error.HTTPError) as exc:
            raise RuntimeError(f"llm_request_failed: {exc}") from exc

        content = _extract_content(raw)
        payload = _parse_json_object(content)
        if not isinstance(payload, dict):
            raise RuntimeError("llm_response_not_object")
        return payload


def _extract_content(raw: dict[str, Any]) -> str:
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("llm_response_shape_invalid") from exc

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
            raise RuntimeError("llm_response_json_invalid") from None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("llm_response_json_invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("llm_response_not_object")
    return payload


def _normalize_payload(
    payload: dict[str, Any],
    *,
    candidates: list[PushCandidate],
    limit: int,
) -> list[PushCardDraft]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []

    candidate_by_uid = {candidate.item_uid: candidate for candidate in candidates}
    drafts: list[PushCardDraft] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_uid = _clean_text(item.get("item_uid"))
        if not item_uid or item_uid in seen:
            continue
        candidate = candidate_by_uid.get(item_uid)
        if candidate is None:
            continue

        recommendation = _clean_text(item.get("recommendation")).lower()
        if recommendation not in {"must_read", "worth_reading", "later"}:
            recommendation = "worth_reading"

        summary, summary_ok = _normalize_summary(_clean_text(item.get("summary")), candidate)
        reason, reason_ok = _normalize_reason(_clean_text(item.get("reason")), candidate)
        tags, tags_ok = _normalize_tags(item.get("tags"), source=candidate.source)
        drafts.append(
            PushCardDraft(
                item_uid=item_uid,
                recommendation=recommendation,
                summary=summary,
                reason=reason,
                tags=tags,
                is_valid=summary_ok and reason_ok and tags_ok,
            )
        )
        seen.add(item_uid)
        if len(drafts) >= limit:
            break
    return drafts


def _top_up_drafts(
    *,
    drafts: list[PushCardDraft],
    candidates: list[PushCandidate],
    limit: int,
) -> list[PushCardDraft]:
    if len(drafts) >= limit:
        return drafts[:limit]

    seen = {draft.item_uid for draft in drafts}
    for draft in _fallback_drafts(candidates=candidates, limit=limit):
        if draft.item_uid in seen:
            continue
        drafts.append(draft)
        seen.add(draft.item_uid)
        if len(drafts) >= limit:
            break
    return drafts


def _fallback_drafts(*, candidates: list[PushCandidate], limit: int) -> list[PushCardDraft]:
    sorted_candidates = sorted(candidates, key=_candidate_sort_key, reverse=True)[:limit]
    drafts: list[PushCardDraft] = []
    for index, candidate in enumerate(sorted_candidates):
        recommendation = "later"
        if index < 3:
            recommendation = "must_read"
        elif index < 6:
            recommendation = "worth_reading"
        drafts.append(
            PushCardDraft(
                item_uid=candidate.item_uid,
                recommendation=recommendation,
                summary=_fallback_summary(candidate),
                reason=_fallback_reason(candidate),
                tags=_fallback_tags(candidate),
                is_valid=False,
            )
        )
    return drafts


def _system_prompt(*, limit: int) -> str:
    return (
        "你是中文信息流编辑，只负责给个人推送页挑选内容。"
        f" 请从候选里选出最值得主动推送的 {limit} 条，并按重要性排序输出。"
        " 优先高信息密度、强观点、强事实、强方法、强可操作性的内容。"
        " 优先 AI、编程、软件工程、工具、产品、系统设计等主题。"
        " 降低标题党、情绪贴、灌水、广告、娱乐八卦的优先级。"
        " 只返回 JSON，不要解释，不要 markdown。"
        ' JSON 结构固定为 {"items":[{"item_uid":"","recommendation":"","summary":"","reason":"","tags":[""]}] }。'
        " summary 必须是中文，28 到 70 字，必须是你自己的概括，不要复述标题，不要直接摘抄原文。"
        " reason 必须是中文，6 到 16 字，说明推送原因。"
        " recommendation 只能是 must_read、worth_reading、later。"
        " tags 返回 1 到 3 个简短标签。"
        " 如果某条内容无法写出合格 summary 和 reason，就不要硬凑。"
    )


def _user_prompt(candidates: list[PushCandidate]) -> str:
    blocks = ["候选内容如下："]
    for index, candidate in enumerate(candidates, start=1):
        blocks.append(
            " | ".join(
                [
                    f"{index}",
                    f"item_uid={candidate.item_uid}",
                    f"source={candidate.source}",
                    f"title={candidate.title or '无标题'}",
                    f"excerpt={candidate.excerpt or '无'}",
                    f"signals={candidate.stats_text or '无'}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _normalize_summary(summary: str, candidate: PushCandidate) -> tuple[str, bool]:
    text = _clean_text(summary)
    if not text or not _has_cjk(text) or len(text) < 18:
        return _fallback_summary(candidate), False
    if _looks_like_title_copy(text, candidate.title):
        return _fallback_summary(candidate), False
    if _looks_like_excerpt_copy(text, candidate.excerpt):
        return _fallback_summary(candidate), False
    if _looks_like_machine_meta(text):
        return _fallback_summary(candidate), False
    return text[:90], True


def _normalize_reason(reason: str, candidate: PushCandidate) -> tuple[str, bool]:
    text = _clean_text(reason)
    if not text or not _has_cjk(text):
        return _fallback_reason(candidate), False
    if _looks_like_machine_meta(text):
        return _fallback_reason(candidate), False
    return text[:24], True


def _normalize_tags(value: object, *, source: str) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return _fallback_tags_from_source(source), False
    tags: list[str] = []
    for entry in value:
        tag = _clean_text(entry)
        if not tag or len(tag) > 10 or _looks_like_machine_meta(tag):
            continue
        tags.append(tag)
        if len(tags) >= 3:
            break
    if not tags:
        return _fallback_tags_from_source(source), False
    return tags, True


def _fallback_summary(candidate: PushCandidate) -> str:
    title = _clean_text(candidate.title)
    if title:
        return f"{_source_label(candidate.source)}候选《{title[:24]}》待重新生成摘要。"
    return f"{_source_label(candidate.source)}候选待重新生成摘要。"


def _fallback_reason(candidate: PushCandidate) -> str:
    topic = _topic_label(candidate)
    if topic:
        return f"{topic}主题待重写"
    return "待重写"


def _fallback_tags(candidate: PushCandidate) -> list[str]:
    tags = []
    topic = _topic_label(candidate)
    if topic:
        tags.append(topic)
    tags.extend(_fallback_tags_from_source(candidate.source))
    return tags[:3]


def _fallback_tags_from_source(source: str) -> list[str]:
    return [_source_label(source)]


def _topic_label(candidate: PushCandidate) -> str:
    text = f"{candidate.title} {candidate.excerpt}".lower()
    mapping = [
        ("ai", "AI"),
        ("llm", "LLM"),
        ("agent", "Agent"),
        ("codex", "Codex"),
        ("claude", "Claude"),
        ("gpt", "GPT"),
        ("模型", "模型"),
        ("编程", "编程"),
        ("代码", "编程"),
        ("开发", "开发"),
        ("工程", "工程"),
        ("产品", "产品"),
        ("系统", "系统"),
        ("推理", "推理"),
        ("论文", "论文"),
    ]
    for needle, label in mapping:
        if needle in text:
            return label
    return ""


def _candidate_sort_key(candidate: PushCandidate) -> tuple[int, int, int, int]:
    topic_bonus = 1 if _topic_label(candidate) else 0
    excerpt_len = min(len(_clean_text(candidate.excerpt)), 240)
    title_len = min(len(_clean_text(candidate.title)), 100)
    signal_len = min(len(_clean_text(candidate.stats_text)), 80)
    return (topic_bonus, excerpt_len, signal_len, title_len)


def _looks_like_title_copy(text: str, title: str) -> bool:
    clean_text = _clean_text(text)
    clean_title = _clean_text(title)
    if not clean_text or not clean_title:
        return False
    return clean_text == clean_title or clean_text in clean_title or clean_title in clean_text


def _looks_like_excerpt_copy(text: str, excerpt: str) -> bool:
    clean_text = _strip_non_word(_clean_text(text))
    clean_excerpt = _strip_non_word(_clean_text(excerpt))
    if len(clean_text) < 18 or len(clean_excerpt) < 18:
        return False
    if clean_text in clean_excerpt or clean_excerpt in clean_text:
        return True
    prefix_len = min(len(clean_text), 24)
    return clean_excerpt.startswith(clean_text[:prefix_len])


def _looks_like_machine_meta(text: str) -> bool:
    lowered = text.lower()
    if any(
        needle in lowered
        for needle in (
            "rank #",
            "heuristic",
            "fallback",
            "dense_body",
            "provider",
            "json",
            "must_read",
            "worth_reading",
        )
    ):
        return True
    return any(char in text for char in "{}[]")


def _strip_non_word(text: str) -> str:
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


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


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)
