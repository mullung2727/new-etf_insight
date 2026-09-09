"""종목 평가 조립과 보고서 생성 (PLAN §7, §16, §17.4).

render_artifact 는 저장된 JSON 만 쓴다 — 분석 LLM 을 다시 호출하지 않는다(§14).
"""
from __future__ import annotations

import collections
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import policy, pricing, themes
from .metrics import has_invalidation

ROOT = Path(__file__).resolve().parents[3]
EXPORT_DIR = ROOT / "etl" / "exports" / "early_signals"


def load_events_by_entity(
    con: sqlite3.Connection, manifest_id: str
) -> dict[str, list[dict[str, Any]]]:
    """이 run 의 manifest 에 속한 원문의 이벤트만 종목별로 모은다.

    events 전체를 읽으면 다른 cutoff 의 run 에서 만든 이벤트가 섞여 시점 격리가
    깨진다(§4.2 manifest 동결). 반드시 manifest 로 거른다.
    """
    rows = con.execute(
        "SELECT e.event_id, e.source_version_id, e.entity_ids_json, e.change_json,"
        " e.anchor_locators_json, s.origin_group_id, s.independence, s.published_at,"
        " s.source_type, s.source_key FROM events e"
        " JOIN source_versions s ON s.source_version_id = e.source_version_id"
        " JOIN manifest_entries m ON m.source_version_id = e.source_version_id"
        " WHERE m.manifest_id = ?", (manifest_id,)
    ).fetchall()
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        (event_id, sv_id, entities_json, change_json, anchors_json,
         group_id, independence, published_at, source_type, source_key) = row
        event = {
            "event_id": event_id, "source_version_id": sv_id,
            "change": json.loads(change_json), "anchors": json.loads(anchors_json),
            "origin_group_id": group_id, "independence": independence,
            "published_at": published_at, "source_type": source_type,
            "source_key": source_key,
        }
        for entity in json.loads(entities_json):
            by_entity[entity].append(event)
    return by_entity


