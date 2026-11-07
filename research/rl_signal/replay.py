"""정책 재생: oracle 과 같은 체결·비용·제약."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from .costs import sell_cost
from .oracle import BUY, HOLD, K_MAX, SELL, step_rewards


def _feasible(t: int, n: int, pos: int, k: int, a: int, can_buy_t: bool, can_sell_t: bool) -> int:
    if t == n - 1:
        return SELL if pos == 1 else HOLD
    if a == BUY and (pos == 1 or k <= 0 or not can_buy_t):
        return HOLD
    if a == SELL and (pos == 0 or not can_sell_t):
        return HOLD
    return a


def _simulate(want: Callable[[int, int, int], int], ask: np.ndarray, bid: np.ndarray,
              aq: np.ndarray, bq: np.ndarray, code: str, k_max: int, slip: int,
              mark0: float = 0.0) -> tuple[float, int, np.ndarray]:
    n = len(ask)
    hold_r, buy_r, sell_r, can_buy, can_sell = step_rewards(
        ask, bid, aq, bq, code, slip=slip, mark0=mark0)
    log_sc = math.log(1.0 - sell_cost(code))
    act = np.zeros(n, dtype=int)
    total, buys, pos, k = 0.0, 0, 0, k_max
    for t in range(n):
        a = _feasible(t, n, pos, k, int(want(t, pos, k)), bool(can_buy[t]), bool(can_sell[t]))
        act[t] = a
        if a == BUY:
            total += buy_r[t] + hold_r[t]
            pos, k, buys = 1, k - 1, buys + 1
        elif a == SELL:
            if t == n - 1 and not can_sell[t]:
                # ponytail: 하한가 잠김은 마감 평가가로 청산 가정
                total += log_sc
            else:
                total += sell_r[t]
            pos = 0
        elif pos == 1 and t < n - 1:
            total += hold_r[t]
    return float(total), int(buys), act


def run(actions, ex_ask1, ex_bid1, ex_aq1, ex_bq1, code: str,
        k_max: int = K_MAX, slip=0, mark0: float = 0.0) -> tuple[float, int, np.ndarray]:
    """칸별 희망 행동(HOLD/BUY/SELL) 배열 → 실제 재생. 상태 pos=0,k=k_max 시작.

    불가능 행동(보유 중 BUY, 무보유 SELL, k=0 BUY, 마지막 칸 BUY, can_buy/can_sell False)은 HOLD.
    마지막 칸 보유면 SELL 강제(잠김은 마감 평가). 반환 (총 로그보상, 매수 수, 실제 행동 배열).
    """
    want = np.asarray(actions, dtype=int)
    ask = np.asarray(ex_ask1, dtype=np.float64)
    bid = np.asarray(ex_bid1, dtype=np.float64)
    aq = np.asarray(ex_aq1, dtype=np.int64)
    bq = np.asarray(ex_bq1, dtype=np.int64)
    return _simulate(lambda t, pos, k: want[t], ask, bid, aq, bq, code, k_max, slip, mark0)


def policy_run(decide: Callable[[int, int, int], int], ex_ask1, ex_bid1, ex_aq1, ex_bq1, code: str,
               k_max: int = K_MAX, slip=0, mark0: float = 0.0) -> tuple[float, int, np.ndarray]:
    """decide(t, pos, k) -> 행동. 상태 의존 정책용."""
    ask = np.asarray(ex_ask1, dtype=np.float64)
    bid = np.asarray(ex_bid1, dtype=np.float64)
    aq = np.asarray(ex_aq1, dtype=np.int64)
    bq = np.asarray(ex_bq1, dtype=np.int64)
    return _simulate(decide, ask, bid, aq, bq, code, k_max, slip, mark0)


def placebo_random(ex_ask1, ex_bid1, ex_aq1, ex_bq1, code: str, reps: int,
                   rng: np.random.Generator, slip=0, mark0: float = 0.0) -> np.ndarray:
    """매 칸 가능한 행동 중 균등 무작위, reps 개 총보상."""
    ask = np.asarray(ex_ask1, dtype=np.float64)
    bid = np.asarray(ex_bid1, dtype=np.float64)
    aq = np.asarray(ex_aq1, dtype=np.int64)
    bq = np.asarray(ex_bq1, dtype=np.int64)
    out = np.zeros(reps)
    for i in range(reps):
        def want(t, pos, k):
            if t == len(ask) - 1:
                return SELL if pos == 1 else HOLD
            if pos == 1:
                return HOLD if rng.integers(2) == 0 else SELL
            return HOLD if k <= 0 or rng.integers(2) == 0 else BUY
        out[i] = _simulate(want, ask, bid, aq, bq, code, K_MAX, slip, mark0)[0]
    return out


def placebo_timing(n_trades: int, ex_ask1, ex_bid1, ex_aq1, ex_bq1, code: str, reps: int,
                   rng: np.random.Generator, slip=0, mark0: float = 0.0) -> np.ndarray:
    """같은 매수 수 n 을 무작위 시점에: sort(rng.choice(N-1, 2n, replace=False)) → (매수, 매도) 쌍.

    N = 칸 수, 마지막 칸 제외(매수 불가) 구간에서 서로 다른 2n 칸 뽑아 정렬 후 consecutive pairing.
    can_buy/can_sell False 쌍은 HOLD(0 기여). n=0 이면 0.
    """
    ask = np.asarray(ex_ask1, dtype=np.float64)
    bid = np.asarray(ex_bid1, dtype=np.float64)
    aq = np.asarray(ex_aq1, dtype=np.int64)
    bq = np.asarray(ex_bq1, dtype=np.int64)
    out = np.zeros(reps)
    if n_trades <= 0:
        return out
    n = len(ask)
    hold_r, buy_r, sell_r, can_buy, can_sell = step_rewards(
        ask, bid, aq, bq, code, slip=slip, mark0=mark0)
    H = np.zeros(n)
    H[1:] = np.cumsum(hold_r[:-1])
    for i in range(reps):
        picks = np.sort(rng.choice(n - 1, size=2 * n_trades, replace=False))
        b, s = picks[0::2], picks[1::2]
        tot = 0.0
        for bi, si in zip(b, s):
            if can_buy[bi] and can_sell[si]:
                tot += buy_r[bi] + H[si] - H[bi] + sell_r[si]
        out[i] = float(tot)
    return out


def placebo_shift(actions, ex_ask1, ex_bid1, ex_aq1, ex_bq1, code: str, reps: int,
                  rng: np.random.Generator, slip=0, mark0: float = 0.0) -> np.ndarray:
    """모델이 실제로 한 행동열(재생 결과)에서 보유 구간들(매수 칸, 매도 칸)을 뽑아,
    모든 구간을 같은 오프셋 o ~ U{1..L-1} (L = n-1) 만큼 원형 이동: 새 매수 = (매수+o) mod L,
    새 매도 = 새 매수 + 길이, L 넘으면 n-1 로 자름. 행동열로 만들어 run(...) 재생, reps 개 총 로그보상.
    구간 0개면 전부 0."""
    act0 = np.asarray(actions, dtype=int)
    ask = np.asarray(ex_ask1, dtype=np.float64)
    bid = np.asarray(ex_bid1, dtype=np.float64)
    aq = np.asarray(ex_aq1, dtype=np.int64)
    bq = np.asarray(ex_bq1, dtype=np.int64)
    out = np.zeros(reps)
    ivs: list[tuple[int, int]] = []
    start = -1
    for t, a in enumerate(act0):
        if a == BUY and start < 0:
            start = t
        elif a == SELL and start >= 0:
            ivs.append((start, t))
            start = -1
    if not ivs:
        return out
    n = len(ask)
    L = n - 1
    for i in range(reps):
        o = int(rng.integers(1, L))
        act = np.zeros(n, dtype=int)
        for b, s in ivs:
            nb = (b + o) % L
            ns = nb + (s - b)
            if ns > L:
                ns = L
            act[nb] = BUY
            act[ns] = SELL
        out[i] = run(act, ask, bid, aq, bq, code, slip=slip, mark0=mark0)[0]
    return out
