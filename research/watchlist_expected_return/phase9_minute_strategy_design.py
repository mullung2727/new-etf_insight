"""9단계: 전반부에서만 견고한 분봉 lower-low 전략을 설계하고 후반부 검증."""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from research.watchlist_expected_return.minute_bar_cache import DEFAULT_CACHE_DIR
from research.watchlist_expected_return.phase4_holding_strategy import summarize_outcomes
from research.watchlist_expected_return.phase7_pullback_strategy import (
    DEFAULT_KRX_DB, DEFAULT_WATCHLIST_DB, load_research_rows,
)
from research.watchlist_expected_return.phase8_minute_pullback_strategy import (
    DEFAULT_OUTPUT_DIR, ENTRY_RULES, find_minute_entry, load_minute_samples,
    simulate_minute_exit,
)


FILTERS = ("all", "after_1000", "rebound_2pct", "volume_surge_1_5x", "shallow_break_3pct", "quality_combo")


def entry_features(sample: dict[str, Any], entry: dict[str, Any]) -> dict[str, float | str]:
    day = [bar for bar in sample["bars"] if bar["date"] == sample["entry_date"] and bar["timestamp"] <= entry["entry_timestamp"]]
    current = day[-1]
    prior_volumes = [bar["volume"] for bar in day[-6:-1] if bar["volume"] is not None]
    median_volume = statistics.median(prior_volumes) if prior_volumes else 0
    running_low = min(bar["low"] for bar in day)
    return {
        "entry_time": current["time"],
        "rebound_from_low": current["close"] / running_low - 1,
        "lower_low_depth": running_low / sample["prior_low"] - 1,
        "volume_ratio_5": current["volume"] / median_volume if median_volume else 0.0,
    }


def passes_filter(features: dict[str, Any], name: str) -> bool:
    if name == "all":
        return True
    if name == "after_1000":
        return features["entry_time"] >= "100000"
    if name == "rebound_2pct":
        return features["rebound_from_low"] >= 0.02
    if name == "volume_surge_1_5x":
        return features["volume_ratio_5"] >= 1.5
    if name == "shallow_break_3pct":
        return features["lower_low_depth"] >= -0.03
    if name == "quality_combo":
        return (
            features["entry_time"] >= "100000"
            and features["rebound_from_low"] >= 0.02
            and features["lower_low_depth"] >= -0.03
        )
    raise ValueError(name)


def exits() -> list[dict[str, Any]]:
    fixed = [{"kind": "fixed_close", "days": 1}]
    thresholds = [
        {"kind": "tp_sl", "tp": tp, "sl": sl, "days": days}
        for tp, sl, days in itertools.product((0.03, 0.05), (0.02, 0.03), (1, 3, 5))
    ]
    return fixed + thresholds


def candidate_id(rule: str, filter_name: str, exit_rule: dict[str, Any]) -> str:
    if exit_rule["kind"] == "fixed_close":
        suffix = "fixed_d1"
    else:
        suffix = f"tp{exit_rule['tp']:.0%}_sl{exit_rule['sl']:.0%}_d{exit_rule['days']}"
    return f"{rule}__{filter_name}__{suffix}"


def build_outcomes(
    samples: list[dict[str, Any]], rule: str, filter_name: str, exit_rule: dict[str, Any]
) -> list[dict[str, Any]]:
    outcomes = []
    for sample in samples:
        entry = find_minute_entry(sample["bars"], sample["entry_date"], sample["prior_low"], rule)
        if not entry or not passes_filter(entry_features(sample, entry), filter_name):
            continue
        outcome = simulate_minute_exit(sample["bars"], entry, sample["trading_dates"], exit_rule)
        if outcome:
            outcomes.append({**outcome, "entry_date": sample["entry_date"], "ticker": sample["ticker"], "holding_days": exit_rule["days"]})
    return outcomes


def eligible(metrics: dict[str, Any]) -> bool:
    return bool(
        metrics["count"] >= 10 and metrics["mean"] > 0 and metrics["median"] > 0
        and metrics["p10"] is not None and metrics["p10"] >= -0.05
    )


