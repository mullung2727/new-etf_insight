"""OAS 당일 추정 — T 이전 실제 OAS + HYG·IEI 로 예측한 하루 변화. 계수는 T 이전 데이터로만."""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TRAIN = 126
PROXY_TICKERS = ("HYG", "IEI")


def make_features(etf_logret: pd.DataFrame, vix_close: pd.Series | None = None) -> pd.DataFrame:
    """설명변수표. index=etf_logret.index(거래일), 컬럼 HYG·IEI·HYG_l1(+VIX·VIX_l1).

    HYG_l1 = HYG 1칸 shift(전날 값). vix_close 가 주어지면 log 종가를 거래일
    index 로 reindex 한 뒤 diff → VIX(거래일 기준 전날 VIX 대비),
    VIX_l1 = VIX 1칸 shift. 첫 행(HYG_l1·VIX·VIX_l1)은 NaN.
    """
    out = pd.DataFrame(
        {"HYG": etf_logret["HYG"], "IEI": etf_logret["IEI"]},
        index=etf_logret.index,
    )
    out["HYG_l1"] = out["HYG"].shift(1)
    if vix_close is not None:
        lv = np.log(vix_close.astype(float)).reindex(etf_logret.index)
        out["VIX"] = lv.diff()
        out["VIX_l1"] = out["VIX"].shift(1)
    return out


def daily_pairs(oas: pd.Series, etf_logret: pd.DataFrame, cols=PROXY_TICKERS) -> pd.DataFrame:
    """연속 거래일 하루 변화 학습 쌍. index=o_cur, 컬럼 d_bp·cols.

    연속한 두 관측 (o_prev, o_cur) 중 o_cur 가 o_prev 바로 다음 거래일인 것만.
    cols 중 하나라도 NaN 인 행은 제외.
    """
    cols = list(cols)
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
        r = etf.loc[cur, cols]
        if bool(r.isna().any()):
            continue
        d_bp = (float(oas.loc[cur]) - float(oas.loc[prev])) * 100
        rows.append((cur, d_bp, *[float(r[c]) for c in cols]))
    out = pd.DataFrame(rows, columns=["date", "d_bp", *cols])
    return out.set_index("date").rename_axis(None)


def fit_ols(pairs: pd.DataFrame, cols=PROXY_TICKERS) -> np.ndarray:
    """d_bp ~ 1 + cols 최소제곱. 반환 [b0, ...], 길이 1+len(cols)."""
    cols = list(cols)
    y = pairs["d_bp"].to_numpy(dtype=float)
    x = np.column_stack(
        [np.ones(len(pairs))] + [pairs[c].to_numpy(dtype=float) for c in cols]
    )
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    return np.asarray(coef, dtype=float)


def nowcast(oas: pd.Series, etf_logret: pd.DataFrame, dates, cols=PROXY_TICKERS) -> pd.DataFrame:
    """거래일별 당일 OAS 추정. T행은 관측일 < T 마지막 관측에서 출발, 계수는 index < T 쌍으로만.

    컬럼 base_date·base·oas(=est)·d10_bp·fitted·n_train. 관측 < T가 없으면 NaN 행.
    표본 < MIN_TRAIN 이면 est = base, fitted False.
    """
    cols = list(cols)
    oas = oas.sort_index()
    etf = etf_logret.sort_index()[cols]
    obs_dates = oas.index.to_numpy()
    vals = oas.to_numpy(dtype=float)
    cal = etf.index.to_numpy()
    feat = etf.to_numpy(dtype=float)
    pairs = daily_pairs(oas, etf_logret, cols)
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
            coef = [float(c) for c in fit_ols(pairs.iloc[sel], cols)]
            lo = int(np.searchsorted(cal, base_date, side="right"))
            hi = int(np.searchsorted(cal, t, side="right"))
            tot = 0.0
            for k in range(lo, hi):
                row = feat[k]
                if np.isnan(row).any():
                    continue
                step = coef[0]
                for j in range(len(cols)):
                    step += coef[j + 1] * row[j]
                tot += step
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


def fit_fixed(oas: pd.Series, etf_logret: pd.DataFrame, start, end, cols=PROXY_TICKERS) -> np.ndarray:
    """고정 계수 — daily_pairs 중 index 가 start 이상 end 이하인 쌍으로 fit_ols."""
    pairs = daily_pairs(oas, etf_logret, cols)
    sel = pairs[(pairs.index >= start) & (pairs.index <= end)]
    return fit_ols(sel, cols)


def pred_change_bp(etf_logret: pd.DataFrame, coef, cols=PROXY_TICKERS) -> pd.Series:
    """거래일별 예측 하루 변화(bp) = b0 + Σ b_k·X_k. cols 중 하나라도 NaN 이면 NaN."""
    cols = list(cols)
    b = [float(c) for c in coef]
    if not cols:
        return pd.Series(b[0], index=etf_logret.index)
    out = b[0]
    for j, c in enumerate(cols):
        out = out + b[j + 1] * etf_logret[c].astype(float)
    return out


def etf_signal(etf_logret, coef, anchor_date, anchor_value, dates, window=10, cols=PROXY_TICKERS) -> pd.DataFrame:
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
    p = pred_change_bp(etf_logret, coef, cols)
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
