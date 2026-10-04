"""외부 제안 "TQQQ 방어전략" 재현 (D14) — 출처: 사용자 제공 외부 제안, 임계값 그대로.

해석 가정 (RESULTS_DEFENSE.md 맨 위에도 기록):
- 판단일 T 종가에 리밸런싱. 방어지표는 T-1까지 값 (백테스트 날짜로 자른 뒤 shift(1)).
- RV20 = QQQ 일간 단순수익 최근 20일 표준편차(ddof=1) x sqrt(252).
- 200MA·mom20 = QQQ 총수익 누적수준 기준. MA는 전체(1999~)로 계산 후 절단.
  mom20 = 수준[t]/수준[t-20] - 1 (20거래일 전 대비).
- VIX/VIX3M = us_macro index_ohlcv 종가 비율. VIX3M 결측이면 (b) 거짓.
- DFII10 20일 변화 = (최근 관측 - 20관측 전 관측) x 100 (bp).
  판단일 T에는 관측일 < T 마지막 관측 기준.
- 위험 슬리브 안 TQQQ = min(OAS 비중, fast 상한, persistent 상한).
  severe면 TQQQ·QQQ를 0.5배하고 BIL 0.5.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

RV20_FAST = 0.20
RATIO_FAST = 1.02
VIX_FAST = 30.0
MOM_PERSIST = -0.03
DFII_REAL_RATE_BP = 40.0
MOM_SEVERE = -0.10
RV20_SEVERE = 0.30


def indicators(qqq_level, qqq_ret, vix, vix3m, dfii10, dates) -> pd.DataFrame:
    """날짜별 방어지표를 T-1 값으로 정렬해 반환 (shift 적용 완료).

    컬럼 rv20·vix·vix_ratio·below200·mom20·dfii_d20_bp, index=dates.
    rv20·vix·vix_ratio·below200·mom20 = 전체 이력으로 원계열을 계산한 뒤
    dates로 자르고 1칸 shift. dfii_d20_bp = 관측일 < T 마지막 관측의
    20관측 변화(bp)라 shift 없이 이미 T-1 기준.
    """
    dates = list(dates)
    qqq_level = qqq_level.sort_index()
    qqq_ret = qqq_ret.sort_index()

    rv_raw = qqq_ret.rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252)
    rv20 = rv_raw.reindex(dates).shift(1)

    v = vix.reindex(dates).shift(1).astype(float)
    ratio = (vix.astype(float) / vix3m.astype(float)).reindex(dates).shift(1)

    ma = qqq_level.rolling(200, min_periods=200).mean()
    below200 = (qqq_level < ma).reindex(dates).shift(1).fillna(False).astype(bool)

    mom_raw = qqq_level / qqq_level.shift(20) - 1
    mom20 = mom_raw.reindex(dates).shift(1).astype(float)

    obs = dfii10.sort_index()
    o_dates = obs.index.to_numpy()
    o_vals = obs.to_numpy(dtype=float)
    d20 = np.full(len(obs), np.nan)
    if len(obs) > 20:
        d20[20:] = (o_vals[20:] - o_vals[:-20]) * 100.0
    dfii_vals = []
    for t in dates:
        j = int(np.searchsorted(o_dates, t, side="left") - 1)
        dfii_vals.append(d20[j] if j >= 0 else np.nan)

    return pd.DataFrame({
        "rv20": rv20.astype(float),
        "vix": v,
        "vix_ratio": ratio.astype(float),
        "below200": below200,
        "mom20": mom20,
        "dfii_d20_bp": pd.Series(dfii_vals, index=dates, dtype=float),
    }, index=dates)


def _count(mask) -> pd.Series:
    return mask.fillna(False).astype(int)


def fast_cap(ind: pd.DataFrame) -> pd.Series:
    """Fast stress 상한. (a) RV20≥20% (b) VIX/VIX3M≥1.02 (c) VIX≥30.

    0개→1.0, 1개→0.7, 2개 이상→0.5. NaN(지표 결측)은 미충족.
    """
    n = (_count(ind["rv20"] >= RV20_FAST)
         + _count(ind["vix_ratio"] >= RATIO_FAST)
         + _count(ind["vix"] >= VIX_FAST))
    cap = pd.Series(1.0, index=ind.index)
    cap[n == 1] = 0.7
    cap[n >= 2] = 0.5
    return cap


def persistent_cap(ind: pd.DataFrame, use_real_rate: bool = True) -> pd.Series:
    """Persistent bear 상한. below200·mom20≤-3%→0.5, +DFII10 20일 ≥+40bp→0.2."""
    base = (ind["below200"].fillna(False).astype(bool)
            & (ind["mom20"] <= MOM_PERSIST).fillna(False))
    cap = pd.Series(1.0, index=ind.index)
    cap[base] = 0.5
    if use_real_rate:
        cap[base & (ind["dfii_d20_bp"] >= DFII_REAL_RATE_BP).fillna(False)] = 0.2
    return cap


def severe_flag(ind: pd.DataFrame) -> pd.Series:
    """Severe bear 여부. below200·mom20≤-10%·RV20≥30%면 True."""
    out = (ind["below200"].fillna(False).astype(bool)
           & (ind["mom20"] <= MOM_SEVERE).fillna(False)
           & (ind["rv20"] >= RV20_SEVERE).fillna(False))
    return out.astype(bool)


def final_weights(w_oas_tqqq: pd.Series, ind: pd.DataFrame,
                  use_fast: bool = True, use_persistent: bool = True,
                  use_real_rate: bool = True, use_severe: bool = True) -> pd.DataFrame:
    """최종 목표비중. columns ["TQQQ", "QQQ", "BIL"], 행 합 1.

    위험 슬리브 TQQQ = min(w_oas, fast 상한, persistent 상한).
    severe면 TQQQ·QQQ를 0.5배하고 BIL 0.5. 끈 블록의 상한은 1.0,
    severe를 끄면 BIL 0. w_oas를 전부 1.0으로 넘기면 "OAS 없음" 변형.
    """
    if not w_oas_tqqq.index.equals(ind.index):
        raise ValueError("w_oas_tqqq and ind must share the same index")
    w_oas = w_oas_tqqq.astype(float)
    fc = fast_cap(ind) if use_fast else pd.Series(1.0, index=ind.index)
    if use_persistent:
        pc = persistent_cap(ind, use_real_rate=use_real_rate)
    else:
        pc = pd.Series(1.0, index=ind.index)
    risky = pd.concat([w_oas, fc, pc], axis=1).min(axis=1)
    if use_severe:
        sev = severe_flag(ind)
    else:
        sev = pd.Series(False, index=ind.index)
    sev = sev.fillna(False).astype(bool)
    tqqq = risky.where(~sev, risky * 0.5)
    qqq = (1.0 - risky).where(~sev, (1.0 - risky) * 0.5)
    bil = pd.Series(np.where(sev.to_numpy(dtype=bool), 0.5, 0.0), index=ind.index)
    return pd.DataFrame({"TQQQ": tqqq, "QQQ": qqq, "BIL": bil}, index=ind.index)