def analyze_design(samples: list[dict[str, Any]], stats: dict[str, int], cost_rate: float = 0.01) -> dict[str, Any]:
    dates = sorted({sample["entry_date"] for sample in samples})
    split_date = dates[len(dates) // 2]
    comparisons = []
    for rule, filter_name, exit_rule in itertools.product(ENTRY_RULES, FILTERS, exits()):
        outcomes = build_outcomes(samples, rule, filter_name, exit_rule)
        early = summarize_outcomes([item for item in outcomes if item["entry_date"] < split_date], cost_rate)
        late = summarize_outcomes([item for item in outcomes if item["entry_date"] >= split_date], cost_rate)
        comparisons.append({
            "id": candidate_id(rule, filter_name, exit_rule), "entry_rule": rule,
            "filter": filter_name, "exit": exit_rule, "early": early, "late": late,
            "eligible_on_early": eligible(early),
        })
    candidates = [item for item in comparisons if item["eligible_on_early"]]
    selected = max(candidates, key=lambda item: (item["early"]["mean"], item["early"]["median"])) if candidates else None
    late = selected["late"] if selected else None
    validated = bool(late and late["count"] >= 10 and late["mean"] > 0 and late["median"] > 0 and late["p10"] >= -0.05)
    selected_outcomes = build_outcomes(samples, selected["entry_rule"], selected["filter"], selected["exit"]) if selected else []
    return {
        "analysis_version": 1, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": len(samples), "data_stats": stats, "split_date": split_date,
        "selection_rule": "early count>=10, mean>0, median>0, p10>=-5%; then max early mean",
        "cost_rate": cost_rate, "candidate_count": len(comparisons),
        "early_eligible_count": len(candidates), "selected": selected,
        "selected_no_buy_rate": round(1 - len(selected_outcomes) / len(samples), 4) if selected else None,
        "selected_cost_sensitivity": {
            str(cost): summarize_outcomes(selected_outcomes, cost) for cost in (0.0, 0.005, 0.01)
        } if selected else {},
        "selected_exit_reasons": dict(sorted(Counter(item["exit_reason"] for item in selected_outcomes).items())) if selected else {},
        "validated_on_late": validated,
        "decision": "candidate_validated" if validated else "no_validated_strategy",
        "top_early_eligible": sorted(candidates, key=lambda item: item["early"]["mean"], reverse=True)[:10],
        "filter_definitions": {
            "all": "추가 필터 없음", "after_1000": "10시 이후 신호만 진입",
            "rebound_2pct": "당일 누적 저점 대비 2% 이상 반등",
            "volume_surge_1_5x": "신호 분봉 거래량이 직전 5개 중앙값의 1.5배 이상",
            "shallow_break_3pct": "전일 저가 이탈 폭이 3% 이내",
            "quality_combo": "10시 이후 + 저점 2% 반등 + 전일 저가 이탈 폭 3% 이내",
        },
        "guardrails": ["후반부 수치는 전략 선택에 사용하지 않음", "왕복 비용 1% 차감", "같은 1분봉 TP·SL 동시 도달은 SL 우선"],
    }


def render_markdown(result: dict[str, Any]) -> str:
    chosen = result["selected"]
    lines = ["# Watchlist 눌림목 연구 — 9단계 분봉 전략 재설계", "",
             f"- 표본: {result['sample_count']}건", f"- 분할일: {result['split_date']}",
             f"- 비교 조합: {result['candidate_count']}개", f"- 전반부 통과: {result['early_eligible_count']}개",
             f"- 선택 전략: {chosen['id'] if chosen else None}", f"- 후반부 검증: {result['validated_on_late']}",
             f"- 선택 전략 미매수율: {result['selected_no_buy_rate']}",
             f"- 결론: {result['decision']}", ""]
    if chosen:
        lines.extend(["## 선택 전략 성과", "", "| 구간 | 표본 | 평균 | 중앙값 | 승률 | p10 | 최악 | MDD |",
                      "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
        for period in ("early", "late"):
            value = chosen[period]
            lines.append(f"| {period} | {value['count']} | {value['mean']} | {value['median']} | {value['positive_rate']} | {value['p10']} | {value['worst']} | {value['max_drawdown']} |")
    lines.extend(["", "## 비용 민감도 — 선택 전략 전체 표본", "", "| 비용 | 표본 | 평균 | 중앙값 | 승률 |",
                  "| ---: | ---: | ---: | ---: | ---: |"])
    for cost, value in result["selected_cost_sensitivity"].items():
        lines.append(f"| {float(cost):.1%} | {value['count']} | {value['mean']} | {value['median']} | {value['positive_rate']} |")
    lines.extend(["", f"- 청산 사유: `{result['selected_exit_reasons']}`"])
    lines.extend(["", "## 전반부 기준 통과 후보 상위 10개", "", "| 전략 | 전반 표본 | 전반 평균 | 전반 중앙값 | 전반 p10 | 후반 평균 |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for item in result["top_early_eligible"]:
        lines.append(f"| {item['id']} | {item['early']['count']} | {item['early']['mean']} | {item['early']['median']} | {item['early']['p10']} | {item['late']['mean']} |")
    lines.extend(["", "## 필터 정의", ""])
    lines.extend(f"- `{key}`: {value}" for key, value in result["filter_definitions"].items())
    lines.extend(["", "## 검증 원칙", ""])
    lines.extend(f"- {item}" for item in result["guardrails"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="분봉 lower-low 전략 재설계")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    samples, stats = load_minute_samples(load_research_rows(args.watchlist_db, args.krx_db), args.cache_dir)
    result = analyze_design(samples, stats)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "phase9_minute_strategy_design.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "phase9_minute_strategy_design.md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"decision": result["decision"], "selected": result["selected"]["id"] if result["selected"] else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
