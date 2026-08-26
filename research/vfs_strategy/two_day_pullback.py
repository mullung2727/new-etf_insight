"""VFS 2일 눌림확인 전략 — 신호 산출 + 진입문턱 스윕.

원본 VFS(눌림 1일 확인, 거래량 x5)가 전 조합 음수였던 데 대한 재설계.
리서치 경과에서 살아남은 조건만 모았다.

신호
    D    거래량 >= 20거래일 이동평균 x2,  종가 > 시가 x1.1
    D+1  거래량 < D거래량 x0.2
    D+2  거래량 < D+1거래량,  종가 < 시가 x0.99,  종가 > (D종가 + D시가)/2

거래정지 가드
    이동평균 구간과 D -> D+1 -> D+2 가 시장 기준 연속 거래일인 것만.
    정지 구간은 ohlcv 에 행이 없어 종목별 윈도우가 건너뛰므로, 그대로 두면
    20거래일 평균이 두 달치를 걸치고 재개 후 미조정 가격 점프가 섞인다.

진입 (D+3 장중 1분봉)
    가격이 D+2 종가 위이면서 당일 시가 대비 문턱(기본 +2%)을 넘는 첫 봉의 종가.
    문턱 미달이면 그날은 매수하지 않는다.

청산
    무손절 — 보유 거래일수 마지막 봉 종가. 손절/익절은 리서치 전 구간에서
    무손절보다 나빴다(문턱 8칸 x 정의 2종 전부).

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.vfs_strategy.two_day_pullback
    etl\\.venv\\Scripts\\python.exe -m research.vfs_strategy.two_day_pullback --check-cache
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path
from typing import Any

import duckdb

from research.vfs_strategy.backtest import (
    COST_RATE,
    DEFAULT_KRX_DB,
    DEFAULT_OUTPUT_DIR,
    load_sample_bars,
)
from research.watchlist_expected_return.minute_bar_store import (
    DEFAULT_DB_PATH,
    connect,
    missing_dates,
)
from research.watchlist_expected_return.phase8_minute_pullback_strategy import (
    simulate_minute_exit,
)

VOL_MA_DAYS = 20
VOL_SPIKE = 2.0                # D일 거래량 / 이동평균 하한
BODY_UP = 1.1                  # D일 종가/시가 하한
D1_VOL_DRY = 0.2               # D+1 거래량 / D일 거래량 상한
D2_BODY_DOWN = 0.99            # D+2 종가/시가 상한 (음봉)
HOLD_DAYS = 1                  # 진입일 이후 보유 거래일
# 음수 문턱 = 시가 아래에서 매수(눌림 추가 매수). 조건이 느슨해져 진입이 빨라진다.
SWEEP_THRESHOLD = (-0.04, -0.03, -0.02, -0.015, -0.01, -0.005,
                   0.0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04)

_SQL = f"""
WITH mkt AS (SELECT DISTINCT date FROM ohlcv),
m AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS ms FROM mkt),
base AS (
    SELECT o.date, o.ticker, o.open, o.close, o.volume, m.ms,
           ROW_NUMBER() OVER (PARTITION BY o.ticker ORDER BY o.date) AS seq
    FROM ohlcv o JOIN m USING (date)
    WHERE o.volume > 0 AND o.open > 0 AND o.close > 0
),
w AS (
    SELECT *,
        AVG(volume) OVER win AS vol_ma,
        COUNT(*)    OVER win AS n_ma,
        MIN(ms)     OVER win AS ma_first_ms,
        LEAD(ms,1)     OVER p AS ms1, LEAD(open,1) OVER p AS open1,
        LEAD(close,1)  OVER p AS close1, LEAD(volume,1) OVER p AS vol1,
        LEAD(ms,2)     OVER p AS ms2, LEAD(date,2) OVER p AS date2,
        LEAD(open,2)   OVER p AS open2, LEAD(close,2) OVER p AS close2,
        LEAD(volume,2) OVER p AS vol2
    FROM base
    WINDOW win AS (PARTITION BY ticker ORDER BY seq
                   ROWS BETWEEN {VOL_MA_DAYS - 1} PRECEDING AND CURRENT ROW),
           p   AS (PARTITION BY ticker ORDER BY seq)
)
SELECT date2 AS signal_date, ticker, close2 AS signal_close
FROM w
WHERE n_ma = {VOL_MA_DAYS}
  AND ms - ma_first_ms = {VOL_MA_DAYS - 1}      -- 이동평균 구간이 연속 거래일
  AND ms1 = ms + 1 AND ms2 = ms1 + 1            -- D -> D+1 -> D+2 연속 거래일
  AND volume >= vol_ma * {VOL_SPIKE}
  AND close > open * {BODY_UP}
  AND date2 IS NOT NULL AND open1 > 0 AND open2 > 0
  AND vol1 < volume * {D1_VOL_DRY}
  AND vol2 < vol1
  AND close2 < open2 * {D2_BODY_DOWN}
  AND close2 > (close + open) * 0.5
