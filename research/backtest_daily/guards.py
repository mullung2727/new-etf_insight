"""일봉 전용 진입 가드 — 상한가·유동성·정지·점프·재무. 통과 None, 제외 시 사유 문자열."""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

FIN_DB = Path(__file__).resolve().parents[2] / "etl/db/financial_indicators.sqlite3"
AVAIL = {"11011": (1, "0401"), "11013": (0, "0601"), "11012": (0, "0901"), "11014": (0, "1201")}

LIMIT_UP = "limit_up"
LIMIT_UNDECIDABLE = "limit_undecidable"
LIQ_LOW = "liq_low"
LIQ_SHORT = "liq_short"


def limit_up_close(df, pos) -> str | None:
    """상한가 가드(종가 진입용). 진입일 종가/전일 종가 - 1 ≥ 29.5% → 제외."""
    m = np.asarray(df["ms"])
    cl = np.asarray(df["close"], dtype=float)
    if pos <= 0 or pos >= len(df):
        return LIMIT_UNDECIDABLE
    if m[pos] != m[pos - 1] + 1:
        return LIMIT_UNDECIDABLE
    prev = cl[pos - 1]
    if not np.isfinite(prev) or prev <= 0:
        return LIMIT_UNDECIDABLE
    cur = cl[pos]
    if not np.isfinite(cur) or cur <= 0:
        return LIMIT_UNDECIDABLE
    if cur / prev - 1 >= 0.295:
        return LIMIT_UP
    return None


def limit_up_open(df, t_pos) -> str | None:
    """상한가 가드(시가 진입용). T+1 시가 / T 종가 - 1 ≥ 29.5% → 제외. 호출 전 T+1 ms 연속 확인."""
    o = float(df["open"].iloc[t_pos + 1])
    c = float(df["close"].iloc[t_pos])
    if not np.isfinite(o) or not np.isfinite(c) or c <= 0:
        return LIMIT_UNDECIDABLE
    return LIMIT_UP if o / c - 1 >= 0.295 else None


def liquidity(df, pos, min_tv=1e9, window=20, min_rows=10) -> str | None:
    """유동성 가드. 진입일 포함 직전 window행 거래대금 평균 ≥ min_tv."""
    tv = np.asarray(df["trading_value"], dtype=float)
    w = tv[max(0, pos - (window - 1)):pos + 1]
    if w.size < min_rows:
        return LIQ_SHORT
    avg = float(np.nanmean(w))
    if not math.isfinite(avg) or avg < min_tv:
        return LIQ_LOW
    return None


def halt_ok(px: pd.DataFrame, market_dates: list[str], window=20) -> np.ndarray:
    """직전 window 시장일에 행 없음·volume=0 하나라도 있으면 False. 행별 마스크 반환."""
    n = len(px)
    out = np.zeros(n, bool)
    if n == 0:
        return out
    tids, _ = pd.factorize(px["ticker"].to_numpy())
    dmap = {d: i for i, d in enumerate(market_dates)}
    dates = px["date"].to_numpy()
    dids = np.array([dmap.get(d, -1) for d in dates], dtype=np.int64)
    vol = px["volume"].to_numpy(float)
    n_t, n_d = int(tids.max()) + 1, len(market_dates)
    clean = np.zeros((n_t, n_d), bool)
    valid = dids >= 0
    clean[tids[valid], dids[valid]] = vol[valid] > 0
    cs = np.cumsum(clean, axis=1, dtype=np.int32)
    ok = np.where(valid & (dids >= window))[0]
    if len(ok):
        t, d = tids[ok], dids[ok]
        s1 = cs[t, d - 1]
        s0 = np.zeros_like(s1)
        m = d - window - 1 >= 0
        s0[m] = cs[t[m], d[m] - window - 1]
        out[ok] = (s1 - s0) == window
    return out


