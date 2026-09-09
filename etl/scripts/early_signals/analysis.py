"""변화 추출 — LLM 은 사실·전망·변경점만 뽑고 추천하지 않는다 (PLAN §6).

모든 LLM 호출은 기존 generate_json 을 통과하고 search=False 를 유지한다(§18).
모델은 지정하지 않는다 — codex CLI 기본값을 쓰는 기존 파이프라인과 같은 경로다.
LLM 출력은 그대로 저장하지 않는다. validate_evidence 가 인용을 원문과 대조하고
identity 가 결정적 키를 만든 뒤에만 이벤트가 된다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .identity import canonical_hash, change_key, locator_hash, normalize_event_identity
from .metrics import classify_target_price_change, resolve_entity, validate_metrics

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schemas" / "extract_changes.json"
PROMPT_PATH = HERE / "prompts" / "extract_changes.md"
PROMPT_VERSION = canonical_hash(PROMPT_PATH.read_text(encoding="utf-8"))[:16]
MAX_CHARS = 12_000
OVERLAP = 500


def build_prompt(record: dict[str, Any], text: str) -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").format(
        published=record.get("published_at") or "",
        source_type=record["source_type"],
        source_key=record["source_key"],
        text=text,
    )


def chunk_text(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[tuple[int, str]]:
    """페이지 경계(\\f)를 보존한 청크. 마지막 페이지까지 처리한다(§6.1).

    반환은 (원문 시작 오프셋, 청크 텍스트) — 인용 좌표를 원문 기준으로 되돌리기 위함이다.
    """
    chunks: list[tuple[int, str]] = []
    offset = 0
    for page in text.split("\f"):
        if len(page) <= max_chars:
            if page.strip():
                chunks.append((offset, page))
            offset += len(page) + 1
            continue
        start = 0
        while start < len(page):
            piece = page[start:start + max_chars]
            if piece.strip():
                chunks.append((offset + start, piece))
            if start + max_chars >= len(page):
                break
            start += max_chars - overlap
        offset += len(page) + 1
    return chunks


def call_llm(prompt: str, *, model: str | None = None, generate=None) -> str:
    generate = generate or _default_generate
    return generate(prompt, output_schema_path=SCHEMA_PATH, search=False, model=model)


def _default_generate(prompt: str, **kwargs) -> str:
    from new_etf_insight.llm import generate_json

    return generate_json(prompt, **kwargs)


def extract_changes(
    record: dict[str, Any], units: list[dict[str, Any]], *, model: str | None = None,
    generate: Callable[..., str] | None = None, extract_version: str = "extract_v1",
    entity_master: dict[str, str] | None = None,
) -> dict[str, Any]:
    """한 원문의 모든 청크를 추출하고 검증된 변화만 이벤트 후보로 만든다."""
    started = time.monotonic()
    raw_changes: list[dict[str, Any]] = []
    calls = 0
    errors: list[str] = []
    for offset, piece in chunk_text(record["extracted_text"]):
        calls += 1
        try:
            payload = json.loads(call_llm(build_prompt(record, piece), model=model,
                                          generate=generate))
        except Exception as exc:      # 청크 하나가 실패해도 문서 전체를 버리지 않는다
            errors.append(f"chunk@{offset}: {exc}")
            continue
        if not payload.get("has_change"):
            continue
        for change in payload.get("changes") or []:
            change["_chunk_offset"] = offset
            raw_changes.append(change)

    events, rejected = [], []
    unresolved = 0
    for change in raw_changes:
        built = build_event(record, units, change, extract_version, entity_master)
        if built is None:
            rejected.append(change)
            continue
        if built.get("_unresolved"):
            unresolved += 1
            rejected.append(change)
            continue
        events.append(built)
    return {
        "source_version_id": record["source_version_id"],
        "events": _dedupe(events),
        "rejected": len(rejected),
        "unresolved_entities": unresolved,
        "llm_calls": calls,
        "errors": errors,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "chars": len(record["extracted_text"]),
    }


def build_event(
    record: dict[str, Any], units: list[dict[str, Any]], change: dict[str, Any],
    extract_version: str, entity_master: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """인용을 원문에서 찾아 anchor 를 확정하고 결정적 키를 만든다.

    인용이 원문에 없으면 이벤트를 만들지 않는다(§6.3 인용 검증, T11).
    """
    quote = (change.get("quote") or "").strip()
    if not quote:
        return None
    anchors = find_anchor_units(record["extracted_text"], units, quote)
    if not anchors:
        return None
    entity_ids, entity_status = _resolve_entities(record, change, entity_master)
    if not entity_ids:
        # 종목을 못 붙인 변화는 unresolved 로 남기고 시세에 연결하지 않는다(§3.1, T09)
        return {"_unresolved": True, "entity_status": entity_status}
    checked_metrics, metric_issues = validate_metrics(change.get("metrics"))
    change = {**change, "metrics": checked_metrics,
              "target_price_cause": classify_target_price_change(checked_metrics),
              "metric_issues": metric_issues}
    key = change_key(change.get("metrics"), change.get("change_type"), change.get("direction"))
    anchor_hashes = [locator_hash(record["source_version_id"], unit) for unit in anchors]
    identity = normalize_event_identity(
        record["source_version_id"], entity_ids, anchor_hashes, key, extract_version)
    return {
        **identity,
        "change_key": key,
        "source_version_id": record["source_version_id"],
        "entity_ids": sorted(set(entity_ids)),
        "anchor_locators": [
            {"locator_hash": h, **{k: unit[k] for k in ("unit_id", "page", "start_char", "end_char")}}
            for h, unit in zip(anchor_hashes, anchors)
        ],
        "change": {k: v for k, v in change.items() if not k.startswith("_")},
        "entity_status": entity_status,
        "extract_version": extract_version,
    }


def _resolve_entities(
    record: dict[str, Any], change: dict[str, Any], master: dict[str, str] | None
) -> tuple[list[str], str]:
    """마스터가 있으면 검증하고, 없으면 원문이 이미 확정한 코드만 쓴다(§3.1)."""
    if master is not None:
        code, status = resolve_entity(change.get("entity_code"), change.get("entity_name"), master)
        if code:
            return [code], status
        fallback = [c for c in record.get("entity_ids", []) if c in master]
        return (fallback, "resolved_by_source") if fallback else ([], status)
    codes = [c for c in [change.get("entity_code")] if c] or record.get("entity_ids", [])
    return (codes, "resolved_unverified") if codes else ([], "unresolved_entity")


def find_anchor_units(
    text: str, units: list[dict[str, Any]], quote: str
) -> list[dict[str, Any]]:
    """인용문이 실제 원문에 있는지 확인하고 겹치는 근거 단위를 돌려준다.

    LLM 이 제시한 부분 인용은 해당 근거 단위 전체 범위로 정규화한다(§6.4).
    """
    start = text.find(quote)
    if start < 0:
        collapsed = " ".join(quote.split())
        start = " ".join(text.split()).find(collapsed)
        if start < 0:
            return []
        return _units_by_normalized(text, units, collapsed)
    end = start + len(quote)
    return [u for u in units if u["start_char"] < end and u["end_char"] > start]


def _units_by_normalized(
    text: str, units: list[dict[str, Any]], collapsed: str
) -> list[dict[str, Any]]:
    """공백만 다른 인용은 단위 텍스트를 정규화해 맞춘다."""
    return [u for u in units if collapsed in " ".join(text[u["start_char"]:u["end_char"]].split())]


def _dedupe(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """청크 겹침으로 같은 변화가 두 번 나오면 하나만 남긴다(§6.4)."""
    seen: dict[str, dict[str, Any]] = {}
    for event in events:
        seen.setdefault(event["event_id"], event)
    return list(seen.values())
