"""가격 시나리오 — LLM 은 가정을 추출하고 계산·등급 제한은 코드가 한다 (PLAN §10.2).

세 방법만 쓴다.
  earnings_multiple  EPS·배수·하방 가정이 모두 기간과 근거를 갖출 때
  broker_reference   최근 45일 서로 다른 증권사 목표가 2건 이상 (등급 최대 medium)
  unavailable        위 조건 불충족 → 가격 매력 unknown

12개월 목표가를 8~12주 목표수익으로 바꾸지 않는다. 목표가의 기간은 그대로 보존하고,
기간이 평가 지평보다 길면 등급 상한을 medium 으로 묶는다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .metrics import period_kind

BROKER_WINDOW_DAYS = 45
BROKER_DISCOUNT = 0.75          # Pc = P0 + 0.75 x (기준값 - P0). 25% 할인은 정책 가정
MIN_BROKERS = 2
REFERENCE_HORIZON_DAYS = 84     # 8~12주 평가 지평
HIGH_UPSIDE, HIGH_REWARD_RISK = 0.20, 2.0
MEDIUM_UPSIDE, MEDIUM_REWARD_RISK = 0.10, 1.5


def build_scenario(
    *, p0: float | None, price_as_of: str | None, events: list[dict[str, Any]],
    cutoff_date: str, low_20d: float | None = None,
) -> dict[str, Any]:
    """가격 시나리오 하나를 만든다. 못 만들면 method=unavailable 이다."""
    base = {
        "method": "unavailable", "reference_horizon": None, "price_as_of": price_as_of,
        "P0": p0, "base_reference_price": None, "conservative_price": None,
        "risk_reference_price": None, "assumptions": [], "evidence_refs": [],
        "upside": None, "downside": None, "reward_risk": None,
        "grade_cap": None, "reason_codes": [],
    }
    if not p0 or p0 <= 0:
        base["reason_codes"].append("no_current_price")
        return base

    scenario = _earnings_multiple(events, p0) or _broker_reference(events, p0, cutoff_date)
    if scenario is None:
        base["reason_codes"].append("scenario_unavailable")
        return base

    result = {**base, **scenario}
    if result["risk_reference_price"] is None and low_20d and low_20d < p0:
        # 근거 있는 하방이 없으면 최근 20일 최저가를 참고로 쓰되 등급을 medium 으로 묶는다.
        # 이 가격에서 손실이 멈춘다는 뜻이 아니다.
        result["risk_reference_price"] = low_20d
        result["assumptions"] = result["assumptions"] + ["risk_price=recent_20d_low(policy)"]
        result["grade_cap"] = "medium"
        result["reason_codes"] = result["reason_codes"] + ["risk_price_from_20d_low"]
    return _compute_ratios(result)


def _compute_ratios(scenario: dict[str, Any]) -> dict[str, Any]:
    """Pr >= P0, 분모 0, 단위 충돌이면 비율은 null 이고 등급은 unknown 이다."""
    p0 = scenario["P0"]
    pc = scenario.get("conservative_price")
    pr = scenario.get("risk_reference_price")
    if pc is None or pr is None:
        scenario["reason_codes"] = scenario["reason_codes"] + ["incomplete_scenario"]
        return scenario
    if pr >= p0:
        scenario["reason_codes"] = scenario["reason_codes"] + ["risk_price_ge_current"]
        return scenario
    upside = pc / p0 - 1
    downside = 1 - pr / p0
    if downside <= 0:
        scenario["reason_codes"] = scenario["reason_codes"] + ["zero_downside"]
        return scenario
    scenario["upside"] = round(upside, 6)
    scenario["downside"] = round(downside, 6)
    scenario["reward_risk"] = round(upside / downside, 4)
    return scenario


def _earnings_multiple(events: list[dict[str, Any]], p0: float) -> dict[str, Any] | None:
    """EPS·배수·하방 가정이 모두 기간과 근거를 갖췄을 때만 성립한다.

    LLM 이 출처 없이 고른 비교 기업·배수는 쓰지 않는다 — metric 에 근거 인용이 붙어
    있어야 하고, 기간이 인식되지 않으면 버린다.
    """
    eps = _pick_metric(events, {"eps", "지배eps", "주당순이익"})
    multiple = _pick_metric(events, {"per", "목표배수", "적정배수", "target per"})
    floor = _pick_metric(events, {"하방eps", "보수적eps", "하방배수"})
    if not eps or not multiple:
        return None
    eps_value = eps["metric"].get("after")
    multiple_value = multiple["metric"].get("after")
    if not eps_value or not multiple_value or eps_value <= 0 or multiple_value <= 0:
        return None
    fair = eps_value * multiple_value
    risk = None
    if floor and floor["metric"].get("after"):
        risk = floor["metric"]["after"] * multiple_value if "eps" in (
            floor["metric"].get("name") or "").lower() else eps_value * floor["metric"]["after"]
    horizon = period_kind(eps["metric"].get("period", ""))
    return {
        "method": "earnings_multiple",
        "reference_horizon": horizon[1] if horizon else None,
        "base_reference_price": fair,
        "conservative_price": fair,
        "risk_reference_price": risk,
        "assumptions": [
            f"EPS={eps_value} ({eps['metric'].get('period')})",
            f"multiple={multiple_value}",
        ],
        "evidence_refs": [eps["event_id"], multiple["event_id"]],
        "grade_cap": None,
        "reason_codes": [],
    }


def _pick_metric(events: list[dict[str, Any]], names: set[str]) -> dict[str, Any] | None:
    """근거(이벤트)에 붙어 있고 비교 가능하다고 검증된 metric 만 고른다."""
    for event in events:
        for metric in event.get("change", {}).get("metrics") or []:
            name = (metric.get("name") or "").strip().lower()
            if name in names and (metric.get("computed") or {}).get("comparable"):
                return {"metric": metric, "event_id": event.get("event_id")}
    return None


def _broker_reference(
    events: list[dict[str, Any]], p0: float, cutoff_date: str
) -> dict[str, Any] | None:
    """최근 45일 서로 다른 증권사 목표가 2건 이상. 낮은 목표가를 기준값으로 쓴다."""
    limit = date.fromisoformat(cutoff_date) - timedelta(days=BROKER_WINDOW_DAYS)
    targets: dict[str, float] = {}
    refs: list[str] = []
    for event in events:
        published = (event.get("published_at") or "")[:10]
        if not published or datetime.strptime(published, "%Y-%m-%d").date() < limit:
            continue
        group = event.get("origin_group_id") or event.get("source_version_id")
        for metric in event.get("change", {}).get("metrics") or []:
            if (metric.get("name") or "").strip().lower() not in {"목표주가", "목표가", "target price"}:
                continue
            value = metric.get("after")
            if value and value > 0 and group not in targets:
                targets[group] = value
                refs.append(event.get("event_id"))
    if len(targets) < MIN_BROKERS:
        return None
    base = min(targets.values())
    return {
        "method": "broker_reference",
        "reference_horizon": "12m_target",      # 목표가 기간을 8~12주로 바꾸지 않는다
        "base_reference_price": base,
        "conservative_price": p0 + BROKER_DISCOUNT * (base - p0),
        "risk_reference_price": None,           # 근거 있는 하방 없음 → 호출부가 20일 최저가로 보완
        "assumptions": [
            f"lowest_of_{len(targets)}_broker_targets={base}",
            f"policy_discount={BROKER_DISCOUNT}",
        ],
        "evidence_refs": refs,
        "grade_cap": "medium",                  # 장기 평가 참고 → 최대 medium
        "reason_codes": ["broker_reference_capped_medium"],
    }


def grade_price(scenario: dict[str, Any]) -> tuple[str, list[str]]:
    """상승 여지와 보상/위험으로 등급을 매기고 방법별 상한을 적용한다(§10.1)."""
    reasons = list(scenario.get("reason_codes", []))
    upside, reward_risk = scenario.get("upside"), scenario.get("reward_risk")
    if upside is None or reward_risk is None:
        return "unknown", reasons or ["price_scenario_incomplete"]
    if upside >= HIGH_UPSIDE and reward_risk >= HIGH_REWARD_RISK:
        grade = "high"
    elif upside >= MEDIUM_UPSIDE and reward_risk >= MEDIUM_REWARD_RISK:
        grade = "medium"
    else:
        grade = "low"
        reasons.append("upside_or_reward_risk_below_medium")
    cap = scenario.get("grade_cap")
    if cap == "medium" and grade == "high":
        grade = "medium"
        reasons.append("grade_capped_by_method")
    horizon = scenario.get("reference_horizon")
    if grade == "high" and horizon == "12m_target":
        grade = "medium"
        reasons.append("grade_capped_by_horizon")
    return grade, reasons
