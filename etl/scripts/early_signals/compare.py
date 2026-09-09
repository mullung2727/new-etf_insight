"""직전 문서 비교 — 지금 변화가 새것인지 반복인지 가른다 (PLAN §6.2, §6.3 novelty).

같은 종목·같은 증권사의 **직전 2개 독립 문서**와 비교한다. 같은 PDF 재게시를 2건으로
채우지 않고, 과거 문서가 0~1개면 그 개수 그대로 비교한다. 없는 이전 값을 추정하지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .metrics import BASIS_ALIASES, period_kind

NOVELTY = ("new_in_corpus", "changed", "repeated", "baseline_missing")
NEEDS_DETAIL = {
    "new", "upgraded", "downgraded", "cancelled", "delayed",
}


def needs_comparison(change: dict[str, Any]) -> bool:
    """§6.2 상세 분석 진입 조건. 단순 호평·급등만이면 비교하지 않는다."""
    if change.get("change_type") in NEEDS_DETAIL:
        return True
    if any(m.get("before") is not None for m in change.get("metrics") or []):
        return True
    return bool((change.get("confirmation_condition") or "").strip())


def load_prior_documents(
    con: sqlite3.Connection, entity_id: str, publisher: str | None, before: str, limit: int = 2
) -> list[dict[str, Any]]:
    """같은 종목·증권사의 직전 독립 문서. origin_group 기준으로 중복을 접는다.

    페이지 한도로 과거를 다 못 봤을 수 있다 — 호출부가 coverage_incomplete 로 표시한다.
    """
    rows = con.execute(
        "SELECT s.source_version_id, s.origin_group_id, s.published_at, s.raw_json,"
        " e.change_json FROM events e"
        " JOIN source_versions s ON s.source_version_id = e.source_version_id"
        " WHERE s.published_at < ? AND e.entity_ids_json LIKE ?"
        " ORDER BY s.published_at DESC",
        (before, f'%"{entity_id}"%'),
    ).fetchall()
    seen: dict[str, dict[str, Any]] = {}
    for sv_id, group_id, published_at, raw_json, change_json in rows:
        raw = json.loads(raw_json) if raw_json else {}
        if publisher and (raw.get("broker") or "") != publisher:
            continue
        entry = seen.setdefault(group_id, {
            "source_version_id": sv_id, "origin_group_id": group_id,
            "published_at": published_at, "publisher": raw.get("broker"), "changes": [],
        })
        entry["changes"].append(json.loads(change_json))
        if len(seen) >= limit and group_id not in seen:
            break
    return list(seen.values())[:limit]


def metric_signature(metric: dict[str, Any]) -> str | None:
    """비교 가능한 metric 만 서명을 만든다. 기간·연결여부가 다르면 다른 항목이다."""
    period = period_kind(metric.get("period", ""))
    basis = BASIS_ALIASES.get((metric.get("basis") or "").strip())
    name = (metric.get("name") or "").strip().lower()
    if not period or not basis or not name:
        return None
    return f"{name}|{period[1]}|{basis}"


def compare_history(
    change: dict[str, Any], priors: list[dict[str, Any]]
) -> dict[str, Any]:
    """지금 변화를 직전 문서와 대조해 novelty 를 정한다.

    비교 대상이 하나도 없으면 baseline_missing 이다 — new_in_corpus 로 올리지 않는다.
    저장 자료에서 처음 봤다는 뜻이지 세상 최초라는 뜻이 아니기 때문이다(§6.3).
    """
    if not priors:
        return {"novelty": "baseline_missing", "prior_documents": 0,
                "prior_claim": None, "reason": "no_prior_document"}

    current = {metric_signature(m): m for m in change.get("metrics") or []}
    current.pop(None, None)
    prior_values: dict[str, Any] = {}
    prior_claims: list[str] = []
    for prior in priors:
        for old in prior["changes"]:
            prior_claims.append(old.get("claim") or "")
            for metric in old.get("metrics") or []:
                signature = metric_signature(metric)
                if signature and signature not in prior_values:
                    prior_values[signature] = metric

    shared = [key for key in current if key in prior_values]
    for key in shared:
        before = prior_values[key].get("after", prior_values[key].get("before"))
        after = current[key].get("after")
        if before is not None and after is not None and before != after:
            return {"novelty": "changed", "prior_documents": len(priors),
                    "prior_claim": prior_values[key], "reason": f"{key} {before}->{after}"}
    if shared:
        return {"novelty": "repeated", "prior_documents": len(priors),
                "prior_claim": prior_values[shared[0]], "reason": "same_values"}

    claim = (change.get("claim") or "").strip()
    if claim and any(claim == old.strip() for old in prior_claims):
        return {"novelty": "repeated", "prior_documents": len(priors),
                "prior_claim": None, "reason": "same_claim"}
    return {"novelty": "new_in_corpus", "prior_documents": len(priors),
            "prior_claim": None, "reason": "no_matching_prior_metric"}
