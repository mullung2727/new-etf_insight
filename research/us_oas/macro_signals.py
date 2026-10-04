"""리스크 후보 지표 — 판단일 T에는 T 이전에 공개된 값만 쓴다 (D18-2).

시점 규칙:
- 시장값(DGS10·DTB3·지수 종가): 관측일 < T (≒ 관측일 ≤ T−1 거래일).
- 첫 공개값(UNRATE): realtime_start < T 인 행만.
- ICNSA: T ≥ 20090601 이면 첫 공개값(realtime_start < T),
  T < 20090601 이면 fred_obs 현재값을 관측일 + 12일 < T 일 때만 쓰고
  approx=True (근사·사후값).
모든 함수는 판단일 목록 dates를 받아 dates 인덱스로 반환한다. 계산 불가분은
NaN (불리언 플래그는 False).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ICNSA_CUTOFF = "20090601"
CLAIMS_LAG_DAYS = 12
CURVE_WINDOW = 252
SAHM_MIN_MONTHS = 15  # m3[last] + 직전 12개 m3에 필요한 월 수
CLAIMS_MIN_WEEKS = 56  # 최근 4주 + 52주 전 4주


def curve(dgs10: pd.Series, dtb3: pd.Series, dates) -> pd.DataFrame:
    """장단기 금리차. spread=DGS10−DTB3(%p), 관측일 < T 마지막 값.

    inv_12m: 최근 252개 관측(거래일代理) spread 최솟값 < 0 이면 True.
    disinvert: inv_12m 이고 현재 spread > 0. spread NaN이면 둘 다 False.
    """
    dgs10 = dgs10.dropna().sort_index()
    dtb3 = dtb3.dropna().sort_index()
    obs_all = sorted(set(dgs10.index) | set(dtb3.index))
    if obs_all:
        hist = (dgs10.reindex(obs_all).ffill()
                - dtb3.reindex(obs_all).ffill()).dropna()
    else:
        hist = pd.Series(dtype=float)
    obs = hist.index.to_numpy()
    vals = hist.to_numpy(dtype=float)
    dates = list(dates)
    rows = []
    for t in dates:
        i = int(np.searchsorted(obs, t, side="left") - 1) if len(obs) else -1
        if i < 0:
            rows.append((np.nan, False, False))
            continue
        s = float(vals[i])
        win = vals[max(0, i - CURVE_WINDOW + 1):i + 1]
        inv = bool((win < 0).any())
        rows.append((s, inv, bool(inv and s > 0)))
    out = pd.DataFrame(rows, index=pd.Index(dates),
                       columns=["spread", "inv_12m", "disinvert"])
    out["inv_12m"] = out["inv_12m"].astype(bool)
    out["disinvert"] = out["disinvert"].astype(bool)
    return out


def sahm(unrate_first: pd.DataFrame, dates) -> pd.Series:
    """Sahm 지표. T에 realtime_start < T 인 월별 첫 공개 실업률만으로.

    m3 = u의 3개월 평균, sahm = m3[last] − min(m3[last−12 .. last−1]).
    공개된 월 15개 미만이면 NaN.
    """
    df = unrate_first.sort_values("date").reset_index(drop=True)
    rs = df["realtime_start"].to_numpy()
    vals = df["value"].to_numpy(dtype=float)
    out = []
    for t in list(dates):
        u = vals[rs < t]
        if len(u) < SAHM_MIN_MONTHS:
            out.append(np.nan)
            continue
        m3 = np.array([u[k - 2:k + 1].mean() for k in range(2, len(u))])
        out.append(float(m3[-1] - m3[-13:-1].min()))
    return pd.Series(out, index=pd.Index(list(dates)), dtype=float, name="sahm")


def _add_days(day: str, n: int) -> str:
    return (pd.Timestamp(day) + pd.Timedelta(days=n)).strftime("%Y%m%d")


def claims_yoy(icnsa_first: pd.DataFrame, icnsa_obs: pd.Series, dates) -> pd.DataFrame:
    """실업수당 청구 YoY. yoy = 최근 4주 합 / 52주 전 4주 합 − 1 (주간 위치 기준).

    T ≥ 20090601: realtime_start < T 첫 공개값. T < 20090601: 관측일+12일 < T 인
    fred_obs 현재값(사후 개정 포함 근사) → approx=True. 56주 미만이면 yoy NaN.
    """
    first = icnsa_first.sort_values("date").reset_index(drop=True)
    f_rs = first["realtime_start"].to_numpy() if len(first) else np.array([])
    f_vals = first["value"].to_numpy(dtype=float) if len(first) else np.array([])
    obs = icnsa_obs.dropna().sort_index()
    o_dates = obs.index.to_numpy()
    o_vals = obs.to_numpy(dtype=float)
    rows = []
    for t in list(dates):
        if t < ICNSA_CUTOFF:
            cutoff = _add_days(t, -CLAIMS_LAG_DAYS - 1)  # obs+12<T ⟺ obs≤T−13
            if len(o_dates):
                j = int(np.searchsorted(o_dates, cutoff, side="right") - 1)
                vis = o_vals[:j + 1] if j >= 0 else np.array([])
            else:
                vis = np.array([])
            approx = True
        else:
            vis = f_vals[f_rs < t]
            approx = False
        if len(vis) < CLAIMS_MIN_WEEKS:
            rows.append((np.nan, approx))
            continue
        recent, prior = vis[-4:].sum(), vis[-56:-52].sum()
        if prior == 0 or not np.isfinite(recent) or not np.isfinite(prior):
            rows.append((np.nan, approx))
        else:
            rows.append((float(recent / prior - 1), approx))
    out = pd.DataFrame(rows, index=pd.Index(list(dates)), columns=["yoy", "approx"])
    out["approx"] = out["approx"].astype(bool)
    return out


def pct_change_n(level: pd.Series, dates, n: int = 63) -> pd.Series:
    """관측일 < T 마지막 관측 기준 n관측(거래일) 변화율: level[i]/level[i−n] − 1.

    i−n < 0 이거나 분모·분자가 0·NaN·inf면 NaN.
    """
    lv = level.dropna().sort_index()
    obs = lv.index.to_numpy()
    vals = lv.to_numpy(dtype=float)
    out = []
    for t in list(dates):
        i = int(np.searchsorted(obs, t, side="left") - 1) if len(obs) else -1
        if i < n or vals[i - n] == 0 or not np.isfinite(vals[[i, i - n]]).all():
            out.append(np.nan)
        else:
            out.append(float(vals[i] / vals[i - n] - 1))
    return pd.Series(out, index=pd.Index(list(dates)), dtype=float)
