"""분봉 전용 TP/SL 청산 — 갭 우선·SL 우선, 하한가·정지·결측 처리(M5·M6·M8)."""
from __future__ import annotations

import math

import numpy as np

from .ticks import lower_limit


def _nan() -> float:
    return float("nan")


def _no_data() -> dict:
    return {"ret": _nan(), "reason": "no_data", "exit_date": None,
            "exit_time": None, "exit_px": _nan()}


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _bars_ok(bars) -> bool:
    try:
        return bars is not None and len(np.asarray(bars["time"])) > 0
    except (KeyError, TypeError, ValueError):
        return False


def _next_open(days, i):
    if i >= len(days):
        return None, None
    d = days[i]
    o = d.get("open")
    if _finite(o):
        return str(d.get("date")), float(o)
    b = d.get("bars")
    if _bars_ok(b):
        try:
            return str(d.get("date")), float(np.asarray(b["open"], dtype=float)[0])
        except (TypeError, ValueError):
            return None, None
    return None, None


def tp_sl_exit(days, entry_idx, entry_px, tp_px=None, sl_px=None,
               hold_days=0, time_bars=None) -> dict:
    """체결 다음 봉부터 TP/SL·시간·종가 청산. ret=exit/entry-1, 비용은 호출부."""
    n_days = len(days)
    if hold_days is None or hold_days < 0 or hold_days >= n_days:
        return _no_data()
    if not _finite(entry_px) or float(entry_px) <= 0:
        return _no_data()
    entry_px = float(entry_px)
    tp = float(tp_px) if _finite(tp_px) else None
    sl = float(sl_px) if _finite(sl_px) else None

    def _ret(px):
        return float(px) / entry_px - 1

    for di in range(hold_days + 1):
        day = days[di]
        date = str(day.get("date"))
        pc = day.get("pc")
        lo_lim = lower_limit(pc) if _finite(pc) and float(pc) > 0 else -math.inf
        bars = day.get("bars")
        last = di == hold_days
        if _bars_ok(bars):
            tm = np.asarray(bars["time"]).astype(int)
            op = np.asarray(bars["open"], dtype=float)
            hi = np.asarray(bars["high"], dtype=float)
            lo = np.asarray(bars["low"], dtype=float)
            cl = np.asarray(bars["close"], dtype=float)
            n = int(tm.size)
            if di == 0:
                try:
                    ei = int(entry_idx)
                except (TypeError, ValueError):
                    return _no_data()
                if ei < 0 or ei >= n:
                    return _no_data()
                start = ei + 1
            else:
                start = 0
            t_idx = None
            if time_bars is not None and di == 0:
                try:
                    t = int(time_bars)
                    cand = int(entry_idx) + t
                    if t > 0 and 0 <= cand < n:
                        t_idx = cand
                except (TypeError, ValueError):
                    t_idx = None

            def _ld_after(j):
                for k in range(j + 1, n):
                    if _finite(cl[k]) and float(cl[k]) > lo_lim:
                        return {"ret": _ret(cl[k]), "reason": "ld_release",
                                "exit_date": date, "exit_time": int(tm[k]),
                                "exit_px": float(cl[k])}
                nd, nx = _next_open(days, di + 1)
                if nd is None:
                    return _no_data()
                return {"ret": _ret(nx), "reason": "ld_next_open",
                        "exit_date": nd, "exit_time": 90000, "exit_px": float(nx)}

            for j in range(max(start, 0), n):
                o, h, lw = float(op[j]), float(hi[j]), float(lo[j])
                reason, px = None, None
                if tp is not None and _finite(o) and o >= tp:
                    reason, px = "gap_tp", o
                elif sl is not None and _finite(o) and o <= sl:
                    reason, px = "gap_sl", o
                elif (tp is not None and sl is not None and _finite(h)
                      and _finite(lw) and h >= tp and lw <= sl):
                    reason, px = "both_sl", sl
                elif sl is not None and _finite(lw) and lw <= sl:
                    reason, px = "sl", sl
                elif tp is not None and _finite(h) and h >= tp:
                    reason, px = "tp", tp
                if reason is not None:
                    if px <= lo_lim:
                        return _ld_after(j)
                    return {"ret": _ret(px), "reason": reason,
                            "exit_date": date, "exit_time": int(tm[j]),
                            "exit_px": float(px)}
                if t_idx is not None and j == t_idx:
                    c = float(cl[j])
                    if c <= lo_lim:
                        return _ld_after(j)
                    return {"ret": _ret(c), "reason": "time",
                            "exit_date": date, "exit_time": int(tm[j]),
                            "exit_px": c}
            if int(tm[-1]) < 151500:
                nd, nx = _next_open(days, di + 1)
                if nd is None:
                    return _no_data()
                return {"ret": _ret(nx), "reason": "halt_next_open",
                        "exit_date": nd, "exit_time": 90000, "exit_px": float(nx)}
            if last:
                c = float(cl[-1])
                if c <= lo_lim:
                    return _ld_after(n - 1)
                return {"ret": _ret(c), "reason": "close",
                        "exit_date": date, "exit_time": int(tm[-1]),
                        "exit_px": c}
            continue
        # 분봉 없는 날: 일봉 보수 처리(M8). TP 안 봄
        d_o, d_l, d_c = day.get("open"), day.get("low"), day.get("close")

        def _daily(px, reason):
            if px <= lo_lim:
                nd, nx = _next_open(days, di + 1)
                if nd is None:
                    return _no_data()
                return {"ret": _ret(nx), "reason": "ld_next_open",
                        "exit_date": nd, "exit_time": 90000, "exit_px": float(nx)}
            return {"ret": _ret(px), "reason": reason,
                    "exit_date": date, "exit_time": None, "exit_px": float(px)}

        if sl is not None:
            if _finite(d_o) and float(d_o) <= sl:
                return _daily(float(d_o), "daily_gap_sl")
            if _finite(d_l) and float(d_l) <= sl:
                return _daily(sl, "daily_sl")
        if last:
            if not _finite(d_c):
                return _no_data()
            return _daily(float(d_c), "daily_close")
    return _no_data()
