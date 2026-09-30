"""일봉 전용 데이터 로드 — etl/db/krx_ohlcv.duckdb 읽기 전용."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

DB = Path(__file__).resolve().parents[2] / "etl/db/krx_ohlcv.duckdb"


def load_px(db=DB) -> pd.DataFrame:
    """정지(0값) 행 제외 일봉 + 시장 거래일 순번 ms(0부터). (ticker, date) 정렬."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        px = con.execute(
            """
            WITH mkt AS (SELECT DISTINCT date FROM ohlcv),
                 m AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS ms FROM mkt)
            SELECT o.date, o.ticker, o.market, o.open, o.high, o.low, o.close,
                   o.volume, o.trading_value, o.market_cap, m.ms
            FROM ohlcv o JOIN m USING (date)
            WHERE o.volume > 0 AND o.open > 0 AND o.close > 0
            ORDER BY o.ticker, o.date
            """
        ).df()
    finally:
        con.close()
    return px.reset_index(drop=True)


def market_dates(db=DB) -> list[str]:
    """ohlcv 에 있는 시장 거래일 전체, 오름차순."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        return [r[0] for r in con.execute("SELECT DISTINCT date FROM ohlcv ORDER BY date").fetchall()]
    finally:
        con.close()


def stock_names(db=DB) -> dict[str, str]:
    """종목코드 → 종목명."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute("SELECT code, name FROM stock_names").fetchall()
    finally:
        con.close()
    return {c: n for c, n in rows}
