"""7단계: watchlist 종목의 선택적 눌림목 진입과 5거래일 이내 청산 비교."""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from research.watchlist_expected_return.phase1_data_audit import (
    DEFAULT_KRX_DB,
    DEFAULT_WATCHLIST_DB,
    connect_sqlite_ro,
)
from research.watchlist_expected_return.phase4_holding_strategy import (
    simulate_tp_sl,
    summarize_outcomes,
)


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "watchlist_pullback_strategy"
ENTRY_RULES = (
    "limit_down_3pct",
    "limit_down_5pct",
    "gap_down_2pct",
    "ma5_reclaim",
    "lower_low_bullish_reversal",
)


def load_research_rows(watchlist_db: Path, krx_db: Path) -> list[dict[str, Any]]:
    """watchlist일 전 5일과 후 10일까지의 일봉 경로를 읽는다."""
    with closing(connect_sqlite_ro(watchlist_db)) as con:
        watchlist = [
            {"date": str(row[0]), "ticker": str(row[1])}
            for row in con.execute("SELECT date, stock_code FROM watchlist ORDER BY date, stock_code")
        ]
    tickers = sorted({row["ticker"] for row in watchlist})
    if not tickers:
        return []
    marks = ",".join("?" for _ in tickers)
    with duckdb.connect(str(krx_db), read_only=True) as con:
        trading_dates = [str(row[0]) for row in con.execute(
            "SELECT DISTINCT date FROM ohlcv ORDER BY date"
        ).fetchall()]
        price_rows = con.execute(
            f"""SELECT date, ticker, open, high, low, close, volume
                FROM ohlcv WHERE ticker IN ({marks}) ORDER BY ticker, date""",
            tickers,
        ).fetchall()
    by_ticker: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for date, ticker, open_, high, low, close, volume in price_rows:
        by_ticker[str(ticker)][str(date)] = {
            "date": str(date), "open": open_, "high": high, "low": low,
            "close": close, "volume": volume,
        }
    date_index = {date: index for index, date in enumerate(trading_dates)}
    output = []
    for item in watchlist:
        index = date_index.get(item["date"])
        prices = by_ticker.get(item["ticker"], {})
        base = prices.get(item["date"])
        if index is None or not base or not base.get("close"):
            continue
        window_dates = trading_dates[max(0, index - 5):index + 11]
        if not all(date in prices for date in window_dates):
            continue
        output.append({
            **item,
            "base_close": base["close"],
            "history": [prices[date] for date in trading_dates[max(0, index - 5):index + 1]],
            "future": [prices[date] for date in trading_dates[index + 1:index + 11]],
        })
    return output


def find_entry(row: dict[str, Any], rule: str, max_wait_days: int = 5) -> dict[str, Any] | None:
    """D+1~D+5에서 최초 신호만 체결한다."""
    prior = list(row["history"])
    for wait_day, day in enumerate(row["future"][:max_wait_days], start=1):
        if not all((day.get(key) or 0) > 0 for key in ("open", "high", "low", "close")):
            prior.append(day)
            continue
        price = None
        if rule in {"limit_down_3pct", "limit_down_5pct"}:
            rate = 0.03 if rule == "limit_down_3pct" else 0.05
            limit_price = row["base_close"] * (1 - rate)
            if day["low"] <= limit_price:
                price = min(day["open"], limit_price)
        elif rule == "gap_down_2pct" and day["open"] <= row["base_close"] * 0.98:
            price = day["open"]
        elif rule == "ma5_reclaim" and len(prior) >= 5:
            ma5 = statistics.fmean(value["close"] for value in prior[-5:])
            if day["low"] <= ma5 <= day["close"]:
                price = day["close"]
        elif rule == "lower_low_bullish_reversal" and prior:
            if day["low"] < prior[-1]["low"] and day["close"] > day["open"]:
                price = day["close"]
        if price:
            return {"entry_price": price, "entry_date": day["date"], "wait_days": wait_day,
                    "entry_future_index": wait_day - 1}
        prior.append(day)
    return None


def exit_candidates() -> list[dict[str, Any]]:
    fixed = [{"kind": "fixed_close", "max_hold_days": day} for day in (1, 3, 5)]
    thresholds = [
        {"kind": "tp_sl", "tp": tp, "sl": sl, "max_hold_days": days, "touch_policy": "sl_first"}
        for tp, sl, days in itertools.product((0.03, 0.05), (0.02, 0.03), (3, 5))
    ]
    return fixed + thresholds


def exit_id(strategy: dict[str, Any]) -> str:
    if strategy["kind"] == "fixed_close":
        return f"fixed_d{strategy['max_hold_days']}"
    return f"tp{strategy['tp']:.0%}_sl{strategy['sl']:.0%}_d{strategy['max_hold_days']}"


def simulate_exit(row: dict[str, Any], entry: dict[str, Any], strategy: dict[str, Any]) -> dict[str, Any] | None:
    after_entry = row["future"][entry["entry_future_index"] + 1:]
    path = [
        {**day, "horizon": horizon}
        for horizon, day in enumerate(after_entry[:strategy["max_hold_days"]], start=1)
    ]
    if len(path) < strategy["max_hold_days"]:
        return None
    if any(not all((day.get(key) or 0) > 0 for key in ("open", "high", "low", "close")) for day in path):
        return None
    simulation_row = {"entry_close": entry["entry_price"], "price_path": path}
    if strategy["kind"] == "fixed_close":
        final = path[-1]
        outcome = {"gross_return": final["close"] / entry["entry_price"] - 1,
                   "holding_days": strategy["max_hold_days"], "exit_reason": "fixed_close"}
    else:
        outcome = simulate_tp_sl(simulation_row, strategy["tp"], strategy["sl"],
                                 strategy["max_hold_days"], "sl_first")
    return {**outcome, "entry_date": entry["entry_date"], "wait_days": entry["wait_days"],
            "ticker": row["ticker"]} if outcome else None


