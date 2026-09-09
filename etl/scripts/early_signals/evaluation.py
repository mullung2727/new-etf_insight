"""선행 발견과 추천 이후 결과 (PLAN §17).

live 기록과 historical_exploration 통계를 섞지 않는다. 급등 판정·수익률은
권리변동이 미해결이면 보류하고 분모를 보존한다(§9.2).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import policy

SURGE_LOOKBACK = 20        # 급등 확인: 종가가 20거래일 전보다 25% 이상(§17.1)
SURGE_THRESHOLD = 0.25
EPISODE_GAP = 20           # 20거래일 연속 미충족 후 다시 충족하면 새 episode
HORIZONS = (28, 56, 84)    # 달력일. 목표일 이후 최초 거래일 종가로 평가(§17.2)


def detect_surge_episodes(
    sessions: list[str], daily: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """급등 episode 를 찾는다. 권리변동 미해결 구간은 판정을 보류한다."""
    episodes: list[dict[str, Any]] = []
    last_hit_index: int | None = None
    for index in range(SURGE_LOOKBACK, len(sessions)):
        today, base = sessions[index], sessions[index - SURGE_LOOKBACK]
        if today not in daily or base not in daily:
            continue
        if not (policy._valid_bar(daily[today]) and policy._valid_bar(daily[base])):
            continue
        if policy._corporate_action(sessions[index - SURGE_LOOKBACK:index + 1], daily):
            continue      # adjustment_pending — 가짜 급등을 배제한다(§9.2)
        if daily[today]["close"] / daily[base]["close"] - 1 < SURGE_THRESHOLD:
            continue
        if last_hit_index is not None and index - last_hit_index <= EPISODE_GAP:
            last_hit_index = index
            continue
        episodes.append({"confirm_date": today, "index": index,
                         "return_20d": round(daily[today]["close"] / daily[base]["close"] - 1, 6)})
        last_hit_index = index
    return episodes


def lead_time(first_observed_date: str, confirm_date: str, sessions: list[str]) -> int | None:
    """선행 기간 = 급등 확인일 - 최초 유효 관찰일(거래일 수).

    실제 상승 시작일까지의 기간이 아니다(§17.1).
    """
    if first_observed_date not in sessions or confirm_date not in sessions:
        return None
    gap = sessions.index(confirm_date) - sessions.index(first_observed_date)
    return gap if gap >= 0 else None


def classify_detection(
    first_observed_date: str, episode: dict[str, Any], sessions: list[str]
) -> str:
    """원문 확보가 급등 확인일 이후면 after_move — 선행 집계에서 제외한다(T02).

    lead_time 은 선행 기간이라 음수를 돌려주지 않으므로 여기서는 순번을 직접 비교한다.
    """
    if first_observed_date not in sessions or episode["confirm_date"] not in sessions:
        return "unresolved"
    gap = sessions.index(episode["confirm_date"]) - sessions.index(first_observed_date)
    return "before_move" if gap > 0 else "after_move"


def entry_date(report_date: str, sessions: list[str]) -> str | None:
    """보고서 완료 이후 **첫 거래일**을 비교 시작일로 쓴다(§17.2, T25)."""
    for session in sessions:
        if session > report_date:
            return session
    return None


def evaluate_outcomes(
    entry: str, sessions: list[str], daily: dict[str, dict[str, Any]],
    horizons: tuple[int, ...] = HORIZONS,
) -> dict[str, Any]:
    """28/56/84 달력일 성과. 목표일 이후 최초 거래일 종가로 평가한다."""
    if entry not in daily or not policy._valid_bar(daily[entry]):
        return {"status": "entry_unavailable"}
    entry_price = daily[entry]["open"] or daily[entry]["close"]
    start = date.fromisoformat(_iso(entry))
    out: dict[str, Any] = {"status": "ok", "entry_date": entry, "entry_price": entry_price,
                           "horizons": {}}
    for days in horizons:
        target = (start + timedelta(days=days)).isoformat().replace("-", "")
        session = next((s for s in sessions if s >= target and s in daily), None)
        if not session or not policy._valid_bar(daily[session]):
            out["horizons"][days] = {"status": "pending"}
            continue
        path = [daily[s] for s in sessions
                if entry <= s <= session and s in daily and policy._valid_bar(daily[s])]
        lows = [bar["low"] for bar in path]
        closes = [bar["close"] for bar in path]
        peak = entry_price
        mdd = 0.0
        for close in closes:
            peak = max(peak, close)
            mdd = min(mdd, close / peak - 1)
        out["horizons"][days] = {
            "status": "done", "exit_date": session,
            "return": round(daily[session]["close"] / entry_price - 1, 6),
            "min_low": round(min(lows) / entry_price - 1, 6) if lows else None,
            "close_mdd": round(mdd, 6),
        }
    return out


def _iso(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}" if "-" not in yyyymmdd else yyyymmdd


def matched_benchmark_bucket(market_cap: int | None) -> str:
    """전일 시총 구간(§17.2 보완). 배정은 직전 거래일 정보로 한다."""
    if not market_cap:
        return "unknown"
    billions = market_cap / 100_000_000
    if billions < 1_000:
        return "<1천억"
    if billions < 3_000:
        return "1~3천억"
    if billions < 10_000:
        return "3천억~1조"
    if billions < 50_000:
        return "1~5조"
    return "5조+"


def summarize_detection(records: list[dict[str, Any]]) -> dict[str, Any]:
    """포착률은 전체 적격 급등 episode 를 분모로 쓴다. 추천 종목 안에서 계산하지 않는다."""
    total = len(records)
    by_status: dict[str, int] = {}
    for item in records:
        by_status[item["detection"]] = by_status.get(item["detection"], 0) + 1
    leads = [item["lead_days"] for item in records
             if item["detection"] == "before_move" and item.get("lead_days") is not None]
    leads.sort()
    return {
        "surge_episodes": total,
        "by_detection": dict(sorted(by_status.items())),
        "before_move_rate": round(by_status.get("before_move", 0) / total, 4) if total else None,
        "lead_days_median": leads[len(leads) // 2] if leads else None,
    }


def build_matched_benchmark(
    entry: str, exit_date: str, bucket: str, sessions: list[str], con,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    """시장·전일 시총 구간을 맞춘 비교 수익률(§17.2 보완).

    전종목 동일가중만 쓰면 시총 구간별 수익률 차이를 흡수하지 못한다. 비교군은
    **직전 거래일 정보**로 배정하고 추천 종목 자체는 뺀다. 유효 종목이 없으면 null 이다.
    """
    exclude = exclude or set()
    prior = sessions[sessions.index(entry) - 1] if entry in sessions and sessions.index(entry) else None
    if not prior:
        return {"bucket": bucket, "return": None, "n": 0, "reason": "no_prior_session"}
    rows = con.execute(
        "WITH p AS (SELECT ticker, market_cap FROM ohlcv WHERE date=? AND close>0 AND volume>0),"
        " a AS (SELECT ticker, open FROM ohlcv WHERE date=? AND open>0 AND volume>0),"
        " b AS (SELECT ticker, close FROM ohlcv WHERE date=? AND close>0)"
        " SELECT p.ticker, p.market_cap, b.close/a.open-1 FROM p JOIN a USING(ticker)"
        " JOIN b USING(ticker)", [prior, entry, exit_date]).fetchall()
    values = [ret for ticker, cap, ret in rows
              if str(ticker) not in exclude and matched_benchmark_bucket(cap) == bucket]
    if not values:
        return {"bucket": bucket, "return": None, "n": 0, "reason": "no_peer"}
    return {"bucket": bucket, "return": round(sum(values) / len(values), 6), "n": len(values),
            "prior_session": prior}


def dedupe_episodes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 episode 의 반복 선정을 독립 표본으로 세지 않는다(§17.2, T26).

    episode 당 가장 이른 판단만 남긴다 — 최초 선정 시점이 성과 시작점이기 때문이다.
    """
    first: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=lambda r: (r.get("entry_date") or "", r.get("subject_id", ""))):
        key = record.get("episode_id") or f"{record.get('subject_id')}|{record.get('entry_date')}"
        first.setdefault(key, record)
    return list(first.values())


