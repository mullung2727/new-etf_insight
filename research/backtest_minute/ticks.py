"""분봉 전용 호가·가격제한 — 2023-01-25 이후 KOSPI·KOSDAQ 통합 호가표."""
from __future__ import annotations

import math


def tick_size(price) -> int:
    """호가단위. 2천·5천·2만·5만·20만·50만 경계."""
    p = float(price)
    if p < 2000:
        return 1
    if p < 5000:
        return 5
    if p < 20000:
        return 10
    if p < 50000:
        return 50
    if p < 200000:
        return 100
    if p < 500000:
        return 500
    return 1000


def tick_below(price) -> int:
    """price 바로 아래 가격대 호가단위. 경계 2,000 이면 1."""
    return tick_size(float(price) - 1e-9)


def upper_limit(pc) -> int:
    """상한가. pc×1.3 을 그 가격 호가단위로 내림."""
    raw = float(pc) * 1.3
    t = tick_size(raw)
    return int(math.floor(raw / t + 1e-9)) * t


def lower_limit(pc) -> int:
    """하한가. pc×0.7 을 그 가격 호가단위로 올림."""
    raw = float(pc) * 0.7
    t = tick_size(raw)
    return int(math.ceil(raw / t - 1e-9)) * t


def buyable(entry_px, pc, th=0.295) -> bool:
    """매수 가능 여부. entry_px/pc-1 < th."""
    try:
        e, p = float(entry_px), float(pc)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(e) and math.isfinite(p)) or p <= 0:
        return False
    return e / p - 1 < th
