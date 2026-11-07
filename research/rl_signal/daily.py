"""전일까지 일봉 지표. 당일 값 금지 — 계산 뒤 다음 거래일로 shift."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from research.backtest_daily.adjust import adj_price, adj_returns, ma_n
from research.backtest_daily.data import DB

DAILY_COLS = ["prev_close", "ma20_gap", "ret5", "vol20"]


def load_etf_px(db=DB) -> pd.DataFrame:
    """etf_ohlcv (date, ticker, close) + ohlcv 기준 ms. read_only, (ticker, date) 정렬."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        px = con.execute(
            """
            WITH mkt AS (SELECT DISTINCT date FROM ohlcv),
                 m AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS ms FROM mkt)
            SELECT e.date, e.ticker, e.close, m.ms
            FROM etf_ohlcv e JOIN m USING (date)
            ORDER BY e.ticker, e.date
            """
        ).df()
    finally:
        con.close()
    return px.reset_index(drop=True)


def _shifted(px: pd.DataFrame, P: np.ndarray, r: np.ndarray) -> pd.DataFrame:
    """px((ticker, date) 정렬 가정) 행마다 전일까지 지표 → 다음 거래일 날짜에 붙임."""
    px = px.sort_values(["ticker", "date"]).reset_index(drop=True)
    tic = px["ticker"].to_numpy()
    ms = np.asarray(px["ms"])
    close = pd.to_numeric(px["close"], errors="coerce").to_numpy(dtype=float)
    P = np.asarray(P, dtype=float)
    r = np.asarray(r, dtype=float)
    n = len(px)
    ma = np.full(n, np.nan)
    for _, idx in px.groupby("ticker", sort=False).indices.items():
        ma[idx] = ma_n(ms[idx], P[idx], 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        ma_gap = P / ma - 1
    ma_gap[~(np.isfinite(ma) & (ma != 0) & np.isfinite(P))] = np.nan
    ret5 = np.full(n, np.nan)
    if n > 5:
        cont = (tic[5:] == tic[:-5]) & (ms[5:] - ms[:-5] == 5)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = P[5:] / P[:-5]
        ok = cont & np.isfinite(ratio)
        ret5[np.flatnonzero(ok) + 5] = ratio[ok] - 1
    vol = pd.Series(r).rolling(20).std(ddof=1).to_numpy(dtype=float, na_value=np.nan)
    out = pd.DataFrame({"date": px["date"].to_numpy(), "ticker": tic})
    prev = np.zeros(n, dtype=bool)
    prev[1:] = (tic[1:] == tic[:-1]) & (ms[1:] == ms[:-1] + 1)
    src = np.flatnonzero(prev) - 1
    for col, raw in (("prev_close", close), ("ma20_gap", ma_gap),
                     ("ret5", ret5), ("vol20", vol)):
        v = np.full(n, np.nan)
        v[prev] = raw[src]
        out[col] = v
    return out


def daily_table(px_stock: pd.DataFrame, px_etf: pd.DataFrame) -> pd.DataFrame:
    """(date, ticker) 마다 그날 **장 시작 전** 알 수 있는 값 = 전 거래일까지로 계산.

    prev_close = 전 거래일 원종가(보정 안 함 — 장중 가격과 같은 기준)
    ma20_gap  = 전일 보정가 / 전일까지 20일 보정가 평균 − 1   (adjust.ma_n)
    ret5      = 전일 보정가 / 6거래일 전 보정가 − 1
    vol20     = 전일까지 20개 보정 일수익 표준편차(ddof=1), 결측 섞이면 NaN
    결과 컬럼: date, ticker, prev_close, ma20_gap, ret5, vol20 (date = 적용일 = 다음 거래일).
    """
    frames = []
    if px_stock is not None and len(px_stock):
        s = px_stock.sort_values(["ticker", "date"]).reset_index(drop=True)
        r = adj_returns(s)
        frames.append(_shifted(s, adj_price(s["ticker"], r), r))
    if px_etf is not None and len(px_etf):
        # ponytail: ETF 는 분할 드묾, 분배락 미보정
        e = px_etf.sort_values(["ticker", "date"]).reset_index(drop=True)
        tic = e["ticker"].to_numpy()
        ms = np.asarray(e["ms"])
        close = pd.to_numeric(e["close"], errors="coerce").to_numpy(dtype=float)
        re = np.full(len(e), np.nan)
        if len(e) > 1:
            cont = (tic[1:] == tic[:-1]) & (ms[1:] == ms[:-1] + 1)
            with np.errstate(divide="ignore", invalid="ignore"):
                rr = close[1:] / close[:-1] - 1
            ok = cont & np.isfinite(rr)
            re[np.flatnonzero(ok) + 1] = rr[ok]
        frames.append(_shifted(e, close, re))
    if not frames:
        return pd.DataFrame(columns=["date", "ticker"] + DAILY_COLS)
    return (pd.concat(frames, ignore_index=True)
            .sort_values(["ticker", "date"]).reset_index(drop=True))


def for_day(table: pd.DataFrame, day: str) -> dict[str, dict]:
    """date == day 인 행 → {ticker: {col: float}}."""
    sub = table[table["date"] == day]
    return {t: dict(zip(DAILY_COLS, (float(v) for v in row)))
            for t, row in zip(sub["ticker"], sub[DAILY_COLS].to_numpy())}
