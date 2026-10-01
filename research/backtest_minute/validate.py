"""분봉 전용 월 walk-forward — 학습 누설 차단(M9). 선택·비용표는 일봉 재사용."""
from __future__ import annotations

import pandas as pd

from research.backtest_daily.stats import COSTS, cost_table
from research.backtest_daily.validate import select_best


def wf_monthly(trades, test_month):
    """학습: 진입일·청산일 모두 그 달 1일 미만. 우회 인자 없음."""
    cut = f"{test_month}01"
    return trades[(trades["entry_date"] < cut) & (trades["exit_date"] < cut)]


def walk_forward_monthly(trades, months, cands, key_col, cost=0.0035, min_n=30):
    """월별 학습→선택→당월 OOS. 선택 없거나 학습 평균 ≤0 이면 매수 안 함."""
    months_dict: dict = {}
    oos_parts = []
    cands = tuple(cands)
    for M in months:
        trn = wf_monthly(trades, M)
        cand, mean, n = select_best(trn, cands, key_col, cost=cost, min_n=min_n)
        rec: dict = {"train_n": int(len(trn))}
        if cand is None or not mean > 0:
            rec.update({"selected": None, "train_mean": mean, "train_cand_n": n,
                        "note": "매수 안 함"})
        else:
            oos = trades[(trades["cand"] == cand)
                         & (trades["entry_date"].str[:6] == str(M))].reset_index(drop=True)
            oos_parts.append(oos)
            st = cost_table(oos["exc"], oos[key_col], costs=COSTS, ref_cost=cost)
            rec.update({"selected": cand, "train_mean": mean, "train_cand_n": int(n),
                        "oos": st})
        months_dict[str(M)] = rec
    oos_df = pd.concat(oos_parts, ignore_index=True) if oos_parts else trades.iloc[0:0]
    return months_dict, oos_df
