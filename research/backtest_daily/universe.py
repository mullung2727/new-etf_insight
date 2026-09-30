"""일봉 전용 신규상장 유니버스 — 신규상장 후보 + spac/pref/spac_origin/ok 판정."""
from __future__ import annotations

import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

DB = Path(__file__).resolve().parents[2] / "etl/db/krx_ohlcv.duckdb"
SPAC_RE = re.compile("스팩|기업인수목적")


def listing_flags(db=DB) -> pd.DataFrame:
    """신규상장 후보 전체(모든 flag). 쓰는 쪽에서 flag == 'ok'로 거른다.

    flag: spac(이름 매칭) > pref(티커 끝자리 ≠ 0) > spac_origin(시가 1900~2300원·
    60일 표준편차 < 0.03) > ok. 컬럼: ticker, name, market, d0, ms0, open0,
    close0, cap0, ret_day0, flag.
    """
    cols = ["ticker", "name", "market", "d0", "ms0", "open0", "close0",
            "cap0", "ret_day0", "flag"]
    con = duckdb.connect(str(db), read_only=True)
    try:
        base = con.execute(
            """
            WITH mkt AS (SELECT DISTINCT date FROM ohlcv),
                 m AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS ms FROM mkt),
                 first AS (SELECT ticker, MIN(date) AS d_first FROM ohlcv GROUP BY ticker),
                 d0 AS (SELECT ticker, MIN(date) AS d0 FROM ohlcv WHERE volume > 0 GROUP BY ticker)
            SELECT f.ticker, d.d0, o.market, m.ms AS ms0,
                   o.open AS open0, o.close AS close0,
                   o.market_cap / 1e8 AS cap0, n.name
            FROM first f
            JOIN d0 d USING (ticker)
            JOIN ohlcv o ON o.ticker = f.ticker AND o.date = d.d0
            JOIN m ON m.date = d.d0
            LEFT JOIN stock_names n ON n.code = f.ticker
            WHERE f.d_first > '20200110'
            """
        ).df()
        if base.empty:
            return pd.DataFrame(columns=cols)
        tickers = ",".join(f"'{t}'" for t in base["ticker"])
        closes = con.execute(
            f"""
            SELECT ticker, date, close FROM ohlcv
            WHERE volume > 0 AND open > 0 AND close > 0 AND ticker IN ({tickers})
            """
        ).df()
    finally:
        con.close()
    base["ret_day0"] = np.where(base["open0"] > 0,
                                base["close0"] / base["open0"] - 1, np.nan)
    base["name"] = base["name"].fillna("")
    is_spac = base["name"].str.contains(SPAC_RE)
    is_pref = ~base["ticker"].str.endswith("0")
    d0end = (pd.to_datetime(base["d0"], format="%Y%m%d")
             + pd.Timedelta(days=60)).dt.strftime("%Y%m%d")
    base["_d0end"] = d0end
    c = closes.merge(base[["ticker", "d0", "_d0end", "close0"]], on="ticker")
    c = c[(c["date"] >= c["d0"]) & (c["date"] <= c["_d0end"])]
    std = c.assign(q=c["close"] / c["close0"]).groupby("ticker")["q"].std(ddof=0)
    base = base.join(std.rename("_std"), on="ticker")
    is_origin = base["open0"].between(1900, 2300) & (base["_std"] < 0.03)
    base["flag"] = np.where(is_spac, "spac",
                            np.where(is_pref, "pref",
                                     np.where(is_origin, "spac_origin", "ok")))
    return base[cols].sort_values(["d0", "ticker"]).reset_index(drop=True)
