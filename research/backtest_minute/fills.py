"""분봉 전용 지정가 체결 — 장 시작 전 주문은 시가, 장중은 한 호가 아래(M4 개정)."""
from __future__ import annotations

import numpy as np

from .ticks import lower_limit, tick_below, upper_limit


def limit_buy_fill(bars, price, pc, *, pre_open, after=None, before=None) -> tuple[int, float] | None:
    """첫 체결 (봉 번호, 체결가) 또는 None.

    pre_open=True(장 시작 전 주문): 첫 봉이 09:00 봉이고 시가 ≤ 지정가면 (0, 시가).
    그 외 장중 규칙: after ≤ time < before 봉 중 저가 ≤ price−1호가인 첫 봉 → (j, price).
    """
    if bars is None:
        return None
    try:
        pc = float(pc)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(pc) and pc > 0):
        return None
    try:
        px = float(price)
    except (TypeError, ValueError):
        return None
    lo = lower_limit(pc)
    hi = upper_limit(pc)
    if px < lo or px > hi:
        return None
    try:
        tm = np.asarray(bars["time"]).astype(int)
        low = np.asarray(bars["low"], dtype=float)
    except (KeyError, TypeError, ValueError):
        return None
    if tm.size == 0 or low.size != tm.size:
        return None
    if pre_open:
        try:
            op = np.asarray(bars["open"], dtype=float)
        except (KeyError, TypeError, ValueError):
            op = None
        if op is not None and op.size == tm.size and tm[0] == 90000:
            o0 = float(op[0])
            if np.isfinite(o0) and o0 <= px:
                return (0, o0)
    thr = px - tick_below(px)
    ok = low <= thr
    if after is not None:
        ok &= tm >= int(after)
    if before is not None:
        ok &= tm < int(before)
    if not ok.any():
        return None
    return (int(np.argmax(ok)), px)


def daily_buy_fill(day_open, day_low, price, *, pre_open) -> float | None:
    """일봉판 체결가 또는 None(M8). pre_open이면 시가 ≤ 지정가 → 시가, 그 외 저가 ≤ price−1호가 → 지정가."""
    try:
        do, dl, px = float(day_open), float(day_low), float(price)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(px) and np.isfinite(dl)):
        return None
    if pre_open and np.isfinite(do) and do <= px:
        return do
    if dl <= px - tick_below(px):
        return px
    return None
