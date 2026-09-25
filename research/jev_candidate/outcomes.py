"""P4 시가 트랙 성과 — PLAN_JEV_CANDIDATE_JUDGE §8 outcomes 중 open 트랙만.

판단 모듈(state·questions·arms)은 이 모듈을 읽지 않는다 (T8).
제외는 평가용 표시일 뿐 선정에 쓰지 않는다 (§2).

Usage (repo root, 분봉 미조회분은 키움에서 채우므로 네트워크 필요):
    etl\\.venv\\Scripts\\python.exe -m research.jev_candidate.outcomes --dates 20260409,20260410
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import duckdb

from research.jev_candidate import store
from research.jev_candidate.questions import QUESTION_SET_VER
from research.watchlist_expected_return import minute_bar_store

KRX_DB = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"

GAP_LIMIT = 0.31  # ±31% 밖 — 권리락/오류 (BACKTEST_DATA §2c)
LOCK_LIMIT = 0.295  # +29.5%↑ — 시가 상한가 매수 불가 (§2b)
DAY_END = "093000"  # 이 시각 strictly 이전 봉만 09:30가로 쓴다


def compute_open(
    d0_close: int | None, d1: dict | None, bars_d1: list[dict]
) -> dict[str, Any]:
    """시가 트랙 1행 (date·ticker 제외) — d1 시가 진입 기준 수익률 4개 + 제외 사유."""
    d1 = d1 or {}
    d1_open = d1.get("open")
    d1_high = d1.get("high")
    d1_low = d1.get("low")
    d1_close = d1.get("close")
    closes = [
        b["close"]
        for b in (bars_d1 or [])
        if b.get("time", "") < DAY_END and b.get("close") is not None
    ]
    d1_0930 = closes[-1] if closes else None
    out: dict[str, Any] = {
        "d0_close": d0_close,
        "d1_open": d1_open,
        "d1_0930": d1_0930,
        "d1_close": d1_close,
        "d1_high": d1_high,
        "d1_low": d1_low,
        "ret_open_0930": None,
        "ret_open_close": None,
        "max_ret_o": None,
        "min_ret_o": None,
        "excluded_open": None,
    }
    if (
        not d1
        or d1_open is None
        or d1_close is None
        or d1_open <= 0
        or d1_close <= 0
        or d1.get("volume") == 0
    ):
        out["excluded_open"] = "no_trade"  # 거래정지 — 갭 비율 계산 불가라 먼저
        return out
    if d0_close is not None and d0_close > 0:
        gap = d1_open / d0_close - 1
        if abs(gap) >= GAP_LIMIT:
            out["excluded_open"] = "gap_artifact"  # 권리락/오류 — 수익률 None 유지
            return out
        if gap >= LOCK_LIMIT:
            out["excluded_open"] = "limit_up"  # 매수 불가 — 수익률 계산 유지, 평가에서 현금 0
    def rel(v):
        return v / d1_open - 1 if v else None

    out["ret_open_0930"] = d1_0930 / d1_open - 1 if d1_0930 is not None else None
    out["ret_open_close"] = rel(d1_close)
    out["max_ret_o"] = rel(d1_high)
    out["min_ret_o"] = rel(d1_low)
    return out


def _daily_map(
    krx_con: duckdb.DuckDBPyConnection, date: str | None, tickers: list[str]
) -> dict[str, dict]:
    """특정일 일봉 맵 — ticker → open/high/low/close/volume."""
    if date is None or not tickers:
        return {}
    placeholders = ", ".join("?" * len(tickers))
    rows = krx_con.execute(
        "SELECT ticker, open, high, low, close, volume FROM ohlcv"
        f" WHERE date = ? AND ticker IN ({placeholders})",
        [date, *tickers],
    ).fetchall()
    return {
        ticker: dict(zip(("open", "high", "low", "close", "volume"), vals))
        for ticker, *vals in rows
    }


def is_stale_outcome(row: dict | None) -> bool:
    """재계산 필요 — D+1 확정 전 캐시 (d1_open/d1_0930 NULL)."""
    return row is None or row.get("d1_open") is None or row.get("d1_0930") is None


def build_outcomes(
    dates: list[str],
    tickers_by_date: dict[str, list[str]],
    krx_con: duckdb.DuckDBPyConnection,
    minute_con: duckdb.DuckDBPyConnection,
) -> list[dict]:
    """일자별 시가 트랙 성과 행 — D+1 = ohlcv 다음 거래일. 분봉 실패는 d1_0930 None."""
    trading = sorted(
        row[0] for row in krx_con.execute("SELECT DISTINCT date FROM ohlcv").fetchall()
    )
    rows: list[dict] = []
    for day in dates:
        later = [d for d in trading if d > day]
        d1_date = later[0] if later else None
        if d1_date is None:
            continue  # D+1 미확정 — no_trade 캐시 금지, 다음에 재계산
        tickers = tickers_by_date.get(day, [])
        d0_map = _daily_map(krx_con, day, tickers)
        d1_map = _daily_map(krx_con, d1_date, tickers)
        fails = 0
        for ticker in tickers:
            bars: list[dict] = []
            if d1_date is not None:
                try:
                    bars = [
                        b
                        for b in minute_bar_store.load_bars(minute_con, ticker, [d1_date])
                        if b["date"] == d1_date
                    ]
                except Exception:
                    fails += 1  # d1_0930 None으로 기록하고 계속
            row = {"date": day, "ticker": ticker}
            row.update(
                compute_open(
                    (d0_map.get(ticker) or {}).get("close"),
                    d1_map.get(ticker),
                    bars,
                )
            )
            rows.append(row)
        if fails:
            print(f"경고: {day} 분봉 조회 실패 {fails}건 (d1_0930 없음)")
    return rows


def main(argv: list[str] | None = None) -> int:
    """CLI — states run 종목 → outcomes 저장 + 일자별 건수 출력."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="YYYYMMDD 콤마 구분")
    ap.add_argument("--db", default=str(store.DEFAULT_DB))
    args = ap.parse_args(argv)
    days = [d.strip() for d in args.dates.split(",") if d.strip()]

    con = store.connect(args.db)
    store.ensure_schema(con)
    krx_con = duckdb.connect(str(KRX_DB), read_only=True)
    try:
        with minute_bar_store.connect() as minute_con:
            for day in days:
                run_id = f"backtest-open-{day}-{QUESTION_SET_VER}"
                tickers = sorted({s["ticker"] for s in store.load_states(con, run_id)})
                have = store.load_outcomes(con, [day])
                todo = [t for t in tickers if is_stale_outcome(have.get((day, t)))]
                rows = build_outcomes([day], {day: todo}, krx_con, minute_con)
                store.save_outcomes(con, rows)
                reasons = Counter(r["excluded_open"] for r in rows if r["excluded_open"])
                detail = ", ".join(f"{k} {v}" for k, v in sorted(reasons.items()))
                print(f"{day}: {len(rows)}건 저장" + (f" (제외: {detail})" if detail else ""))
    finally:
        krx_con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
