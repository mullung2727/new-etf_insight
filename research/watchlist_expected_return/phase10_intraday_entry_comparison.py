"""신규 장중 진입 3개와 기존 종가확인 전략을 같은 청산 규칙으로 비교한다."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from research.watchlist_expected_return.five_minute_high_breakout import find_entry as five_minute_high_breakout
from research.watchlist_expected_return.minute_bar_cache import DEFAULT_CACHE_DIR
from research.watchlist_expected_return.phase4_holding_strategy import summarize_outcomes
from research.watchlist_expected_return.phase7_pullback_strategy import DEFAULT_KRX_DB, DEFAULT_WATCHLIST_DB, load_research_rows
from research.watchlist_expected_return.phase8_minute_pullback_strategy import DEFAULT_OUTPUT_DIR, find_minute_entry, load_minute_samples, simulate_minute_exit
from research.watchlist_expected_return.prior_low_reclaim import find_entry as prior_low_reclaim
from research.watchlist_expected_return.vwap_reclaim import find_entry as vwap_reclaim


EntryFinder = Callable[[list[dict[str, Any]], float], dict[str, Any] | None]
EXIT_RULE = {"kind": "tp_sl", "tp": 0.03, "sl": 0.03, "days": 3}


def close_confirm(day_bars: list[dict[str, Any]], prior_low: float) -> dict[str, Any] | None:
    entry_date = day_bars[0]["date"] if day_bars else ""
    return find_minute_entry(day_bars, entry_date, prior_low, "close_confirm")


ENTRY_FINDERS: dict[str, EntryFinder] = {
    "prior_low_reclaim": prior_low_reclaim,
    "five_minute_high_breakout": five_minute_high_breakout,
    "vwap_reclaim": vwap_reclaim,
    "close_confirm": close_confirm,
}


def analyze(samples: list[dict[str, Any]], stats: dict[str, int], cost_rate: float = 0.01) -> dict[str, Any]:
    dates = sorted({sample["entry_date"] for sample in samples})
    if len(dates) < 2:
        raise ValueError("시간 분할에 필요한 거래일 표본이 부족함")
    split_date = dates[len(dates) // 2]
    strategies = []
    for name, finder in ENTRY_FINDERS.items():
        outcomes = []
        for sample in samples:
            day = [bar for bar in sample["bars"] if bar["date"] == sample["entry_date"]]
            entry = finder(day, sample["prior_low"])
            if not entry:
                continue
            outcome = simulate_minute_exit(sample["bars"], entry, sample["trading_dates"], EXIT_RULE)
            if outcome:
                outcomes.append({
                    **outcome,
                    "entry_date": sample["entry_date"],
                    "ticker": sample["ticker"],
                    "holding_days": EXIT_RULE["days"],
                })
        strategies.append({
            "strategy": name,
            "entry_count": len(outcomes),
            "no_buy_rate": round(1 - len(outcomes) / len(samples), 4),
            "early": summarize_outcomes([o for o in outcomes if o["entry_date"] < split_date], cost_rate),
            "late": summarize_outcomes([o for o in outcomes if o["entry_date"] >= split_date], cost_rate),
            "total": summarize_outcomes(outcomes, cost_rate),
        })
    return {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": len(samples), "data_stats": stats, "split_date": split_date,
        "cost_rate": cost_rate, "exit_rule": EXIT_RULE, "strategies": strategies,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# 1분봉 눌림목 진입 전략 4종 비교", "",
        f"- 전체 표본: {result['sample_count']}건", f"- 시간 분할일: {result['split_date']}",
        f"- 왕복 비용: {result['cost_rate']:.1%}", "- 공통 청산: TP +3%, SL -3%, 최대 3거래일", "",
        "| 전략 | 매수 | 미매수율 | 전반 평균 | 후반 표본 | 후반 평균 | 후반 중앙값 | 후반 승률 | 전체 평균 | 전체 MDD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in result["strategies"]:
        lines.append(
            f"| {item['strategy']} | {item['entry_count']} | {item['no_buy_rate']:.2%} | "
            f"{item['early']['mean']:.4f} | {item['late']['count']} | {item['late']['mean']:.4f} | "
            f"{item['late']['median']:.4f} | {item['late']['positive_rate']:.2%} | "
            f"{item['total']['mean']:.4f} | {item['total']['max_drawdown']:.4f} |"
        )
    lines.extend(["", "## 해석 주의", "", "- 진입 규칙만 다르고 청산과 비용 조건은 모두 동일하다.",
                  "- 전반부는 참고 구간, 후반부는 시간 외 검증 구간이다.",
                  "- 같은 1분봉에서 TP와 SL이 모두 닿으면 SL을 우선한다."])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="1분봉 눌림목 진입 전략 4종 비교")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    samples, stats = load_minute_samples(load_research_rows(args.watchlist_db, args.krx_db), args.cache_dir)
    result = analyze(samples, stats)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "phase10_intraday_entry_comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "phase10_intraday_entry_comparison.md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"sample_count": result["sample_count"], "strategies": len(result["strategies"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