def analyze_pullbacks(rows: list[dict[str, Any]], cost_rate: float = 0.01) -> dict[str, Any]:
    dates = sorted({row["date"] for row in rows})
    if len(dates) < 2:
        raise ValueError("시간 분할에 필요한 watchlist 날짜가 부족함")
    split_date = dates[len(dates) // 2]
    results = []
    for rule in ENTRY_RULES:
        entries = [(row, find_entry(row, rule)) for row in rows]
        entries = [(row, entry) for row, entry in entries if entry]
        strategy_results = []
        for strategy in exit_candidates():
            outcomes = [simulate_exit(row, entry, strategy) for row, entry in entries]
            outcomes = [outcome for outcome in outcomes if outcome]
            early = [outcome for outcome in outcomes if outcome["entry_date"] < split_date]
            late = [outcome for outcome in outcomes if outcome["entry_date"] >= split_date]
            strategy_results.append({"id": exit_id(strategy), "strategy": strategy,
                                     "early": summarize_outcomes(early, cost_rate),
                                     "late": summarize_outcomes(late, cost_rate)})
        selectable = [item for item in strategy_results if item["early"]["count"] >= 10]
        selected = max(selectable, key=lambda item: item["early"]["mean"]) if selectable else None
        late = selected["late"] if selected else None
        results.append({
            "rule": rule,
            "eligible_count": len(rows),
            "entry_count": len(entries),
            "no_buy_rate": round(1 - len(entries) / len(rows), 4) if rows else None,
            "avg_wait_days": round(statistics.fmean(entry["wait_days"] for _, entry in entries), 4) if entries else None,
            "selected_on_early": selected,
            "late_validated": bool(late and late["count"] >= 10 and late["mean"] > 0),
            "strategies": strategy_results,
        })
    validated = [item for item in results if item["late_validated"]]
    best = max(validated, key=lambda item: item["selected_on_early"]["late"]["mean"]) if validated else None
    return {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": len(rows), "split_date": split_date, "cost_rate": cost_rate,
        "entry_wait_days": 5, "max_holding_days_after_entry": 5,
        "definitions": {
            "limit_down_3pct": "watchlist일 종가보다 3% 낮은 지정가 최초 체결",
            "limit_down_5pct": "watchlist일 종가보다 5% 낮은 지정가 최초 체결",
            "gap_down_2pct": "watchlist일 종가 대비 2% 이상 갭하락한 날 시가 매수",
            "ma5_reclaim": "당일 저가가 직전 5일 종가 평균 이하이고 종가는 평균 이상일 때 종가 매수",
            "lower_low_bullish_reversal": "전일 저가 이탈 후 양봉이면 종가 매수",
        },
        "rules": results,
        "best_validated": best["rule"] if best else None,
        "decision": "validated_candidate_found" if best else "no_validated_candidate",
        "guardrails": [
            "전반부에서 청산 규칙을 선택하고 후반부 성과를 별도 표시",
            "동일 일봉 TP·SL 동시 도달은 SL 우선",
            "지정가 아래로 갭하락하면 시가 체결로 계산",
            "MA·반전 규칙은 종가를 확인한 뒤 해당 종가 체결 가능하다는 근사",
            "거래정지 등 OHLC가 0인 보유 경로는 강제청산 가격을 알 수 없어 성과 표본에서 제외",
            "왕복 거래비용은 실제 주문 표본을 참고한 민감도 가정이며 확정 비용이 아님",
        ],
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Watchlist 기대수익 연구 — 7단계 눌림목 매수",
        "", f"- 분석 표본: {result['sample_count']}건", f"- 시간 분할일: {result['split_date']}",
        f"- 왕복 비용: {result['cost_rate']:.1%}", f"- 결론: {result['decision']}",
        f"- 후반부 검증 최우수: {result['best_validated']}", "", "## 정의별 결과", "",
        "| 눌림목 | 매수 | 미매수율 | 평균 대기일 | 선택 청산 | 후반 표본 | 후반 평균 | 후반 승률 | 검증 |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for item in result["rules"]:
        selected = item["selected_on_early"]
        late = selected["late"] if selected else {}
        lines.append(
            f"| {item['rule']} | {item['entry_count']} | {item['no_buy_rate']} | {item['avg_wait_days']} | "
            f"{selected['id'] if selected else None} | {late.get('count')} | {late.get('mean')} | "
            f"{late.get('positive_rate')} | {item['late_validated']} |"
        )
    lines.extend(["", "## 눌림목 정의", ""])
    lines.extend(f"- `{key}`: {value}" for key, value in result["definitions"].items())
    lines.extend(["", "## 해석 제한", ""])
    lines.extend(f"- {item}" for item in result["guardrails"])
    return "\n".join(lines) + "\n"


def write_results(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "phase7_pullback_strategy.json"
    md_path = output_dir / "phase7_pullback_strategy.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="watchlist 눌림목 진입 및 5일 이내 청산 비교")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cost-rate", type=float, default=0.01)
    args = parser.parse_args(argv)
    result = analyze_pullbacks(load_research_rows(args.watchlist_db, args.krx_db), args.cost_rate)
    for path in write_results(result, args.output_dir):
        print(f"[phase7] {path}")


if __name__ == "__main__":
    main()
