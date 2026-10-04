"""OAS 당일 추정 — T 이전 실제 OAS + HYG·IEI 로 예측한 하루 변화. 계수는 T 이전 데이터로만."""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TRAIN = 126
PROXY_TICKERS = ("HYG", "IEI")


def daily_pairs(oas: pd.Series, etf_logret: pd.DataFrame) -> pd.DataFrame:
    """연속 거래일 하루 변화 학습 쌍. index=o_cur, 컬럼 d_bp·HYG·IEI.

    연속한 두 관측 (o_prev, o_cur) 중 o_cur 가 o_prev 바로 다음 거래일인 것만.
    ETF 값 NaN 행은 제외.
    """
    oas = oas.sort_index()
    etf = etf_logret.sort_index()
    cal = etf.index.to_numpy()
    obs = oas.index.to_numpy()
    rows = []
    for k in range(1, len(obs)):
        prev, cur = obs[k - 1], obs[k]
        j = int(np.searchsorted(cal, prev, side="right"))
        if j >= len(cal) or cal[j] != cur:
            continue
        r = etf.loc[cur, list(PROXY_TICKERS)]
        if bool(r.isna().any()):
            continue
        d_bp = (float(oas.loc[cur]) - float(oas.loc[prev])) * 100
        rows.append((cur, d_bp, float(r["HYG"]), float(r["IEI"])))
    out = pd.DataFrame(rows, columns=["date", "d_bp", *PROXY_TICKERS])
    return out.set_index("date").rename_axis(None)


def fit_ols(pairs: pd.DataFrame) -> np.ndarray:
    """d_bp ~ 1 + HYG + IEI 최소제곱. 반환 [b0, b1, b2]."""
    y = pairs["d_bp"].to_numpy(dtype=float)
    x = np.column_stack([
        np.ones(len(pairs)),
        pairs["HYG"].to_numpy(dtype=float),
        pairs["IEI"].to_numpy(dtype=float),
    ])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    return np.asarray(coef, dtype=float)


def nowcast(oas: pd.Series, etf_logret: pd.DataFrame, dates) -> pd.DataFrame:
    """거래일별 당일 OAS 추정. T행은 관측일 < T 마지막 관측에서 출발, 계수는 index < T 쌍으로만.

    컬럼 base_date·base·oas(=est)·d10_bp·fitted·n_train. 관측 < T가 없으면 NaN 행.
    표본 < MIN_TRAIN 이면 est = base, fitted False.
    """
    oas = oas.sort_index()
    etf = etf_logret.sort_index()[list(PROXY_TICKERS)]
    obs_dates = oas.index.to_numpy()
    vals = oas.to_numpy(dtype=float)
    cal = etf.index.to_numpy()
    hyg = etf["HYG"].to_numpy(dtype=float)
    iei = etf["IEI"].to_numpy(dtype=float)
    pairs = daily_pairs(oas, etf_logret)
    pidx = pairs.index.to_numpy()
    dates = list(dates)
    rows = []
    for t in dates:
        i = int(np.searchsorted(obs_dates, t, side="left") - 1)
        if i < 0:
            rows.append((None, np.nan, np.nan, np.nan, False, 0))
            continue
        base_date = obs_dates[i]
        base = float(vals[i])
        sel = np.flatnonzero(pidx < t)
        n = len(sel)
        if n < MIN_TRAIN:
            est, fitted = base, False
        else:
            b0, b1, b2 = (float(c) for c in fit_ols(pairs.iloc[sel]))
            lo = int(np.searchsorted(cal, base_date, side="right"))
            hi = int(np.searchsorted(cal, t, side="right"))
            tot = 0.0
            for k in range(lo, hi):
                h, s = hyg[k], iei[k]
                if np.isnan(h) or np.isnan(s):
                    continue
                tot += b0 + b1 * h + b2 * s
            est, fitted = base + tot / 100, True
        d10 = (est - float(vals[i - 9])) * 100 if i - 9 >= 0 else np.nan
        rows.append((base_date, base, est, d10, fitted, n))
    out = pd.DataFrame(
        rows, index=dates,
        columns=["base_date", "base", "oas", "d10_bp", "fitted", "n_train"],
    )
    out["fitted"] = out["fitted"].astype(bool)
    out["n_train"] = out["n_train"].astype(int)
    return out


