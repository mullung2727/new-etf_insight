"""8단계: lower-low 양봉 후보를 1분봉 진입·청산으로 세분화한다."""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from research.watchlist_expected_return.minute_bar_cache import (
    DEFAULT_CACHE_DIR,
    load_or_fetch_minutes,
)
from research.watchlist_expected_return.phase4_holding_strategy import summarize_outcomes
from research.watchlist_expected_return.phase7_pullback_strategy import (
    DEFAULT_KRX_DB,
    DEFAULT_WATCHLIST_DB,
    load_research_rows,
)


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "watchlist_pullback_strategy"
ENTRY_RULES = ("close_confirm", "five_bar_high_break", "vwap_reclaim", "low_rebound_1pct")


def regular_bars(payload: dict[str, Any], dates: list[str]) -> list[dict[str, Any]]:
    wanted = set(dates)
    return [
        bar for bar in payload["bars"]
        if bar["date"] in wanted and "090000" <= bar["time"] <= "153000"
    ]


def find_minute_entry(
    bars: list[dict[str, Any]], entry_date: str, prior_low: float, rule: str
) -> dict[str, Any] | None:
    day = [bar for bar in bars if bar["date"] == entry_date]
    if not day or min(bar["low"] for bar in day) >= prior_low:
        return None
    if rule == "close_confirm":
        bar = day[-1]
        if bar["close"] <= day[0]["open"]:
            return None
        return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}

    running_low = float("inf")
    session_open = day[0]["open"]
    cumulative_value = cumulative_volume = 0
    prior_vwap_side = None
    for index, bar in enumerate(day):
        running_low = min(running_low, bar["low"])
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        cumulative_value += typical * (bar["volume"] or 0)
        cumulative_volume += bar["volume"] or 0
        vwap = cumulative_value / cumulative_volume if cumulative_volume else None
        lower_low_seen = running_low < prior_low
        signal = False
        if rule == "five_bar_high_break" and lower_low_seen and index >= 5:
            signal = bar["close"] > session_open and bar["close"] > max(item["high"] for item in day[index - 5:index])
        elif rule == "vwap_reclaim" and lower_low_seen and vwap is not None:
            side = bar["close"] >= vwap
            signal = prior_vwap_side is False and side and bar["close"] > session_open
            prior_vwap_side = side
        elif rule == "low_rebound_1pct" and lower_low_seen:
            signal = bar["close"] > session_open and bar["close"] >= running_low * 1.01
        if signal:
            return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
    return None


def exit_candidates() -> list[dict[str, Any]]:
    fixed = [{"kind": "fixed_close", "days": day} for day in (1, 3, 5)]
    threshold = [
        {"kind": "tp_sl", "tp": tp, "sl": sl, "days": days}
        for tp, sl, days in itertools.product((0.03, 0.05), (0.02, 0.03), (3, 5))
    ]
    return fixed + threshold


def strategy_id(strategy: dict[str, Any]) -> str:
    if strategy["kind"] == "fixed_close":
        return f"fixed_d{strategy['days']}"
    return f"tp{strategy['tp']:.0%}_sl{strategy['sl']:.0%}_d{strategy['days']}"


def simulate_minute_exit(
    bars: list[dict[str, Any]], entry: dict[str, Any], trading_dates: list[str],
    strategy: dict[str, Any],
) -> dict[str, Any] | None:
    entry_time = entry["entry_timestamp"]
    price = entry["entry_price"]
    dates = trading_dates[:strategy["days"] + 1]
    path = [bar for bar in bars if bar["date"] in dates and bar["timestamp"] > entry_time]
    required_exit_dates = set(trading_dates[1:strategy["days"] + 1])
    if not required_exit_dates.issubset({bar["date"] for bar in path}):
        return None
    if strategy["kind"] == "tp_sl":
        tp_price, sl_price = price * (1 + strategy["tp"]), price * (1 - strategy["sl"])
        for bar in path:
            if bar["open"] >= tp_price:
                return {"gross_return": bar["open"] / price - 1, "exit_reason": "gap_tp"}
            if bar["open"] <= sl_price:
                return {"gross_return": bar["open"] / price - 1, "exit_reason": "gap_sl"}
            tp_hit, sl_hit = bar["high"] >= tp_price, bar["low"] <= sl_price
            if tp_hit and sl_hit:
                return {"gross_return": -strategy["sl"], "exit_reason": "same_minute_both_sl"}
            if tp_hit:
                return {"gross_return": strategy["tp"], "exit_reason": "tp"}
            if sl_hit:
                return {"gross_return": -strategy["sl"], "exit_reason": "sl"}
    exit_date = trading_dates[strategy["days"]]
    final = [bar for bar in path if bar["date"] == exit_date][-1]
    return {"gross_return": final["close"] / price - 1, "exit_reason": "forced_close"}


