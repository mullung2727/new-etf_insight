"""일봉 전용 통계 — 일/월 가중 평균·t, 비용표, 일별 집계."""
from __future__ import annotations

import math
import statistics

import numpy as np
import pandas as pd

COSTS = (0.006, 0.0035, 0.0023)


def weighted(values, keys) -> tuple[float, float, int, int]:
    """NaN 제외 → 키별 평균 → 평균. t = mean/(pstdev/sqrt(n_keys)).

    keys 가 날짜면 일 가중, 월(YYYYMM)이면 월 가중. 반환 (평균, t, n_keys, n).
    """
    v = np.asarray(values, dtype=float)
    mo = np.asarray(keys)
    keep = np.isfinite(v)
    v, mo = v[keep], mo[keep]
    n = int(v.size)
    if n == 0:
        return (math.nan, math.nan, 0, 0)
    gm = pd.Series(v).groupby(mo).mean().to_numpy()
    n_keys = int(gm.size)
    mean = float(gm.mean())
    if n_keys < 2:
        return (mean, math.nan, n_keys, n)
    sd = statistics.pstdev(gm)
    if sd == 0:
        return (mean, math.nan, n_keys, n)
    return (mean, mean / (sd / math.sqrt(n_keys)), n_keys, n)


def day_key(dates) -> list[str]:
    """'YYYYMMDD' 그대로 — weighted 와 함께 일 가중에 쓴다."""
    return [str(d) for d in dates]


def month_key(dates) -> list[str]:
    """'YYYYMMDD' → 앞 6자리 — weighted 와 함께 월 가중에 쓴다."""
    return [str(d)[:6] for d in dates]


def _jf(v):
    if v is None:
        return None
    f = float(v)
    return None if not math.isfinite(f) else f


def cost_table(exc, keys, costs=(0.006, 0.0035, 0.0023), ref_cost=0.0035) -> dict:
    """비용 수준별 가중 통계 + net(ref_cost) 중앙값·승률.

    반환 {"costs": {str(c): {"mean","t","n_months","n"}}, "med", "win"}.
    """
    exc = np.asarray(exc, dtype=float)
    keys = np.asarray(keys)
    out = {}
    for c in costs:
        mw = weighted(exc - c, keys)
        out[str(c)] = {"mean": _jf(mw[0]), "t": _jf(mw[1]),
                       "n_months": int(mw[2]), "n": int(mw[3])}
    net = exc - ref_cost
    fin = net[np.isfinite(net)]
    med = float(np.median(fin)) if fin.size else math.nan
    win = float(np.mean(fin > 0)) if fin.size else math.nan
    return {"costs": out, "med": _jf(med), "win": _jf(win)}


def _date_col(df: pd.DataFrame) -> str:
    if "date" in df.columns:
        return "date"
    if "sig_date" in df.columns:
        return "sig_date"
    raise KeyError("need 'date' column")


def day_daily_means(df: pd.DataFrame, col: str) -> pd.Series:
    return df.groupby(_date_col(df))[col].mean()


def day_weighted_mean(df: pd.DataFrame, col: str) -> float:
    if len(df) == 0:
        return float("nan")
    return float(day_daily_means(df, col).mean())


def day_median(df: pd.DataFrame, col: str) -> float:
    """날짜평균들의 중앙값 (날짜 1개=관측 1개)."""
    if len(df) == 0:
        return float("nan")
    return float(day_daily_means(df, col).median())


def day_win_rate(df: pd.DataFrame, col: str) -> float:
    """날짜별 (값>0 비율, NaN 제외) → 날짜 평균."""
    if len(df) == 0:
        return float("nan")
    g = df.groupby(_date_col(df))[col].apply(lambda s: (s[s.notna()] > 0).mean())
    return float(g.mean())


def day_tstat(df: pd.DataFrame, col: str) -> float:
    m = day_daily_means(df, col).to_numpy(float)
    m = m[np.isfinite(m)]
    if len(m) < 2:
        return float("nan")
    sd = m.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return float("nan")
    return float(m.mean() / sd * np.sqrt(len(m)))
