"""처리량 실측 게이트 — 일일 운영이 가능한지 숫자로 판정한다 (PLAN §12.3).

§12.2 의 600회/150분(policy_v2)은 제안 상한이지 보장이 아니다. 여기서 재는 것은
  - 하루 신규 유입량 (텔레그램·리포트)
  - 실측 단가 (저장된 processing_results 기준, 모델별)
  - 순처리량 = 예산 내 처리 가능량 - 신규 유입량
순처리량이 0 이하이면 완료일을 산출하지 않고 적체 증가로 표시한다. 지어내지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
TELEGRAM_DB = ROOT / "etl" / "db" / "telegram_public.sqlite3"
REPORT_DIR = ROOT / "etl" / "exports" / "stock_reports"

BUDGET_CALLS = 600          # §12.2 (policy_v2) 1차 추출 논리 호출 상한
BUDGET_SECONDS = 150 * 60   # §12.2 (policy_v2) 총 150분


def daily_inflow(cutoff_date: str, days: int = 30) -> dict[str, Any]:
    """최근 days 일의 하루 평균 신규 유입. 실제 관측치만 쓴다."""
    end = date.fromisoformat(cutoff_date)
    start = end - timedelta(days=days)
    con = sqlite3.connect(f"file:{TELEGRAM_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT substr(date_kst, 1, 10) AS day, count(*) FROM telegram_posts"
            " WHERE date_kst >= ? AND date_kst <= ? GROUP BY day",
            (start.isoformat(), end.isoformat() + " 23:59:59"),
        ).fetchall()
    finally:
        con.close()
    telegram = [count for _, count in rows]

    reports: dict[str, int] = {}
    for pdf in REPORT_DIR.glob("*/*.pdf"):
        day = pdf.name[:10]
        if start.isoformat() <= day <= end.isoformat():
            reports[day] = reports.get(day, 0) + 1

    return {
        "window_days": days,
        "telegram_per_day": round(statistics.fmean(telegram), 1) if telegram else 0,
        "telegram_observed_days": len(telegram),
        "report_per_day": round(statistics.fmean(reports.values()), 1) if reports else 0,
        "report_observed_days": len(reports),
        "total_per_day": round((statistics.fmean(telegram) if telegram else 0)
                               + (statistics.fmean(reports.values()) if reports else 0), 1),
    }


def measured_rate(con: sqlite3.Connection) -> dict[str, Any]:
    """저장된 처리 결과에서 모델별 실측 단가를 뽑는다."""
    rows = con.execute(
        "SELECT model_identity, elapsed_sec, char_count FROM processing_results"
        " WHERE stage='extract_changes' AND status='done' AND elapsed_sec IS NOT NULL"
    ).fetchall()
    by_model: dict[str, list[tuple[float, int]]] = {}
    for model, elapsed, chars in rows:
        by_model.setdefault(model, []).append((elapsed, chars or 0))
    out = {}
    for model, values in by_model.items():
        times = sorted(v[0] for v in values)
        out[model] = {
            "samples": len(values),
            "sec_per_source_p50": round(times[len(times) // 2], 2),
            "sec_per_source_p95": round(times[int(len(times) * 0.95)], 2),
            "mean_chars": int(statistics.fmean(v[1] for v in values)),
        }
    return out


def net_throughput(
    rate_sec: float, inflow_per_day: float, *,
    budget_calls: int = BUDGET_CALLS, budget_seconds: int = BUDGET_SECONDS,
    runs_per_day: int = 1,
) -> dict[str, Any]:
    """하루 처리 가능량과 순처리량. 시간·호출 상한 중 먼저 닿는 쪽이 실효 한도다."""
    by_time = int(budget_seconds / rate_sec) if rate_sec > 0 else 0
    capacity = min(budget_calls, by_time) * runs_per_day
    net = capacity - inflow_per_day
    return {
        "rate_sec_per_source": round(rate_sec, 2),
        "capacity_by_call_limit": budget_calls * runs_per_day,
        "capacity_by_time_limit": by_time * runs_per_day,
        "daily_capacity": capacity,
        "binding_limit": "calls" if budget_calls <= by_time else "time",
        "inflow_per_day": inflow_per_day,
        "net_per_day": round(net, 1),
    }


def backlog_forecast(backlog: int, net_per_day: float) -> dict[str, Any]:
    """순처리량이 0 이하이면 완료일을 산출하지 않는다(§12.3)."""
    if net_per_day <= 0:
        return {"backlog": backlog, "status": "backlog_growing",
                "days_to_clear": None, "runs_to_clear": None,
                "note": "신규 유입이 처리량 이상이라 완료 시점을 산출할 수 없다"}
    return {"backlog": backlog, "status": "clearing",
            "days_to_clear": round(backlog / net_per_day, 1),
            "runs_to_clear": int(-(-backlog // net_per_day))}


def assess_feasibility(
    con: sqlite3.Connection, cutoff_date: str, backlog: int, *, runs_per_day: int = 1
) -> dict[str, Any]:
    """§12.3 전체 판정. 정상 운영 준비 완료 여부까지 낸다."""
    inflow = daily_inflow(cutoff_date)
    rates = measured_rate(con)
    scenarios = {}
    for model, stats in rates.items():
        throughput = net_throughput(stats["sec_per_source_p50"], inflow["total_per_day"],
                                    runs_per_day=runs_per_day)
        scenarios[model] = {**throughput,
                            "forecast": backlog_forecast(backlog, throughput["net_per_day"])}
    ready = any(s["net_per_day"] > 0 for s in scenarios.values())
    return {
        "cutoff_date": cutoff_date, "inflow": inflow, "measured_rates": rates,
        "scenarios": scenarios, "runs_per_day": runs_per_day,
        "operations_ready": ready,
        "note": ("한 모델이라도 순처리량이 양수면 그 설정으로 운영 가능하다"
                 if ready else
                 "모든 설정에서 신규 유입이 처리량 이상이다 — 한도·주기를 재설정해야 한다"),
    }


def load_backlog(con: sqlite3.Connection, run_id: str) -> int:
    row = con.execute("SELECT throughput_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row or not row[0]:
        return 0
    return int(json.loads(row[0]).get("backlog") or 0)
