"""사후 최적 정답지. 상태 (칸, 보유 여부, 남은 진입 횟수) 동적계획법."""

from __future__ import annotations

import numpy as np

from .costs import buy_cost, sell_cost

HOLD, BUY, SELL = 0, 1, 2
K_MAX = 5


def step_rewards(ask1, bid1, code: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """칸별 로그 보상 조각 (전부 길이 n). 가치 = 청산 가능액(매수1호가 기준).

    hold_r[t] = log(bid1[t+1]/bid1[t]) (t=0..n-2, 보유 중 t→t+1; 마지막 원소 0.0 미사용)
    buy_r[t] = log(bid1[t]/ask1[t]) − log(1 + buy_cost(code)) (t 칸 매수 즉시 평가)
    sell_r[t] = log(1 − sell_cost(code)) (t 칸 bid1 에 매도)
    """
    ask = np.asarray(ask1, dtype=np.float64)
    bid = np.asarray(bid1, dtype=np.float64)
    n = len(ask)
    hold_r = np.zeros(n)
    hold_r[:-1] = np.log(bid[1:] / bid[:-1])
    buy_r = np.log(bid / ask) - np.log(1.0 + buy_cost(code))
    sell_r = np.full(n, np.log(1.0 - sell_cost(code)))
    return hold_r, buy_r, sell_r


def solve(ask1, bid1, code: str, k_max: int = K_MAX) -> dict:
    """동적계획법. 행동 뒤 보유면 t→t+1 에 hold_r[t] 추가.

    마지막 칸: pos=1 이면 SELL 강제(HOLD -inf), pos=0 이면 HOLD 강제(BUY 금지).
    동률은 HOLD > SELL > BUY 순. 반환 q/action/pos/total/trades.
    """
    ask = np.asarray(ask1, dtype=np.float64)
    bid = np.asarray(bid1, dtype=np.float64)
    n = len(ask)
    hold_r, buy_r, sell_r = step_rewards(ask, bid, code)
    nk = k_max + 1
    q = np.full((n, 2, nk, 3), -np.inf)
    v = np.zeros((n + 1, 2, nk))
    for t in range(n - 1, -1, -1):
        last = t == n - 1
        for k in range(nk):
            q[t, 0, k, HOLD] = v[t + 1, 0, k]
            if not last and k > 0:
                q[t, 0, k, BUY] = buy_r[t] + hold_r[t] + v[t + 1, 1, k - 1]
            if last:
                q[t, 1, k, SELL] = sell_r[t]
            else:
                q[t, 1, k, HOLD] = hold_r[t] + v[t + 1, 1, k]
                q[t, 1, k, SELL] = sell_r[t] + v[t + 1, 0, k]
            for pos in (0, 1):
                best = -np.inf
                for a in (HOLD, SELL, BUY):
                    if q[t, pos, k, a] > best:
                        best = q[t, pos, k, a]
                v[t, pos, k] = best
    action = np.zeros(n, dtype=int)
    pos = np.zeros(n, dtype=int)
    p, k = 0, k_max
    for t in range(n):
        best, a_best = -np.inf, HOLD
        for a in (HOLD, SELL, BUY):
            if q[t, p, k, a] > best:
                best, a_best = q[t, p, k, a], a
        action[t], pos[t] = a_best, p
        if a_best == BUY:
            p, k = 1, k - 1
        elif a_best == SELL:
            p = 0
    return {
        "q": q,
        "action": action,
        "pos": pos,
        "total": float(q[0, 0, k_max].max()),
        "trades": int((action == BUY).sum()),
    }
