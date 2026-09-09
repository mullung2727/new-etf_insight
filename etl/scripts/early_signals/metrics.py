"""수치 검증과 종목 해석 — 코드가 계산하고 LLM 은 추출만 한다 (PLAN §3.1, §6.3).

T07 분기/연간·연결/별도·단위를 섞은 증감률을 만들지 않는다. before=0 이면 비율 대신
     방향만 남긴다.
T08 목표가 변경 원인을 earnings_revision / multiple_revision / mixed / unexplained 로
     가른다. 네이버 prevGoalPrice 는 이전 목표가로 쓰지 않는다(§2 확인한 제약).
T09 종목코드는 마스터로 검증한다. 숫자 6자리로만 제한하지 않고 문자 포함 코드도 받는다.
"""
from __future__ import annotations

import re
from typing import Any

PERIOD_ANNUAL = re.compile(r"^(20\d{2})$")
PERIOD_QUARTER = re.compile(r"^(20\d{2})[ ._-]?Q([1-4])$", re.IGNORECASE)
PERIOD_HALF = re.compile(r"^(20\d{2})[ ._-]?H([12])$", re.IGNORECASE)
BASIS_ALIASES = {
    "연결": "consolidated", "consolidated": "consolidated",
    "별도": "separate", "separate": "separate", "개별": "separate",
}
# 같은 뜻의 단위만 환산한다. 서로 다른 차원(원 vs %)은 절대 섞지 않는다.
UNIT_SCALE = {
    "원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000,
    "조원": 1_000_000_000_000, "%": 1, "%p": 1, "배": 1, "장": 1, "개": 1,
}
UNIT_DIMENSION = {
    "원": "money", "천원": "money", "백만원": "money", "억원": "money", "조원": "money",
    "%": "ratio", "%p": "ratio_point", "배": "multiple", "장": "count", "개": "count",
}


def period_kind(period: str) -> tuple[str, str] | None:
    """(종류, 정규화 문자열). 인식 못 하면 None — 비교하지 않는다."""
    text = (period or "").strip()
    if PERIOD_ANNUAL.match(text):
        return "annual", text
    matched = PERIOD_QUARTER.match(text)
    if matched:
        return "quarter", f"{matched.group(1)}Q{matched.group(2)}"
    matched = PERIOD_HALF.match(text)
    if matched:
        return "half", f"{matched.group(1)}H{matched.group(2)}"
    return None


def comparable(metric: dict[str, Any]) -> tuple[bool, str]:
    """before/after 를 같은 잣대로 비교해도 되는지(§6.3).

    기간 종류·연결여부·단위 차원이 하나라도 다르면 증감률을 만들지 않는다.
    """
    period = period_kind(metric.get("period", ""))
    if not period:
        return False, "period_unrecognized"
    basis = BASIS_ALIASES.get((metric.get("basis") or "").strip())
    if not basis:
        return False, "basis_unknown"
    unit = (metric.get("unit") or "").strip()
    if unit not in UNIT_DIMENSION:
        return False, "unit_unknown"
    return True, ""


def compute_change(metric: dict[str, Any]) -> dict[str, Any]:
    """증감률은 코드가 계산한다. LLM 이 준 수치는 검증 후에만 쓴다.

    before=0 이면 비율을 만들지 않고 방향만 남긴다(흑자전환 등).
    """
    ok, reason = comparable(metric)
    before, after = metric.get("before"), metric.get("after")
    out = {"comparable": ok, "reason": reason, "change_pct": None, "direction": None,
           "abs_change": None}
    if not ok or after is None:
        return out
    if before is None:
        out["direction"] = "new_value"
        return out
    out["abs_change"] = after - before
    if before == 0:
        out["direction"] = "turned_positive" if after > 0 else (
            "turned_negative" if after < 0 else "flat")
        out["reason"] = "before_zero"
        return out
    out["change_pct"] = round(after / before - 1, 6)
    out["direction"] = "up" if after > before else ("down" if after < before else "flat")
    return out


def validate_metrics(metrics: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], list[str]]:
    """비교 가능한 것만 계산해 붙이고, 나머지는 사유를 남긴다."""
    issues: list[str] = []
    out = []
    for metric in metrics or []:
        computed = compute_change(metric)
        if not computed["comparable"]:
            issues.append(f"{metric.get('name', '?')}:{computed['reason']}")
        out.append({**metric, "computed": computed})
    return out, issues


TARGET_PRICE_NAMES = {"목표주가", "목표가", "target price", "tp"}
EARNINGS_NAMES = {"영업이익", "당기순이익", "eps", "지배순이익", "순이익"}
MULTIPLE_NAMES = {"per", "pbr", "ev/ebitda", "목표배수", "적정배수", "멀티플"}


def classify_target_price_change(metrics: list[dict[str, Any]]) -> str:
    """목표가 변경 원인 분리(§6.3).

    이익 추정 변경이 함께 있으면 earnings_revision, 배수 변경이면 multiple_revision,
    둘 다면 mixed, 근거가 없으면 unexplained 다.
    """
    names = {(m.get("name") or "").strip().lower() for m in metrics}
    if not (names & TARGET_PRICE_NAMES):
        return "not_target_price"
    earnings = bool(names & EARNINGS_NAMES)
    multiple = bool(names & MULTIPLE_NAMES)
    if earnings and multiple:
        return "mixed"
    if earnings:
        return "earnings_revision"
    if multiple:
        return "multiple_revision"
    return "unexplained"


def resolve_entity(
    code: str | None, name: str | None, master: dict[str, str]
) -> tuple[str | None, str]:
    """(확정 코드, 상태). LLM 추정 코드로 시세를 연결하지 않는다(§3.1).

    코드는 숫자 6자리로만 제한하지 않는다 — 문자 포함 코드도 마스터에 있으면 받는다.
    """
    code = (code or "").strip().upper()
    name = (name or "").strip()
    if code and code in master:
        if name and master[code] != name:
            return code, "name_mismatch"      # 코드 우선, 불일치는 표시
        return code, "resolved"
    by_name = {value: key for key, value in master.items()}
    if name and name in by_name:
        return by_name[name], "resolved_by_name"
    if code or name:
        return None, "unresolved_entity"
    return None, "no_entity"


INVALIDATION_TYPES = {"cancelled", "delayed"}


def has_invalidation(events: list[dict[str, Any]]) -> bool:
    """핵심 계약 취소·지연은 호평 다수보다 먼저 처리한다(§10.3-2, T12)."""
    return any(
        event.get("change", {}).get("change_type") in INVALIDATION_TYPES
        and event.get("change", {}).get("direction") in ("negative", "mixed")
        for event in events
    )
