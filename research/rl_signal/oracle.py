"""사후 최적 정답지. 상태 (칸, 보유 여부, 남은 진입 횟수) 동적계획법."""

from __future__ import annotations

import math

import numpy as np

from .costs import UNIT_KRW, buy_cost, sell_cost, tick, tick_below

HOLD, BUY, SELL = 0, 1, 2
K_MAX = 5


def step_rewards(ex_ask1, ex_bid1, ex_aq1, ex_bq1, code: str,
                 unit_krw=UNIT_KRW, slip=0, mark0: float = 0.0,
                 cost_mult: float = 1.0,
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """칸별 로그 보상 조각. v[t]=bid>0이면 bid, 아니면 ask. 둘 다 0이면 v[t-1], 첫 칸이면 mark0(판단 mid).

    cost_mult=buy_cost·sell_cost 배율(기본 1.0, 학습 정답지는 costs.LABEL_COST_MULT).
    hold_r[t]=log(v[t+1]/v[t]), buy_r=log(v/매수가)-log(1+buy_cost*mult), sell_r=log(매도가/v)+log(1-sell_cost*mult).
    매수=ask+slip*tick, 매도=bid-slip*tick_below. can_buy/can_sell=가격>0 and 1호가 잔량>=ceil(unit_krw/가격).
    """
    ask = np.asarray(ex_ask1, dtype=np.float64)
    bid = np.asarray(ex_bid1, dtype=np.float64)
    aq = np.asarray(ex_aq1, dtype=np.int64)
    bq = np.asarray(ex_bq1, dtype=np.int64)
    n = len(ask)
    v = np.zeros(n, dtype=np.float64)
    for t in range(n):
        if bid[t] > 0:
            v[t] = bid[t]
        elif ask[t] > 0:
            v[t] = ask[t]
        elif t > 0:
            v[t] = v[t - 1]
        else:
            v[t] = mark0
    can_buy = np.zeros(n, dtype=bool)
    can_sell = np.zeros(n, dtype=bool)
    for t in range(n):
        if ask[t] > 0:
            can_buy[t] = aq[t] >= math.ceil(unit_krw / ask[t])
        if bid[t] > 0:
            can_sell[t] = bq[t] >= math.ceil(unit_krw / bid[t])
    hold_r = np.zeros(n)
    for t in range(n - 1):
        if v[t] > 0 and v[t + 1] > 0:
            hold_r[t] = math.log(v[t + 1] / v[t])
    buy_r = np.full(n, -np.inf)
    sell_r = np.full(n, -np.inf)
    log_bc = math.log(1.0 + buy_cost(code) * cost_mult)
    log_sc = math.log(1.0 - sell_cost(code) * cost_mult)
    for t in range(n):
        if ask[t] > 0 and v[t] > 0:
            px = ask[t] + slip * tick(code, ask[t])
            if px > 0:
                buy_r[t] = math.log(v[t] / px) - log_bc
            else:
                can_buy[t] = False
        if bid[t] > 0 and v[t] > 0:
            px = bid[t] - slip * tick_below(code, bid[t])
            if px > 0:
                sell_r[t] = math.log(px / v[t]) + log_sc
            else:
                can_sell[t] = False
    return hold_r, buy_r, sell_r, can_buy, can_sell


def solve(ex_ask1, ex_bid1, ex_aq1, ex_bq1, code: str, k_max: int = K_MAX, slip=0,
          mark0: float = 0.0, cost_mult: float = 1.0) -> dict:
    """동적계획법. BUY는 can_buy[t]일 때만, SELL은 can_sell[t]일 때만. mark0=첫 칸 체결가 없을 때 판단 mid.

    cost_mult=비용 배율(기본 1.0이면 기존과 동일, 학습 정답지는 costs.LABEL_COST_MULT).
    마지막 칸: pos=1이면 SELL 강제(HOLD -inf), pos=0이면 HOLD 강제(BUY 금지).
    마지막 칸 보유인데 can_sell False면 v 평가 청산+sell_cost*mult만.
    동률은 HOLD > SELL > BUY 순. 반환 q/action/pos/total/trades.
    """
    ask = np.asarray(ex_ask1, dtype=np.float64)
    bid = np.asarray(ex_bid1, dtype=np.float64)
    aq = np.asarray(ex_aq1, dtype=np.int64)
    bq = np.asarray(ex_bq1, dtype=np.int64)
    n = len(ask)
    hold_r, buy_r, sell_r, can_buy, can_sell = step_rewards(ask, bid, aq, bq, code,
                                                              slip=slip, mark0=mark0,
                                                              cost_mult=cost_mult)
    nk = k_max + 1
    q = np.full((n, 2, nk, 3), -np.inf)
    v = np.zeros((n + 1, 2, nk))
    log_sc = math.log(1.0 - sell_cost(code) * cost_mult)
    for t in range(n - 1, -1, -1):
        last = t == n - 1
        for k in range(nk):
            q[t, 0, k, HOLD] = v[t + 1, 0, k]
            if not last and k > 0 and can_buy[t]:
                q[t, 0, k, BUY] = buy_r[t] + hold_r[t] + v[t + 1, 1, k - 1]
            if last:
                if can_sell[t]:
                    q[t, 1, k, SELL] = sell_r[t]
                else:
                    # ponytail: 하한가 잠김은 마감 평가가로 청산 가정
                    q[t, 1, k, SELL] = log_sc
            else:
                q[t, 1, k, HOLD] = hold_r[t] + v[t + 1, 1, k]
                if can_sell[t]:
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
