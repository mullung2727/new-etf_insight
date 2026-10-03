"""목표 비중 → 일별 포트폴리오 수익 (시장 무관, DB 안 읽음)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def run(weights: pd.DataFrame, returns: pd.DataFrame, cost: float) -> pd.DataFrame:
    """날짜별 목표 비중을 들고 간 포트폴리오의 일별 수익·회전율·비용.

    weights 행 T = T일 종가에 맞출 목표 비중. index·columns가 returns와
    정확히 같아야 한다. 합 < 1이면 나머지는 현금(수익 0).
    """
    if not weights.index.equals(returns.index):
        raise ValueError("weights index must equal returns index")
    if not weights.columns.equals(returns.columns):
        raise ValueError("weights columns must equal returns columns")
    if bool(weights.isna().any().any()):
        raise ValueError("weights contains NaN")
    if bool((weights < 0).any().any()):
        raise ValueError("weights contains negative")
    if bool((weights.sum(axis=1) > 1 + 1e-9).any()):
        raise ValueError("weights row sums above 1")

    assets = list(returns.columns)
    n = len(returns)
    r = returns.to_numpy(dtype=float)
    tgt = weights.to_numpy(dtype=float)

    rets = np.zeros(n)
    turnovers = np.zeros(n)
    costs = np.zeros(n)
    w_posts = np.zeros((n, len(assets)))
    w_prev = np.zeros(len(assets))
    for t in range(n):
        rt = r[t]
        r_gross = 0.0
        for j in range(len(assets)):
            v = rt[j]
            if np.isnan(v):
                if w_prev[j] == 0:
                    continue
                raise ValueError(f"NaN return for held asset {assets[j]!r}")
            r_gross += w_prev[j] * v
        if 1 + r_gross == 0:
            raise ValueError("1 + r_gross == 0")
        w_drift = np.zeros(len(assets))
        for j in range(len(assets)):
            v = 0.0 if np.isnan(rt[j]) else rt[j]
            w_drift[j] = w_prev[j] * (1 + v) / (1 + r_gross)
        target = tgt[t]
        if t == 0 or not np.allclose(target, tgt[t - 1]):
            turnover = float(np.abs(target - w_drift).sum())
            w_post = target.copy()
        else:
            # ponytail: 목표 불변이면 드리프트 유지(명세 "신호 발생 시 조정"). 매일 재조정이 필요해지면 옵션 추가
            turnover = 0.0
            w_post = w_drift
        cost_t = turnover * cost
        rets[t] = (1 + r_gross) * (1 - cost_t) - 1
        turnovers[t] = turnover
        costs[t] = cost_t
        w_posts[t] = w_post
        w_prev = w_post

    out = pd.DataFrame(index=returns.index)
    out["ret"] = rets
    out["turnover"] = turnovers
    out["cost"] = costs
    for j, a in enumerate(assets):
        out[f"w_{a}"] = w_posts[:, j]
    return out