def summarize_outcomes(
    records: list[dict[str, Any]], horizon: int, label: str = ""
) -> dict[str, Any]:
    """수익률 요약. pending·제외는 분모를 보존해 따로 센다(§9.2)."""
    done = [r for r in records
            if (r.get("outcome", {}).get("horizons", {}).get(horizon, {}).get("status")) == "done"]
    pending = sum(1 for r in records
                  if (r.get("outcome", {}).get("horizons", {}).get(horizon, {})
                      .get("status")) == "pending")
    blocked = sum(1 for r in records if r.get("outcome", {}).get("status") != "ok")
    values = [r["outcome"]["horizons"][horizon]["return"] for r in done]
    excess = [r["outcome"]["horizons"][horizon].get("excess") for r in done]
    excess = [e for e in excess if e is not None]
    summary = {"label": label, "horizon_days": horizon, "n": len(values),
               "pending": pending, "blocked": blocked}
    if values:
        values_sorted = sorted(values)
        summary.update({
            "mean": round(sum(values) / len(values), 6),
            "median": round(values_sorted[len(values_sorted) // 2], 6),
            "win_rate": round(sum(1 for v in values if v > 0) / len(values), 4),
        })
    if excess:
        summary["mean_excess_matched"] = round(sum(excess) / len(excess), 6)
        summary["n_excess"] = len(excess)
    return summary
