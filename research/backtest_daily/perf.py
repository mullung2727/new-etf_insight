"""일별 수익열 성과 지표 — CAGR·MDD·Sharpe 등 (시장 무관)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

PERIODS = 252


def _check(ret: pd.Series) -> None:
    if bool(ret.isna().any()):
        raise ValueError("ret contains NaN")


def equity(ret: pd.Series) -> pd.Series:
    _check(ret)
    return (1 + ret).cumprod()


def cagr(ret: pd.Series, periods: int = PERIODS) -> float:
    _check(ret)
    if len(ret) == 0:
        return float("nan")
    return float(equity(ret).iloc[-1] ** (periods / len(ret)) - 1)


def vol(ret: pd.Series, periods: int = PERIODS) -> float:
    _check(ret)
    return float(ret.std(ddof=1) * math.sqrt(periods))


def sharpe(ret: pd.Series, rf: pd.Series | None = None, periods: int = PERIODS) -> float:
    _check(ret)
    ex = ret - rf if rf is not None else ret
    sd = ex.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(ex.mean() / sd * math.sqrt(periods))


def max_drawdown(ret: pd.Series) -> float:
    _check(ret)
    if len(ret) == 0:
        return float("nan")
    e = (1 + ret).cumprod()
    peak = e.cummax().clip(lower=1.0)
    return float(((e / peak) - 1).min())


def calmar(ret: pd.Series, periods: int = PERIODS) -> float:
    mdd = max_drawdown(ret)
    if mdd == 0:
        return float("nan")
    return float(cagr(ret, periods) / abs(mdd))


def yearly(ret: pd.Series) -> pd.Series:
    _check(ret)
    years = [str(i)[:4] for i in ret.index]
    return (1 + ret).groupby(years).prod() - 1


def drawdowns(ret: pd.Series, n: int = 5) -> pd.DataFrame:
    """겹치지 않는 낙폭 구간을 깊이순 상위 n개. 컬럼 peak·trough·recovery·depth·days."""
    _check(ret)
    cols = ["peak", "trough", "recovery", "depth", "days"]
    if len(ret) == 0:
        return pd.DataFrame(columns=cols)
    e = (1 + ret).cumprod()
    dates = list(e.index)
    vals = e.to_numpy(dtype=float)
    m = len(vals)
    episodes: list[tuple[int, int, int | None, float, int]] = []
    peak_val = 1.0
    peak_pos = -1  # -1 = 시작 자산 1.0 (첫 날짜 전)
    i = 0
    while i < m:
        if vals[i] >= peak_val:
            peak_val = vals[i]
            peak_pos = i
            i += 1
            continue
        trough_pos = i
        j = i
        rec: int | None = None
        while j < m:
            if vals[j] < vals[trough_pos]:
                trough_pos = j
            if vals[j] >= peak_val:
                rec = j
                break
            j += 1
        depth = float(vals[trough_pos] / peak_val - 1)
        if rec is None:
            episodes.append((peak_pos, trough_pos, None, depth, (m - 1) - peak_pos))
            break
        episodes.append((peak_pos, trough_pos, rec, depth, rec - peak_pos))
        peak_val = vals[rec]
        peak_pos = rec
        i = rec + 1
    episodes.sort(key=lambda ep: ep[3])
    rows = [{
        "peak": None if pp == -1 else dates[pp],
        "trough": dates[tp],
        "recovery": None if rec is None else dates[rec],
        "depth": depth,
        "days": days,
    } for (pp, tp, rec, depth, days) in episodes[:n]]
    return pd.DataFrame(rows, columns=cols)


def trades_per_year(turnover: pd.Series, periods: int = PERIODS) -> float:
    if len(turnover) == 0:
        return float("nan")
    return float((turnover > 0).sum() / (len(turnover) / periods))


def summary(ret: pd.Series, rf: pd.Series | None = None, periods: int = PERIODS) -> dict:
    return {
        "cagr": cagr(ret, periods),
        "vol": vol(ret, periods),
        "sharpe": sharpe(ret, rf, periods),
        "mdd": max_drawdown(ret),
        "calmar": calmar(ret, periods),
        "final": float(equity(ret).iloc[-1]) if len(ret) else float("nan"),
    }
