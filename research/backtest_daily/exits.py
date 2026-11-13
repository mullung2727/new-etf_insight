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


def bracket_exit(ms, O, H, L, C, entry_pos, p_entry, stop_px, tp_px,
                 time_bars=None, max_hold=120, ms_max=None):
    """손절(종가)·익절(장중 지정가)·시간 청산. (청산 iloc|None, 청산가|None, 사유)."""
    m = np.asarray(ms)
    Ov = np.asarray(O, dtype=float)
    Hv = np.asarray(H, dtype=float)
    Cv = np.asarray(C, dtype=float)
    n = len(m)
    e = int(entry_pos)
    ms_e = int(m[e])
    use_tp = bool(np.isfinite(tp_px))
    t_row = None
    if time_bars is not None:
        tb = ms_e + int(time_bars)
        j = int(np.searchsorted(m, tb, side="left"))
        if j == n:
            t_row = None
        else:
            t_row = j if int(m[j]) == tb else j - 1
    x_row = None
    xt = ms_e + int(max_hold)
    jx = int(np.searchsorted(m, xt, side="left"))
    if jx < n and int(m[jx]) == xt:
        x_row = jx
    elif jx < n:
        x_row = jx - 1
    for i in range(e, n):
        if use_tp:
            if i != e and Ov[i] >= tp_px:
                return (i, float(Ov[i]), "tp_gap")
            # ponytail: 호가 한 칸 대신 H > tp 엄격 비교, 수정가라 호가 단위 계산 불가
            if Hv[i] > tp_px:
                return (i, float(tp_px), "tp")
        if Cv[i] < stop_px:
            prev = (i > 0 and int(m[i]) == int(m[i - 1]) + 1
                    and np.isfinite(Cv[i - 1]) and Cv[i - 1] > 0)
            if prev and Cv[i] / Cv[i - 1] - 1.0 <= -0.295:
                if i + 1 < n:
                    return (i + 1, float(Ov[i + 1]), "sl_limit_down")
                break
            return (i, float(Cv[i]), "sl")
        if t_row is not None and i == t_row and Cv[i] <= p_entry:
            return (i, float(Cv[i]), "time")
        if x_row is not None and i == x_row:
            return (i, float(Cv[i]), "max")
    if ms_max is not None and ms_e + int(max_hold) > int(ms_max):
        return (None, None, "open")
    return (n - 1, float(Cv[n - 1]), "last")
