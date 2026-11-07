"""정책 재생: oracle 과 같은 체결·비용·제약."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .oracle import BUY, HOLD, K_MAX, SELL, step_rewards


def _feasible(t: int, n: int, pos: int, k: int, a: int) -> int:
    if t == n - 1:
        return SELL if pos == 1 else HOLD
    if a == BUY and (pos == 1 or k <= 0):
        return HOLD
    if a == SELL and pos == 0:
        return HOLD
    return a


def _simulate(want: Callable[[int, int, int], int], ask: np.ndarray, bid: np.ndarray,
              code: str, k_max: int) -> tuple[float, int, np.ndarray]:
    n = len(ask)
    hold_r, buy_r, sell_r = step_rewards(ask, bid, code)
    act = np.zeros(n, dtype=int)
    total, buys, pos, k = 0.0, 0, 0, k_max
    for t in range(n):
        a = _feasible(t, n, pos, k, int(want(t, pos, k)))
        act[t] = a
        if a == BUY:
            total += buy_r[t] + hold_r[t]
            pos, k, buys = 1, k - 1, buys + 1
        elif a == SELL:
            total += sell_r[t]
            pos = 0
        elif pos == 1 and t < n - 1:
            total += hold_r[t]
    return float(total), int(buys), act


def run(actions, ask1, bid1, code: str, k_max: int = K_MAX) -> tuple[float, int, np.ndarray]:
    """칸별 희망 행동(HOLD/BUY/SELL) 배열 → 실제 재생. 상태 pos=0,k=k_max 시작.

    불가능 행동(보유 중 BUY, 무보유 SELL, k=0 BUY, 마지막 칸 BUY)은 HOLD. 마지막 칸 보유면 SELL 강제.
    보상 = oracle.step_rewards 조각 합 (oracle.solve 와 같은 정의). 반환 (총 로그보상, 매수 수, 실제 행동 배열).
    """
    want = np.asarray(actions, dtype=int)
    ask = np.asarray(ask1, dtype=np.float64)
    bid = np.asarray(bid1, dtype=np.float64)
    return _simulate(lambda t, pos, k: want[t], ask, bid, code, k_max)


def policy_run(decide: Callable[[int, int, int], int], ask1, bid1, code: str,
               k_max: int = K_MAX) -> tuple[float, int, np.ndarray]:
    """decide(t, pos, k) -> 행동. 상태 의존 정책용."""
    ask = np.asarray(ask1, dtype=np.float64)
    bid = np.asarray(bid1, dtype=np.float64)
    return _simulate(decide, ask, bid, code, k_max)


def placebo_random(ask1, bid1, code: str, reps: int, rng: np.random.Generator) -> np.ndarray:
    """매 칸 가능한 행동 중 균등 무작위, reps 개 총보상."""
    ask = np.asarray(ask1, dtype=np.float64)
    bid = np.asarray(bid1, dtype=np.float64)
    out = np.zeros(reps)
    for i in range(reps):
        def want(t, pos, k):
            if t == len(ask) - 1:
                return SELL if pos == 1 else HOLD
            if pos == 1:
                return HOLD if rng.integers(2) == 0 else SELL
            return HOLD if k <= 0 or rng.integers(2) == 0 else BUY
        out[i] = _simulate(want, ask, bid, code, K_MAX)[0]
    return out


def placebo_timing(n_trades: int, ask1, bid1, code: str, reps: int,
                   rng: np.random.Generator) -> np.ndarray:
    """같은 매수 수 n 을 무작위 시점에: sort(rng.choice(N-1, 2n, replace=False)) → (매수, 매도) 쌍.

    N = 칸 수, 마지막 칸 제외(매수 불가) 구간에서 서로 다른 2n 칸 뽑아 정렬 후 consecutive pairing.
    n=0 이면 0. 누적합으로 계산(루프 최소).
    """
    ask = np.asarray(ask1, dtype=np.float64)
    bid = np.asarray(bid1, dtype=np.float64)
    out = np.zeros(reps)
    if n_trades <= 0:
        return out
    n = len(ask)
    hold_r, buy_r, sell_r = step_rewards(ask, bid, code)
    H = np.zeros(n)
    H[1:] = np.cumsum(hold_r[:-1])
    for i in range(reps):
        picks = np.sort(rng.choice(n - 1, size=2 * n_trades, replace=False))
        b, s = picks[0::2], picks[1::2]
        out[i] = float((buy_r[b] + H[s] - H[b] + sell_r[s]).sum())
    return out