def jump_ok(px: pd.DataFrame, th=0.31) -> np.ndarray:
    """|close[t]/close[t-1]-1| ≥ th 이면 False (종목 직전 행). 행별 마스크 반환."""
    n = len(px)
    if n == 0:
        return np.empty(0, bool)
    tk = px["ticker"].to_numpy()
    cl = px["close"].to_numpy(float)
    prev = np.full(n, np.nan)
    same = tk[1:] == tk[:-1]
    idx = np.where(same)[0] + 1
    prev[idx] = cl[idx - 1]
    ok_prev = np.isfinite(prev) & (prev > 0)
    excl = np.zeros(n, bool)
    excl[ok_prev] = np.abs(cl[ok_prev] / prev[ok_prev] - 1) >= th
    return ~excl


def fin_flags(T: pd.DataFrame, x2: int, fin_db=FIN_DB) -> pd.DataFrame:
    """신호 행에 재무 요건 해당 여부(fin_bad)·데이터 없음(fin_nodata) 을 붙인다.

    KRX 상폐 재무요건만 본다: 매출액(코스닥 30억/코스피 50억), 세전손실 > 자기자본 50%
    (코스닥만), 자본잠식률 50% 이상, 자기자본 10억 미만, 시총(코스닥 40억/코스피 50억).
    x2=1 상폐 요건 그대로, x2=2 문턱 두 배 보수적. 공시 가능일은 AVAIL 기준.
    필요 컬럼: ticker, ent_date, market, market_cap.
    """
    sq = sqlite3.connect(str(fin_db))
    a = pd.read_sql("""SELECT stock_code, bsns_year y, reprt_code rc, fs_div,
                              account_nm n, amount v FROM accounts
                       WHERE account_nm IN ('매출액','법인세차감전 순이익','자본총계','자본금')
                         AND reprt_code IN ('11011','11012','11013','11014')""", sq)
    sq.close()
    a = a.pivot_table(index=["stock_code", "y", "rc", "fs_div"], columns="n",
                      values="v", aggfunc="last").reset_index()
    a = a.sort_values("fs_div").drop_duplicates(["stock_code", "y", "rc"], keep="first")
    a["avail"] = [int(str(int(y) + AVAIL[r][0]) + AVAIL[r][1]) for y, r in zip(a["y"], a["rc"])]

    ann = (a[a["rc"] == "11011"][["stock_code", "avail", "매출액", "법인세차감전 순이익", "자본총계"]]
           .rename(columns={"자본총계": "eq_y", "법인세차감전 순이익": "pretax"})
           .dropna(subset=["avail"]).sort_values("avail"))
    bs = a[["stock_code", "avail", "자본총계", "자본금"]].dropna().sort_values("avail")

    t = T.copy()
    t["k"] = t["ent_date"].astype(int)
    t = t.sort_values("k")
    t = pd.merge_asof(t, ann, left_on="k", right_on="avail", left_by="ticker",
                      right_by="stock_code", direction="backward").drop(columns=["avail", "stock_code"])
    t = pd.merge_asof(t.sort_values("k"), bs, left_on="k", right_on="avail", left_by="ticker",
                      right_by="stock_code", direction="backward").drop(columns=["avail", "stock_code"])
    kq = t["market"] == "KOSDAQ"
    rev_min = np.where(kq, 30e8, 50e8) * x2
    cap_min = np.where(kq, 40e8, 50e8) * x2
    t["fin_nodata"] = t["자본총계"].isna() | t["매출액"].isna()
    t["fin_bad"] = ((t["매출액"] < rev_min)
                    | (kq & (-t["pretax"] > t["eq_y"].clip(lower=0) * 0.5 / x2))
                    | ((t["자본금"] - t["자본총계"]) / t["자본금"] >= 0.5 / x2)
                    | (t["자본총계"] < 10e8 * x2)
                    | (t["market_cap"].astype(float) < cap_min)).fillna(False)
    return t
