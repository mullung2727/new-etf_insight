"""테마 형성과 후보 수명주기 (PLAN §8, §11).

테마는 산업명이 아니라 **같은 수익 메커니즘**으로 묶는다. 같은 회사에 두 사업이
있다는 이유로 합치지 않는다. LLM 은 연결을 제안만 하고 validate_theme_links 가
근거 참조와 기업 수를 확인한다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

THEME_WINDOW_DAYS = 30
FORMING_COMPANIES = 2
SUPPORTED_COMPANIES = 3
SUPPORTED_GROUPS = 2

# 수명주기 임계 (§11)
CONFIRMATION_GRACE_DAYS = 7
STALE_EVIDENCE_DAYS = 30
INACTIVE_DAYS = 60
EPISODE_DAYS = 84


def normalize_mechanism(text: str) -> str:
    """메커니즘 문자열 정규화. 표기가 달라도 같은 것은 같은 키가 되게 한다."""
    lowered = re.sub(r"\s+", " ", (text or "").strip().lower())
    return re.sub(r"[^\w가-힣 ]", "", lowered)


def validate_theme_links(
    theme: dict[str, Any], events_by_entity: dict[str, list[dict[str, Any]]], cutoff_date: str
) -> dict[str, Any]:
    """LLM 이 제안한 테마 연결에서 근거 없는 기업을 떨어뜨리고 상태를 정한다.

    각 기업 연결마다 **그 메커니즘을 실제로 기술한 원문 근거**가 있어야 한다.
    회사 수와 주식코드 수를 따로 세어, 같은 기업의 다른 코드로 기업 수를 늘리지 않는다.
    """
    mechanism = normalize_mechanism(theme.get("mechanism", ""))
    limit = date.fromisoformat(cutoff_date) - timedelta(days=THEME_WINDOW_DAYS)
    linked: list[dict[str, Any]] = []
    dropped: list[str] = []
    for entity in theme.get("entity_ids", []):
        supporting = [
            event for event in events_by_entity.get(entity, [])
            if _within(event, limit)
            and mechanism
            and mechanism in normalize_mechanism(_mechanism_text(event))
        ]
        if supporting:
            linked.append({
                "entity_id": entity,
                "company_key": _company_key(entity, supporting),
                "event_ids": [e.get("event_id") for e in supporting],
                "origin_groups": sorted({e.get("origin_group_id") for e in supporting
                                         if e.get("origin_group_id")}),
            })
        else:
            dropped.append(entity)

    companies = {item["company_key"] for item in linked}
    groups = {group for item in linked for group in item["origin_groups"]}
    return {
        "theme_id": theme.get("theme_id"),
        "name": theme.get("name"),
        "mechanism": theme.get("mechanism"),
        "linked": linked,
        "dropped_entities": dropped,
        "company_count": len(companies),
        "ticker_count": len(linked),
        "origin_group_count": len(groups),
        "status": theme_status(len(companies), len(groups), linked),
    }


def _mechanism_text(event: dict[str, Any]) -> str:
    change = event.get("change", {})
    return " ".join(filter(None, [change.get("mechanism"), change.get("claim"),
                                  change.get("category_raw")]))


def _company_key(entity: str, events: list[dict[str, Any]]) -> str:
    """같은 기업의 우선주·다른 코드가 기업 수를 부풀리지 않게 한다.

    지금은 코드 앞 5자리를 회사 근사로 쓴다(005930/005935 → 00593). 정확한 기업 식별자가
    생기면 그것으로 교체한다.
    """
    return entity[:5] if len(entity) >= 5 else entity


def _within(event: dict[str, Any], limit: date) -> bool:
    published = (event.get("published_at") or "")[:10]
    if not published:
        return False
    try:
        return datetime.strptime(published, "%Y-%m-%d").date() >= limit
    except ValueError:
        return False


def theme_status(companies: int, groups: int, linked: list[dict[str, Any]]) -> str:
    """§8 상태 기준. supported 는 기업 3곳 이상 + origin_group 2개 이상."""
    if not linked:
        return "inactive"
    if companies >= SUPPORTED_COMPANIES and groups >= SUPPORTED_GROUPS:
        return "supported"
    if companies >= FORMING_COMPANIES:
        return "forming"
    return "single_company"


def apply_counterevidence(state: str, has_counterevidence: bool) -> str:
    """핵심 반박·지연이 있으면 지지 조건을 잃는다(§8 weakening)."""
    if has_counterevidence and state in ("supported", "forming"):
        return "weakening"
    return state


# ── 후보 수명주기 (§11) ──────────────────────────────────────────────────────
def lifecycle(
    *, cutoff_date: str, latest_evidence_date: str | None, expected_end: str | None,
    episode_started: str | None, has_open_condition: bool,
) -> dict[str, Any]:
    """기한 경과에 따른 강등·종료를 판정한다. LLM 이 기한을 미루지 못한다."""
    today = date.fromisoformat(cutoff_date)
    flags: list[str] = []
    timing_hold = False

    if expected_end:
        end = _parse_day(expected_end)
        if end and today > end + timedelta(days=CONFIRMATION_GRACE_DAYS):
            flags.append("confirmation_overdue")
            timing_hold = True      # timing high 해제

    days_since = None
    if latest_evidence_date:
        latest = _parse_day(latest_evidence_date)
        if latest:
            days_since = (today - latest).days
            if days_since > STALE_EVIDENCE_DAYS:
                flags.append("stale_evidence")
            if days_since > INACTIVE_DAYS and not has_open_condition:
                flags.append("inactive")

    episode_over = False
    if episode_started:
        started = _parse_day(episode_started)
        if started and (today - started).days > EPISODE_DAYS:
            flags.append("episode_expired")
            episode_over = True

    return {
        "flags": flags,
        "timing_hold": timing_hold,
        "block_review_buy": "stale_evidence" in flags or timing_hold,
        "episode_over": episode_over,
        "days_since_evidence": days_since,
    }


def _parse_day(value: str) -> date | None:
    text = (value or "").strip()[:10]
    if len(text) == 7:
        text += "-28"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
