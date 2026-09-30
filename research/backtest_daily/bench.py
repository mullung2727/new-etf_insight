"""일봉 전용 사이즈중립 벤치 — 전날 시총 구간별 동일가중 지수."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

CAP_EDGES = [0, 1000, 3000, 10000, 50000, 1e12]  # 억
CAP_LABELS = ["<1천억", "1~3천억", "3천~1조", "1~5조", "5조+"]


def size_index(px: pd.DataFrame, r: np.ndarray) -> pd.DataFrame:
    """전날 시총 구간별 일별 동일가중 누적지수(1부터). 인덱스 ms, 컬럼 CAP_LABELS."""
    tic = px["ticker"].to_numpy()
    ms = np.asarray(px["ms"])
    cap = px["market_cap"].to_numpy(dtype=float)
    n = len(px)
    same = np.zeros(n, dtype=bool)
    same[1:] = tic[1:] == tic[:-1]
    pcap = np.full(n, np.nan)
    idx = np.flatnonzero(same)
    pcap[idx] = cap[idx - 1]
    d = pd.DataFrame({"ms": ms, "r": np.asarray(r, dtype=float), "cap": pcap / 1e8})
    d = d[np.isfinite(d["r"]) & np.isfinite(d["cap"]) & (d["cap"] > 0)]
    d["bkt"] = pd.cut(d["cap"], bins=CAP_EDGES, labels=CAP_LABELS, right=False)
    piv = d.pivot_table(index="ms", columns="bkt", values="r",
                        aggfunc="mean", observed=True)
    full = np.arange(int(ms.min()), int(ms.max()) + 1)
    piv = piv.reindex(full, fill_value=0.0).fillna(0.0)
    for c in CAP_LABELS:
        if c not in piv.columns:
            piv[c] = 0.0
    return (1 + piv[CAP_LABELS]).cumprod()


def cap_bucket(cap_eok) -> int | None:
    """시총(억) → 구간 번호. size_index의 pd.cut(right=False)과 같은 경계."""
    if cap_eok is None or not np.isfinite(cap_eok):
        return None
    if not 0 <= cap_eok < 1e12:
        return None
    return int(np.searchsorted(CAP_EDGES, cap_eok, side="right")) - 1


def entry_cap(df, pos) -> float:
    """진입 전날(ms-1) 시총. 그날 행이 없으면 진입일 시총."""
    m = np.asarray(df["ms"])
    cap = np.asarray(df["market_cap"], dtype=float)
    j = int(np.searchsorted(m, m[pos] - 1))
    if j < len(df) and m[j] == m[pos] - 1:
        return float(cap[j])
    return float(cap[pos])


def bench_return(idx_arr, ms_min, bkt, ms_from, ms_to) -> float:
    """시총구간 지수 수익률 idx[to]/idx[from]-1. 불가 시 NaN."""
    if bkt is None or bkt < 0 or bkt >= idx_arr.shape[1]:
        return math.nan
    base = idx_arr[ms_from - ms_min, bkt]
    cur = idx_arr[ms_to - ms_min, bkt]
    if not (np.isfinite(base) and base > 0) or not np.isfinite(cur):
        return math.nan
    return float(cur / base - 1)
