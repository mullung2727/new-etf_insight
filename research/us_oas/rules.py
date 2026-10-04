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


def below_ma(level: pd.Series, window: int = 200) -> pd.Series:
    """level이 같은 날 포함 최근 window개 단순평균보다 작으면 True.

    level index는 오름차순 날짜. 평균을 못 내는 처음 window-1개는 False.
    (T 종가 판단에 T 종가 사용 — 당일 확정값이라 허용.)
    """
    ma = level.rolling(window, min_periods=window).mean()
    return (level < ma).astype(bool)


def hold_while_below(w: pd.Series, below: pd.Series) -> pd.Series:
    """200일선 아래에선 더 줄이는 건 허용, 늘리는 건 금지.

    날짜 순서대로 out[T] = min(w[T], out[T-1]) if below[T] else w[T].
    첫 날은 w[T]. 두 Series의 index가 같아야 한다(아니면 ValueError).
    """
    if not w.index.equals(below.index):
        raise ValueError("w and below must share the same index")
    vals = w.to_numpy(dtype=float)
    mask = below.fillna(False).astype(bool).to_numpy(dtype=bool)
    out = np.empty(len(vals), dtype=float)
    for i in range(len(vals)):
        if i == 0 or not mask[i]:
            out[i] = vals[i]
        elif vals[i] < out[i - 1]:
            out[i] = vals[i]
        else:
            out[i] = out[i - 1]
    return pd.Series(out, index=w.index)


def combine_weights(w_oas_tqqq: pd.Series, below: pd.Series, ma_tqqq: float, defense: str) -> pd.DataFrame:
    """추세 상한 + OAS 감축 조합. columns ["TQQQ", "QQQ", "BIL"], 행 합 1.

    t_ma = ma_tqqq if below else 1.0, TQQQ = min(w_oas_tqqq, t_ma).
    나머지 1 - TQQQ: below 이고 defense == "BIL" 이면 (1 - t_ma)은 BIL,
    남는 (t_ma - TQQQ)은 QQQ. 그 외는 전부 QQQ.
    OAS 끈 경우는 호출 측에서 w_oas_tqqq를 전부 1.0으로 넘긴다.
    """
    if defense not in ("QQQ", "BIL"):
        raise ValueError("defense must be 'QQQ' or 'BIL'")
    if not w_oas_tqqq.index.equals(below.index):
        raise ValueError("w_oas_tqqq and below must share the same index")
    mask = below.fillna(False).astype(bool).to_numpy(dtype=bool)
    w_oas = w_oas_tqqq.to_numpy(dtype=float)
    t_ma = np.where(mask, float(ma_tqqq), 1.0)
    tqqq = np.minimum(w_oas, t_ma)
    bil = np.where(mask & (defense == "BIL"), 1.0 - t_ma, 0.0)
    qqq = 1.0 - tqqq - bil
    return pd.DataFrame({"TQQQ": tqqq, "QQQ": qqq, "BIL": bil}, index=w_oas_tqqq.index)


def bil_overlay(below: pd.Series, bil_flag: pd.Series, ma_tqqq: float, share: float) -> pd.DataFrame:
    """200일선 아래 + 타이밍 조건일에 rest 일부를 BIL로. columns ["TQQQ", "QQQ", "BIL"], 행 합 1.

    TQQQ = ma_tqqq if below else 1.0, rest = 1 - TQQQ.
    below & bil_flag인 날: BIL = share·rest, QQQ = rest - BIL.
    그 외: BIL = 0, QQQ = rest. share는 0~1.
    """
    if not 0.0 <= share <= 1.0:
        raise ValueError("share must be within 0..1")
    if not below.index.equals(bil_flag.index):
        raise ValueError("below and bil_flag must share the same index")
    mask = below.fillna(False).astype(bool).to_numpy(dtype=bool)
    flag = bil_flag.fillna(False).astype(bool).to_numpy(dtype=bool)
    tqqq = np.where(mask, float(ma_tqqq), 1.0)
    rest = 1.0 - tqqq
    bil = np.where(mask & flag, float(share) * rest, 0.0)
    qqq = rest - bil
    return pd.DataFrame({"TQQQ": tqqq, "QQQ": qqq, "BIL": bil}, index=below.index)


def trend_state(level: pd.Series, window: int, reentry: str,
                confirm_days: int = 20, band: float = 0.03) -> pd.Series:
    """이동평균 추세 상태기계 → 그날 종가에 맞출 TQQQ 비중 (1.0 / 0.5 / 0.0).

    ma = level의 window 단순이동평균(당일 포함). ma NaN인 날은 1.0(보유).
    보유(1.0)·복귀 중(0.5)에서 level < ma면 즉시 0.0.
    이탈(0.0)에서 복귀: instant=당일 ma 위, confirm=연속 confirm_days일째,
    band=ma×(1+band) 이상, staged=ma 위 첫날 0.5 뒤 연속 confirm_days일째(진입일 포함) 1.0.
    아래로 가면 카운트 리셋. reentry가 넷 중 하나가 아니면 ValueError.
    """
    if reentry not in ("instant", "confirm", "band", "staged"):
        raise ValueError("reentry must be one of 'instant', 'confirm', 'band', 'staged'")
    ma = level.rolling(window, min_periods=window).mean()
    lv = level.to_numpy(dtype=float)
    mv = ma.to_numpy(dtype=float)
    out = np.empty(len(lv), dtype=float)
    state = 1.0
    count = 0
    for i in range(len(lv)):
        if np.isnan(mv[i]):
            state, count, out[i] = 1.0, 0, 1.0
            continue
        above = lv[i] >= mv[i]
        if state == 1.0:
            if above:
                out[i] = 1.0
            else:
                state, count, out[i] = 0.0, 0, 0.0
        elif state == 0.5:  # staged 복귀 중
            if not above:
                state, count, out[i] = 0.0, 0, 0.0
            else:
                count += 1
                if count >= confirm_days:
                    state, out[i] = 1.0, 1.0
                else:
                    out[i] = 0.5
        elif reentry == "instant":
            if above:
                state, out[i] = 1.0, 1.0
            else:
                out[i] = 0.0
        elif reentry == "confirm":
            if above:
                count += 1
                if count >= confirm_days:
                    state, out[i] = 1.0, 1.0
                else:
                    out[i] = 0.0
            else:
                count, out[i] = 0, 0.0
        elif reentry == "band":
            if lv[i] >= mv[i] * (1 + band):
                state, out[i] = 1.0, 1.0
            else:
                out[i] = 0.0
        else:  # staged
            if above:
                count = 1
                if count >= confirm_days:
                    state, out[i] = 1.0, 1.0
                else:
                    state, out[i] = 0.5, 0.5
            else:
                out[i] = 0.0
    return pd.Series(out, index=level.index)
