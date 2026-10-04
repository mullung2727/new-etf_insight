"""미국 일봉·거시 로더 — etl/db/us_ohlcv.duckdb(ETF), us_macro.duckdb(FRED·지수). 읽기 전용."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
US_OHLCV_DB = ROOT / "etl" / "db" / "us_ohlcv.duckdb"
US_MACRO_DB = ROOT / "etl" / "db" / "us_macro.duckdb"


def load_etf_tr(tickers: list[str], db: Path = US_OHLCV_DB) -> pd.DataFrame:
    """ETF 일간 총수익 `(close_t + 당일배당) / 전일close - 1`.

    index 'YYYYMMDD'(요청 티커 날짜 합집합, 오름차순), columns=tickers 순서.
    티커별 첫 행·상장 전은 NaN. 테이블에 없는 티커는 ValueError.
    """
    tickers = list(tickers)
    con = duckdb.connect(str(db), read_only=True)
    try:
        have = {r[0] for r in con.execute("SELECT DISTINCT ticker FROM etf_ohlcv").fetchall()}
        for t in tickers:
            if t not in have:
                raise ValueError(f"unknown ticker: {t}")
        px = con.execute("SELECT date, ticker, close FROM etf_ohlcv").df()
        div = con.execute("SELECT ticker, date, amount FROM etf_dividends").df()
    finally:
        con.close()
    px = px[px["ticker"].isin(tickers)]
    dates = sorted(px["date"].unique().tolist())
    div_amt = div.set_index(["ticker", "date"])["amount"] if len(div) else None
    out = pd.DataFrame(index=dates, columns=tickers, dtype=float)
    for t in tickers:
        closes = px[px["ticker"] == t].sort_values("date").set_index("date")["close"].astype(float)
        if div_amt is not None and t in set(div["ticker"]):
            d = div_amt.loc[t].reindex(closes.index).fillna(0.0)
        else:
            d = pd.Series(0.0, index=closes.index)
        out[t] = ((closes + d) / closes.shift(1) - 1).reindex(dates)
    return out


def load_fred(series_id: str, as_of=None, db: Path = US_MACRO_DB) -> pd.Series:
    """FRED 관측일별 최신값. `as_of`가 있으면 그 시점에 보인 값으로.

    index 'YYYYMMDD' 오름차순, name=series_id. 없는 시리즈는 ValueError.
    """
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT date, value, fetched_at FROM fred_obs WHERE series_id = ? ORDER BY date, fetched_at",
            [series_id],
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise ValueError(f"unknown series: {series_id}")
    df = pd.DataFrame(rows, columns=["date", "value", "fetched_at"])
    if as_of is not None:
        cutoff = pd.to_datetime(as_of)
        df = df[pd.to_datetime(df["fetched_at"]) <= cutoff]
        if df.empty:
            raise ValueError(f"no obs for {series_id} as of {as_of}")
    latest = df.sort_values("fetched_at").groupby("date", sort=True).tail(1)
    s = latest.set_index("date")["value"].astype(float).sort_index()
    s.name = series_id
    return s


def load_index(ticker: str, db: Path = US_MACRO_DB) -> pd.Series:
    """index_ohlcv 종가. index 'YYYYMMDD', name=ticker. 없으면 ValueError."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT date, close FROM index_ohlcv WHERE ticker = ? ORDER BY date",
            [ticker],
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise ValueError(f"unknown index ticker: {ticker}")
    s = pd.Series({d: c for d, c in rows}, dtype=float).sort_index()
    s.index.name = None
    s.name = ticker
    return s


def load_first_release(series_id: str, db: Path = US_MACRO_DB) -> pd.DataFrame:
    """FRED 첫 공개값(ALFRED output_type=4).

    columns date·value·realtime_start·backfilled, date 오름차순.
    없는 시리즈는 ValueError.
    """
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            "SELECT date, value, realtime_start, backfilled FROM fred_first_release"
            " WHERE series_id = ? ORDER BY date",
            [series_id],
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise ValueError(f"unknown series: {series_id}")
    df = pd.DataFrame(rows, columns=["date", "value", "realtime_start", "backfilled"])
    df["value"] = df["value"].astype(float)
    df["backfilled"] = df["backfilled"].astype(bool)
    return df
