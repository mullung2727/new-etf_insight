"""VFS 신호 1분봉 백테스트 — 신호일 15:19 종가 매수, TP/SL/보유일 청산.

신호: etl/scripts/build_vfs.compute_vfs (거래량 폭발 장대양봉 다음날 눌림)
진입: 신호일 15:19 1분봉 종가 (phase9 close_confirm 과 동일 가정)
청산: research/watchlist_expected_return/phase8 의 simulate_minute_exit 재사용
      — 갭 시가 우선, 동일 1분봉 TP·SL 동시 터치 시 SL 우선, 미달 시 마지막날 종가 강제청산

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.vfs_strategy.backtest --from 20260303 --to 20260824
    etl\\.venv\\Scripts\\python.exe -m research.vfs_strategy.backtest --dry-run   # 후보 건수만
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[2]
# etl/scripts 는 패키지가 아니라 sys.path 진입점 규약(_bootstrap)을 쓴다.
sys.path.insert(0, str(ROOT / "etl" / "scripts"))

from build_vfs import compute_vfs  # noqa: E402

from research.watchlist_expected_return import minute_bar_store as minute_store  # noqa: E402
from research.watchlist_expected_return.minute_bar_store import DEFAULT_DB_PATH as DEFAULT_MINUTE_DB  # noqa: E402
from research.watchlist_expected_return.phase4_holding_strategy import summarize_outcomes  # noqa: E402
from research.watchlist_expected_return.phase8_minute_pullback_strategy import (  # noqa: E402
    simulate_minute_exit,
)

DEFAULT_KRX_DB = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent

ENTRY_TIME = "151900"          # 종가 확정 근사 — 15:19 1분봉 종가에 체결 가정
STRATEGY = {"kind": "tp_sl", "tp": 0.05, "sl": 0.03, "days": 3}
COST_RATE = 0.006              # 왕복 거래비용 — 실계좌 수수료+거래세+슬리피지 여유
                               # (phase1_data_audit 의 1%는 모의계좌 0.7% 수수료 기준이라 과대)

SWEEP_TP = (0.03, 0.04, 0.05, 0.06, 0.07)
SWEEP_SL = (0.01, 0.02, 0.03, 0.04)
SWEEP_DAYS = (1, 2, 3)         # 더 늘리려면 load_candidates(days=) 를 키우면 된다 —
                               # 분봉 DB가 날짜 단위라 이미 받은 날은 재조회하지 않는다


def load_candidates(
    krx_db: Path, from_date: str, to_date: str, days: int = STRATEGY["days"]
) -> list[dict[str, Any]]:
    """VFS 신호 → 진입일(=신호일) + 보유 거래일 horizon 이 확보된 후보만."""
    with duckdb.connect(str(krx_db), read_only=True) as con:
        trading_dates = [str(row[0]) for row in con.execute(
            "SELECT DISTINCT date FROM ohlcv WHERE date BETWEEN ? AND ? ORDER BY date",
            [from_date, to_date],
        ).fetchall()]
        signals = compute_vfs(con, from_date, to_date)
        # 장중 진입 규칙의 기준선(신호일 저가/종가)용
        daily = {
            (str(row[0]), str(row[1])): {"low": row[2], "close": row[3]}
            for row in con.execute(
                "SELECT date, ticker, low, close FROM ohlcv WHERE date BETWEEN ? AND ?",
                [from_date, to_date],
            ).fetchall()
        }

    index = {date: position for position, date in enumerate(trading_dates)}
    horizon = days + 1                        # 진입일 + 보유 거래일
    out = []
    for signal_date in sorted(signals):
        position = index.get(signal_date)
        if position is None or position + horizon > len(trading_dates):
            continue                          # 청산 horizon 미확보 → 표본 제외
        dates = trading_dates[position:position + horizon]
        for ticker in signals[signal_date]:
            bar = daily.get((signal_date, ticker), {})
            out.append({
                "ticker": ticker, "signal_date": signal_date, "trading_dates": dates,
                "signal_low": bar.get("low"), "signal_close": bar.get("close"),
            })
    return out


def find_entry(bars: list[dict[str, Any]], signal_date: str) -> dict[str, Any] | None:
    """신호일 ENTRY_TIME 1분봉 종가를 진입가로 잡는다. 해당 봉이 없으면 표본 제외."""
    bar = next(
        (item for item in bars if item["date"] == signal_date and item["time"] == ENTRY_TIME),
        None,
    )
    if not bar:
        return None
    return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}


def load_sample_bars(
    candidates: list[dict[str, Any]], db_path: Path = DEFAULT_MINUTE_DB
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """후보별 정규장 1분봉을 분봉 DB에서 읽고, 없는 날짜만 키움에서 채운다."""
    stats = {"candidates": len(candidates), "cache_complete": 0, "dropped_incomplete_cache": 0}
    samples = []
    with minute_store.connect(db_path) as con:
        for item in candidates:
            dates = item["trading_dates"]
            bars = minute_store.load_bars(con, item["ticker"], dates)
            by_date = {bar["date"] for bar in bars}
            # 첫날 장 시작부터 있어야 진입 시점 판단이 가능하다 — 부분 수신 표본은 제외.
            if by_date != set(dates) or bars[0]["time"] > "090000":
                stats["dropped_incomplete_cache"] += 1
                continue
            stats["cache_complete"] += 1
            samples.append({**item, "bars": bars})
    return samples, stats


def build_samples(
    candidates: list[dict[str, Any]], db_path: Path = DEFAULT_MINUTE_DB
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """load_sample_bars + 신호일 종가 진입가 확정까지 끝낸 표본만 반환."""
    with_bars, stats = load_sample_bars(candidates, db_path)
    stats.update({"entry_found": 0, "dropped_no_entry_bar": 0})
    samples = []
    for item in with_bars:
        entry = find_entry(item["bars"], item["signal_date"])
        if not entry:
            stats["dropped_no_entry_bar"] += 1
            continue
        stats["entry_found"] += 1
        samples.append({**item, "entry": entry})
    return samples, stats


def run_backtest(
    samples: list[dict[str, Any]],
    strategy: dict[str, Any] = STRATEGY,
    cost_rate: float = COST_RATE,
) -> dict[str, Any]:
    """표본별 청산 시뮬 → 전체 + 전/후반 분할 요약."""
    outcomes = []
    for sample in samples:
        outcome = simulate_minute_exit(
            sample["bars"], sample["entry"], sample["trading_dates"], strategy
        )
        if not outcome:
            continue                          # horizon 분봉 결손 → 성과 표본 제외
        outcomes.append({
            **outcome,
            "entry_date": sample["signal_date"],   # 진입일 = 신호일 (15:19 체결)
            "ticker": sample["ticker"],
            "entry_price": sample["entry"]["entry_price"],
            "net_return": round(outcome["gross_return"] - cost_rate, 6),
            "holding_days": strategy["days"],
        })

    reasons: dict[str, int] = {}
    for outcome in outcomes:
        reasons[outcome["exit_reason"]] = reasons.get(outcome["exit_reason"], 0) + 1

    dates = sorted({outcome["entry_date"] for outcome in outcomes})
    split_date = dates[len(dates) // 2] if len(dates) >= 2 else None
    early = [item for item in outcomes if split_date and item["entry_date"] < split_date]
    late = [item for item in outcomes if split_date and item["entry_date"] >= split_date]

    return {
        "strategy": strategy,
        "entry_time": ENTRY_TIME,
        "cost_rate": cost_rate,
        "simulated": len(outcomes),
        "dropped_no_exit_path": len(samples) - len(outcomes),
        "exit_reasons": dict(sorted(reasons.items())),
        "split_date": split_date,
        "overall": summarize_outcomes(outcomes, cost_rate),
        "early": summarize_outcomes(early, cost_rate),
        "late": summarize_outcomes(late, cost_rate),
        "trades": sorted(outcomes, key=lambda item: (item["entry_date"], item["ticker"])),
    }


def run_sweep(samples: list[dict[str, Any]], cost_rate: float = COST_RATE) -> dict[str, Any]:
    """TP × SL × 보유일 전 조합을 같은 표본에 돌려 비교한다.

    분봉은 최장 보유일 horizon 으로 이미 확보돼 있고 simulate_minute_exit 가
    trading_dates[:days+1] 로 잘라 쓰므로, 짧은 보유일은 추가 조회 없이 계산된다.
    """
    combos = []
    for days in SWEEP_DAYS:
        for take_profit in SWEEP_TP:
            for stop_loss in SWEEP_SL:
                strategy = {"kind": "tp_sl", "tp": take_profit, "sl": stop_loss, "days": days}
                result = run_backtest(samples, strategy, cost_rate)
                combos.append({
                    "id": f"tp{take_profit:.0%}_sl{stop_loss:.0%}_d{days}",
                    "strategy": strategy,
                    "simulated": result["simulated"],
                    "overall": result["overall"],
                    "early": result["early"],
                    "late": result["late"],
                    "exit_reasons": result["exit_reasons"],
                    # 양 구간 모두 양수여야 시간 분할 검증 통과 (phase9~11 과 동일 기준)
                    "passes_split": bool(
                        result["early"]["mean"] is not None
                        and result["late"]["mean"] is not None
                        and result["early"]["mean"] > 0
                        and result["late"]["mean"] > 0
                    ),
                })
    ranked = sorted(combos, key=lambda item: item["overall"]["mean"] or -1, reverse=True)
    return {
        "cost_rate": cost_rate,
        "entry_time": ENTRY_TIME,
        "grid": {"tp": list(SWEEP_TP), "sl": list(SWEEP_SL), "days": list(SWEEP_DAYS)},
        "combo_count": len(combos),
        "passing_split": sum(item["passes_split"] for item in combos),
        "combos": ranked,
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.2f}%"


def render_markdown(result: dict[str, Any], stats: dict[str, int], period: tuple[str, str]) -> str:
    strategy = result["strategy"]
    entry_label = f"{result['entry_time'][:2]}:{result['entry_time'][2:4]}"
    lines = [
        "# VFS 신호 1분봉 백테스트",
        "",
        f"- 기간: {period[0]} ~ {period[1]}",
        f"- 진입: 신호일 {entry_label} 1분봉 종가",
        f"- 청산: TP +{strategy['tp']:.0%} / SL -{strategy['sl']:.0%} / 최대 {strategy['days']}거래일",
        f"- 동일 1분봉 TP·SL 동시 터치 시 SL 우선, 왕복 비용 {result['cost_rate']:.0%} 차감",
        "",
        "## 표본",
        "",
        f"- VFS 신호 후보: {stats['candidates']}건",
        f"- 분봉 캐시 완전: {stats['cache_complete']}건 (불완전 제외 {stats['dropped_incomplete_cache']}건)",
        f"- 진입봉 확보: {stats['entry_found']}건 (진입봉 없음 제외 {stats['dropped_no_entry_bar']}건)",
        f"- 청산 시뮬 완료: {result['simulated']}건 (청산경로 결손 제외 {result['dropped_no_exit_path']}건)",
        "",
        "## 성과",
        "",
        "| 구간 | 표본 | 평균 | 중앙값 | 승률 | 최악 | MDD |",
        "|---|---|---|---|---|---|---|",
    ]
    split = result["split_date"]
    for label, key in (("전체", "overall"), (f"전반(<{split})", "early"), (f"후반(>={split})", "late")):
        summary = result[key]
        lines.append(
            f"| {label} | {summary['count']} | {_pct(summary['mean'])} | {_pct(summary['median'])} "
            f"| {_pct(summary['positive_rate'])} | {_pct(summary['worst'])} | {_pct(summary['max_drawdown'])} |"
        )
    lines += ["", "## 청산 사유", "", "| 사유 | 건수 |", "|---|---|"]
    for reason, count in result["exit_reasons"].items():
        lines.append(f"| {reason} | {count} |")
    lines += ["", "## 개별 체결", "", "| 신호일 | 종목 | 진입가 | 청산사유 | 순수익률 |", "|---|---|---|---|---|"]
    for trade in result["trades"]:
        lines.append(
            f"| {trade['entry_date']} | {trade['ticker']} | {trade['entry_price']:,} "
            f"| {trade['exit_reason']} | {_pct(trade['net_return'])} |"
        )
    return "\n".join(lines) + "\n"


def render_sweep_markdown(result: dict[str, Any], stats: dict[str, int], period: tuple[str, str]) -> str:
    grid = result["grid"]
    lines = [
        "# VFS 신호 1분봉 파라미터 스윕",
        "",
        f"- 기간: {period[0]} ~ {period[1]}, 표본 {stats['entry_found']}건",
        f"- 진입: 신호일 {result['entry_time'][:2]}:{result['entry_time'][2:4]} 1분봉 종가",
        f"- 왕복 비용 {result['cost_rate']:.1%} 차감, 동일 1분봉 TP·SL 동시 터치 시 SL 우선",
        f"- 격자: TP {[f'{v:.0%}' for v in grid['tp']]} × SL {[f'{v:.0%}' for v in grid['sl']]} "
        f"× 보유 {grid['days']}일 = {result['combo_count']}조합",
        f"- 전·후반 모두 평균 양수인 조합: **{result['passing_split']}개**",
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
    parser = argparse.ArgumentParser(description="VFS 신호 1분봉 백테스트")
    parser.add_argument("--from", dest="from_date", default="20260303")
    parser.add_argument("--to", dest="to_date", default="20260824")
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--minute-db", type=Path, default=DEFAULT_MINUTE_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cost", type=float, default=COST_RATE, help="왕복 거래비용 (기본 0.006)")
    parser.add_argument("--sweep", action="store_true",
                        help="TP × SL × 보유일 격자 스윕 → sweep.json/md")
    parser.add_argument("--dry-run", action="store_true",
                        help="분봉 조회 없이 후보 건수만 출력")
    args = parser.parse_args(argv)

    max_days = max(SWEEP_DAYS) if args.sweep else STRATEGY["days"]
    candidates = load_candidates(args.krx_db, args.from_date, args.to_date, max_days)
    if args.dry_run:
        print(json.dumps({
            "candidates": len(candidates),
            "signal_dates": len({item["signal_date"] for item in candidates}),
        }, ensure_ascii=False))
        return

    samples, stats = build_samples(candidates, args.minute_db)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.sweep:
        sweep = run_sweep(samples, args.cost)
        sweep["stats"] = stats
        sweep["period"] = [args.from_date, args.to_date]
        (args.output_dir / "sweep.json").write_text(
            json.dumps(sweep, ensure_ascii=False, indent=2), encoding="utf-8")
        (args.output_dir / "sweep.md").write_text(
            render_sweep_markdown(sweep, stats, (args.from_date, args.to_date)), encoding="utf-8")
        best = sweep["combos"][0]
        print(json.dumps({
            "samples": stats["entry_found"], "combos": sweep["combo_count"],
            "passing_split": sweep["passing_split"],
            "best": best["id"], "best_mean": best["overall"]["mean"],
        }, ensure_ascii=False))
        return

    result = run_backtest(samples, STRATEGY, args.cost)
    result["stats"] = stats
    result["period"] = [args.from_date, args.to_date]

    (args.output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "results.md").write_text(
        render_markdown(result, stats, (args.from_date, args.to_date)), encoding="utf-8")
    print(json.dumps({
        "candidates": stats["candidates"], "simulated": result["simulated"],
        "mean": result["overall"]["mean"], "positive_rate": result["overall"]["positive_rate"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
