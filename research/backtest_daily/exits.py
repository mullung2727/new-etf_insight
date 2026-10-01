"""일봉 전용 청산 — 고정보유·트레일링. 상폐·정지는 마지막 가격 고정(D2)."""
from __future__ import annotations

import numpy as np


def hold_exit(df, entry_pos, H, ms_max) -> tuple[int | None, bool, str | None]:
    """(청산 iloc|None, frozen, 사유|None). 목표ms에 행이 없으면 이전 마지막행."""
    m = np.asarray(df["ms"])
    target = int(m[entry_pos]) + H
    if target > ms_max:
        return (None, False, "open")
    j = int(np.searchsorted(m, target, side="right")) - 1
    return (j, bool(m[j] != target), None)


def trailing_exit(ms, P, entry_pos, p_entry, stop=0.20, max_hold=250, ms_max=None):
    """TS 청산 (청산 iloc|None, frozen, 사유|None).

    진입 다음 행부터 판정. peak = 진입가·진입일~종가 중 최대, P < peak*(1-stop) 첫 행 종가 청산.
    미발동이면 진입ms+max_hold(없으면 이전 마지막행). 목표ms > ms_max면 open(기발동은 포함).
    """
    m = np.asarray(ms)
    Pv = np.asarray(P, dtype=float)
    ms_e = int(m[entry_pos])
    target = ms_e + max_hold
    lo = entry_pos + 1
    hi = min(int(np.searchsorted(m, target, side="right")), len(m))
    if lo < hi:
        seg = Pv[lo:hi]
        base = np.empty(len(seg) + 1)
        base[0] = max(float(p_entry), float(Pv[entry_pos]))
        base[1:] = seg
        run = np.fmax.accumulate(base[:-1])
        trig = np.flatnonzero(seg < run * (1 - stop))
        if trig.size:
            return (int(lo + trig[0]), False, None)
    if ms_max is not None and target > ms_max:
        return (None, False, "open")
    j = int(np.searchsorted(m, target, side="right")) - 1
    return (j, bool(m[j] != target), None)
