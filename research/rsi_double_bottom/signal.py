"""RSI 쌍바닥(60분봉) — 60분봉 생성·지표·상태머신."""
from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from research.backtest_minute.data import CLOSE_T, FULL_SINCE, MB, OPEN_T

RSI_LEN = 14
BB_LEN = 20
BB_MULT = 2.0
RSI_OS = 30.0
WATCH_BARS = 21
BODY_LEN = 20


def hourly_bars(mb_db=MB, since=FULL_SINCE, minutes=60) -> pd.DataFrame:
    """minute_bars → N분봉 집계. hb = 버킷 시작 시각 HHMM 정수
    (60분 → 900,…,1500 / 30분 → 900,930,…,1500. 153000 봉은
    마지막 버킷 1500에 합산)."""
    maxidx = (929 - 540) // minutes
    m = ("(CAST(substr(time, 1, 2) AS INT) * 60"
         " + CAST(substr(time, 3, 2) AS INT))")
    idx = f"LEAST(({m} - 540) // {minutes}, {maxidx})"
    start = f"(540 + ({idx}) * {minutes})"
    hb_expr = f"(({start}) // 60) * 100 + (({start}) % 60)"
    con = duckdb.connect(str(mb_db), read_only=True)
    try:
        df = con.execute(
            f"""SELECT ticker, date,
                      {hb_expr} AS hb,
                      arg_min(open, time) AS open,
                      max(high) AS high,
                      min(low) AS low,
                      arg_max(close, time) AS close,
                      sum(volume) AS volume,
                      max(time) AS last_time
               FROM minute_bars
               WHERE date >= ? AND time BETWEEN ? AND ?
               GROUP BY ticker, date, {hb_expr}
               ORDER BY ticker, date, hb""",
            [str(since), OPEN_T, CLOSE_T],
        ).df()
    finally:
        con.close()
    if len(df):
        df["ticker"] = df["ticker"].astype(str)
        df["date"] = df["date"].astype(str)
        df["hb"] = df["hb"].astype(int)
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        df["last_time"] = df["last_time"].astype(str)
    cols = ["ticker", "date", "hb", "open", "high", "low", "close",
            "volume", "last_time"]
    return df[cols].reset_index(drop=True)


def _rsi_wilder(close: pd.Series, n: int = RSI_LEN) -> pd.Series:
    """표준 Wilder RSI (TradingView ta.rsi/rma 시드).

    첫 평균 이득/손실 = 첫 n개 delta 단순평균, 이후 avg=(prev*(n-1)+cur)/n.
    위치 n부터 값, 그 전 NaN. 손실 0 → 100, 완전 횡보 → NaN.
    """
    vals = close.to_numpy(dtype=float)
    m = len(vals)
    rsi = np.full(m, np.nan)
    if m > n:
        delta = np.empty(m)
        delta[0] = np.nan
        delta[1:] = vals[1:] - vals[:-1]
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        ag = float(np.mean(gain[1:n + 1]))
        al = float(np.mean(loss[1:n + 1]))
        if al == 0:
            rsi[n] = 100.0 if ag != 0 else np.nan
        else:
            rsi[n] = 100 - 100 / (1 + ag / al)
        for i in range(n + 1, m):
            ag = (ag * (n - 1) + gain[i]) / n
            al = (al * (n - 1) + loss[i]) / n
            if al == 0:
                rsi[i] = 100.0 if ag != 0 else np.nan
            else:
                rsi[i] = 100 - 100 / (1 + ag / al)
    return pd.Series(rsi, index=close.index, dtype=float)


def add_indicators(df: pd.DataFrame, breaks: set | None = None) -> pd.DataFrame:
    """RSI·BB·캔들 패턴 열 추가. breaks={(ticker, date)} 날짜 첫 봉부터 새 seg."""
    out = df.sort_values(["ticker", "date", "hb"],
                         kind="mergesort").reset_index(drop=True)
    out["ticker"] = out["ticker"].astype(str)
    out["date"] = out["date"].astype(str)
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].astype(float)
    brk = {(str(t), str(d)) for t, d in (breaks or set())}
    is_brk = pd.Series([(t, d) in brk for t, d in
                        zip(out["ticker"], out["date"])], index=out.index)
    out["seg"] = is_brk.groupby(out["ticker"], sort=False).cumsum()
    g = out.groupby(["ticker", "seg"], sort=False)
    op = out["open"]
    cl = out["close"]
    out["bull"] = cl > op
    out["bear"] = cl < op
    bear1 = g["bear"].shift(1).fillna(False).astype(bool)
    bear2 = g["bear"].shift(2).fillna(False).astype(bool)
    bull1 = g["bull"].shift(1).fillna(False).astype(bool)
    op1 = g["open"].shift(1)
    cl1 = g["close"].shift(1)
    out["body"] = (cl - op).abs()
    out["body_avg20"] = g["body"].transform(
        lambda s: s.shift(1).rolling(BODY_LEN, min_periods=BODY_LEN).mean())
    big = out["body"] > out["body_avg20"]
    out["bull_engulf"] = bear1 & out["bull"] & (op <= cl1) & (cl >= op1) & big
    out["bear_engulf"] = bull1 & out["bear"] & (op >= cl1) & (cl <= op1) & big
    out["three_bear"] = out["bear"] & bear1 & bear2
    out["rsi14"] = g["close"].transform(_rsi_wilder)
    out["bb_mid"] = g["close"].transform(lambda s: s.rolling(BB_LEN).mean())
    out["bb_sd"] = g["close"].transform(lambda s: s.rolling(BB_LEN).std(ddof=0))
    out["bb_lo"] = out["bb_mid"] - BB_MULT * out["bb_sd"]
    out["bb_up"] = out["bb_mid"] + BB_MULT * out["bb_sd"]
    return out


