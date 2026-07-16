"""신규 장중 진입 3개와 기존 종가확인 전략을 같은 청산 규칙으로 비교한다."""
from __future__ import annotations

import argparse
import itertools
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from research.watchlist_expected_return.five_minute_high_breakout import find_entry as five_minute_high_breakout
from research.watchlist_expected_return.minute_bar_cache import DEFAULT_CACHE_DIR
from research.watchlist_expected_return.phase4_holding_strategy import summarize_outcomes
from research.watchlist_expected_return.phase7_pullback_strategy import DEFAULT_KRX_DB, DEFAULT_WATCHLIST_DB, load_research_rows
from research.watchlist_expected_return.phase8_minute_pullback_strategy import (
    DEFAULT_OUTPUT_DIR,
    load_minute_payloads,
    regular_bars,
    simulate_minute_exit,
)
from research.watchlist_expected_return.prior_low_reclaim import find_entry as prior_low_reclaim
from research.watchlist_expected_return.vwap_reclaim import find_entry as vwap_reclaim


EntryFinder = Callable[[list[dict[str, Any]], float], dict[str, Any] | None]


def exit_candidates() -> list[dict[str, Any]]:
    return [
        {"kind": "tp_sl", "tp": tp, "sl": sl, "days": days}
        for tp, sl, days in itertools.product(
            (0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10),
            (0.03, 0.04, 0.05),
            (1, 2, 3, 5),
        )
        if tp > sl
    ]


def exit_id(strategy: dict[str, Any]) -> str:
    return f"tp{strategy['tp']:.0%}_sl{strategy['sl']:.0%}_d{strategy['days']}"


def close_confirm(day_bars: list[dict[str, Any]], prior_low: float) -> dict[str, Any] | None:
    bars_to_signal = [bar for bar in day_bars if bar["time"] <= "151900"]
    signal_bar = next((bar for bar in bars_to_signal if bar["time"] == "151900"), None)
    if not signal_bar or min(bar["low"] for bar in bars_to_signal) >= prior_low:
        return None
    if signal_bar["close"] <= bars_to_signal[0]["open"]:
        return None
    return {"entry_price": signal_bar["close"], "entry_timestamp": signal_bar["timestamp"]}


ENTRY_FINDERS: dict[str, EntryFinder] = {
    "prior_low_reclaim": prior_low_reclaim,
    "five_minute_high_breakout": five_minute_high_breakout,
    "vwap_reclaim": vwap_reclaim,
    "close_confirm": close_confirm,
}


def find_production_signal(
    bars: list[dict[str, Any]], dates: list[str], initial_prior_low: float
) -> tuple[int, str, float, dict[str, Any]] | None:
    prior_low = initial_prior_low
    for index, date in enumerate(dates[:5]):
        day = [bar for bar in bars if bar["date"] == date]
        entry = close_confirm(day, prior_low) if day and prior_low else None
        if entry:
            return index, date, prior_low, entry
        if day:
            prior_low = min(bar["low"] for bar in day)
    return None