def find_lower_low_day(row: dict[str, Any]) -> tuple[int, float] | None:
    """D+1~D+5 중 전일 저가를 처음 이탈한 날과 비교 저가를 반환한다."""
    prior_low = row["history"][-1]["low"]
    for index, day in enumerate(row["future"][:5]):
        if day.get("low") and prior_low and day["low"] < prior_low:
            return index, prior_low
        prior_low = day.get("low") or prior_low
    return None


def load_minute_payloads(
    requests: list[tuple[str, str, str]], cache_dir: Path
) -> dict[tuple[str, str], dict[str, Any]]:
    """같은 종목·기준일 요청은 가장 이른 시작일로 한 번만 조회한다."""
    earliest_by_key: dict[tuple[str, str], str] = {}
    for ticker, base_dt, earliest_dt in requests:
        key = (ticker, base_dt)
        earliest_by_key[key] = min(earliest_by_key.get(key, earliest_dt), earliest_dt)
    return {
        key: load_or_fetch_minutes(key[0], key[1], earliest_dt, cache_dir=cache_dir)
        for key, earliest_dt in earliest_by_key.items()
    }


def load_minute_samples(rows: list[dict[str, Any]], cache_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    samples, stats = [], {"daily_entries": 0, "complete_horizon": 0, "cache_complete": 0}
    candidates = []
    for row in rows:
        lower_low = find_lower_low_day(row)
        if not lower_low:
            continue
        stats["daily_entries"] += 1
        entry_index, prior_low = lower_low
        dates = [day["date"] for day in row["future"][entry_index:entry_index + 6]]
        if len(dates) < 6:
            continue
        stats["complete_horizon"] += 1
        candidates.append((row, prior_low, dates))

    payloads = load_minute_payloads(
        [(row["ticker"], dates[-1], dates[0]) for row, _, dates in candidates],
        cache_dir,
    )
    for row, prior_low, dates in candidates:
        payload = payloads[(row["ticker"], dates[-1])]
        if not payload["complete"]:
            continue
        stats["cache_complete"] += 1
        samples.append({
            "watchlist_date": row["date"], "ticker": row["ticker"],
            "entry_date": dates[0], "trading_dates": dates,
            "prior_low": prior_low,
            "bars": regular_bars(payload, dates),
        })
    return samples, stats


def analyze(samples: list[dict[str, Any]], stats: dict[str, int], cost_rate: float = 0.01) -> dict[str, Any]:
    dates = sorted({sample["entry_date"] for sample in samples})
    if len(dates) < 2:
        raise ValueError("시간 분할에 필요한 완전한 분봉 표본이 부족함")
    split_date = dates[len(dates) // 2]
    rules = []
    for rule in ENTRY_RULES:
        entries = []
        for sample in samples:
            entry = find_minute_entry(sample["bars"], sample["entry_date"], sample["prior_low"], rule)
            if entry:
                entries.append((sample, entry))
        comparisons = []
        for strategy in exit_candidates():
            outcomes = []
            for sample, entry in entries:
                outcome = simulate_minute_exit(sample["bars"], entry, sample["trading_dates"], strategy)
                if outcome:
                    outcomes.append({**outcome, "entry_date": sample["entry_date"], "ticker": sample["ticker"], "holding_days": strategy["days"]})
            early = [item for item in outcomes if item["entry_date"] < split_date]
            late = [item for item in outcomes if item["entry_date"] >= split_date]
            comparisons.append({"id": strategy_id(strategy), "strategy": strategy,
                                "early": summarize_outcomes(early, cost_rate),
                                "late": summarize_outcomes(late, cost_rate)})
        selectable = [item for item in comparisons if item["early"]["count"] >= 10]
        selected = max(selectable, key=lambda item: item["early"]["mean"]) if selectable else None
        late = selected["late"] if selected else None
        rules.append({"rule": rule, "entry_count": len(entries),
                      "no_buy_rate": round(1 - len(entries) / len(samples), 4),
                      "selected_on_early": selected,
                      "late_validated": bool(late and late["count"] >= 10 and late["mean"] > 0),
                      "comparisons": comparisons})
    validated = [rule for rule in rules if rule["late_validated"]]
    best = max(validated, key=lambda item: item["selected_on_early"]["late"]["mean"]) if validated else None
    return {
        "analysis_version": 1, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_stats": stats, "sample_count": len(samples), "split_date": split_date,
        "cost_rate": cost_rate, "best_validated": best["rule"] if best else None,
        "cost_sensitivity": {
            str(cost): {
                rule["rule"]: summarize_outcomes(
                    [
                        {**outcome, "entry_date": sample["entry_date"], "ticker": sample["ticker"], "holding_days": chosen["strategy"]["days"]}
                        for sample in samples
                        if (entry := find_minute_entry(sample["bars"], sample["entry_date"], sample["prior_low"], rule["rule"]))
                        if (outcome := simulate_minute_exit(sample["bars"], entry, sample["trading_dates"], chosen["strategy"]))
                    ], cost
                )
                for rule in rules if (chosen := rule["selected_on_early"])
            }
            for cost in (0.0, 0.005, 0.01)
        },
        "rules": rules,
        "definitions": {
            "close_confirm": "lower-low 발생일이 양봉으로 끝나면 정규장 마지막 1분봉 종가 매수",
            "five_bar_high_break": "lower-low 발생 후 시가 위에서 직전 5개 1분봉 고가를 종가로 돌파할 때 매수",
            "vwap_reclaim": "lower-low 발생 후 시가 위에서 누적 VWAP을 종가로 재돌파할 때 매수",
            "low_rebound_1pct": "lower-low 발생 후 시가 위에서 당일 누적 저점 대비 1% 반등한 종가에 매수",
        },
        "guardrails": [
            "09:00~15:30 정규장 1분봉만 사용", "TP·SL은 1분봉 시간순으로 판정",
            "같은 1분봉 안에서 TP·SL이 모두 닿으면 SL 우선", "왕복 비용 1% 차감",
            "전반부에서 청산 규칙을 선택하고 후반부는 별도 검증",
        ],
    }


def render_markdown(result: dict[str, Any]) -> str:
    stats = result["data_stats"]
    lines = ["# Watchlist 눌림목 연구 — 1분봉 세분화", "",
             f"- lower-low 후보: {stats['daily_entries']}건",
             f"- 완전한 5거래일 표본: {result['sample_count']}건",
             f"- 시간 분할일: {result['split_date']}", f"- 비용: {result['cost_rate']:.1%}",
             f"- 후반부 검증 최우수: {result['best_validated']}", "", "## 결과", "",
             "| 진입 | 매수 | 미매수율 | 선택 청산 | 후반 표본 | 후반 평균 | 후반 승률 | 검증 |",
             "| --- | ---: | ---: | --- | ---: | ---: | ---: | --- |"]
    for rule in result["rules"]:
        chosen = rule["selected_on_early"]
        late = chosen["late"] if chosen else {}
        lines.append(f"| {rule['rule']} | {rule['entry_count']} | {rule['no_buy_rate']} | {chosen['id'] if chosen else None} | {late.get('count')} | {late.get('mean')} | {late.get('positive_rate')} | {rule['late_validated']} |")
    lines.extend(["", "## 진입 정의", ""])
    lines.extend(f"- `{key}`: {value}" for key, value in result["definitions"].items())
    lines.extend(["", "## 비용 민감도 — 선택 청산 전체 표본", "",
                  "| 비용 | 진입 | 표본 | 평균 | 승률 |", "| ---: | --- | ---: | ---: | ---: |"])
    for cost, rules in result["cost_sensitivity"].items():
        for name, metrics in rules.items():
            lines.append(f"| {float(cost):.1%} | {name} | {metrics['count']} | {metrics['mean']} | {metrics['positive_rate']} |")
    lines.extend(["", "## 보수적 처리", ""])
    lines.extend(f"- {item}" for item in result["guardrails"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="lower-low 양봉 1분봉 연구")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    samples, stats = load_minute_samples(load_research_rows(args.watchlist_db, args.krx_db), args.cache_dir)
    result = analyze(samples, stats)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "phase8_minute_pullback_strategy.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "phase8_minute_pullback_strategy.md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"sample_count": len(samples), "best_validated": result["best_validated"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
