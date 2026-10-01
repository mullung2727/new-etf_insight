"""일봉 전용 검증 — walk-forward·후보 선택·placebo 분위. 학습 누설 차단 강제(D7)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .stats import COSTS, cost_table, weighted


def wf_train(trades, year):
    """학습 집합: 진입일 < Y0101 이고 청산일 < Y0101. 우회 인자 없음(D7)."""
    cut = f"{year}0101"
    return trades[(trades["entry_date"] < cut) & (trades["exit_date"] < cut)]


def select_best(trades, cands, key_col, cost=0.0035, min_n=30):
    """n≥min_n 후보 중 net 가중평균 최대. (cand, 평균, n), 없으면 None들."""
    best = (None, None, None)
    for cand in cands:
        s = trades[trades["cand"] == cand]
        if len(s) == 0:
            continue
        mw = weighted(s["exc"].to_numpy(float) - cost, s[key_col].to_numpy())
        if mw[3] >= min_n and math.isfinite(mw[0]) and (best[0] is None or mw[0] > best[1]):
            best = (cand, mw[0], mw[3])
    return best


def walk_forward(trades, years, cands, key_col, cost=0.0035, min_n=30):
    """연도별 학습→선택→당해 OOS. (연도별 dict, OOS 이어붙임 df).

    선택 없거나 학습 평균 ≤ 0 이면 그 해 매수 안 함. OOS = 같은 cand·진입연도 == Y.
    """
    years_dict: dict = {}
    oos_parts = []
    cands = tuple(cands)
    for Y in years:
        trn = wf_train(trades, Y)
        cand, mean, n = select_best(trn, cands, key_col, cost=cost, min_n=min_n)
        rec: dict = {"train_n": int(len(trn))}
        if cand is None or not mean > 0:
            rec.update({"selected": None, "train_mean": mean, "train_cand_n": n,
                        "note": "매수 안 함"})
        else:
            oos = trades[(trades["cand"] == cand)
                         & (trades["entry_date"].str[:4] == str(Y))].reset_index(drop=True)
            oos_parts.append(oos)
            st = cost_table(oos["exc"], oos[key_col], costs=COSTS, ref_cost=cost)
            rec.update({"selected": cand, "train_mean": mean, "train_cand_n": int(n),
                        "oos": st})
        years_dict[str(Y)] = rec
    oos_df = pd.concat(oos_parts, ignore_index=True) if oos_parts else trades.iloc[0:0]
    return years_dict, oos_df


def placebo_percentile(actual, draws) -> tuple[float, float]:
    """(draws 평균, 실제값 분위). pct = mean(draws ≤ actual)*100, NaN 제외."""
    d = np.asarray(draws, dtype=float)
    d = d[np.isfinite(d)]
    if d.size == 0:
        return (math.nan, math.nan)
    mean = float(np.mean(d))
    if actual is None or not math.isfinite(actual):
        return (mean, math.nan)
    return (mean, float(np.mean(d <= actual)) * 100)
