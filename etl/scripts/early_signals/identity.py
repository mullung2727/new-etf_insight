"""식별키 결정적 생성 — PLAN §6.4.

LLM 은 식별키를 만들지 않는다. 여기 함수만 키를 만들고 storage.persist_* 가 저장 직전
같은 규칙을 다시 강제한다. 이 모듈의 규칙을 바꾸면 과거 이벤트를 재계산해야 하므로
IDENTITY_VERSION 을 올려 분리한다(PLAN §21.1).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable

IDENTITY_VERSION = "identity_v1"

# 문장 분할 규칙도 식별키의 일부다(§6.4). 종결부호+공백, 개행, 목록 항목 경계.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。？！])\s+|\n+|(?=^\s*[-*·•]\s)", re.MULTILINE)
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "ref", "referrer",
})


def canonical_hash(payload: Any) -> str:
    """키 정렬 JSON → UTF-8 → SHA-256 소문자 hex. null 은 명시한다."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(extracted_text: str) -> str:
    """source_version 의 content_hash — PDF 원본 바이트가 아니라 **추출 텍스트**의 해시.

    추출기가 바뀌어 텍스트가 달라지면 새 source_version 이 된다. 그래야 이전 버전의
    인용 좌표가 계속 유효하다(§6.4).
    """
    return hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()


def split_units(text: str, page: int | None) -> list[dict[str, Any]]:
    """근거 단위 = 문장 또는 표의 셀. 좌표는 원문 텍스트의 Unicode 인덱스(시작 포함/끝 제외).

    이벤트가 변화 1건이므로 단위를 문단이 아니라 문장으로 둔다. 한 문단의 서로 다른
    변화가 대개 다른 문장에 있어 좌표만으로 갈린다(§6.4).
    """
    units: list[dict[str, Any]] = []
    cursor = 0
    for index, piece in enumerate(_SENTENCE_SPLIT.split(text)):
        if piece is None:
            continue
        start = text.find(piece, cursor)
        if start < 0 or not piece.strip():
            cursor += len(piece or "")
            continue
        end = start + len(piece)
        cursor = end
        units.append({
            "unit_id": f"{page if page is not None else 'x'}:{len(units)}",
            "page": page,
            "start_char": start,
            "end_char": end,
            "text": piece,
        })
    return units


def locator_hash(source_version_id: str, unit: dict[str, Any]) -> str:
    """인용 위치 키 — identity_version, source_version_id, page, unit_id, 좌표."""
    return canonical_hash({
        "identity_version": IDENTITY_VERSION,
        "source_version_id": source_version_id,
        "page": unit.get("page"),
        "unit_id": unit["unit_id"],
        "start_char": unit["start_char"],
        "end_char": unit["end_char"],
    })


def normalize_metric_key(metric: dict[str, Any]) -> str:
    """metric 항목 키 — 항목명·기간·연결/별도 구분을 함께 넣는다."""
    name = _normalize_text(str(metric.get("name") or ""))
    return "|".join([
        METRIC_DICTIONARY.get(name, name),
        _normalize_text(str(metric.get("period") or "")),
        _normalize_text(str(metric.get("basis") or "")),
    ])


# 지표 사전 — 표기가 갈리는 항목만 최소로 둔다. 없으면 정규화한 원문 문자열을 쓴다.
METRIC_DICTIONARY = {
    "영업마진": "영업이익률",
    "영업이익율": "영업이익률",
    "opm": "영업이익률",
    "매출": "매출액",
    "순익": "당기순이익",
}


def change_key(
    metrics: Iterable[dict[str, Any]] | None,
    change_type: str | None,
    direction: str | None,
) -> str:
    """같은 근거 단위의 서로 다른 변화를 가르는 키(§6.4).

    1순위 정규화한 metric 항목 키 집합 → 2순위 (change_type, direction) →
    둘 다 같으면 같은 변화로 보고 병합한다(호출부가 같은 키를 받아 중복 저장을 막는다).
    """
    keys = sorted({normalize_metric_key(m) for m in (metrics or []) if m})
    if keys:
        return canonical_hash({"kind": "metrics", "keys": keys})
    return canonical_hash({
        "kind": "qualitative",
        "change_type": change_type,
        "direction": direction,
    })


def normalize_event_identity(
    source_version_id: str,
    entity_ids: Iterable[str],
    anchor_locator_hashes: Iterable[str],
    change_key_value: str,
    extract_version: str,
) -> dict[str, str]:
    """event_fingerprint 와 event_id 를 함께 만든다.

    자유문장(claim, mechanism)과 LLM 등급은 해시 입력에서 제외한다 — 표현만 바뀌어도
    키가 유지돼야 중복 이벤트가 쌓이지 않는다(§6.4).
    """
    anchors = sorted(set(anchor_locator_hashes))
    if not anchors:
        raise ValueError("검증된 anchor 가 없는 이벤트는 저장하지 않는다 (§6.4)")
    fingerprint = canonical_hash({
        "identity_version": IDENTITY_VERSION,
        "entity_ids": sorted(set(entity_ids)),
        "anchor_locator_hashes": anchors,
        "change_key": change_key_value,
    })
    event_id = canonical_hash({
        "source_version_id": source_version_id,
        "event_fingerprint": fingerprint,
        "extract_version": extract_version,
    })
    return {"event_fingerprint": fingerprint, "event_id": event_id}


def normalize_origin_group(
    *,
    pdf_bytes_hash: str | None = None,
    url: str | None = None,
    publisher_id: str | None = None,
    published_date: str | None = None,
    entity_ids: Iterable[str] | None = None,
    title: str | None = None,
    source_version_id: str | None = None,
) -> tuple[str, str]:
    """(origin_group_id, independence) — 독립 근거를 세는 키(§6.4).

    우선순위: PDF 바이트 해시 → 정규화 URL → (발행주체, 발행일, 기업집합, 제목).
    셋 다 없으면 source_version_id 자체를 그룹으로 삼고 independence=unknown 이다.
    """
    if pdf_bytes_hash:
        return canonical_hash({"v": IDENTITY_VERSION, "pdf": pdf_bytes_hash}), "known"
    if url:
        return canonical_hash({"v": IDENTITY_VERSION, "url": _normalize_url(url)}), "known"
    if publisher_id and published_date and title:
        return canonical_hash({
            "v": IDENTITY_VERSION,
            "publisher_id": publisher_id,
            "published_date": published_date,
            "entity_ids": sorted(set(entity_ids or [])),
            "title": _normalize_text(title),
        }), "known"
    if not source_version_id:
        raise ValueError("origin_group 입력이 모두 비었다")
    return canonical_hash({"v": IDENTITY_VERSION, "fallback": source_version_id}), "unknown"


def _normalize_text(value: str) -> str:
    """공백 정규화 · 소문자 · 구두점 제거. 제목/항목명 비교용."""
    text = unicodedata.normalize("NFKC", value).lower()
    text = re.sub(r"[^\w\s가-힣]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_url(url: str) -> str:
    """스킴·호스트 소문자, 추적 파라미터 제거, 쿼리 정렬."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if k.lower() not in _TRACKING_PARAMS)
    return urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(),
        parts.path.rstrip("/") or "/", urlencode(query), "",
    ))
