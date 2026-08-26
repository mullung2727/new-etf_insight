"""VFS 신호 다음 거래일(D+2) 장중 조건부 진입 스윕.

신호일 종가 매수(backtest.py)가 60조합 전부 음수였던 데 대한 대안 실험.
진입을 신호 다음날 장중으로 미루고, 진입 조건 4종 × TP/SL/보유일을 비교한다.

진입 규칙 (전부 D+2 정규장 1분봉만 사용, 미래 봉 미사용):
  prior_low_reclaim        신호일 저가 이탈 후 회복하는 첫 1분봉 종가   (기존 모듈 재사용)
  five_minute_high_breakout 이탈 후 직전 5분봉 고가 돌파 첫 종가        (기존 모듈 재사용)
  vwap_reclaim             이탈 후 당일 누적 VWAP 재돌파 첫 종가        (기존 모듈 재사용)
  opening_range_breakout   09:00~09:30 고가 돌파 첫 종가 (이탈 게이트 없음)

기준선(prior_low 인자)은 신호일 저가로 고정한다.
진입 신호가 없는 날은 매수하지 않으므로 규칙마다 표본 수가 다르다 — 그 자체가 비교 대상.

분봉은 minute_bar_store 의 날짜 단위 DB에서 읽으므로 보유일수를 바꿔도
이미 받아둔 날짜는 재조회하지 않는다.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.vfs_strategy.intraday --from 20260303 --to 20260824
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from research.vfs_strategy.backtest import (
    COST_RATE,
    DEFAULT_KRX_DB,
    DEFAULT_MINUTE_DB,
    DEFAULT_OUTPUT_DIR,
    _pct,
    load_candidates,
    load_sample_bars,
)
from research.watchlist_expected_return.five_minute_high_breakout import find_entry as five_minute_high_breakout
from research.watchlist_expected_return.phase4_holding_strategy import summarize_outcomes
from research.watchlist_expected_return.phase8_minute_pullback_strategy import simulate_minute_exit
from research.watchlist_expected_return.prior_low_reclaim import find_entry as prior_low_reclaim
from research.watchlist_expected_return.vwap_reclaim import find_entry as vwap_reclaim

EntryFinder = Callable[[list[dict[str, Any]], float], dict[str, Any] | None]

OPENING_RANGE_END = "093000"
SWEEP_TP = (0.03, 0.04)
SWEEP_SL = (0.02, 0.03)
SWEEP_DAYS = (1, 2)            # 3일은 분봉 캐시 키가 밀려 재조회 필요 → 제외
CANDIDATE_HORIZON = 3          # 신호일 + D+2 + D+3 + D+4 → load_candidates(days=3) 과 동일 키


def opening_range_breakout(day_bars: list[dict[str, Any]], prior_low: float) -> dict[str, Any] | None:
    """09:00~09:30 고가를 처음 돌파하는 1분봉 종가. 저가 이탈 게이트 없음(재상승 확인형)."""
    opening = [bar for bar in day_bars if bar["time"] <= OPENING_RANGE_END]
    if not opening:
        return None
    range_high = max(bar["high"] for bar in opening)
    for bar in day_bars:
        if bar["time"] > OPENING_RANGE_END and bar["close"] > range_high:
            return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
    return None


ENTRY_RULES: dict[str, EntryFinder] = {
    "prior_low_reclaim": prior_low_reclaim,
    "five_minute_high_breakout": five_minute_high_breakout,
    "vwap_reclaim": vwap_reclaim,
    "opening_range_breakout": opening_range_breakout,
}


def find_entries(samples: list[dict[str, Any]], rule: EntryFinder) -> list[dict[str, Any]]:
    """표본별 D+2 장중 진입 시점을 찾는다. 신호 없으면 그 표본은 매수하지 않는다."""
    found = []
    for sample in samples:
        if sample["signal_low"] is None:
            continue
        entry_date = sample["trading_dates"][1]
        day_bars = [bar for bar in sample["bars"] if bar["date"] == entry_date]
        if not day_bars:
            continue
        entry = rule(day_bars, float(sample["signal_low"]))
        if entry:
            found.append({**sample, "entry": entry, "entry_date": entry_date})
    return found


def simulate(entries: list[dict[str, Any]], strategy: dict[str, Any], cost_rate: float) -> list[dict[str, Any]]:
    outcomes = []
    for sample in entries:
        # 진입일부터 보유일수만큼: [D+2, D+3, ...] — 신호일(index 0)은 제외
        dates = sample["trading_dates"][1:2 + strategy["days"]]
        if len(dates) < strategy["days"] + 1:
            continue
        outcome = simulate_minute_exit(sample["bars"], sample["entry"], dates, strategy)
        if not outcome:
            continue
        outcomes.append({
            **outcome,
            "entry_date": sample["entry_date"],
            "entry_timestamp": sample["entry"]["entry_timestamp"],
            "signal_date": sample["signal_date"],
            "ticker": sample["ticker"],
            "entry_price": sample["entry"]["entry_price"],
            "net_return": round(outcome["gross_return"] - cost_rate, 6),
            "holding_days": strategy["days"],
        })
    return outcomes


def run_sweep(samples: list[dict[str, Any]], cost_rate: float = COST_RATE) -> dict[str, Any]:
    combos = []
    rule_stats = {}
    for name, rule in ENTRY_RULES.items():
        entries = find_entries(samples, rule)
        rule_stats[name] = {"entry_found": len(entries), "no_entry": len(samples) - len(entries)}
        for days in SWEEP_DAYS:
            for take_profit in SWEEP_TP:
                for stop_loss in SWEEP_SL:
                    strategy = {"kind": "tp_sl", "tp": take_profit, "sl": stop_loss, "days": days}
                    outcomes = simulate(entries, strategy, cost_rate)
                    dates = sorted({item["entry_date"] for item in outcomes})
                    split = dates[len(dates) // 2] if len(dates) >= 2 else None
                    early = [item for item in outcomes if split and item["entry_date"] < split]
                    late = [item for item in outcomes if split and item["entry_date"] >= split]
                    reasons: dict[str, int] = {}
                    for item in outcomes:
                        reasons[item["exit_reason"]] = reasons.get(item["exit_reason"], 0) + 1
                    combos.append({
                        "entry_rule": name,
                        "id": f"{name}_tp{take_profit:.0%}_sl{stop_loss:.0%}_d{days}",
                        "strategy": strategy,
                        "simulated": len(outcomes),
                        "split_date": split,
                        "exit_reasons": dict(sorted(reasons.items())),
                        "overall": summarize_outcomes(outcomes, cost_rate),
                        "early": summarize_outcomes(early, cost_rate),
                        "late": summarize_outcomes(late, cost_rate),
                        "passes_split": bool(
                            early and late
                            and summarize_outcomes(early, cost_rate)["mean"] > 0
                            and summarize_outcomes(late, cost_rate)["mean"] > 0
                        ),
                    })
    ranked = sorted(combos, key=lambda item: item["overall"]["mean"] or -1, reverse=True)
    return {
        "cost_rate": cost_rate,
        "entry_side": "next_trading_day_intraday",
        "grid": {"rules": list(ENTRY_RULES), "tp": list(SWEEP_TP),
                 "sl": list(SWEEP_SL), "days": list(SWEEP_DAYS)},
        "combo_count": len(combos),
        "passing_split": sum(item["passes_split"] for item in combos),
        "rule_stats": rule_stats,
        "combos": ranked,
    }


def render_markdown(result: dict[str, Any], stats: dict[str, int], period: tuple[str, str]) -> str:
    grid = result["grid"]
    lines = [
        "# VFS 신호 — 다음 거래일 장중 조건부 진입 스윕",
        "",
        f"- 기간: {period[0]} ~ {period[1]}, 분봉 확보 후보 {stats['cache_complete']}건",
        "- 진입: 신호 다음 거래일(D+2) 정규장 1분봉, 기준선 = 신호일 저가",
        f"- 왕복 비용 {result['cost_rate']:.1%} 차감, 동일 1분봉 TP·SL 동시 터치 시 SL 우선",
        f"- 격자: 진입 {len(grid['rules'])}종 × TP {[f'{v:.0%}' for v in grid['tp']]} "
        f"× SL {[f'{v:.0%}' for v in grid['sl']]} × 보유 {grid['days']}일 = {result['combo_count']}조합",
        f"- 전·후반 모두 평균 양수인 조합: **{result['passing_split']}개**",
        "",
        "## 진입 규칙별 신호 발생",
        "",
        "| 진입 규칙 | 진입 발생 | 미발생(매수 안함) |",
        "|---|---|---|",
    ]
    for name, item in result["rule_stats"].items():
        lines.append(f"| {name} | {item['entry_found']} | {item['no_entry']} |")
    lines += [
        "",
        "## 전체 평균 순위",
        "",
        "| # | 조합 | 표본 | 전체평균 | 승률 | 전반평균 | 후반평균 | 분할통과 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rank, combo in enumerate(result["combos"], start=1):
        overall, early, late = combo["overall"], combo["early"], combo["late"]
        lines.append(
            f"| {rank} | {combo['id']} | {combo['simulated']} | {_pct(overall['mean'])} "
            f"| {_pct(overall['positive_rate'])} | {_pct(early['mean'])} | {_pct(late['mean'])} "
            f"| {'O' if combo['passes_split'] else '-'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="VFS 다음날 장중 진입 스윕")
    parser.add_argument("--from", dest="from_date", default="20260303")
    parser.add_argument("--to", dest="to_date", default="20260824")
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--minute-db", type=Path, default=DEFAULT_MINUTE_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cost", type=float, default=COST_RATE)
    args = parser.parse_args(argv)

    candidates = load_candidates(args.krx_db, args.from_date, args.to_date, CANDIDATE_HORIZON)
    samples, stats = load_sample_bars(candidates, args.minute_db)
    result = run_sweep(samples, args.cost)
    result["stats"] = stats
    result["period"] = [args.from_date, args.to_date]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "intraday_sweep.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "intraday_sweep.md").write_text(
        render_markdown(result, stats, (args.from_date, args.to_date)), encoding="utf-8")
    best = result["combos"][0]
    print(json.dumps({
        "bar_samples": stats["cache_complete"], "combos": result["combo_count"],
        "passing_split": result["passing_split"],
        "best": best["id"], "best_mean": best["overall"]["mean"], "best_n": best["simulated"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
