"""12단계: watchlist D+1~D+5 장중 과도 하락 매수를 1분봉으로 검증한다.

기존 눌림목(15:19 lower-low 양봉 반전)과 달리 반등 확인을 기다리지 않는다.
기준가(편입일 고가·종가 / 전일 종가 / 당일 시가 / 장중 고점) 대비 하락률을 처음 만족한 완료 1분봉의
다음 봉 시가로 매수하고, pullback.json 과 같은 TP+5% / SL-5% / D+3 15:19 만기로 청산한다.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.watchlist_expected_return.phase12_intraday_drop_entry --stage coverage
    etl\\.venv\\Scripts\\python.exe -m research.watchlist_expected_return.phase12_intraday_drop_entry --stage fetch
    etl\\.venv\\Scripts\\python.exe -m research.watchlist_expected_return.phase12_intraday_drop_entry --stage dev
    etl\\.venv\\Scripts\\python.exe -m research.watchlist_expected_return.phase12_intraday_drop_entry --stage final --combo running_high:0.05
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from research.watchlist_expected_return.minute_bar_store import (
    DEFAULT_DB_PATH as DEFAULT_MINUTE_DB,
    DEFAULT_SCOPE,
    connect as connect_minute_store,
    fetch_into_store,
    missing_dates,
)
from research.watchlist_expected_return.phase4_holding_strategy import summarize_outcomes
from research.watchlist_expected_return.phase7_pullback_strategy import (
    DEFAULT_KRX_DB,
    DEFAULT_WATCHLIST_DB,
    load_research_rows,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "etl" / "scripts"))  # 운영 상장연령 가드를 그대로 쓴다
from build_watchlist import compute_watchlist  # noqa: E402
from listing_age_guard import is_listing_age_allowed, load_first_trade_dates  # noqa: E402

DEFAULT_OUTPUT_DIR = ROOT / "research" / "watchlist_pullback_strategy"
BACKFILL_DB = ROOT / "temp" / "backfilled_watchlist.sqlite3"   # temp/ 는 gitignore
BACKFILL_SQL_FROM = "20240801"        # LOOKBACK 60거래일 여유를 위해 krx 전 구간에서 산출
LAST_BUY_TIME = "151900"      # 정규장 매수·청산 판정 마감 (운영 --force-exit-time 과 동일)
MAX_WAIT_DAYS = 5             # pullback.json max_wait_days
MAX_HOLD_DAYS = 3             # pullback.json max_hold_days
TP, SL = 0.05, 0.05           # pullback.json
COST_RATE = 0.006             # 왕복 비용 고정 가정 (BACKTEST_DATA §6)
DEV_FRACTION = 0.6
CORPORATE_ACTION_BAND = (0.67, 1.5)   # BACKTEST_DATA §1(c)
REFERENCES = ("watchlist_high", "watchlist_close", "prev_close", "day_open", "running_high")
DEFAULT_REFERENCES = ("watchlist_high",)
DEFAULT_DROP_RATES = (0.03, 0.05, 0.07, 0.10)


# ── 모집단 ────────────────────────────────────────────────────────────────────
def backfill_watchlist_db(krx_db: Path, start_date: str, db_path: Path = BACKFILL_DB) -> Path:
    """운영 build_watchlist 와 같은 규칙으로 편입일을 소급 산출해 임시 DB에 담는다.

    watchlist 테이블은 20260529 부터라 1분봉 하한(20250801)까지 비어 있다.
    운영 DB 는 건드리지 않고 load_research_rows 가 읽을 형태만 새로 만든다.
    """
    with duckdb.connect(str(krx_db), read_only=True) as con:
        latest = con.execute("SELECT max(date) FROM ohlcv").fetchone()[0]
        watchlist = compute_watchlist(con, BACKFILL_SQL_FROM, str(latest))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE watchlist (date TEXT, stock_code TEXT)")
    con.executemany(
        "INSERT INTO watchlist VALUES (?, ?)",
        [(date, ticker) for date, tickers in watchlist.items() if date >= start_date
         for ticker in tickers],
    )
    con.commit()
    con.close()
    return db_path


def load_population(
    watchlist_db: Path, krx_db: Path, backfill_from: str | None = None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """운영과 같은 필터를 건 watchlist 편입 건 + D+1~D+8 일봉 경로."""
    if backfill_from:
        watchlist_db = backfill_watchlist_db(krx_db, backfill_from)
    rows = load_research_rows(watchlist_db, krx_db)
    stats = {"watchlist_rows_with_daily_path": len(rows)}
    with duckdb.connect(str(krx_db), read_only=True) as con:
        tickers = [row["ticker"] for row in rows]
        first_dates = load_first_trade_dates(con, tickers, max(row["date"] for row in rows))
        marks = ",".join("?" for _ in set(tickers))
        shares = defaultdict(dict)
        for ticker, date, list_shrs in con.execute(
            f"SELECT ticker, date, list_shrs FROM ohlcv WHERE ticker IN ({marks})",
            sorted(set(tickers)),
        ).fetchall():
            shares[str(ticker)][str(date)] = list_shrs

    kept = []
    for row in rows:
        if not is_listing_age_allowed(first_dates.get(row["ticker"]), row["date"]):
            stats["excluded_new_listing"] = stats.get("excluded_new_listing", 0) + 1
            continue
        if _has_corporate_action(shares[row["ticker"]], [row["date"], *[d["date"] for d in row["future"][:8]]]):
            stats["excluded_corporate_action"] = stats.get("excluded_corporate_action", 0) + 1
            continue
        kept.append(row)
    stats["population"] = len(kept)
    return kept, stats


def _has_corporate_action(by_date: dict[str, Any], dates: list[str]) -> bool:
    """감시·보유 창 안에서 상장주식수가 밴드 밖으로 변하면 미조정 가격 점프로 본다."""
    values = [by_date.get(date) for date in dates]
    values = [value for value in values if value]
    low, high = CORPORATE_ACTION_BAND
    return any(not (low <= later / earlier <= high) for earlier, later in zip(values, values[1:]))


def window_dates(row: dict[str, Any]) -> list[str]:
    """D+1~D+8 — D+5 진입 시 D+3 만기까지 필요한 거래일."""
    return [day["date"] for day in row["future"][:MAX_WAIT_DAYS + MAX_HOLD_DAYS]]


# ── 분봉 ──────────────────────────────────────────────────────────────────────
def read_bars(con, ticker: str, dates: list[str]) -> dict[str, list[dict[str, Any]]]:
    """이미 적재된 정규장 1분봉만 읽는다(수집은 --stage fetch 에서만)."""
    if not dates:      # 편입 직후라 D+1 조차 없는 최근 행
        return {}
    marks = ",".join("?" for _ in dates)
    rows = con.execute(
        f"SELECT date, time, timestamp, open, high, low, close, volume FROM minute_bars "
        f"WHERE ticker=? AND scope=? AND date IN ({marks}) ORDER BY timestamp",
        [ticker, DEFAULT_SCOPE, *dates],
    ).fetchall()
    keys = ("date", "time", "timestamp", "open", "high", "low", "close", "volume")
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bar = dict(zip(keys, row))
        by_date[bar["date"]].append(bar)
    return by_date


# ── 진입 ──────────────────────────────────────────────────────────────────────
def scan_day(
    bars: list[dict[str, Any]], day_open: int, prev_close: int | None, reference: str, rate: float,
    watchlist_high: int | None = None, watchlist_close: int | None = None,
    direction: str = "down",
) -> dict[str, Any] | None:
    """완료 1분봉 종가가 기준가 대비 하락(down)·상승(up)률을 처음 만족하면 다음 봉 시가로 매수."""
    running_high = day_open
    for index, bar in enumerate(bars):
        running_high = max(running_high, bar["high"])
        if bar["time"] > LAST_BUY_TIME:
            return None
        base = {"watchlist_high": watchlist_high, "watchlist_close": watchlist_close,
                "prev_close": prev_close, "day_open": day_open,
                "running_high": running_high}[reference]
        if not base or base <= 0:
            continue
        trigger = base * (1 - rate) if direction == "down" else base * (1 + rate)
        if (bar["close"] > trigger) if direction == "down" else (bar["close"] < trigger):
            continue
        following = bars[index + 1] if index + 1 < len(bars) else None
        if following is None or following["time"] > LAST_BUY_TIME:
            return None      # 15:19 이후 체결은 불가 — 다음 날로 이월하지 않는다
        return {
            "signal_timestamp": bar["timestamp"], "signal_time": bar["time"],
            "reference_price": base, "trigger_price": round(trigger, 2),
            "entry_timestamp": following["timestamp"], "entry_price": following["open"],
        }
    return None


def find_drop_entry(
    row: dict[str, Any], bars_by_date: dict[str, list[dict[str, Any]]], reference: str, rate: float,
    direction: str = "down",
) -> dict[str, Any] | None:
    """D+1~D+5 를 순서대로 보고 최초 매수 1회만 잡는다."""
    prev_close = row["base_close"]
    watchlist_high = row["history"][-1]["high"]      # 거래량이 터진 편입일 고가 — 5일 내내 고정
    for offset, day in enumerate(row["future"][:MAX_WAIT_DAYS]):
        bars = bars_by_date.get(day["date"], [])
        if bars and (day["open"] or 0) > 0:
            signal = scan_day(bars, day["open"], prev_close, reference, rate,
                              watchlist_high, row["base_close"], direction)
            if signal:
                return {**signal, "entry_date": day["date"], "watch_day": offset + 1,
                        "future_index": offset, "include_entry_bar": True}
        prev_close = day["close"] if (day["close"] or 0) > 0 else None
    return None


def find_close_confirm_entry(
    row: dict[str, Any], bars_by_date: dict[str, list[dict[str, Any]]]
) -> dict[str, Any] | None:
    """비교군 — 운영 15:19 lower-low 양봉 반전. 15:19 이하 마지막 체결가로 매수."""
    prior_low = row["history"][-1]["low"]
    for offset, day in enumerate(row["future"][:MAX_WAIT_DAYS]):
        bars = [bar for bar in bars_by_date.get(day["date"], []) if bar["time"] <= LAST_BUY_TIME]
        if bars and (day["open"] or 0) > 0 and (prior_low or 0) > 0:
            last = bars[-1]
            if min(bar["low"] for bar in bars) < prior_low and last["close"] > day["open"]:
                return {"signal_timestamp": last["timestamp"], "signal_time": last["time"],
                        "reference_price": prior_low, "trigger_price": prior_low,
                        "entry_timestamp": last["timestamp"], "entry_price": last["close"],
                        "entry_date": day["date"], "watch_day": offset + 1,
                        "future_index": offset, "include_entry_bar": False}
        prior_low = day["low"] if (day["low"] or 0) > 0 else prior_low
    return None


# ── 청산 ──────────────────────────────────────────────────────────────────────
def simulate_exit(
    bars_by_date: dict[str, list[dict[str, Any]]], hold_dates: list[str], entry: dict[str, Any],
    tp: float = TP, sl: float = SL,
) -> dict[str, Any] | None:
    """진입 체결 이후 경로를 1분봉으로 따라가며 TP/SL, 없으면 만기 15:19 강제청산.

    ``hold_dates`` = [매수일, D+1, D+2, D+3]. 15:19 이후 봉은 판정에서 제외한다.
    """
    price = entry["entry_price"]
    tp_price, sl_price = price * (1 + tp), price * (1 - sl)
    path = []
    for date in hold_dates:
        for bar in bars_by_date.get(date, []):
            if bar["time"] > LAST_BUY_TIME:
                continue
            if bar["timestamp"] > entry["entry_timestamp"] or (
                entry["include_entry_bar"] and bar["timestamp"] == entry["entry_timestamp"]
            ):
                path.append(bar)
    if any(not any(bar["date"] == date for bar in path) for date in hold_dates[1:]):
        return None      # 보유 경로 분봉 결손 — 미청산으로 따로 센다

    worst_low = price
    for bar in path:
        reason = exit_price = None
        if bar["open"] >= tp_price:
            reason, exit_price = "gap_tp", bar["open"]
        elif bar["open"] <= sl_price:
            reason, exit_price = "gap_sl", bar["open"]
        else:
            worst_low = min(worst_low, bar["low"])
            tp_hit, sl_hit = bar["high"] >= tp_price, bar["low"] <= sl_price
            if tp_hit and sl_hit:
                reason, exit_price = "same_minute_both_sl", sl_price
            elif sl_hit:
                reason, exit_price = "sl", sl_price
            elif tp_hit:
                reason, exit_price = "tp", tp_price
        if reason:
            return _outcome(entry, path, bar, exit_price, reason, worst_low)
    final = [bar for bar in path if bar["date"] == hold_dates[-1]][-1]
    return _outcome(entry, path, final, final["close"], "forced_1519", worst_low)


def _outcome(
    entry: dict[str, Any], path: list[dict[str, Any]], exit_bar: dict[str, Any],
    exit_price: float, reason: str, worst_low: float,
) -> dict[str, Any]:
    price = entry["entry_price"]
    traversed = path[: path.index(exit_bar) + 1]
    return {
        "gross_return": exit_price / price - 1,
        "exit_reason": reason,
        "exit_timestamp": exit_bar["timestamp"],
        "exit_price": round(exit_price, 2),
        "exit_date": exit_bar["date"],
        "holding_days": _date_gap(entry["entry_date"], exit_bar["date"], path),
        "holding_minutes": len(traversed),
        "max_adverse": round(worst_low / price - 1, 6),
    }


def _date_gap(entry_date: str, exit_date: str, path: list[dict[str, Any]]) -> int:
    dates = sorted({bar["date"] for bar in path} | {entry_date})
    return dates.index(exit_date)


# ── 조합 실행 ─────────────────────────────────────────────────────────────────
def run_combination(
    rows: list[dict[str, Any]], bars: dict[str, dict[str, list[dict[str, Any]]]],
    finder, label: str,
) -> dict[str, Any]:
    """편입 건별 최초 신호를 뽑고, 시간순으로 보유중 종목 중복매수를 차단한다."""
    candidates = []
    for row in rows:
        entry = finder(row, bars[_key(row)])
        if entry:
            candidates.append((row, entry))
    candidates.sort(key=lambda item: (item[1]["entry_timestamp"], item[0]["ticker"]))

    busy_until: dict[str, str] = {}
    trades, blocked, incomplete = [], 0, 0
    for row, entry in candidates:
        if entry["entry_timestamp"] <= busy_until.get(row["ticker"], ""):
            blocked += 1
            continue
        hold_dates = [day["date"] for day in
                      row["future"][entry["future_index"]:entry["future_index"] + MAX_HOLD_DAYS + 1]]
        outcome = simulate_exit(bars[_key(row)], hold_dates, entry) if len(hold_dates) == MAX_HOLD_DAYS + 1 else None
        if not outcome:
            incomplete += 1
            continue
        busy_until[row["ticker"]] = outcome["exit_timestamp"]
        trades.append({
            "watchlist_date": row["date"], "ticker": row["ticker"], "combination": label,
            "signal_time": entry["signal_time"], "reference_price": entry["reference_price"],
            "trigger_price": entry["trigger_price"], "entry_timestamp": entry["entry_timestamp"],
            "entry_price": entry["entry_price"],
            "tp_price": round(entry["entry_price"] * (1 + TP), 2),
            "sl_price": round(entry["entry_price"] * (1 - SL), 2),
            "entry_date": entry["entry_date"], "watch_day": entry["watch_day"],
            "net_return": round(outcome["gross_return"] - COST_RATE, 6), **outcome,
        })
    return {"combination": label, "candidate_count": len(candidates), "blocked_by_holding": blocked,
            "incomplete_exit_path": incomplete, "trades": trades}


def _key(row: dict[str, Any]) -> str:
    return f"{row['ticker']}_{row['date']}"


def combination_metrics(result: dict[str, Any], population: int) -> dict[str, Any]:
    trades = result["trades"]
    outcomes = [{"gross_return": t["gross_return"], "holding_days": t["holding_days"],
                 "entry_date": t["entry_date"]} for t in trades]
    summary = summarize_outcomes(outcomes, COST_RATE)
    returns = [t["net_return"] for t in trades]
    reasons = defaultdict(int)
    for trade in trades:
        reasons[trade["exit_reason"]] += 1
    return {
        **{key: result[key] for key in
           ("combination", "candidate_count", "blocked_by_holding", "incomplete_exit_path")},
        "population": population,
        "no_buy_rate": round(1 - len(trades) / population, 4) if population else None,
        "trade_dates": len({t["entry_date"] for t in trades}),
        "trade_tickers": len({t["ticker"] for t in trades}),
        **summary,
        "exit_reason_share": {key: round(value / len(trades), 4) for key, value in reasons.items()} if trades else {},
        "avg_holding_minutes": round(statistics.fmean(t["holding_minutes"] for t in trades), 1) if trades else None,
        "avg_max_adverse": round(statistics.fmean(t["max_adverse"] for t in trades), 6) if trades else None,
        "worst_max_adverse": round(min((t["max_adverse"] for t in trades), default=0), 6) if trades else None,
        "mean_excluding_top5": round(statistics.fmean(sorted(returns)[:-5]), 6) if len(returns) > 6 else None,
        "date_cluster_ci95": date_cluster_ci(trades),
        "monthly": monthly_breakdown(trades),
    }


def date_cluster_ci(trades: list[dict[str, Any]], draws: int = 2000, seed: int = 12) -> list[float] | None:
    """거래일 단위 재표집 평균의 95% 구간. 보유기간 중첩까지 푼 구간은 아니다."""
    by_date = defaultdict(list)
    for trade in trades:
        by_date[trade["entry_date"]].append(trade["net_return"])
    dates = list(by_date)
    if len(dates) < 5:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        sample = [value for date in rng.choices(dates, k=len(dates)) for value in by_date[date]]
        means.append(statistics.fmean(sample))
    means.sort()
    return [round(means[int(draws * 0.025)], 6), round(means[int(draws * 0.975)], 6)]


def monthly_breakdown(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_month = defaultdict(list)
    for trade in trades:
        by_month[trade["entry_date"][:6]].append(trade["net_return"])
    return {month: {"count": len(values), "mean": round(statistics.fmean(values), 6)}
            for month, values in sorted(by_month.items())}


# ── 기간 분할 ─────────────────────────────────────────────────────────────────
def split_population(rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int]:
    """공통 워치리스트 편입일 기준 앞 60% / 뒤 40%. 경계에 걸친 편입 건은 버린다."""
    dates = sorted({row["date"] for row in rows})
    split_date = dates[int(len(dates) * DEV_FRACTION)]
    development, validation, dropped = [], [], 0
    for row in rows:
        if row["date"] >= split_date:
            validation.append(row)
        elif window_dates(row)[-1] < split_date:
            development.append(row)
        else:
            dropped += 1
    return split_date, development, validation, dropped


# ── 스테이지 ──────────────────────────────────────────────────────────────────
def collect_bars(rows: list[dict[str, Any]], minute_db: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    with connect_minute_store(minute_db, read_only=True) as con:
        return {_key(row): read_bars(con, row["ticker"], window_dates(row)) for row in rows}


def coverage(rows: list[dict[str, Any]], minute_db: Path) -> dict[str, Any]:
    with connect_minute_store(minute_db, read_only=True) as con:
        needed = {(row["ticker"], date) for row in rows for date in window_dates(row)}
        pending = {(row["ticker"], date) for row in rows
                   for date in missing_dates(con, row["ticker"], window_dates(row))}
    return {"required_ticker_days": len(needed), "fetched": len(needed) - len(pending),
            "missing": len(pending), "rows_short_window": sum(len(window_dates(row)) < 8 for row in rows)}


def fetch_missing(rows: list[dict[str, Any]], minute_db: Path) -> dict[str, int]:
    inserted = 0
    with connect_minute_store(minute_db) as con:
        for index, row in enumerate(rows, start=1):
            inserted += fetch_into_store(con, row["ticker"], window_dates(row))
            if index % 25 == 0:
                print(f"[phase12] fetch {index}/{len(rows)} bars+={inserted}", flush=True)
    return {"rows": len(rows), "bars_inserted": inserted}


def combinations(
    drop_rates: tuple[float, ...], references: tuple[str, ...], direction: str = "down",
) -> list[tuple[str, str, float]]:
    sign = "-" if direction == "down" else "+"
    return [(f"{reference}_{sign}{rate:.0%}", reference, rate)
            for reference in references for rate in drop_rates]


def analyze(
    rows: list[dict[str, Any]], bars: dict[str, dict[str, list[dict[str, Any]]]],
    combos: list[tuple[str, str, float]], direction: str = "down",
) -> list[dict[str, Any]]:
    results = []
    for label, reference, rate in combos:
        result = run_combination(
            rows, bars,
            lambda row, day_bars, r=reference, v=rate: find_drop_entry(row, day_bars, r, v, direction),
            label,
        )
        results.append({"reference": reference, "drop_rate": rate,
                        **combination_metrics(result, len(rows)), "_trades": result["trades"]})
    benchmark = run_combination(rows, bars, find_close_confirm_entry, "close_confirm_1519")
    results.append({"reference": "benchmark", "drop_rate": None,
                    **combination_metrics(benchmark, len(rows)), "_trades": benchmark["trades"]})
    return results


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# 장중 {'과도 하락' if payload['direction'] == 'down' else '상승 돌파'} 매수 — {payload['stage']} 구간", "",
        f"- 생성: {payload['generated_at']}",
        f"- 모집단: {payload['population']}건 (워치리스트 편입 {payload['population_stats']})",
        f"- 분할일: {payload['split_date']} (앞 {DEV_FRACTION:.0%} 개발 / 뒤 검증, 경계 제거 {payload['boundary_dropped']}건)",
        f"- 구간 편입건: {payload['stage_population']}건 · 기간 {payload['stage_range']}",
        f"- 고정 조건: TP +{TP:.0%} / SL -{SL:.0%} / 매수일 기준 D+{MAX_HOLD_DAYS} 15:19 만기 / 비용 {COST_RATE:.1%}",
        "", "## 조합별 결과", "",
        "| 조합 | 매수 | 미매수율 | 평균 | 중앙 | 승률 | TP | SL | 만기 | MAE평균 | 보유분 | 날짜CI95 | top5제외 | 차단 | 결손 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for item in payload["results"]:
        share = item["exit_reason_share"]
        tp_share = round(share.get("tp", 0) + share.get("gap_tp", 0), 4)
        sl_share = round(share.get("sl", 0) + share.get("gap_sl", 0) + share.get("same_minute_both_sl", 0), 4)
        lines.append(
            f"| {item['combination']} | {item['count']} | {item['no_buy_rate']} | {item['mean']} | "
            f"{item['median']} | {item['positive_rate']} | {tp_share} | {sl_share} | "
            f"{share.get('forced_1519', 0)} | {item['avg_max_adverse']} | {item['avg_holding_minutes']} | "
            f"{item['date_cluster_ci95']} | {item['mean_excluding_top5']} | "
            f"{item['blocked_by_holding']} | {item['incomplete_exit_path']} |"
        )
    lines.extend(["", "## 월별 평균 (비용 차감)", "", "| 조합 | " +
                  " | ".join(payload["months"]) + " |", "| --- |" + " ---: |" * len(payload["months"])])
    for item in payload["results"]:
        cells = [f"{item['monthly'][month]['mean']} ({item['monthly'][month]['count']})"
                 if month in item["monthly"] else "-" for month in payload["months"]]
        lines.append(f"| {item['combination']} | " + " | ".join(cells) + " |")
    lines.extend(["", "## 처리 규칙", ""] + [f"- {note}" for note in payload["guardrails"]])
    return "\n".join(lines) + "\n"


GUARDRAILS = [
    "신호는 완료된 1분봉 종가로 판정하고 체결은 다음 봉 시가 — 신호봉 가격은 청산 경로에 쓰지 않는다.",
    "watchlist_high / watchlist_close 는 거래량이 터진 편입일의 일봉 고가·종가이며 D+1~D+5 내내 고정이다.",
    "running_high 는 당일 시가에서 시작해 판정 봉까지의 고가만 누적한다(미래 봉 미포함).",
    "당일 시가는 일봉 시가를 쓴다. 첫 1분봉이 09:00이 아닌 날은 무거래일 뿐 결손이 아니다.",
    "직전 거래일 종가가 0이면 그 날 prev_close 규칙은 신호를 내지 않는다(오래된 종가 대입 금지).",
    "매수·청산 판정은 15:19 이하 봉만 사용한다. 15:20~15:30 동시호가 구간은 양쪽 모두에서 제외.",
    "만기 청산가는 D+3의 15:19 이하 마지막 체결 봉 종가다(운영 --force-exit-time 15:19:00 근사).",
    "보유 경로(D+1~D+3)에 봉이 하나도 없는 날이 있으면 청산가를 만들지 않고 결손으로 센다.",
    "같은 봉에서 TP·SL이 모두 닿으면 SL. 봉 시가가 이미 기준을 넘으면 시가로 청산.",
    "최대 역행폭은 진입봉~청산봉의 저가로 재되, 갭 청산봉의 저가는 청산 이후일 수 있어 제외한다.",
    "보유시간은 진입~청산 사이 체결 1분봉 개수다. 무거래 분은 빠지므로 하한 근사다.",
    "편입 건당 매수 1회, 보유 중 같은 종목 재매수 차단(운영 pullback_orders 규칙과 동일).",
    "상장 30일 미만, 감시·보유 창 안 상장주식수 급변(기업행위) 건은 모집단에서 제외.",
    "날짜 클러스터 신뢰구간은 같은 날 거래만 묶은 것으로 보유기간 중첩 의존성은 남아 있다.",
    "왕복 0.6%는 수수료·세금·슬리피지를 합친 고정 가정이며 실체결 재현값이 아니다.",
]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="장중 과도 하락 매수 1분봉 백테스트")
    parser.add_argument("--stage", choices=("coverage", "fetch", "dev", "final", "all"), default="coverage")
    parser.add_argument("--drop-rates", type=float, nargs="+", default=list(DEFAULT_DROP_RATES))
    parser.add_argument("--references", nargs="+", choices=REFERENCES, default=list(DEFAULT_REFERENCES))
    parser.add_argument("--direction", choices=("down", "up"), default="down",
                        help="down=기준가 대비 하락 매수, up=기준가 대비 상승 돌파 매수")
    parser.add_argument("--combo", default=None, help="final 단계 고정 조건. 예 running_high:0.05")
    parser.add_argument("--backfill-from", default=None,
                        help="watchlist 를 이 날짜부터 소급 산출해 쓴다. 예 20250801")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--minute-db", type=Path, default=DEFAULT_MINUTE_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rows, population_stats = load_population(args.watchlist_db, args.krx_db, args.backfill_from)
    split_date, development, validation, dropped = split_population(rows)
    if args.stage == "coverage":
        print(json.dumps({"population_stats": population_stats, "split_date": split_date,
                          "development": len(development), "validation": len(validation),
                          "boundary_dropped": dropped,
                          "coverage": coverage(rows, args.minute_db)}, ensure_ascii=False, indent=2))
        return
    if args.stage == "fetch":
        print(json.dumps(fetch_missing(rows, args.minute_db), ensure_ascii=False))
        return

    stage_rows = {"dev": development, "final": validation, "all": rows}[args.stage]
    if args.stage == "final":
        if not args.combo:
            raise SystemExit("--stage final 은 --combo REFERENCE:RATE 가 필요하다")
        reference, rate = args.combo.split(":")
        combos = [(f"{reference}_{float(rate):.0%}", reference, float(rate))]
    else:
        combos = combinations(tuple(args.drop_rates), tuple(args.references), args.direction)

    bars = collect_bars(stage_rows, args.minute_db)
    results = analyze(stage_rows, bars, combos, args.direction)
    trades = [trade for item in results for trade in item.pop("_trades")]
    months = sorted({month for item in results for month in item["monthly"]})
    payload = {
        "analysis_version": 1, "stage": args.stage,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "population": len(rows), "population_stats": population_stats,
        "split_date": split_date, "boundary_dropped": dropped,
        "stage_population": len(stage_rows),
        "stage_range": f"{min(row['date'] for row in stage_rows)}~{max(row['date'] for row in stage_rows)}",
        "tp": TP, "sl": SL, "max_wait_days": MAX_WAIT_DAYS, "max_hold_days": MAX_HOLD_DAYS,
        "cost_rate": COST_RATE, "direction": args.direction, "months": months, "results": results,
        "guardrails": GUARDRAILS, "trades": trades,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"phase12_intraday_drop_entry_{args.direction}_{args.stage}"
    (args.output_dir / f"{stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / f"{stem}.md").write_text(render_markdown(payload), encoding="utf-8")
    print(f"[phase12] {args.output_dir / stem}.json / .md · 조합 {len(results)} · 거래 {len(trades)}")


if __name__ == "__main__":
    main()
