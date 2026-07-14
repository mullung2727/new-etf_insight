"""4단계: D+1~D+5 보유기간과 TP/SL 조합 비교."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from research.watchlist_expected_return.phase1_data_audit import (
    DEFAULT_KRX_DB,
    DEFAULT_WATCHLIST_DB,
    load_ohlcv,
)
from research.watchlist_expected_return.phase3_rising_features import load_feature_rows


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"
TP_VALUES = (0.03, 0.05, 0.07)
SL_VALUES = (0.02, 0.03, 0.05)
MAX_HOLD_DAYS = (1, 2, 3, 4, 5)
COST_RATES = (0.0, 0.005, 0.01)


def load_price_paths(watchlist_db: Path, krx_db: Path) -> list[dict[str, Any]]:
    rows = load_feature_rows(watchlist_db, krx_db)
    with duckdb.connect(str(krx_db), read_only=True) as krx:
        trading_dates = [str(row[0]) for row in krx.execute(
            "SELECT DISTINCT date FROM ohlcv ORDER BY date"
        ).fetchall()]
        date_index = {date: index for index, date in enumerate(trading_dates)}
        required_dates: set[str] = set()
        for row in rows:
            index = date_index.get(row["date"])
            if index is not None:
                required_dates.update(trading_dates[index + 1:index + 6])
        prices = load_ohlcv(
            krx,
            sorted(required_dates),
            sorted({row["ticker"] for row in rows}),
        )

    output: list[dict[str, Any]] = []
    for row in rows:
        index = date_index.get(row["date"])
        if index is None:
            continue
        path = []
        for horizon, future_date in enumerate(trading_dates[index + 1:index + 6], start=1):
            price = prices.get((future_date, row["ticker"]))
            if price and all(price.get(key) is not None for key in ("open", "high", "low", "close")):
                path.append({"horizon": horizon, "date": future_date, **price})
            else:
                break
        if path:
            output.append({**row, "price_path": path})
    return output


def simulate_tp_sl(
    row: dict[str, Any],
    tp: float,
    sl: float,
    max_hold_days: int,
    touch_policy: str = "sl_first",
) -> dict[str, Any] | None:
    entry = row.get("entry_close")
    path = row.get("price_path", [])[:max_hold_days]
    if not entry or not path:
        return None
    for day in path:
        open_return = day["open"] / entry - 1
        if open_return >= tp:
            return {"gross_return": open_return, "holding_days": day["horizon"], "exit_reason": "gap_tp"}
        if open_return <= -sl:
            return {"gross_return": open_return, "holding_days": day["horizon"], "exit_reason": "gap_sl"}
        tp_hit = day["high"] / entry - 1 >= tp
        sl_hit = day["low"] / entry - 1 <= -sl
        if tp_hit and sl_hit:
            reason = "sl" if touch_policy == "sl_first" else "tp"
            value = -sl if reason == "sl" else tp
            return {"gross_return": value, "holding_days": day["horizon"], "exit_reason": f"both_{reason}"}
        if tp_hit:
            return {"gross_return": tp, "holding_days": day["horizon"], "exit_reason": "tp"}
        if sl_hit:
            return {"gross_return": -sl, "holding_days": day["horizon"], "exit_reason": "sl"}
    final = path[-1]
    return {
        "gross_return": final["close"] / entry - 1,
        "holding_days": final["horizon"],
        "exit_reason": "forced_close",
    }


def simulate_fixed_close(row: dict[str, Any], hold_days: int) -> dict[str, Any] | None:
    entry = row.get("entry_close")
    path = row.get("price_path", [])
    if not entry or len(path) < hold_days:
        return None
    final = path[hold_days - 1]
    return {
        "gross_return": final["close"] / entry - 1,
        "holding_days": hold_days,
        "exit_reason": "fixed_close",
    }


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _max_drawdown(outcomes: list[dict[str, Any]], cost_rate: float) -> float | None:
    if not outcomes:
        return None
    by_date: dict[str, list[float]] = defaultdict(list)
    for outcome in outcomes:
        by_date[outcome["entry_date"]].append(outcome["gross_return"] - cost_rate)
    equity = peak = 1.0
    max_drawdown = 0.0
    for date in sorted(by_date):
        equity *= 1 + statistics.fmean(by_date[date])
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)
    return round(max_drawdown, 6)


def summarize_outcomes(outcomes: list[dict[str, Any]], cost_rate: float) -> dict[str, Any]:
    returns = [outcome["gross_return"] - cost_rate for outcome in outcomes]
    if not returns:
        return {
            "count": 0, "mean": None, "median": None, "positive_rate": None,
            "p10": None, "worst": None, "max_drawdown": None, "avg_holding_days": None,
        }
    return {
        "count": len(returns),
        "mean": round(statistics.fmean(returns), 6),
        "median": round(statistics.median(returns), 6),
        "positive_rate": round(sum(value > 0 for value in returns) / len(returns), 4),
        "p10": round(_quantile(returns, 0.1) or 0.0, 6),
        "worst": round(min(returns), 6),
        "max_drawdown": _max_drawdown(outcomes, cost_rate),
        "avg_holding_days": round(statistics.fmean(outcome["holding_days"] for outcome in outcomes), 4),
    }


def _run_strategy(rows: list[dict[str, Any]], strategy: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes = []
    for row in rows:
        if strategy["kind"] == "fixed_close":
            outcome = simulate_fixed_close(row, strategy["max_hold_days"])
        else:
            outcome = simulate_tp_sl(
                row,
                strategy["tp"],
                strategy["sl"],
                strategy["max_hold_days"],
                strategy.get("touch_policy", "sl_first"),
            )
        if outcome:
            outcomes.append({**outcome, "entry_date": row["date"], "ticker": row["ticker"]})
    return outcomes


def strategy_id(strategy: dict[str, Any]) -> str:
    if strategy["kind"] == "fixed_close":
        return f"fixed_d{strategy['max_hold_days']}_close"
    return (
        f"tp{int(strategy['tp'] * 100)}_sl{int(strategy['sl'] * 100)}_"
        f"d{strategy['max_hold_days']}_{strategy.get('touch_policy', 'sl_first')}"
    )


def candidate_strategies() -> list[dict[str, Any]]:
    fixed = [{"kind": "fixed_close", "max_hold_days": day} for day in MAX_HOLD_DAYS]
    threshold = [
        {"kind": "tp_sl", "tp": tp, "sl": sl, "max_hold_days": day, "touch_policy": "sl_first"}
        for tp, sl, day in itertools.product(TP_VALUES, SL_VALUES, MAX_HOLD_DAYS)
    ]
    return fixed + threshold


def _no_worse_risk(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    return (
        candidate["p10"] is not None
        and baseline["p10"] is not None
        and candidate["max_drawdown"] is not None
        and baseline["max_drawdown"] is not None
        and candidate["p10"] >= baseline["p10"]
        and candidate["max_drawdown"] >= baseline["max_drawdown"]
    )


def analyze_strategies(rows: list[dict[str, Any]], cost_rate: float = 0.01) -> dict[str, Any]:
    cost_rates = tuple(sorted(set((*COST_RATES, cost_rate))))
    dates = sorted({row["date"] for row in rows})
    split_date = dates[len(dates) // 2]
    early = [row for row in rows if row["date"] < split_date]
    late = [row for row in rows if row["date"] >= split_date]
    baseline = {"kind": "tp_sl", "tp": 0.05, "sl": 0.03, "max_hold_days": 1, "touch_policy": "sl_first"}
    baseline_early = summarize_outcomes(_run_strategy(early, baseline), cost_rate)
    baseline_late = summarize_outcomes(_run_strategy(late, baseline), cost_rate)

    comparisons = []
    for strategy in candidate_strategies():
        early_metrics = summarize_outcomes(_run_strategy(early, strategy), cost_rate)
        late_metrics = summarize_outcomes(_run_strategy(late, strategy), cost_rate)
        comparisons.append({
            "id": strategy_id(strategy),
            "strategy": strategy,
            "early": early_metrics,
            "late": late_metrics,
            "eligible_on_early": (
                early_metrics["mean"] is not None
                and baseline_early["mean"] is not None
                and early_metrics["mean"] > baseline_early["mean"]
                and _no_worse_risk(early_metrics, baseline_early)
            ),
        })
    eligible = [item for item in comparisons if item["eligible_on_early"]]
    selected = max(eligible, key=lambda item: item["early"]["mean"]) if eligible else None
    validated = bool(
        selected
        and selected["late"]["count"] >= 20
        and selected["late"]["mean"] > 0
        and selected["late"]["mean"] > baseline_late["mean"]
        and _no_worse_risk(selected["late"], baseline_late)
    )

    training_rows = early
    medians = {
        feature: statistics.median(row[feature] for row in training_rows if row.get(feature) is not None)
        for feature in ("entry_volume", "entry_close", "market_cap", "trading_value")
    }
    segments = {
        "all": rows,
        "high_entry_volume": [row for row in rows if row.get("entry_volume") is not None and row["entry_volume"] >= medians["entry_volume"]],
        "phase3_compound": [
            row for row in rows
            if row.get("entry_volume") is not None and row["entry_volume"] >= medians["entry_volume"]
            and row.get("entry_close") is not None and row["entry_close"] <= medians["entry_close"]
            and row.get("market_cap") is not None and row["market_cap"] <= medians["market_cap"]
            and row.get("trading_value") is not None and row["trading_value"] <= medians["trading_value"]
        ],
    }
    chosen_strategy = selected["strategy"] if selected else baseline
    segment_results = {}
    for name, segment_rows in segments.items():
        segment_early = [row for row in segment_rows if row["date"] < split_date]
        segment_late = [row for row in segment_rows if row["date"] >= split_date]
        segment_results[name] = {
            "count": len(segment_rows),
            "early_count": len(segment_early),
            "late_count": len(segment_late),
            "baseline": {
                str(cost): {
                    "all": summarize_outcomes(_run_strategy(segment_rows, baseline), cost),
                    "early": summarize_outcomes(_run_strategy(segment_early, baseline), cost),
                    "late": summarize_outcomes(_run_strategy(segment_late, baseline), cost),
                }
                for cost in cost_rates
            },
            "selected": {
                str(cost): {
                    "all": summarize_outcomes(_run_strategy(segment_rows, chosen_strategy), cost),
                    "early": summarize_outcomes(_run_strategy(segment_early, chosen_strategy), cost),
                    "late": summarize_outcomes(_run_strategy(segment_late, chosen_strategy), cost),
                }
                for cost in cost_rates
            },
        }

    validated_segments = []
    for name, segment in segment_results.items():
        base = segment["baseline"][str(cost_rate)]["late"]
        chosen = segment["selected"][str(cost_rate)]["late"]
        if (
            chosen["count"] >= 20
            and chosen["mean"] > 0
            and chosen["mean"] > base["mean"]
            and _no_worse_risk(chosen, base)
        ):
            validated_segments.append(name)

    optimistic_baseline = {**baseline, "touch_policy": "tp_first"}
    optimistic_selected = {**chosen_strategy, "touch_policy": "tp_first"} if chosen_strategy["kind"] == "tp_sl" else chosen_strategy
    return {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": len(rows),
        "split_date": split_date,
        "selection_cost_rate": cost_rate,
        "baseline": {"id": strategy_id(baseline), "strategy": baseline, "early": baseline_early, "late": baseline_late},
        "selected": selected,
        "validated_on_late": validated,
        "decision": "candidate_validated" if validated else "no_validated_improvement",
        "segment_thresholds_from_early": medians,
        "segments": segment_results,
        "validated_segments": validated_segments,
        "touch_policy_sensitivity": {
            "baseline_sl_first": summarize_outcomes(_run_strategy(rows, baseline), cost_rate),
            "baseline_tp_first": summarize_outcomes(_run_strategy(rows, optimistic_baseline), cost_rate),
            "selected_sl_first": summarize_outcomes(_run_strategy(rows, chosen_strategy), cost_rate),
            "selected_tp_first": summarize_outcomes(_run_strategy(rows, optimistic_selected), cost_rate),
        },
        "comparisons": comparisons,
        "guardrails": [
            "D+1 강제청산 가격은 15:19 가격 대신 일봉 종가를 사용한 근사치",
            "같은 날 TP·SL 동시 도달은 기본 SL 우선, TP 우선은 민감도 결과",
            "비용 0.5%·1.0%는 확정 수수료가 아니라 비용 민감도",
            "전반부 선택 후 후반부에서 수익과 위험이 모두 개선돼야 검증 통과",
        ],
    }


def render_markdown(result: dict[str, Any]) -> str:
    selected = result["selected"]
    lines = [
        "# Watchlist 기대수익 연구 — 4단계 보유·청산 전략 비교",
        "",
        f"- 표본: {result['sample_count']}건",
        f"- 분할일: {result['split_date']}",
        f"- 비용 가정: {result['selection_cost_rate']}",
        f"- 기준 전략: {result['baseline']['id']}",
        f"- 전반부 선택 전략: {selected['id'] if selected else None}",
        f"- 후반부 검증 통과: {result['validated_on_late']}",
        f"- 결론: {result['decision']}",
        "",
        "## 전반부·후반부 비교",
        "",
        "| 전략 | 구간 | 표본 | 평균 | 중앙값 | 승률 | p10 | MDD | 평균 보유일 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, data in (("baseline", result["baseline"]), ("selected", selected)):
        if not data:
            continue
        for period in ("early", "late"):
            value = data[period]
            lines.append(
                f"| {label}:{data['id']} | {period} | {value['count']} | {value['mean']} | "
                f"{value['median']} | {value['positive_rate']} | {value['p10']} | "
                f"{value['max_drawdown']} | {value['avg_holding_days']} |"
            )
    lines.extend(["", "## 특징군 후반부 결과 — 비용 1%", "", "| 구간 | 후반부 표본 | 기준 평균 | 선택 평균 | 기준 p10 | 선택 p10 |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for name, segment in result["segments"].items():
        baseline = segment["baseline"]["0.01"]["late"]
        chosen = segment["selected"]["0.01"]["late"]
        lines.append(f"| {name} | {segment['late_count']} | {baseline['mean']} | {chosen['mean']} | {baseline['p10']} | {chosen['p10']} |")
    lines.extend(["", f"- 후반부 검증을 통과한 특징군: {result['validated_segments']}"])
    lines.extend(["", "## 해석 제한", ""])
    lines.extend(f"- {item}" for item in result["guardrails"])
    return "\n".join(lines) + "\n"


def write_results(result: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "phase4_holding_strategy.json"
    md_path = output_dir / "phase4_holding_strategy.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="watchlist 보유기간 및 TP/SL 전략 비교")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--selection-cost-rate", type=float, default=0.01)
    args = parser.parse_args(argv)
    rows = load_price_paths(args.watchlist_db, args.krx_db)
    result = analyze_strategies(rows, args.selection_cost_rate)
    for path in write_results(result, args.output_dir):
        print(f"[phase4] {path}")


if __name__ == "__main__":
    main()
