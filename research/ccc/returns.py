"""월말 종가·월수익."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

OHLCV_DB = Path(__file__).resolve().parents[2] / "etl" / "db" / "us_ohlcv.duckdb"


def ticker_key(t: str) -> str:
    """티커 정규화."""
    return t.upper().replace(".", "-").replace("/", "-")


def month_end_prices(db_path=OHLCV_DB, start: str = "20091201") -> pd.DataFrame:
    """티커·월별 마지막 종가."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        q = """
        SELECT ticker, ym, close, market FROM (
          SELECT ticker, CAST(SUBSTR(date, 1, 6) AS INTEGER) AS ym, close, market,
                 ROW_NUMBER() OVER (PARTITION BY ticker, SUBSTR(date, 1, 6) ORDER BY date DESC) AS rn
          FROM ohlcv WHERE date >= ?
        ) WHERE rn = 1
        """
        df = con.execute(q, [start]).fetchdf()
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=["ticker", "ym", "close", "market"])
    df["ym"] = df["ym"].astype(int)
    return df[["ticker", "ym", "close", "market"]].sort_values(["ticker", "ym"]).reset_index(drop=True)


def monthly_returns(px: pd.DataFrame) -> pd.DataFrame:
    """전월 대비 월수익 (연속 월만)."""
    cols = ["ticker", "ym", "ret", "prev_close", "market"]
    if px.empty:
        return pd.DataFrame(columns=cols)
    df = px.sort_values(["ticker", "ym"]).copy()
    df["prev_ym"] = df.groupby("ticker")["ym"].shift(1)
    df["prev_close_raw"] = df.groupby("ticker")["close"].shift(1)
    y = df["ym"] // 100
    m = df["ym"] % 100
    pm = m - 1
    py = y.where(pm >= 1, y - 1)
    pm = pm.where(pm >= 1, 12)
    ok = df["prev_ym"] == py * 100 + pm
    df["prev_close"] = df["prev_close_raw"].where(ok)
    df["ret"] = df["close"] / df["prev_close"] - 1
    df.loc[~ok, "ret"] = float("nan")
    df.loc[~(df["prev_close"] > 0), "ret"] = float("nan")
    return df[cols].reset_index(drop=True)
