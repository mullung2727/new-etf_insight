"""분봉 전용 전일 피처 결합·누설 검사 — F 피처를 D=F+1에만 붙인다."""
from __future__ import annotations

import numpy as np
import pandas as pd


def attach_prev(d, feat):
    """F 피처를 ms=f_ms+1 D행에만 inner merge. F종가 불일치·날짜 역전이면 AssertionError."""
    f = feat.copy()
    f["ms"] = f["f_ms"].astype(int) + 1
    m = d.merge(f.drop(columns=["f_ms"]), on=["ticker", "ms"], how="inner")
    if len(m):
        mism = ~np.isclose(m["f_close"].astype(float), m["pc"].astype(float),
                           rtol=1e-9, equal_nan=False)
        if float(np.mean(mism)) >= 0.001:
            raise AssertionError(
                f"룩어헤드 검사 실패: F일 종가와 전일 종가 불일치 {float(np.mean(mism)):.2%}")
        if "f_date" in m.columns and not bool((m["f_date"] < m["date"]).all()):
            raise AssertionError("룩어헤드 검사 실패: 피처 날짜가 매매일보다 늦거나 같다")
    return m.reset_index(drop=True)


def leak_report(d, cols, same_day_col="d_close", thresh=0.25) -> None:
    """조건 변수 × 당일 등락 spearman. |상관| > thresh 이면 AssertionError."""
    same_day = (pd.to_numeric(d[same_day_col], errors="coerce")
                / pd.to_numeric(d["pc"], errors="coerce") - 1)
    bad = []
    for c in cols:
        v = pd.to_numeric(d[c], errors="coerce")
        r = v.corr(same_day, method="spearman")
        if r is not None and np.isfinite(r) and abs(float(r)) > thresh:
            bad.append(c)
    if bad:
        raise AssertionError(f"당일 정보 의심 변수: {bad}")
