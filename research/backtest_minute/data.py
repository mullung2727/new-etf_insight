"""분봉 전용 유니버스·분봉 로드 — 전일 정보만으로 D 후보 확정(M2), 읽기 전용(M3)."""
from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from research.backtest_daily.data import DB, load_px, market_dates
from research.backtest_daily.guards import halt_ok, jump_ok, liquidity
from research.backtest_daily.universe import listing_flags

MB = Path(__file__).resolve().parents[2] / "etl/db/minute_bars.duckdb"
OPEN_T, CLOSE_T = "090000", "153000"
FULL_SINCE = "20251201"


def universe(krx_db=DB, since=FULL_SINCE) -> pd.DataFrame:
    """KOSPI·KOSDAQ 전 종목. F 거래행마다 D=다음 시장일 1행. D 정보는 시가만."""
    md = market_dates(krx_db)
    px = load_px(krx_db)
    px = px[px["market"].isin(["KOSPI", "KOSDAQ"])].reset_index(drop=True)
    if not len(px) or len(md) < 2:
        return pd.DataFrame(columns=["date", "ticker", "market", "ms", "f_date",
                                     "pc", "ptv", "pcap", "pvol", "d_open",
                                     "ok_listing", "ok_halt", "ok_jump", "ok_liq"])
    nxt = {md[i]: (md[i + 1], i + 1) for i in range(len(md) - 1)}
    fdates = px["date"].astype(str).to_numpy()
    f = px[np.array([d in nxt for d in fdates])].copy()
    if not len(f):
        return pd.DataFrame(columns=["date", "ticker", "market", "ms", "f_date",
                                     "pc", "ptv", "pcap", "pvol", "d_open",
                                     "ok_listing", "ok_halt", "ok_jump", "ok_liq"])
    f["f_date"] = f["date"].astype(str).to_numpy()
    dn = [nxt[d] for d in f["f_date"]]
    f["date"] = [d for d, _ in dn]
    f["ms"] = [m for _, m in dn]
    f = f[f["date"].astype(str) >= str(since)].copy()
    if not len(f):
        return pd.DataFrame(columns=["date", "ticker", "market", "ms", "f_date",
                                     "pc", "ptv", "pcap", "pvol", "d_open",
                                     "ok_listing", "ok_halt", "ok_jump", "ok_liq"])
    f = f.rename(columns={"close": "pc", "trading_value": "ptv",
                          "market_cap": "pcap", "volume": "pvol"})
    o_map = {(t, d): o for t, d, o in zip(px["ticker"].astype(str),
                                          px["date"].astype(str),
                                          px["open"].astype(float))}
    f["d_open"] = [o_map.get((t, d), np.nan) for t, d in
                  zip(f["ticker"].astype(str), f["date"].astype(str))]
    f["date"] = f["date"].astype(str)
    f["ticker"] = f["ticker"].astype(str)
    f["ms"] = f["ms"].astype(int)
    # 전일 필터 플래그 (행은 버리지 않음)
    lf = listing_flags(krx_db)
    flag_map = dict(zip(lf["ticker"].astype(str), lf["flag"].astype(str))) \
        if len(lf) else {}
    f["ok_listing"] = [flag_map.get(t, "ok") == "ok" for t in f["ticker"]]
    hm = halt_ok(px, md)
    jm = jump_ok(px)
    px_key = list(zip(px["ticker"].astype(str), px["date"].astype(str)))
    h_map = dict(zip(px_key, hm.tolist() if hasattr(hm, "tolist") else list(hm)))
    j_map = dict(zip(px_key, jm.tolist() if hasattr(jm, "tolist") else list(jm)))
    f["ok_halt"] = [bool(h_map.get((t, d), False)) for t, d in
                    zip(f["ticker"], f["f_date"])]
    f["ok_jump"] = [bool(j_map.get((t, d), False)) for t, d in
                    zip(f["ticker"], f["f_date"])]
    need = set(zip(f["ticker"].astype(str), f["f_date"].astype(str)))
    liq_map: dict = {}
    for t, g in px.groupby("ticker", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        dates = g["date"].astype(str).tolist()
        t = str(t)
        for pos, d in enumerate(dates):
            if (t, d) in need:
                liq_map[(t, d)] = liquidity(g, pos) is None
    f["ok_liq"] = [bool(liq_map.get((t, d), False)) for t, d in
                  zip(f["ticker"], f["f_date"])]
    cols = ["date", "ticker", "market", "ms", "f_date", "pc", "ptv", "pcap",
            "pvol", "d_open", "ok_listing", "ok_halt", "ok_jump", "ok_liq"]
    return f[cols].sort_values(["date", "ticker"]).reset_index(drop=True)


def day_bars(keys, mb_db=MB) -> dict:
    """(date, ticker)별 정규장 봉 배열. 없으면 None. 읽기 전용."""
    keys = [(str(d), str(t)) for d, t in list(keys)]
    if not keys:
        return {}
    uniq = sorted(set(keys))
    kdf = pd.DataFrame(uniq, columns=["date", "ticker"])
    con = duckdb.connect(str(mb_db), read_only=True)
    try:
        con.register("keys_df", kdf)
        df = con.execute(
            f"""SELECT b.date, b.ticker, b.time, b.open, b.high, b.low, b.close, b.volume
                FROM minute_bars b JOIN keys_df USING (date, ticker)
                WHERE b.time BETWEEN '{OPEN_T}' AND '{CLOSE_T}'
                ORDER BY b.date, b.ticker, b.time"""
        ).df()
    finally:
        con.close()
    out: dict = {}
    if len(df):
        df["date"] = df["date"].astype(str)
        df["ticker"] = df["ticker"].astype(str)
        for (d, t), g in df.groupby(["date", "ticker"], sort=False):
            g = g.sort_values("time")
            out[(d, t)] = {
                "time": g["time"].astype(int).to_numpy(),
                "open": g["open"].astype(float).to_numpy(),
                "high": g["high"].astype(float).to_numpy(),
                "low": g["low"].astype(float).to_numpy(),
                "close": g["close"].astype(float).to_numpy(),
                "volume": g["volume"].astype(float).to_numpy(),
            }
    return {k: out.get(k) for k in keys}


def snapshot(bars, t) -> float:
    """t=90000 이고 첫 봉이 090000 이면 그 open, 그 외 time≤t 마지막 봉 close. 없으면 NaN."""
    if bars is None:
        return float("nan")
    try:
        ti = int(t)
        tm = np.asarray(bars["time"]).astype(int)
        op = np.asarray(bars["open"], dtype=float)
        cl = np.asarray(bars["close"], dtype=float)
    except (KeyError, TypeError, ValueError):
        return float("nan")
    if tm.size == 0:
        return float("nan")
    if ti == 90000:
        return float(op[0]) if tm[0] == 90000 else float("nan")
    j = int(np.searchsorted(tm, ti, side="right")) - 1
    if j < 0:
        return float("nan")
    return float(cl[j])


def coverage(keys, mb_db=MB) -> float:
    """분봉 있는 키 비율. 키 없으면 NaN."""
    keys = list(keys)
    if not keys:
        return float("nan")
    m = day_bars(keys, mb_db=mb_db)
    return float(np.mean([v is not None for v in m.values()]))