ORDER BY signal_date, ticker
"""


def load_candidates(krx_db: Path, hold_days: int = HOLD_DAYS) -> list[dict[str, Any]]:
    """신호 + 진입일(D+3) 기준 보유 horizon 이 확보된 후보만."""
    with duckdb.connect(str(krx_db), read_only=True) as con:
        dates = [str(row[0]) for row in con.execute(
            "SELECT DISTINCT date FROM ohlcv ORDER BY date").fetchall()]
        rows = con.execute(_SQL).fetchall()
    index = {date: position for position, date in enumerate(dates)}
    out = []
    for signal_date, ticker, signal_close in rows:
        position = index.get(str(signal_date))
        if position is None or position + hold_days + 2 > len(dates):
            continue                                  # 진입일 + 보유 horizon 미확보
        out.append({
            "ticker": ticker,
            "signal_date": str(signal_date),
            "trading_dates": dates[position + 1:position + hold_days + 2],
            "signal_close": signal_close,
        })
    return out


def find_entry(day_bars: list[dict[str, Any]], signal_close: float,
               threshold: float) -> dict[str, Any] | None:
    """당일 시가 대비 threshold 이상 오르고 신호일 종가 위인 첫 봉의 종가."""
    if not day_bars:
        return None
    day_open = day_bars[0]["open"]
    for bar in day_bars:
        if bar["close"] > signal_close and bar["close"] >= day_open * (1 + threshold):
            return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
    return None


_DAILY_SQL = """
SELECT date, ticker, open, high, close FROM ohlcv WHERE volume > 0 AND open > 0
"""


def load_daily_proxy(krx_db: Path, candidates: list[dict[str, Any]],
                     hold_days: int = HOLD_DAYS) -> list[dict[str, Any]]:
    """분봉 없는 구간용 일봉 근사.

    진입가 = max(신호일 종가, 진입일 시가 x (1+문턱)) — 이 값에 닿아야(고가 >= 진입가) 체결.
    분봉의 '문턱을 넘는 첫 봉 종가'를 '문턱 가격에 지정가 체결'로 바꾼 근사다.
    ka10080 이 20250801 이후만 주므로 그 이전 표본은 이 경로로만 잰다.
    """
    with duckdb.connect(str(krx_db), read_only=True) as con:
        daily = {(str(d), t): (o, h, c) for d, t, o, h, c in con.execute(_DAILY_SQL).fetchall()}
    out = []
    for item in candidates:
        entry_date = item["trading_dates"][0]
        exit_date = item["trading_dates"][hold_days]
        entry_day = daily.get((entry_date, item["ticker"]))
        exit_day = daily.get((exit_date, item["ticker"]))
        if not entry_day or not exit_day:
            continue
        out.append({**item, "day_open": entry_day[0], "day_high": entry_day[1],
                    "exit_close": exit_day[2]})
    return out


def run_daily_sweep(proxies: list[dict[str, Any]],
                    cost_rate: float = COST_RATE) -> list[dict[str, Any]]:
    results = []
    for threshold in SWEEP_THRESHOLD:
        outcomes = []
        for item in proxies:
            entry_price = max(item["signal_close"], item["day_open"] * (1 + threshold))
            if item["day_high"] < entry_price:
                continue                                   # 문턱 미달 → 매수 없음
            outcomes.append((item["exit_close"] / entry_price - 1 - cost_rate,
                             item["signal_date"]))
        dates = sorted({date for _, date in outcomes})
        if not dates:
            continue
        stats = summarize(outcomes, dates[len(dates) // 2])
        results.append({"threshold": threshold, "no_entry": len(proxies) - len(outcomes), **stats})
    return results


def summarize(outcomes: list[tuple[float, str]], split_date: str) -> dict[str, Any] | None:
    values = [value for value, _ in outcomes]
    if not values:
        return None
    mean = st.fmean(values)
    stderr = st.pstdev(values) / len(values) ** 0.5 if len(values) > 1 else 0.0
    early = [value for value, date in outcomes if date < split_date]
    late = [value for value, date in outcomes if date >= split_date]
    early_mean = st.fmean(early) if early else None
    late_mean = st.fmean(late) if late else None
    return {
        "n": len(values), "mean": mean, "stderr": stderr,
        "t": mean / stderr if stderr else 0.0,
        "win_rate": sum(1 for value in values if value > 0) / len(values),
        "early": early_mean, "late": late_mean,
        "passes_split": bool(early and late and early_mean > 0 and late_mean > 0),
    }


def run_sweep(samples: list[dict[str, Any]], hold_days: int = HOLD_DAYS,
              cost_rate: float = COST_RATE) -> list[dict[str, Any]]:
    strategy = {"kind": "fixed_close", "days": hold_days}
    results = []
    for threshold in SWEEP_THRESHOLD:
        entered = []
        for sample in samples:
            day_bars = [bar for bar in sample["bars"]
                        if bar["date"] == sample["trading_dates"][0]]
            entry = find_entry(day_bars, sample["signal_close"], threshold)
            if entry:
                entered.append({**sample, "entry": entry})
        outcomes = []
        for sample in entered:
            outcome = simulate_minute_exit(
                sample["bars"], sample["entry"],
                sample["trading_dates"][:hold_days + 1], strategy)
            if outcome:
                outcomes.append((outcome["gross_return"] - cost_rate, sample["signal_date"]))
        dates = sorted({date for _, date in outcomes})
        if not dates:
            continue
        stats = summarize(outcomes, dates[len(dates) // 2])
        results.append({"threshold": threshold, "no_entry": len(samples) - len(entered), **stats})
    return results


def render_markdown(results: list[dict[str, Any]], sample_count: int,
                    period: tuple[str, str]) -> str:
    lines = [
        "# VFS 2일 눌림확인 — 진입 문턱 스윕",
        "",
        f"- 기간 {period[0]} ~ {period[1]}, 분봉 확보 표본 {sample_count}건",
        f"- 진입: D+3 장중, D+2 종가 위 + 당일 시가 대비 문턱 돌파 첫 1분봉",
        f"- 청산: 무손절, 보유 {HOLD_DAYS}거래일, 왕복 비용 {COST_RATE:.1%} 차감",
        "",
        "| 문턱 | 진입 | 미진입 | 순평균 | 승률 | t | 전반 | 후반 | 분할통과 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item['threshold']:.1%} | {item['n']} | {item['no_entry']} "
            f"| {item['mean']:.2%} | {item['win_rate']:.1%} | {item['t']:.2f} "
            f"| {item['early']:.2%} | {item['late']:.2%} "
            f"| {'O' if item['passes_split'] else '-'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="VFS 2일 눌림확인 진입문턱 스윕")
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--minute-db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--hold-days", type=int, default=HOLD_DAYS)
    parser.add_argument("--cost", type=float, default=COST_RATE)
    parser.add_argument("--check-cache", action="store_true",
                        help="분봉 조회 없이 후보/미확보 건수만 출력")
    parser.add_argument("--daily", action="store_true",
                        help="일봉 근사로 스윕 (분봉 없는 과거 구간용)")
    parser.add_argument("--from-date", help="신호일 하한 YYYYMMDD")
    parser.add_argument("--to-date", help="신호일 상한 YYYYMMDD")
    args = parser.parse_args(argv)

    candidates = load_candidates(args.krx_db, args.hold_days)
    if args.from_date:
        candidates = [c for c in candidates if c["signal_date"] >= args.from_date]
    if args.to_date:
        candidates = [c for c in candidates if c["signal_date"] <= args.to_date]

    if args.daily:
        proxies = load_daily_proxy(args.krx_db, candidates, args.hold_days)
        results = run_daily_sweep(proxies, args.cost)
        print(json.dumps({
            "mode": "daily_proxy", "candidates": len(candidates), "proxies": len(proxies),
            "period": [candidates[0]["signal_date"], candidates[-1]["signal_date"]] if candidates else [],
            "rows": [{"threshold": r["threshold"], "n": r["n"], "mean": round(r["mean"], 5),
                      "win": round(r["win_rate"], 3), "t": round(r["t"], 2),
                      "early": round(r["early"], 5), "late": round(r["late"], 5),
                      "pass": r["passes_split"]} for r in results],
        }, ensure_ascii=False, indent=1))
        return

    if args.check_cache:
        with connect(args.minute_db, read_only=True) as con:
            gaps = sum(1 for item in candidates
                       if missing_dates(con, item["ticker"], item["trading_dates"]))
        print(json.dumps({"candidates": len(candidates), "minute_missing": gaps},
                         ensure_ascii=False))
        return

    samples, stats = load_sample_bars(candidates, args.minute_db)
    results = run_sweep(samples, args.hold_days, args.cost)
    period = (candidates[0]["signal_date"], candidates[-1]["signal_date"]) if candidates else ("-", "-")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "two_day_pullback.json").write_text(
        json.dumps({"stats": stats, "period": list(period), "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "two_day_pullback.md").write_text(
        render_markdown(results, len(samples), period), encoding="utf-8")
    print(json.dumps({
        "candidates": len(candidates), "samples": len(samples), "period": list(period),
        "rows": [{"threshold": r["threshold"], "n": r["n"], "mean": round(r["mean"], 5),
                  "t": round(r["t"], 2), "pass": r["passes_split"]} for r in results],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
