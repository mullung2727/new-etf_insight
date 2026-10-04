"""TQQQ·OAS 규칙 — 판단일 T 는 T 이전 OAS 관측값만 쓴다."""
from __future__ import annotations

import numpy as np
import pandas as pd

SPEC_WEIGHTS = {"A": 1.0, "B": 0.7, "C": 0.4, "D": 0.2, "E": 0.7}
BOLL_WINDOW = 126
BOLL_WIDTH = 1.0
BOLL_WEIGHTS = {"A": {"risk_off": 0.4, "normal": 1.0, "risk_on": 1.0},
                "B": {"risk_off": 0.7, "normal": 1.0, "risk_on": 1.0}}


def align_oas(oas: pd.Series, dates) -> pd.DataFrame:
    """거래일별 OAS 정렬. T행은 관측일 < T 중 마지막 관측 `i` 기준.

    컬럼 oas_date·oas·d10_bp(=(oas[i]-oas[i-10])*100)·mid·upper·lower(126개 평균±1σ, ddof=1).
    관측 < T가 없으면 그 행 전부 NaN.
    """
    oas = oas.sort_index()
    obs_dates = oas.index.to_numpy()
    vals = oas.to_numpy(dtype=float)
    dates = list(dates)
    rows = []
    for t in dates:
        i = int(np.searchsorted(obs_dates, t, side="left") - 1)
        if i < 0:
            rows.append((None, np.nan, np.nan, np.nan, np.nan, np.nan))
            continue
        d10 = (vals[i] - vals[i - 10]) * 100 if i >= 10 else np.nan
        if i >= BOLL_WINDOW - 1:
            win = vals[i - BOLL_WINDOW + 1:i + 1]
            mid = float(win.mean())
            sd = float(win.std(ddof=1))
            upper = mid + BOLL_WIDTH * sd
            lower = mid - BOLL_WIDTH * sd
        else:
            mid = upper = lower = np.nan
        rows.append((obs_dates[i], vals[i], d10, mid, upper, lower))
    return pd.DataFrame(rows, index=dates,
                        columns=["oas_date", "oas", "d10_bp", "mid", "upper", "lower"])


def spec_state(oas: float, d10_bp: float, prev: str) -> str:
    """명세 판정 1~5 순서 그대로. d10 NaN이면 비교 거짓, oas NaN이면 prev 유지."""
    if pd.isna(oas):
        return prev
    if prev == "E" and (oas < 4.5 or d10_bp <= -50):
        return "A"
    if oas >= 8 or (oas >= 6 and d10_bp <= 0):
        return "E"
    if prev == "E":
        return "E"
    if oas < 5:
        if d10_bp >= 150:
            return "D"
        if d10_bp >= 100:
            return "C"
        if d10_bp >= 50:
            return "B"
        return "A"
    return prev


def spec_states(aligned: pd.DataFrame) -> pd.Series:
    """첫 prev="A"로 행마다 spec_state 순차 적용."""
    states = []
    prev = "A"
    for _, row in aligned.iterrows():
        prev = spec_state(row["oas"], row["d10_bp"], prev)
        states.append(prev)
    return pd.Series(states, index=aligned.index)


def boll_regime(aligned: pd.DataFrame) -> pd.Series:
    """oas > upper → risk_off, oas < lower → risk_on, 그 외(밴드 NaN 포함) → normal."""
    def _reg(row) -> str:
        if row["oas"] > row["upper"]:
            return "risk_off"
        if row["oas"] < row["lower"]:
            return "risk_on"
        return "normal"
    return aligned.apply(_reg, axis=1)


def to_weights(tqqq_weight: pd.Series) -> pd.DataFrame:
    """TQQQ 비중 Series → columns ["TQQQ", "QQQ"] 목표비중표."""
    out = pd.DataFrame(index=tqqq_weight.index)
    out["TQQQ"] = tqqq_weight.astype(float)
    out["QQQ"] = 1.0 - out["TQQQ"]
    return out


def spec_weights(aligned: pd.DataFrame) -> pd.DataFrame:
    """SPEC_WEIGHTS로 상태 → TQQQ 비중 → 목표비중표."""
    return to_weights(spec_states(aligned).map(SPEC_WEIGHTS))


def boll_weights(aligned: pd.DataFrame, variant: str) -> pd.DataFrame:
    """BOLL_WEIGHTS[variant]로 국면 → TQQQ 비중 → 목표비중표."""
    return to_weights(boll_regime(aligned).map(BOLL_WEIGHTS[variant]))
