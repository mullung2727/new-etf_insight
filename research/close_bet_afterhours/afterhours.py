"""종가 매수 → NXT 시간외 TP/SL → 종료 전 청산 백테스트.

실행 (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.close_bet_afterhours.afterhours collect [--start YYYYMMDD] [--end YYYYMMDD]
    etl\\.venv\\Scripts\\python.exe -m research.close_bet_afterhours.afterhours run --out PATH [--cutoff 195500] [--start ...] [--end ...]

후보 = watchlist.sqlite3 llm_scores에서 score >= 0, 날짜별 score 상위 3
(동점 ticker 오름차순). 진입 = 당일 ohlcv 종가, 경로는 당일 NXT
분봉(time 봉 START) 중 154000~cutoff. 청산 순서: 갭 시가 우선 →
같은 봉 TP·SL 동시터치는 SL → 미달이면 마지막 봉 종가.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WATCHLIST_DB = ROOT / "etl" / "db" / "watchlist.sqlite3"
KRX_DB = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"

ROUNDTRIP_COST = 0.006
SESSION_START = "154000"
DEFAULT_CUTOFF = "195500"
TP_LEVELS = (0.01, 0.02, 0.03)
SL_LEVELS = (0.01, 0.02, 0.03)


def combo_key(tp: float, sl: float) -> str:
    return f"tp{round(tp * 100)}_sl{round(sl * 100)}"


def select_candidates(rows: list[dict]) -> list[dict]:
    """score >= 0만 날짜별 score 내림차순·ticker 오름차순 상위 3."""
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        score = r.get("score")
        if score is None or score < 0:
            continue
        by_date.setdefault(r["date"], []).append(r)
    out = []
    for date in sorted(by_date):
        ranked = sorted(by_date[date],
                        key=lambda r: (-r["score"], r["ticker"]))[:3]
        out.extend({"date": r["date"], "ticker": r["ticker"],
                    "score": r["score"]} for r in ranked)
    return out


def filter_afterhours_bars(bars: list[dict], date: str,
                           cutoff: str = DEFAULT_CUTOFF) -> list[dict]:
    """당일·[154000, cutoff] 봉만 시간 오름차순으로."""
    return sorted(
        (b for b in bars
         if b["date"] == date and SESSION_START <= b["time"] <= cutoff),
        key=lambda b: b["time"])


def simulate_exit(entry: float, bars: list[dict],
                  tp: float, sl: float) -> tuple[float, str] | None:
    """TP/SL 시간외 청산. 빈 경로면 None."""
    tp_lv, sl_lv = entry * (1 + tp), entry * (1 - sl)
    last = len(bars) - 1
    for i, b in enumerate(bars):
        o, h, low = b["open"], b["high"], b["low"]
        if o >= tp_lv:
            return o, "tp_open"
        if o <= sl_lv:
            return o, "sl_open"
        if low <= sl_lv:
            return sl_lv, "sl"
        if h >= tp_lv:
            return tp_lv, "tp"
        if i == last:
            return b["close"], "time"
    return None


def net_return(entry: float, exit_px: float) -> float:
    """비용 차감 순수익률."""
    return exit_px / entry - 1 - ROUNDTRIP_COST


def t_stat(nets: list[float]) -> float:
    """평균 / (표본표준편차 / sqrt(n)). n < 2 또는 분산 0이면 0.0."""
    if len(nets) < 2:
        return 0.0
    sd = statistics.stdev(nets)
    if sd == 0:
        return 0.0
    return (sum(nets) / len(nets)) / (sd / math.sqrt(len(nets)))


def summarize(nets: list[float]) -> dict:
    """수익률 리스트 → 지표 dict."""
    n = len(nets)
    wins = [r for r in nets if r > 0]
    losses = [r for r in nets if r < 0]
    cum, peak, mdd = 1.0, 1.0, 0.0
    for r in nets:
        cum *= 1 + r
        peak = max(peak, cum)
        mdd = max(mdd, 1 - cum / peak)
    return {
        "n": n,
        "mean": sum(nets) / n if n else 0.0,
        "median": statistics.median(nets) if n else 0.0,
        "win_rate": len(wins) / n if n else 0.0,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": (sum(wins) / -sum(losses)) if losses else 0.0,
        "t_stat": t_stat(nets),
        "cum": cum - 1,
        "mdd": mdd,
    }


def _load_candidates(start: str | None, end: str | None) -> list[dict]:
    import sqlite3

    con = sqlite3.connect(f"file:{WATCHLIST_DB}?mode=ro", uri=True)
    try:
        sql = "SELECT date, ticker, score FROM llm_scores"
        conds, params = [], []
        if start:
            conds.append("date >= ?")
            params.append(start)
        if end:
            conds.append("date <= ?")
            params.append(end)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        cols = ("date", "ticker", "score")
        return [dict(zip(cols, row)) for row in con.execute(sql, params)]
    finally:
        con.close()


def _load_ohlcv() -> tuple[dict[tuple[str, str], dict], dict[str, list[str]]]:
    """{(ticker, date): {open, close}}, {ticker: [dates asc]}."""
    import duckdb

    con = duckdb.connect(str(KRX_DB), read_only=True)
    try:
        rows = con.execute(
            "SELECT ticker, date, open, close FROM ohlcv").fetchall()
    finally:
        con.close()
    px, by_t = {}, {}
    for ticker, date, o, c in rows:
        px[(ticker, date)] = {"open": o, "close": c}
        by_t.setdefault(ticker, []).append(date)
    for dates in by_t.values():
        dates.sort()
    return px, by_t


def _next_open(px: dict, by_t: dict, ticker: str,
               date: str) -> float | None:
    dates = by_t.get(ticker, [])
    try:
        nxt = dates[dates.index(date) + 1]
    except (ValueError, IndexError):
        return None
    row = px.get((ticker, nxt))
    return row["open"] if row else None


def cmd_collect(start: str | None, end: str | None) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / "broker" / ".env")
    load_dotenv(ROOT / ".env")

    from research.watchlist_expected_return.minute_bar_store import (
        connect as connect_minute_store,
        load_bars,
    )

    cands = select_candidates(_load_candidates(start, end))
    by_ticker: dict[str, list[str]] = {}
    for c in cands:
        by_ticker.setdefault(c["ticker"], []).append(c["date"])

    covered_days, tickers_with_bars = 0, 0
    with connect_minute_store() as mcon:
        for i, ticker in enumerate(sorted(by_ticker)):
            bars = load_bars(mcon, f"{ticker}_NX",
                             sorted(set(by_ticker[ticker])))
            dates_with = {b["date"] for b in bars}
            if dates_with:
                tickers_with_bars += 1
            covered_days += sum(1 for d in by_ticker[ticker]
                                if d in dates_with)
            if (i + 1) % 20 == 0:
                print(f"{i + 1}/{len(by_ticker)} tickers",
                      file=sys.stderr)
    print(json.dumps({
        "candidate_ticker_days": len(cands),
        "tickers_with_any_bars": tickers_with_bars,
        "ticker_days_covered": covered_days,
    }, ensure_ascii=False))
    return 0


def _run_trades(cands: list[dict], px: dict, by_t: dict,
                mcon, cutoff: str) -> tuple[list[dict], int]:
    from research.watchlist_expected_return.minute_bar_store import load_bars

    by_ticker: dict[str, list[str]] = {}
    for c in cands:
        by_ticker.setdefault(c["ticker"], []).append(c["date"])
    bars_by_td: dict[tuple[str, str], list[dict]] = {}
    for ticker in sorted(by_ticker):
        for b in load_bars(mcon, f"{ticker}_NX",
                           sorted(set(by_ticker[ticker]))):
            bars_by_td.setdefault((ticker, b["date"]), []).append(b)

    trades, uncovered = [], 0
    for c in cands:
        ticker, date = c["ticker"], c["date"]
        entry_row = px.get((ticker, date))
        if not entry_row or not entry_row["close"]:
            uncovered += 1
            continue
        entry = entry_row["close"]
        path = filter_afterhours_bars(bars_by_td.get((ticker, date), []),
                                      date, cutoff)
        if not path:
            uncovered += 1
            continue
        combos = {}
        for tp in TP_LEVELS:
            for sl in SL_LEVELS:
                exit_px, reason = simulate_exit(entry, path, tp, sl)
                combos[combo_key(tp, sl)] = {
                    "exit": exit_px, "reason": reason,
                    "net": net_return(entry, exit_px)}
        nxt = _next_open(px, by_t, ticker, date)
        trades.append({
            "date": date, "ticker": ticker, "score": c["score"],
            "entry": entry, "bars": len(path),
            "volume": sum(b["volume"] for b in path),
            "max_up": max(b["high"] for b in path) / entry - 1,
            "max_down": min(b["low"] for b in path) / entry - 1,
            "combos": combos,
            "hold_ah": {"exit": path[-1]["close"],
                        "net": net_return(entry, path[-1]["close"])},
            "next_open": ({"exit": nxt, "net": net_return(entry, nxt)}
                          if nxt else None),
        })
    return trades, uncovered


def _summarize_all(trades: list[dict]) -> dict:
    keys = ([combo_key(tp, sl) for tp in TP_LEVELS for sl in SL_LEVELS]
            + ["hold_ah", "next_open"])
    out = {}
    for key in keys:
        nets, reasons, daily = [], {}, {}
        for t in trades:
            cell = t[key] if key in ("hold_ah", "next_open") else t["combos"][key]
            if cell is None:
                continue
            nets.append(cell["net"])
            if key not in ("hold_ah", "next_open"):
                reasons[cell["reason"]] = reasons.get(cell["reason"], 0) + 1
            daily.setdefault(t["date"], []).append(cell["net"])
        out[key] = {
            "trades": summarize(nets),
            "daily": summarize([sum(v) / len(v) for v in daily.values()]),
            "reasons": reasons or ({("time" if key == "hold_ah" else "open"): len(nets)}
                                   if nets else {}),
        }
    return out


def cmd_run(out_path: str, cutoff: str, start: str | None,
            end: str | None) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / "broker" / ".env")
    load_dotenv(ROOT / ".env")

    from research.watchlist_expected_return.minute_bar_store import (
        connect as connect_minute_store,
    )

    cands = select_candidates(_load_candidates(start, end))
    px, by_t = _load_ohlcv()
    with connect_minute_store() as mcon:
        trades, uncovered = _run_trades(cands, px, by_t, mcon, cutoff)
    summary = _summarize_all(trades)
    payload = {
        "_spec": {"cutoff": cutoff, "start": start, "end": end,
                  "roundtrip_cost": ROUNDTRIP_COST,
                  "tp_levels": list(TP_LEVELS), "sl_levels": list(SL_LEVELS),
                  "n_candidates": len(cands), "n_covered": len(trades),
                  "n_uncovered": uncovered},
        "trades": trades,
        "summary": summary,
    }
    if os.path.dirname(out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"{'combo':<10}{'n':>6}{'daily_mean%':>13}{'win%':>7}{'t':>8}")
    for key, s in summary.items():
        print(f"{key:<10}{s['trades']['n']:>6}"
              f"{s['daily']['mean'] * 100:>13.2f}"
              f"{s['trades']['win_rate'] * 100:>7.1f}"
              f"{s['trades']['t_stat']:>8.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="종가→NXT 시간외 TP/SL 백테스트")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_collect = sub.add_parser("collect", help="NXT 분봉 수집·커버리지")
    p_collect.add_argument("--start", default=None)
    p_collect.add_argument("--end", default=None)
    p_run = sub.add_parser("run", help="시뮬레이션·JSON 출력")
    p_run.add_argument("--out", required=True)
    p_run.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    p_run.add_argument("--start", default=None)
    p_run.add_argument("--end", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "collect":
        return cmd_collect(args.start, args.end)
    return cmd_run(args.out, args.cutoff, args.start, args.end)


if __name__ == "__main__":
    raise SystemExit(main())
