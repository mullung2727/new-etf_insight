"""일봉 전용 이벤트 경로 — D0 기준 누적·초과 행렬. 상폐 후 마지막 값 고정(D2)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .bench import cap_bucket, size_index


def _blocks(tic: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """종목 연속 블록의 시작/끝 행번호."""
    n = len(tic)
    ch = np.flatnonzero(tic[1:] != tic[:-1]) + 1 if n else np.empty(0, int)
    starts = np.concatenate(([0], ch)).astype(int)
    ends = np.concatenate((ch, [n])).astype(int)
    return starts, ends


def event_paths(px: pd.DataFrame, r: np.ndarray, uni: pd.DataFrame,
                K: int = 500) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """uni 각 종목 D0 종가 기준 k=0..K 누적(R)·초과(X)·벤치누적(B) 행렬."""
    idx = size_index(px, r)
    ms_min, ms_max = int(idx.index.min()), int(idx.index.max())
    arr = idx.to_numpy()
    tic = px["ticker"].to_numpy()
    ms = np.asarray(px["ms"])
    cap = px["market_cap"].to_numpy(dtype=float)
    rr = np.asarray(r, dtype=float)
    starts, ends = _blocks(tic)
    pos = {t: i for i, t in enumerate(tic[starts])} if len(px) else {}
    nu = len(uni)
    R = np.full((nu, K + 1), np.nan)
    B = np.full((nu, K + 1), np.nan)
    ut = uni["ticker"].to_numpy()
    ums0 = np.asarray(uni["ms0"])
    ucap0 = uni["cap0"].to_numpy(dtype=float)
    for j in range(nu):
        t, ms0 = ut[j], int(ums0[j])
        bi = pos.get(t)
        if bi is None:
            tm = tr = tc = np.empty(0)
        else:
            tm, tr = ms[starts[bi]:ends[bi]], rr[starts[bi]:ends[bi]]
            tc = cap[starts[bi]:ends[bi]]
        bc = np.nan
        if tm.size:
            p = int(np.searchsorted(tm, ms0 - 1))
            if p < tm.size and tm[p] == ms0 - 1 and tc[p] > 0:
                bc = tc[p] / 1e8
        if not np.isfinite(bc):
            bc = ucap0[j]
        bkt = cap_bucket(bc)
        last = tm[-1] if tm.size else -1
        base = arr[ms0 - ms_min, bkt] if bkt is not None else np.nan
        lev, miss, dead = 1.0, 0, False
        for k in range(K + 1):
            m = ms0 + k
            if m > ms_max:
                break
            if not dead:
                if m > last:
                    dead = True
                else:
                    p = int(np.searchsorted(tm, m))
                    if p < tm.size and tm[p] == m:
                        miss = 0
                        # D0 종가 기준: k=0 은 기준점이라 그날 수익을 곱하지 않음
                        if k >= 1 and np.isfinite(tr[p]):
                            lev *= 1 + tr[p]
                    else:
                        miss += 1
                        if miss >= 20:
                            dead = True
            # 상폐·장기정지 후 NaN 이면 생존편향 → 마지막 값 고정 (D17)
            R[j, k] = lev - 1
            if bkt is not None and np.isfinite(base) and base > 0:
                v = arr[m - ms_min, bkt]
                B[j, k] = v / base - 1 if np.isfinite(v) else np.nan
    with np.errstate(divide="ignore", invalid="ignore"):
        X = (1 + R) / (1 + B) - 1
    return R, X, B
