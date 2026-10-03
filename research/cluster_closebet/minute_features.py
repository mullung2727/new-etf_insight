"""분봉 Late-Momentum feature 추출 — Top30 종목·일자별 p1430/p1500/p1520.

실행 (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.cluster_closebet.minute_features \
        --start YYYYMMDD --end YYYYMMDD --out PATH
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_LM_CUTS = (("p1430", "143000"), ("p1500", "150000"), ("p1520", "152000"))


def lm_prices(bars_of_one_day: list[dict]) -> dict | None:
    """pXXXX = "090000" <= time < "XX:XX:00" 구간 마지막 봉의 close.

    time은 봉 START 시각(HHMMSS). 셋 중 하나라도 없으면 None.
    """
    out = {}
    for key, cut in _LM_CUTS:
        cand = [b for b in bars_of_one_day if "090000" <= b["time"] < cut]
        if not cand:
            return None
        out[key] = max(cand, key=lambda b: b["time"])["close"]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    # minute_bar_store → broker.kiwoom.quotes 경로의 자격증명 설정과 동일하게
    # broker/.env 우선, repo-root .env 폴백으로 로드한다.
    from dotenv import load_dotenv

    load_dotenv(ROOT / "broker" / ".env")
    load_dotenv(ROOT / ".env")

    import duckdb

    from .data import load_daily
    from .select import top_by_turnover

    con = duckdb.connect(str(ROOT / "etl" / "db" / "krx_ohlcv.duckdb"),
                         read_only=True)
    try:
        rows = load_daily(con, args.start, args.end)
    finally:
        con.close()

    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    ticker_dates: dict[str, list[str]] = {}
    for date in sorted(by_date):
        for r in top_by_turnover(by_date[date]):
            ticker_dates.setdefault(r["ticker"], []).append(date)

    out: dict[str, dict] = {}
    if os.path.exists(args.out):
        with open(args.out) as f:
            out = json.load(f)

    from research.watchlist_expected_return.minute_bar_store import (
        connect as connect_minute_store,
        load_bars,
    )

    def _save():
        d = os.path.dirname(args.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(out, f)

    t0 = time.time()
    count = 0
    with connect_minute_store() as mcon:
        for ticker in sorted(ticker_dates):
            need = [d for d in ticker_dates[ticker]
                    if f"{ticker}|{d}" not in out]
            if need:
                # 종목별 날짜를 한 번의 호출로 묶는다.
                bars = load_bars(mcon, ticker, sorted(need))
                per_day: dict[str, list[dict]] = {}
                for b in bars:
                    if b["date"] in need:
                        per_day.setdefault(b["date"], []).append(b)
                for d in need:
                    got = lm_prices(per_day.get(d, []))
                    if got is not None:
                        out[f"{ticker}|{d}"] = got
            count += 1
            if count % 50 == 0:
                print(f"{count} {time.time() - t0:.1f}s", file=sys.stderr)
                _save()
    _save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