def _prep(g: pd.DataFrame) -> dict:
    g = g.sort_values(["date", "hb"], kind="mergesort").reset_index(drop=True)
    return {
        "ticker": str(g["ticker"].iloc[0]) if len(g) else "",
        "date": g["date"].astype(str).to_numpy(),
        "hb": g["hb"].to_numpy(),
        "last_time": g["last_time"].astype(str).to_numpy(),
        "close": g["close"].to_numpy(float),
        "low": g["low"].to_numpy(float),
        "rsi": g["rsi14"].to_numpy(float),
        "bb_lo": g["bb_lo"].to_numpy(float),
        "bull_eng": np.asarray(g["bull_engulf"].fillna(False), dtype=bool),
        "bear_eng": np.asarray(g["bear_engulf"].fillna(False), dtype=bool),
        "three_bear": np.asarray(g["three_bear"].fillna(False), dtype=bool),
        "n": len(g),
    }


def find_entries(g: pd.DataFrame) -> list[dict]:
    """한 ticker 한 seg에서 RSI 쌍바닥 진입 신호. IDLE→WATCH→FIRST_REV."""
    p = _prep(g)
    n = p["n"]
    sigs: list[dict] = []
    state = "IDLE"
    setup = -1
    first_low = np.nan
    rev1 = -1
    for i in range(n):
        if state == "IDLE":
            if (p["rsi"][i] < RSI_OS and (p["bear_eng"][i] or p["three_bear"][i])
                    and p["close"][i] < p["bb_lo"][i]):
                state, setup, first_low = "WATCH", i, p["low"][i]
        elif state == "WATCH":
            if p["low"][i] < p["bb_lo"][i]:
                first_low, setup = min(first_low, p["low"][i]), i
            if p["bull_eng"][i]:
                state, rev1 = "FIRST_REV", i
            elif i - setup > WATCH_BARS:
                state = "IDLE"
        else:  # FIRST_REV
            if p["low"][i] < p["bb_lo"][i]:
                state, setup, first_low = "WATCH", i, p["low"][i]
            elif p["bull_eng"][i]:
                if i > rev1 + 1:
                    rng = np.arange(rev1 + 1, i + 1)
                    j = int(rng[len(rng) - 1 - np.argmin(p["low"][rng][::-1])])
                    if j in (i - 1, i) and p["low"][j] >= p["bb_lo"][j]:
                        sigs.append({
                            "ticker": p["ticker"],
                            "sig_date": p["date"][i],
                            "sig_hb": int(p["hb"][i]),
                            "sig_last_time": p["last_time"][i],
                            "sig_close": float(p["close"][i]),
                            "sl": float(p["low"][j]),
                            "first_low": float(first_low),
                            "bottom2_date": p["date"][j],
                            "bottom2_hb": int(p["hb"][j]),
                            "setup_date": p["date"][setup],
                        })
                        state = "IDLE"
            elif i - setup > WATCH_BARS:
                state = "IDLE"
    return sigs


def find_naive(g: pd.DataFrame) -> list[dict]:
    """rsi14 30 상향→하향 교차 봉마다 신호. dict 형태는 find_entries 와 동일."""
    p = _prep(g)
    sigs: list[dict] = []
    for i in range(1, p["n"]):
        if p["rsi"][i] < RSI_OS <= p["rsi"][i - 1]:
            sigs.append({
                "ticker": p["ticker"],
                "sig_date": p["date"][i],
                "sig_hb": int(p["hb"][i]),
                "sig_last_time": p["last_time"][i],
                "sig_close": float(p["close"][i]),
                "sl": float(p["low"][i]),
                "first_low": float(p["low"][i]),
                "bottom2_date": p["date"][i],
                "bottom2_hb": int(p["hb"][i]),
                "setup_date": p["date"][i],
            })
    return sigs
