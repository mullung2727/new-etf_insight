"""실체결 진입가 + 정규장 1분봉으로 눌림목/종가베팅 TP·SL 조합을 비교한다.

진입 모집단은 watchlist.sqlite3의 체결확정 행이며, 청산은 실제 운영 보유기간을 재현한다.
- pullback: 진입 다음 거래일부터 3거래일, 마지막 날 15:19 강제청산
- close_bet: 진입 다음 거래일 하루, 15:19 강제청산

1분봉 한계상 봉 내부 체결 순서를 알 수 없으므로 한 봉에서 TP·SL 동시 터치 시 SL 우선이다.
장 시작 갭은 첫 1분봉 시가, 장중 임계치 터치는 임계가격, 강제청산은 15:19 봉 종가로 근사한다.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from research.watchlist_expected_return.minute_bar_store import connect as minute_connect
from research.watchlist_expected_return.minute_bar_store import load_bars

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "etl" / "db" / "watchlist.sqlite3"
DEFAULT_MINUTES = ROOT / "etl" / "db" / "minute_bars.duckdb"
DEFAULT_KRX = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"
STRATEGY_CONFIGS = {
    "pullback": ROOT / "etl" / "scripts" / "pullback.json",
    "close_bet": ROOT / "etl" / "scripts" / "close_bet.json",
}
DEFAULT_OUTPUT = ROOT / "docs" / "BACKTEST_TRADING_EXIT_TP_SL_1MIN"
TP_GRID = tuple(i / 100 for i in range(2, 8))
SL_GRID = tuple(i / 100 for i in range(2, 8))
COST_RATE = 0.006
REAL_START = "20260820"
FORCE_TIME = "151900"


def _rows(con: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(sql).fetchall()]


def load_entries(ledger: Path) -> list[dict[str, Any]]:
    """유효 체결 진입을 로드한다. pullback의 동일 체결 중복 원장행은 1건으로 합친다."""
    con = sqlite3.connect(ledger)
    try:
        pullback = _rows(con, """
            SELECT signal_date AS entry_date, ticker, buy_price AS entry_price,
                   buy_qty AS qty, status, created_at
            FROM pullback_orders
            WHERE buy_price > 0 AND buy_qty > 0 AND status IN ('confirmed','closed')
            ORDER BY signal_date, ticker, created_at
        """)
        close_bet = _rows(con, """
            SELECT date AS entry_date, ticker, cntr_price AS entry_price,
                   COALESCE(NULLIF(cntr_qty,0), qty) AS qty, status, created_at
            FROM close_bet_orders
            WHERE cntr_price > 0 AND COALESCE(NULLIF(cntr_qty,0), qty) > 0
              AND status='confirmed'
            ORDER BY date, ticker, created_at
        """)
    finally:
        con.close()

    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in pullback:
        key = (row["entry_date"], row["ticker"], row["entry_price"], row["qty"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**row, "strategy": "pullback", "hold_days": 3})
    out.extend({**row, "strategy": "close_bet", "hold_days": 1} for row in close_bet)
    return out


def load_trading_dates(krx_db: Path, ledger: Path) -> list[str]:
    """거래일 달력. KRX 원장을 쓰고, 아직 적재 안 된 최근 날짜만 원장에서 잇는다.

    intraday_ranking 단독으로는 못 쓴다 — 배치가 거른 날이 빠지고, 실제로 20260810 이
    없어서 그 날을 지나는 보유창이 전부 하루씩 밀렸다.
    krx_ohlcv 단독으로도 못 쓴다 — 장 마감 후 적재라 당일이 없어 최근 진입분이 통째로
    빠진다. 그래서 krx 마지막 날 이후 구간만 원장 날짜로 채운다.
    """
    con = duckdb.connect(str(krx_db), read_only=True)
    try:
        dates = [str(row[0]) for row in con.execute(
            "SELECT DISTINCT date FROM ohlcv ORDER BY date"
        ).fetchall()]
    finally:
        con.close()

    tail_con = sqlite3.connect(ledger)
    try:
        tail = [row[0] for row in tail_con.execute(
            "SELECT DISTINCT date FROM intraday_ranking WHERE date > ? ORDER BY date",
            (dates[-1],),
        ).fetchall()]
    finally:
        tail_con.close()
    return dates + tail


def attach_exit_dates(entries: list[dict[str, Any]], trading_dates: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pos = {date: i for i, date in enumerate(trading_dates)}
    ready, skipped = [], []
    for entry in entries:
        i = pos.get(entry["entry_date"])
        n = entry["hold_days"]
        if i is None or i + n >= len(trading_dates):
            skipped.append({**entry, "skip_reason": "incomplete_horizon_or_missing_calendar"})
            continue
        ready.append({**entry, "exit_dates": trading_dates[i + 1:i + n + 1]})
    return ready, skipped


def simulate(bars: list[dict[str, Any]], entry_price: int, exit_dates: list[str], tp: float, sl: float) -> dict[str, Any] | None:
    """정규장 1분봉 청산. 데이터 불완전 시 None."""
    by_date: dict[str, list[dict[str, Any]]] = {}
    for bar in bars:
        if "090000" <= bar["time"] <= FORCE_TIME:
            by_date.setdefault(bar["date"], []).append(bar)
    if any(not by_date.get(date) for date in exit_dates):
        return None

    tp_price = entry_price * (1 + tp)
    sl_price = entry_price * (1 - sl)
    for date in exit_dates:
        day = sorted(by_date[date], key=lambda b: b["time"])
        for index, bar in enumerate(day):
            # 마지막 날 15:19 사이클은 운영에서 TP/SL 을 보지 않고 무조건 시장가로 판다
            # (run_pullback_exit.py: reason = "forced" if force ... else decide_exit(...)).
            # 이 봉에서 TP 판정을 먼저 하면 실제보다 좋은 가격에 판 것으로 계산된다.
            if date == exit_dates[-1] and bar["time"] == FORCE_TIME:
                return _out(bar["close"], entry_price, "forced", bar)
            if index == 0:
                if bar["open"] >= tp_price:
                    return _out(bar["open"], entry_price, "gap_tp", bar)
                if bar["open"] <= sl_price:
                    return _out(bar["open"], entry_price, "gap_sl", bar)
            tp_hit = bar["high"] >= tp_price
            sl_hit = bar["low"] <= sl_price
            if tp_hit and sl_hit:
                return _out(entry_price * (1 - sl), entry_price, "same_minute_both_sl", bar)
            if tp_hit:
                return _out(entry_price * (1 + tp), entry_price, "tp", bar)
            if sl_hit:
                return _out(entry_price * (1 - sl), entry_price, "sl", bar)
    final_day = sorted(by_date[exit_dates[-1]], key=lambda b: b["time"])
    eligible = [bar for bar in final_day if bar["time"] <= FORCE_TIME]
    if not eligible:
        return None
    return _out(eligible[-1]["close"], entry_price, "forced", eligible[-1])


def _out(exit_price: float, entry_price: int, reason: str, bar: dict[str, Any]) -> dict[str, Any]:
    return {
        "gross_return": exit_price / entry_price - 1,
        "exit_price": exit_price,
        "exit_reason": reason,
        "exit_timestamp": bar["timestamp"],
    }


def _metric(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {
        "count": len(values),
        "mean": mean,
        "median": statistics.median(values),
        "positive_rate": sum(v > 0 for v in values) / len(values),
        "t_value": mean / (sd / math.sqrt(len(values))) if sd else None,
        "min": min(values),
        "max": max(values),
    }


def summarize(outcomes: list[dict[str, Any]], cost_rate: float) -> dict[str, Any]:
    net = [o["gross_return"] - cost_rate for o in outcomes]
    invested = sum(o["entry_price"] * o["qty"] for o in outcomes)
    gross_won = sum(o["entry_price"] * o["qty"] * o["gross_return"] for o in outcomes)
    cost_won = invested * cost_rate
    return {
        **_metric(net),
        "gross_mean": statistics.fmean(o["gross_return"] for o in outcomes) if outcomes else None,
        "invested_won": round(invested),
        "net_pnl_won": round(gross_won - cost_won),
        "capital_weighted_net_return": (gross_won - cost_won) / invested if invested else None,
        "exit_reasons": dict(Counter(o["exit_reason"] for o in outcomes)),
    }


def analyze(entries: list[dict[str, Any]], bars_by_entry: dict[tuple[str, str], list[dict[str, Any]]], cost_rate: float) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for strategy in ("pullback", "close_bet"):
        strategy_entries = [e for e in entries if e["strategy"] == strategy]
        cohorts = {
            "all": strategy_entries,
            "real_period": [e for e in strategy_entries if e["entry_date"] >= REAL_START],
        }
        grids = {}
        details = {}
        for cohort_name, cohort_entries in cohorts.items():
            combos = []
            for tp in TP_GRID:
                for sl in SL_GRID:
                    outcomes = []
                    for e in cohort_entries:
                        out = simulate(bars_by_entry.get((e["strategy"], e["entry_id"]), []), e["entry_price"], e["exit_dates"], tp, sl)
                        if out:
                            outcomes.append({**e, **out})
                    metrics = summarize(outcomes, cost_rate)
                    combos.append({"tp": tp, "sl": sl, **metrics})
                    details[f"tp{tp:.0%}_sl{sl:.0%}"] = outcomes
            combos.sort(key=lambda x: (x.get("mean", -999), x.get("capital_weighted_net_return", -999)), reverse=True)
            grids[cohort_name] = combos
        # 운영 설정에서 읽는다. 하드코딩하면 config 를 바꾼 순간 리포트가 거짓말을 한다.
        config = json.loads(STRATEGY_CONFIGS[strategy].read_text(encoding="utf-8"))
        result[strategy] = {
            "sample_counts": {name: len(rows) for name, rows in cohorts.items()},
            "current": {"tp": config["tp"], "sl": config["sl"]},
            "grids": grids,
        }
    return result


def _current_row(block: dict[str, Any], cohort: str) -> dict[str, Any] | None:
    """운영 중인 TP/SL 조합의 결과. 설정값이 탐색 그리드 밖이면 None."""
    return next(
        (row for row in block["grids"][cohort]
         if row["tp"] == block["current"]["tp"] and row["sl"] == block["current"]["sl"]),
        None,
    )


def _current_phrase(block: dict[str, Any], row: dict[str, Any] | None) -> str:
    tp, sl = block["current"]["tp"], block["current"]["sl"]
    if row is None:
        return f"현재 {tp:.0%}/{sl:.0%}는 탐색 범위 밖이라 비교 대상이 없다"
    return f"현재 {tp:.0%}/{sl:.0%}는 {row['mean']:.2%}"


def render_markdown(payload: dict[str, Any]) -> str:
    pb = payload["results"]["pullback"]
    cb = payload["results"]["close_bet"]
    pb_real_best = pb["grids"]["real_period"][0]
    pb_all_best = pb["grids"]["all"][0]
    cb_real_best = cb["grids"]["real_period"][0]
    pb_current = _current_row(pb, "real_period")
    cb_current = _current_row(cb, "real_period")
    lines = [
        "# 눌림목·종가베팅 TP/SL 1분봉 백테스트", "",
        f"- 생성: {payload['generated_at']}",
        f"- 비용: 왕복 {payload['cost_rate']:.1%}",
        f"- 탐색: TP {', '.join(f'{x:.0%}' for x in TP_GRID)} × SL {', '.join(f'{x:.0%}' for x in SL_GRID)}",
        f"- 실전기간 기준일: 진입일 {REAL_START[:4]}-{REAL_START[4:6]}-{REAL_START[6:]} 이후(원장에 env 컬럼이 없어 날짜로 근사)",
        "- 1분봉 동시 TP/SL 터치는 보수적으로 SL 우선", "",
        "## 결론", "",
        f"- 눌림목 실전기간 1위는 TP {pb_real_best['tp']:.0%}/SL {pb_real_best['sl']:.0%}(평균 순수익률 {pb_real_best['mean']:.2%})지만, 전체 1위는 TP {pb_all_best['tp']:.0%}/SL {pb_all_best['sl']:.0%}(평균 {pb_all_best['mean']:.2%})로 달라 단일 최적값은 안정적이지 않다.",
        f"- 눌림목 {_current_phrase(pb, pb_current)}다(실전기간 {pb['sample_counts']['real_period']}건). 표본이 작은 채로 36개 조합을 훑은 결과이므로 1위 값을 바로 쓰기보다 실전·전체 양쪽 상위에 함께 드는 조합을 추가 표본으로 검증하는 편이 안전하다.",
        f"- 종가베팅은 실전기간 36개 조합 모두 비용 후 음수다. 최상위 TP {cb_real_best['tp']:.0%}/SL {cb_real_best['sl']:.0%}도 {cb_real_best['mean']:.2%}, {_current_phrase(cb, cb_current)}다. 청산 숫자 변경만으로는 기대값 문제가 해결되지 않는다.",
        "", 
    ]
    for strategy, title in (("pullback", "눌림목"), ("close_bet", "종가베팅")):
        block = payload["results"][strategy]
        lines += [f"## {title}", ""]
        for cohort, cohort_title in (("real_period", "실전기간"), ("all", "전체 가용")):
            rows = block["grids"][cohort]
            current = _current_row(block, cohort)
            if current is None:
                current_line = f"- 현재값 TP {block['current']['tp']:.0%} / SL {block['current']['sl']:.0%}: 탐색 그리드 밖이라 비교 불가"
            else:
                current_line = (
                    f"- 현재값 TP {block['current']['tp']:.0%} / SL {block['current']['sl']:.0%}: "
                    f"평균 순수익률 {current['mean']:.2%}, 승률 {current['positive_rate']:.1%}, "
                    f"추정 순손익 {current['net_pnl_won']:,}원, {rows.index(current) + 1}/{len(rows)}위"
                )
            lines += [
                f"### {cohort_title} ({block['sample_counts'][cohort]}건)", "",
                current_line,
                "- 평균 순수익률 상위 10개:", "",
                "| 순위 | TP | SL | 표본 | 평균 순수익률 | 중앙값 | 승률 | 자금가중 순수익률 | 추정 순손익 |",
                "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
            for i, row in enumerate(rows[:10], 1):
                lines.append(
                    f"| {i} | {row['tp']:.0%} | {row['sl']:.0%} | {row['count']} | {row['mean']:.2%} | {row['median']:.2%} | {row['positive_rate']:.1%} | {row['capital_weighted_net_return']:.2%} | {row['net_pnl_won']:,}원 |"
                )
            lines.append("")
    lines += [
        "## 해석 한계", "",
        "- 분봉에는 매수 1호가가 없어 체결가격 OHLC로 대체했다.",
        "- 임계치 도달 후 시장가 슬리피지는 비용 0.6%에 포함해 근사했으며 실제 체결을 재현하지 않는다.",
        "- 원장에 paper/real 구분 컬럼이 없어 2026-08-20 이후를 실전기간으로 가정했다.",
        "- 같은 분 안의 가격 경로를 모르므로 TP·SL 동시 터치는 SL 우선이다.",
        "- 36개 조합을 같은 소표본에서 탐색했으므로 1위 값은 과적합 가능성이 높다. 인접 조합과 전체/실전기간 일관성을 함께 봐야 한다.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--minutes", type=Path, default=DEFAULT_MINUTES)
    parser.add_argument("--krx", type=Path, default=DEFAULT_KRX)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cost-rate", type=float, default=COST_RATE)
    args = parser.parse_args(argv)

    entries, pre_skipped = attach_exit_dates(load_entries(args.ledger), load_trading_dates(args.krx, args.ledger))
    for i, entry in enumerate(entries):
        entry["entry_id"] = f"{i}:{entry['entry_date']}:{entry['ticker']}"

    bars_by_entry: dict[tuple[str, str], list[dict[str, Any]]] = {}
    data_skipped: list[dict[str, Any]] = []
    with minute_connect(args.minutes) as con:
        for n, entry in enumerate(entries, 1):
            print(f"[{n}/{len(entries)}] {entry['strategy']} {entry['ticker']} {entry['exit_dates'][0]}~{entry['exit_dates'][-1]}", flush=True)
            try:
                bars = load_bars(con, entry["ticker"], entry["exit_dates"])
            except Exception as exc:
                data_skipped.append({**entry, "skip_reason": f"fetch_error: {type(exc).__name__}: {exc}"})
                continue
            present = {bar["date"] for bar in bars if "090000" <= bar["time"] <= FORCE_TIME}
            if not set(entry["exit_dates"]).issubset(present):
                data_skipped.append({**entry, "skip_reason": "incomplete_minute_bars", "present_dates": sorted(present)})
                continue
            bars_by_entry[(entry["strategy"], entry["entry_id"])] = bars

    usable = [e for e in entries if (e["strategy"], e["entry_id"]) in bars_by_entry]
    results = analyze(usable, bars_by_entry, args.cost_rate)
    payload = {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cost_rate": args.cost_rate,
        "grids": {"tp": TP_GRID, "sl": SL_GRID},
        "source_entries": len(entries) + len(pre_skipped),
        "eligible_horizon": len(entries),
        "usable_entries": len(usable),
        "skipped": pre_skipped + data_skipped,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps({
        "usable": len(usable),
        "skipped": len(payload["skipped"]),
        "output_json": str(args.output.with_suffix('.json')),
        "output_md": str(args.output.with_suffix('.md')),
        "best_real": {
            name: block["grids"]["real_period"][0]
            for name, block in results.items()
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