def assess_entities(
    by_entity: dict[str, list[dict[str, Any]]], cutoff_date: str, *, duck=None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """종목별 세 축 등급과 행동을 계산한다. 등급·행동은 코드가 정한다(§13.3)."""
    own = duck is None
    if own:
        import duckdb

        duck = duckdb.connect(str(policy.KRX_DB), read_only=True)
    try:
        calendar = policy.market_calendar(cutoff_date.replace("-", ""), duck)
        sessions = calendar["sessions"]
        assessments = []
        for entity, events in sorted(by_entity.items()):
            groups = {e["source_version_id"]: e["origin_group_id"] for e in events}
            evidence, ev_reasons = policy.grade_evidence(events, groups)
            timing, tm_reasons = policy.grade_timing(events, cutoff_date)
            daily = policy.load_daily(entity, sessions[-policy.LOOKBACK_HIGH:], duck)
            context = policy.compute_price_context(entity, sessions, daily)
            scenario = pricing.build_scenario(
                p0=context.get("close"), price_as_of=context.get("price_as_of"),
                events=events, cutoff_date=cutoff_date,
                low_20d=_recent_low(sessions, daily))
            price, pr_reasons = pricing.grade_price(scenario)
            latest = max((e.get("published_at") or "" for e in events), default="")[:10]
            ends = [(e["change"].get("expected_end") or "").strip() for e in events]
            life = themes.lifecycle(
                cutoff_date=cutoff_date, latest_evidence_date=latest or None,
                expected_end=next((x for x in sorted(ends) if x), None),
                episode_started=min((e.get("published_at") or "" for e in events),
                                    default="")[:10] or None,
                has_open_condition=any(
                    (e["change"].get("confirmation_condition") or "").strip() for e in events))
            if life["timing_hold"] and timing == "high":
                timing = "medium"      # 확인 기한 초과 → timing high 해제(§11)
                tm_reasons = tm_reasons + ["confirmation_overdue"]
            decision = policy.apply_policy(
                evidence=evidence, timing=timing, price=price,
                has_new_change=bool(events), price_context=context,
                invalidated=has_invalidation(events),
                has_open_condition=any(
                    (e["change"].get("confirmation_condition") or "").strip() for e in events),
            )
            if decision["action"] == "review_buy" and life["block_review_buy"]:
                decision = {"action": "watch",
                            "reason_codes": decision["reason_codes"] + life["flags"]}
            assessments.append({
                "subject_type": "entity", "subject_id": entity,
                "grades": {"evidence": evidence, "timing": timing, "price": price},
                "action": decision["action"],
                "reason_codes": sorted(set(decision["reason_codes"] + ev_reasons
                                           + tm_reasons + pr_reasons + life["flags"])),
                "lifecycle": life,
                "event_count": len(events),
                "origin_groups": len({g for g in groups.values() if g}),
                "scenario": {k: scenario.get(k) for k in
                             ("method", "reference_horizon", "P0", "conservative_price",
                              "risk_reference_price", "upside", "downside", "reward_risk",
                              "assumptions")},
                "price": {k: context.get(k) for k in
                          ("price_as_of", "state", "close", "return_5d", "return_20d",
                           "return_60d", "turnover_ratio", "turnover_20d", "market_cap",
                           "drawdown_120d", "history_ok")},
                "events": events,
            })
    finally:
        if own:
            duck.close()
    for item in assessments:      # episode 는 같은 종목의 연속 판단을 묶는다(§17.2, T26)
        item.setdefault("episode_id", None)
    feasibility = summarize_feasibility(assessments)
    feasibility["market_calendar"] = {
        "sessions": len(sessions),
        "median_listings": calendar["median_listings"],
        "sparse_sessions_dropped": [s["date"] for s in calendar["sparse_sessions"]],
    }
    return assessments, feasibility


def _recent_low(sessions: list[str], daily: dict[str, dict[str, Any]]) -> float | None:
    """최근 20 거래일 최저가. 근거 있는 하방이 없을 때만 참고로 쓴다(§10.2)."""
    lows = [daily[d]["low"] for d in sessions[-20:]
            if d in daily and policy._valid_bar(daily[d])]
    return min(lows) if lows else None


def summarize_feasibility(assessments: list[dict[str, Any]]) -> dict[str, Any]:
    """§17.4 — 등급 분포와 저장된 reason_codes 분포를 그대로 집계한다."""
    grades = {axis: defaultdict(int) for axis in ("evidence", "timing", "price")}
    actions: dict[str, int] = defaultdict(int)
    reasons: dict[str, int] = defaultdict(int)
    for item in assessments:
        for axis, value in item["grades"].items():
            grades[axis][value] += 1
        actions[item["action"]] += 1
        for code in item["reason_codes"]:
            reasons[code] += 1
    return {
        "entities": len(assessments),
        "grades": {axis: dict(sorted(counts.items())) for axis, counts in grades.items()},
        "actions": dict(sorted(actions.items())),
        "reason_codes": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "price_methods": dict(sorted(collections.Counter(
            a.get("scenario", {}).get("method", "unavailable") for a in assessments).items())),
        "two_or_more_origin_groups": sum(1 for a in assessments if a["origin_groups"] >= 2),
        "observed_fact_entities": sum(
            1 for a in assessments
            if any(e["change"].get("fact_type") == "observed_fact" for e in a["events"])),
    }


def render_artifact(payload: dict[str, Any], out_dir: Path = EXPORT_DIR) -> Path:
    """저장된 결과만으로 Markdown 을 만든다. 실패해도 분석을 다시 돌리지 않는다(§14)."""
    cutoff_date = payload["cutoff_date"]
    folder = out_dir / cutoff_date.replace("-", "")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{payload['run_id']}.md"
    feasibility = payload["feasibility"]
    lines = [
        f"# 급등 전 변화 포착 — {cutoff_date} 기준", "",
        f"- 실행: `{payload['run_id']}` / 모드 {payload['mode']}",
        f"- 기준 시각(KST): {cutoff_date} 15:40 · 가격 기준일: {payload.get('price_as_of') or '-'}",
        f"- 처리 범위: 원문 {payload['manifest_sources']:,}건 중 **{payload['processed']:,}건 추출**"
        f" (대기 {payload['backlog']:,}건)",
        f"- 결과 상태: **{payload['result_status']}**", "",
        "## 1. 처리 범위와 누락", "",
        f"- 이 실행은 전체 범위를 처리하지 못했다. 대기 {payload['backlog']:,}건이 남아 있어"
        f" 전체 시장에 대한 결론이 아니다.",
        f"- 추출 이벤트 {payload['events']:,}건 · 변화 있는 원문 {payload['with_events']:,}건"
        f" · 인용 검증 탈락 {payload['rejected']:,}건",
        f"- 종목 연결 완료 {feasibility['entities']:,}개",
        f"- 시장 거래일 {feasibility['market_calendar']['sessions']:,}일"
        f" (수집 결손일 제외 {len(feasibility['market_calendar']['sparse_sessions_dropped'])}일:"
        f" {', '.join(feasibility['market_calendar']['sparse_sessions_dropped'][-5:]) or '없음'})", "",
        "## 2. 신규 매수 검토", "",
    ]
    picked = payload["selected"]
    if picked:
        for item in picked:
            lines.append(f"- **{item['subject_id']}** — {item['action']}")
    else:
        lines.append("없음. 자격을 충족한 후보가 0개다.")
    lines.extend(["", "## 3. 행동 분포", "", "| 행동 | 종목 수 |", "| --- | ---: |"])
    lines.extend(f"| {action} | {count} |" for action, count in feasibility["actions"].items())

    lines.extend(["", "## 4. 등급 분포 (§17.4)", "", "| 축 | high | medium | low | unknown |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for axis in ("evidence", "timing", "price"):
        counts = feasibility["grades"][axis]
        lines.append(f"| {axis} | " + " | ".join(
            str(counts.get(grade, 0)) for grade in ("high", "medium", "low", "unknown")) + " |")

    lines.extend(["", "## 5. 탈락 원인 분해 (reason_codes 분포)", "",
                  "| 사유 | 종목 수 |", "| --- | ---: |"])
    lines.extend(f"| {code} | {count} |"
                 for code, count in list(feasibility["reason_codes"].items())[:20])

    lines.extend(["", "## 6. 변화가 잡힌 종목 상위", "",
                  "| 종목 | 이벤트 | 독립그룹 | 근거 | 시점 | 가격 | 행동 |",
                  "| --- | ---: | ---: | --- | --- | --- | --- |"])
    top = sorted(payload["assessments"], key=lambda a: -a["event_count"])[:15]
    for item in top:
        grades = item["grades"]
        lines.append(
            f"| {item['subject_id']} | {item['event_count']} | {item['origin_groups']} | "
            f"{grades['evidence']} | {grades['timing']} | {grades['price']} | {item['action']} |")

    lines.extend(["", "## 7. 해석 제한", "",
                  "- historical_exploration 모드다. 지금 확보한 과거 자료로 재현했으므로"
                  " 실시간 포착 성과가 아니다(§4.3).",
                  "- 예산 한도로 표본만 처리했다. 미처리분을 no_candidates 로 숨기지 않는다(§12.3).",
                  "- 가격 시나리오 입력을 아직 구현하지 않아 price 축은 전부 unknown 이다.",
                  f"- 생성 {datetime.now().astimezone().isoformat(timespec='seconds')}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
