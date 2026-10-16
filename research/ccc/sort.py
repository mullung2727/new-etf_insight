"""월별 횡단면·10분위·통계."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.ccc.panel import asof, ccc_table, shares_asof
from research.ccc.returns import ticker_key


def _ym_add(ym: int, d: int) -> tuple[int, int]:
    y, m = divmod(ym, 100)
    i = y * 12 + (m - 1) + d
    return (i // 12, i % 12 + 1)


def formation_window(ym: int) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """구성일·분기창."""
    y, m = divmod(ym, 100)
    d = pd.Timestamp(y, m, 1) - pd.Timedelta(days=1)
    ly, lm = _ym_add(ym, -6)
    hy, hm = _ym_add(ym, -4)
    lo = pd.Timestamp(ly, lm, 1)
    ny, nm = (hy + 1, 1) if hm == 12 else (hy, hm + 1)
    hi = pd.Timestamp(ny, nm, 1) - pd.Timedelta(days=1)
    return (d, lo, hi)


def cross_section(ym: int, facts: pd.DataFrame, rets: pd.DataFrame,
                  companies: pd.DataFrame, ind_map: dict, scheme: int = 48) -> pd.DataFrame:
    """한 달 표본."""
    cols = ["ym", "cik", "ticker", "qend", "ccc", "ccc_adj", "ind", "ret", "me", "market", "microcap", "size_q"]
    if facts.empty or rets.empty or companies.empty:
        return pd.DataFrame(columns=cols)
    d, lo, hi = formation_window(ym)
    pre = facts[facts["end"] >= lo - pd.Timedelta(days=400)]
    if pre.empty:
        return pd.DataFrame(columns=cols)
    f = asof(pre, d)
    if f.empty:
        return pd.DataFrame(columns=cols)
    ccc = ccc_table(f)
    if ccc.empty:
        return pd.DataFrame(columns=cols)
    ccc = ccc[(ccc["qend"] >= lo) & (ccc["qend"] <= hi)]
    if ccc.empty:
        return pd.DataFrame(columns=cols)
    ccc = ccc.sort_values("qend").drop_duplicates("cik", keep="last")
    comp = companies[["cik", "ticker", "sic"]].copy()
    comp = comp[comp["ticker"].notna() & comp["sic"].notna()].copy()
    if comp.empty:
        return pd.DataFrame(columns=cols)
    comp["sic"] = comp["sic"].astype(int)
    comp = comp[(comp["sic"] < 6000) | (comp["sic"] > 6999)]
    if comp.empty:
        return pd.DataFrame(columns=cols)
    comp["ticker"] = comp["ticker"].astype(str).map(ticker_key)
    comp["ind"] = comp["sic"].map(
        lambda s: (lambda v: None if v is None else (v[0] if isinstance(v, tuple) else v))(
            ind_map.get((scheme, int(s)))))
    comp = comp[comp["ind"].notna()]
    if comp.empty:
        return pd.DataFrame(columns=cols)
    m = ccc.merge(comp, on="cik", how="inner")
    if m.empty:
        return pd.DataFrame(columns=cols)
    sh = shares_asof(f)
    if sh.empty:
        return pd.DataFrame(columns=cols)
    rym = rets[rets["ym"] == ym].copy()
    if rym.empty:
        return pd.DataFrame(columns=cols)
    rym["ticker"] = rym["ticker"].astype(str).map(ticker_key)
    rym = rym.drop_duplicates("ticker", keep="last")
    all_comp = companies[["cik", "ticker"]].copy()
    all_comp = all_comp[all_comp["ticker"].notna()].copy()
    all_comp["ticker"] = all_comp["ticker"].astype(str).map(ticker_key)
    uni = all_comp.merge(sh.rename("shares"), left_on="cik", right_index=True, how="inner")
    uni = uni.merge(rym[["ticker", "prev_raw_close", "market"]], on="ticker", how="inner")
    uni["me_all"] = uni["prev_raw_close"] * uni["shares"]
    ny_all = uni[(uni["me_all"] > 0) & (uni["market"] == "NYSE")]["me_all"]
    if len(ny_all) > 0:
        qs = [float(ny_all.quantile(q)) for q in (0.2, 0.4, 0.6, 0.8)]
        if any(not np.isfinite(v) for v in qs):
            qs = None
    else:
        qs = None
    m = m.merge(sh.rename("shares"), left_on="cik", right_index=True, how="inner")
    m = m.merge(rym[["ticker", "ret", "prev_close", "prev_raw_close", "market"]], on="ticker", how="inner")
    if m.empty:
        return pd.DataFrame(columns=cols)
    m["me"] = m["prev_raw_close"] * m["shares"]
    m = m[(m["me"] > 0) & (m["equity"] > 0) & m["ret"].notna()]
    if m.empty:
        return pd.DataFrame(columns=cols)
    lo1, hi99 = m["ccc"].quantile(0.01), m["ccc"].quantile(0.99)
    m["ccc"] = m["ccc"].clip(lo1, hi99)
    m["ccc_adj"] = m["ccc"] - m.groupby("ind")["ccc"].transform("median")
    if qs is None:
        m["size_q"] = 0
        m["microcap"] = False
    else:
        m["size_q"] = np.searchsorted(np.asarray(qs), m["me"].to_numpy(float), side="right") + 1
        m["microcap"] = m["size_q"] == 1
    m["ym"] = ym
    m["microcap"] = m["microcap"].astype(bool)
    return m[cols].reset_index(drop=True)


def decile_returns(cs: pd.DataFrame, col: str, weight: str) -> pd.Series:
    """10분위 수익."""
    q = pd.qcut(cs[col], 10, labels=list(range(1, 11)))
    df = pd.DataFrame({"q": q, "ret": cs["ret"].to_numpy(float), "me": cs["me"].to_numpy(float)})
    if weight == "ew":
        s = df.groupby("q", observed=False)["ret"].mean()
    else:
        df["w"] = df["ret"] * df["me"]
        g = df.groupby("q", observed=False)
        s = g["w"].sum() / g["me"].sum()
        s = s.replace([float("inf"), float("-inf")], float("nan"))
    s.index = s.index.astype(int)
    return s.sort_index().rename(None)


def nw_tstat(y, x=None, lags: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """OLS + Newey-West t."""
    y = np.asarray(y, dtype=float).ravel()
    if x is None:
        X = np.ones((len(y), 1))
    else:
        xa = np.asarray(x, dtype=float)
        if xa.ndim == 1:
            xa = xa[:, None]
        X = np.column_stack([np.ones(len(y)), xa])
    ok = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    y, X = y[ok], X[ok]
    n, k = X.shape
    if n == 0 or n <= k:
        return (np.full(k, np.nan), np.full(k, np.nan))
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    try:
        XtX_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return (beta, np.full(k, np.nan))
    S = np.zeros((k, k))
    for t in range(n):
        xt = X[t][:, None]
        S += (e[t] ** 2) * (xt @ xt.T)
    for j in range(1, lags + 1):
        if j >= n:
            break
        w = 1 - j / (lags + 1)
        Gj = np.zeros((k, k))
        for t in range(j, n):
            Gj += e[t] * e[t - j] * (X[t][:, None] @ X[t - j][None, :])
        S += w * (Gj + Gj.T)
    V = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.maximum(np.diag(V), 0))
    t = np.where(se > 0, beta / np.where(se > 0, se, 1), np.nan)
    return (beta, t)


def spread_stats(spread: pd.Series, factors: pd.DataFrame) -> dict:
    """스프레드 평균·알파."""
    s = spread.dropna().astype(float)
    idx = s.index.intersection(factors.index)
    s = s.loc[idx]
    f = factors.loc[idx]
    b0, t0 = nw_tstat(s.to_numpy(), lags=6)
    b1, t1 = nw_tstat(s.to_numpy(), f[["Mkt-RF"]].to_numpy(), lags=6)
    b3, t3 = nw_tstat(s.to_numpy(), f[["Mkt-RF", "SMB", "HML"]].to_numpy(), lags=6)
    b5, t5 = nw_tstat(s.to_numpy(), f[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"]].to_numpy(), lags=6)
    return {"mean": float(b0[0]), "t": float(t0[0]),
            "capm_a": float(b1[0]), "capm_t": float(t1[0]),
            "ff3_a": float(b3[0]), "ff3_t": float(t3[0]),
            "ff5m_a": float(b5[0]), "ff5m_t": float(t5[0]), "n": int(len(s))}


def size_double_sort(cs: pd.DataFrame, col: str) -> pd.Series:
    """사이즈×CCC 이중정렬 스프레드."""
    out = {g: float("nan") for g in range(1, 6)}
    size = cs["size_q"]
    for g in range(1, 6):
        sub = cs[size == g]
        if len(sub) < 5:
            continue
        try:
            q = pd.qcut(sub[col], 5, labels=[1, 2, 3, 4, 5])
        except ValueError:
            continue
        m = sub.groupby(q, observed=True)["ret"].mean()
        if 1 in m.index and 5 in m.index:
            out[g] = float(m.loc[1] - m.loc[5])
    return pd.Series(out).sort_index().rename(None)