def oracle(oas: pd.Series, dates) -> pd.DataFrame:
    """ORACLE 정렬 — T에 관측일 ≤ T 마지막 실제값을 쓴다. 실매매 불가, 지연 제거 상한 비교용.

    컬럼 oas_date·oas·d10_bp(=(oas[j]-oas[j-10])*100).
    """
    oas = oas.sort_index()
    obs_dates = oas.index.to_numpy()
    vals = oas.to_numpy(dtype=float)
    dates = list(dates)
    rows = []
    for t in dates:
        j = int(np.searchsorted(obs_dates, t, side="right") - 1)
        if j < 0:
            rows.append((None, np.nan, np.nan))
            continue
        d10 = (vals[j] - vals[j - 10]) * 100 if j >= 10 else np.nan
        rows.append((obs_dates[j], float(vals[j]), d10))
    return pd.DataFrame(rows, index=dates, columns=["oas_date", "oas", "d10_bp"])


def fit_fixed(oas: pd.Series, etf_logret: pd.DataFrame, start, end) -> np.ndarray:
    """고정 계수 — daily_pairs 중 index 가 start 이상 end 이하인 쌍으로 fit_ols. 반환 [b0, b1, b2]."""
    pairs = daily_pairs(oas, etf_logret)
    sel = pairs[(pairs.index >= start) & (pairs.index <= end)]
    return fit_ols(sel)


def pred_change_bp(etf_logret: pd.DataFrame, coef) -> pd.Series:
    """거래일별 예측 하루 변화(bp) = b0 + b1·HYG + b2·IEI. HYG·IEI 중 NaN 이면 NaN."""
    b0, b1, b2 = (float(c) for c in coef)
    hyg = etf_logret["HYG"].astype(float)
    iei = etf_logret["IEI"].astype(float)
    return b0 + b1 * hyg + b2 * iei


def etf_signal(etf_logret, coef, anchor_date, anchor_value, dates, window=10) -> pd.DataFrame:
    """실 OAS 를 쓰지 않는 신호, 백테스트·실매매 같은 계산.

    p = pred_change_bp. T 의 d10_bp = T 를 포함한 최근 window 거래일 p 의 합
    (T 종가 시점에 T 의 ETF 수익은 이미 확정 → 당일 사용 허용, window 안에 NaN 있으면 NaN).
    수준 oas = anchor_value + (C_T − C_anchor)/100, C = p 의 누적합(NaN 은 0으로 누적).
    anchor_date 는 dates 안에 있어야 함(없으면 ValueError). anchor 이전도 같은 식으로 계산.
    반환 컬럼 oas·d10_bp·pred_bp, index = dates.
    """
    dates = list(dates)
    if anchor_date not in dates:
        raise ValueError(f"anchor_date {anchor_date} not in dates")
    p = pred_change_bp(etf_logret, coef)
    pred = p.reindex(dates)
    vals = pred.to_numpy(dtype=float)
    d10 = np.full(len(dates), np.nan)
    for i in range(len(dates)):
        if i + 1 < window:
            continue
        w = vals[i - window + 1:i + 1]
        if np.isnan(w).any():
            continue
        d10[i] = float(w.sum())
    fill = np.where(np.isnan(vals), 0.0, vals)
    cum = np.cumsum(fill)
    c_anchor = cum[dates.index(anchor_date)]
    oas_vals = float(anchor_value) + (cum - c_anchor) / 100.0
    return pd.DataFrame({"oas": oas_vals, "d10_bp": d10, "pred_bp": vals}, index=dates)