def load_production_samples(
    rows: list[dict[str, Any]], cache_dir: Path
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """운영과 같이 D+1~D+5의 15:19 lower-low 양봉 신호를 순서대로 찾는다."""
    candidates = []
    for row in rows:
        dates = [day["date"] for day in row["future"][:10]]
        if len(dates) == 10:
            candidates.append((row, dates))
    payloads = load_minute_payloads(
        [(row["ticker"], dates[-1], dates[0]) for row, dates in candidates],
        cache_dir,
    )
    samples = []
    for row, dates in candidates:
        payload = payloads[(row["ticker"], dates[-1])]
        if not payload["complete"]:
            continue
        bars = regular_bars(payload, dates)
        signal = find_production_signal(bars, dates, row["history"][-1]["low"])
        if signal:
            index, date, prior_low, entry = signal
            trading_dates = dates[index:index + 6]
            if len(trading_dates) == 6:
                samples.append({
                    "watchlist_date": row["date"], "ticker": row["ticker"],
                    "entry_date": date, "trading_dates": trading_dates,
                    "prior_low": prior_low, "bars": regular_bars(payload, trading_dates),
                    "production_entry": entry,
                })
    stats = {
        "eligible_rows": len(candidates),
        "cache_complete": sum(
            bool(payloads[(row["ticker"], dates[-1])]["complete"])
            for row, dates in candidates
        ),
        "production_signals": len(samples),
    }
    return samples, stats


def analyze(samples: list[dict[str, Any]], stats: dict[str, int], cost_rate: float = 0.01) -> dict[str, Any]:
    dates = sorted({sample["entry_date"] for sample in samples})
    if len(dates) < 2:
        raise ValueError("시간 분할에 필요한 거래일 표본이 부족함")
    split_date = dates[len(dates) // 2]
    strategies = []
    for name, finder in ENTRY_FINDERS.items():
        entries = []
        for sample in samples:
            day = [bar for bar in sample["bars"] if bar["date"] == sample["entry_date"]]
            entry = finder(day, sample["prior_low"])
            if entry:
                entries.append((sample, entry))
        comparisons = []
        for exit_rule in exit_candidates():
            outcomes = []
            for sample, entry in entries:
                outcome = simulate_minute_exit(sample["bars"], entry, sample["trading_dates"], exit_rule)
                if outcome:
                    outcomes.append({
                        **outcome,
                        "entry_date": sample["entry_date"],
                        "ticker": sample["ticker"],
                        "holding_days": exit_rule["days"],
                    })
            comparisons.append({
                "id": exit_id(exit_rule),
                "exit_rule": exit_rule,
                "early": summarize_outcomes([o for o in outcomes if o["entry_date"] < split_date], cost_rate),
                "late": summarize_outcomes([o for o in outcomes if o["entry_date"] >= split_date], cost_rate),
                "total": summarize_outcomes(outcomes, cost_rate),
            })
        selectable = [item for item in comparisons if item["total"]["count"] >= 10]
        selected = max(selectable, key=lambda item: item["total"]["mean"]) if selectable else None
        strategies.append({
            "strategy": name,
            "entry_count": len(entries),
            "no_buy_rate": round(1 - len(entries) / len(samples), 4),
            "selected_on_total": selected,
            "comparisons": comparisons,
        })
    selected = [item for item in strategies if item["selected_on_total"]]
    best = max(selected, key=lambda item: item["selected_on_total"]["total"]["mean"]) if selected else None
    return {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": len(samples), "data_stats": stats, "split_date": split_date,
        "cost_rate": cost_rate, "exit_candidate_count": len(exit_candidates()),
        "best_on_total": best["strategy"] if best else None, "strategies": strategies,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# 1분봉 눌림목 진입 전략 4종 비교", "",
        f"- 전체 표본: {result['sample_count']}건", f"- 시간 분할일: {result['split_date']}",
        f"- 왕복 비용: {result['cost_rate']:.1%}",
        f"- 청산 조합: {result['exit_candidate_count']}개 (TP>SL)",
        f"- 전체 표본 최우수 진입: {result['best_on_total']}", "",
        "| 전략 | 매수 | 미매수율 | 전체 선택 청산 | 전체 표본 | 전체 평균 | 전체 중앙값 | 전체 승률 | 전체 MDD |",
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in result["strategies"]:
        chosen = item["selected_on_total"]
        total = chosen["total"] if chosen else {}
        lines.append(
            f"| {item['strategy']} | {item['entry_count']} | {item['no_buy_rate']:.2%} | "
            f"{chosen['id'] if chosen else None} | {total.get('count')} | {total.get('mean')} | "
            f"{total.get('median')} | {total.get('positive_rate')} | {total.get('max_drawdown')} |"
        )
    lines.extend(["", "## 해석 주의", "", "- 전체 표본 평균으로 청산 조합을 선택한 탐색 결과다.",
                  "- 전후반 분리 검증이 아니므로 최종 전략 확정에는 별도 시간 외 검증이 필요하다.",
                  "- 같은 1분봉에서 TP와 SL이 모두 닿으면 SL을 우선한다."])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="1분봉 눌림목 진입 전략 4종 비교")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    samples, stats = load_production_samples(
        load_research_rows(args.watchlist_db, args.krx_db), args.cache_dir
    )
    result = analyze(samples, stats)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "phase10_intraday_entry_comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "phase10_intraday_entry_comparison.md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"sample_count": result["sample_count"], "strategies": len(result["strategies"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
