"""무상 장중 공시 분봉 이벤트 분석.

실행: cd etl && PYTHONPATH=.. uv run python ../research/rights_issue/minute_event.py
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from research.backtest_daily import data as ddata, stats
from research.backtest_minute import data as mdata, fills, ticks

ROOT = Path(__file__).parent
OUT = ROOT / "out"
APPLIED = ("적용값: 진입=공시시각 이후 첫 분봉 시가(거래정지 후 재개가) · 기준가=close−cmp_prev(없으면 D0 직전일 종가) · "
           "상한가 buyable 가드(entry 불가→A·B NaN, p_close 불가→C NaN) · 비용 0.35%"
           " · 진입 분기 비교: 청산 D+1 시가, dip 지정가 = 재개시가×(1−k%) 호가내림, "
           "limit_buy_fill 장중 규칙(after=재개봉, before=15:20), 미체결→종가/스킵")
KS = (5, 15, 30, 60)
COLS = ["acptno", "code", "name", "D0", "공시시각", "T", "T1", "entry_time",
        "halt_min", "pc", "d0_open", "p_T", "entry", "p_close", "d1_open",
        "p5", "p15", "p30", "p60", "post_high", "pre_open", "pre_pc",
        "entry_gap", "r5", "r15", "r30", "r60", "run_high", "A", "B", "C"]


def t_of(hhmm):
    """'HH:MM' → int HHMMSS. 실패 시 ValueError."""
    h, m = str(hhmm).strip().split(":")
    return int(h) * 10000 + int(m) * 100


def add_min(t, k):
    """HHMMSS(초=00 전제) + k분 → HHMMSS."""
    t = int(t)
    tot = (t // 10000) * 60 + (t // 100) % 100 + int(k)
    return (tot // 60) * 10000 + (tot % 60) * 100


def path_time(t0, k):
    """t0+k분. 153000 넘으면 None."""
    tt = add_min(t0, k)
    return None if tt > 153000 else tt


def div(a, b):
    """a/b-1. NaN·b≤0 가드."""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return float("nan")
    if not (np.isfinite(a) and np.isfinite(b)) or b <= 0:
        return float("nan")
    return a / b - 1


def post_high_of(bars, t1):
    """time>T1 봉들의 high 최대. 없으면 NaN."""
    if bars is None:
        return float("nan")
    tm = np.asarray(bars["time"]).astype(int)
    hi = np.asarray(bars["high"], dtype=float)
    h = hi[tm > int(t1)]
    h = h[np.isfinite(h)]
    return float(h.max()) if h.size else float("nan")


def first_bar_after(bars, T):
    """time>T 첫 봉 (idx, time, open). 없으면 None."""
    if bars is None:
        return None
    tm = np.asarray(bars["time"]).astype(int)
    op = np.asarray(bars["open"], dtype=float)
    mask = tm > int(T)
    if not mask.any():
        return None
    idx = int(np.flatnonzero(mask)[np.argmin(tm[mask])])
    return (idx, int(tm[idx]), float(op[idx]))


def halt_min_of(t0, t1):
    """HHMMSS 두 시각의 분 차이 (t1-t0)."""
    t0, t1 = int(t0), int(t1)
    return ((t1 // 10000) * 60 + (t1 // 100) % 100
            - ((t0 // 10000) * 60 + (t0 // 100) % 100))


def run_high_of(bars, entry_time):
    """time>=entry_time 봉들의 high 최대 (entry 봉 포함). 없으면 NaN."""
    if bars is None:
        return float("nan")
    tm = np.asarray(bars["time"]).astype(int)
    hi = np.asarray(bars["high"], dtype=float)
    h = hi[tm >= int(entry_time)]
    h = h[np.isfinite(h)]
    return float(h.max()) if h.size else float("nan")


def indicators(p_t, entry, d0_open, pc, p_close, d1_open, paths, post_high):
    """지표 전부 gross 소수. paths={5,15,30,60: 가격}."""
    r = {
        "pre_open": div(p_t, d0_open),
        "pre_pc": div(p_t, pc),
        "entry_gap": div(entry, p_t),
        "r5": div(paths.get(5), entry),
        "r15": div(paths.get(15), entry),
        "r30": div(paths.get(30), entry),
        "r60": div(paths.get(60), entry),
        "run_high": div(post_high, entry),
        "A": div(p_close, entry),
        "B": div(d1_open, entry),
        "C": div(d1_open, p_close),
    }
    if not ticks.buyable(entry, pc):
        r["A"] = float("nan")
        r["B"] = float("nan")
    if not ticks.buyable(p_close, pc):
        r["C"] = float("nan")
    return r


def main():
    print(APPLIED)
    E = pd.read_csv(OUT / "events.csv", dtype=str, encoding="utf-8")
    F = E[(E["유형"] == "무상") & (E["시각구분"] == "장중")
          & (E["D0"] >= "20251201")].copy()
    F["code"] = F["code"].astype(str).str.zfill(6)
    F["D0"] = F["D0"].astype(str)
    print(f"대상 {len(F)}건 (events.csv 전체 {len(E)}건)")
    skip = {}

    def drop(reason):
        skip[reason] = skip.get(reason, 0) + 1

    px = ddata.load_px()
    mdates = ddata.market_dates()
    midx = {d: i for i, d in enumerate(mdates)}
    px["ticker"] = px["ticker"].astype(str).str.zfill(6)
    px["date"] = px["date"].astype(str)
    sub = px[px["ticker"].isin(set(F["code"]))]
    cmpv = pd.to_numeric(sub["cmp_prev"], errors="coerce").to_numpy(dtype=float)
    rec = {(t, d): (float(o), float(c), float(cp))
           for t, d, o, c, cp in zip(sub["ticker"], sub["date"],
                                    sub["open"], sub["close"], cmpv)}

    bars_map = mdata.day_bars([(d, c) for d, c in zip(F["D0"], F["code"])])
    recs = []
    for r in F.to_dict("records"):
        d0, code = r["D0"], r["code"]
        try:
            T = t_of(r["공시시각"])
        except (ValueError, AttributeError):
            drop("공시시각 파싱 실패")
            continue
        T1 = add_min(T, 1)
        drow = rec.get((code, d0))
        if drow is None:
            drop("D0 일봉 없음")
            continue
        d0_open, p_close, cmp0 = drow
        if np.isfinite(cmp0):
            pc = p_close - cmp0
        else:
            i = midx.get(d0)
            prv = rec.get((code, mdates[i - 1])) if i else None
            pc = prv[1] if prv else float("nan")
        if not np.isfinite(pc) or pc <= 0:
            drop("pc 없음")
            continue
        bars = bars_map.get((d0, code))
        if bars is None:
            drop("분봉 없음")
            continue
        p_t = mdata.snapshot(bars, T)
        fb = first_bar_after(bars, T)
        if fb is None:
            drop("공시후 거래 없음")
            continue
        _, entry_time, entry = fb
        halt_min = halt_min_of(T, entry_time)
        i = midx.get(d0)
        d1 = rec.get((code, mdates[i + 1])) \
            if i is not None and i + 1 < len(mdates) else None
        d1_open = d1[0] if d1 else float("nan")
        paths = {k: (mdata.snapshot(bars, tt)
                     if (tt := path_time(entry_time, k)) is not None else float("nan"))
                 for k in KS}
        ph = run_high_of(bars, entry_time)
        ind = indicators(p_t, entry, d0_open, pc, p_close, d1_open, paths, ph)
        recs.append({"acptno": r["acptno"], "code": code, "name": r["name"],
                     "D0": d0, "공시시각": r["공시시각"], "T": T, "T1": T1,
                     "entry_time": entry_time, "halt_min": halt_min,
                     "pc": pc, "d0_open": d0_open, "p_T": p_t, "entry": entry,
                     "p_close": p_close, "d1_open": d1_open,
                     "p5": paths[5], "p15": paths[15], "p30": paths[30],
                     "p60": paths[60], "post_high": ph, **ind})

    D = pd.DataFrame(recs, columns=COLS)
    OUT.mkdir(parents=True, exist_ok=True)
    D.to_csv(OUT / "minute_events.csv", index=False, encoding="utf-8")
    print("제외: " + (", ".join(f"{k}={v}" for k, v in skip.items())
                      if skip else "없음") + f" → 포함 {len(D)}건")

    def pct(v):
        return "-" if pd.isna(v) else f"{float(v) * 100:.1f}%"

    def t2(v):
        return "-" if pd.isna(v) else f"{float(v):.2f}"

    def hmin(v):
        return "-" if pd.isna(v) else f"{int(float(v))}"

    cols = ["pre_open", "pre_pc", "entry_gap", "r5", "r30", "run_high",
            "A", "B", "C"]
    print("name".ljust(14) + "D0        " + "시각   " + "halt_min".rjust(8)
          + "".join(c.rjust(8) for c in cols))
    if not len(D):
        print("(없음)")
    for _, r in D.iterrows():
        print(f"{str(r['name'])[:12]:<14}{r['D0']}  {r['공시시각']:<6}"
              + hmin(r["halt_min"]).rjust(8)
              + "".join(pct(r[c]).rjust(8) for c in cols))

    for c in ("A", "B", "C"):
        x = D[c].to_numpy(float) if len(D) else np.array([])
        keys = stats.day_key(D["D0"]) if len(D) else []
        ct = stats.cost_table(x, keys)
        m = ct["costs"]["0.0035"]
        print(f"[{c}] mean={pct(m['mean'])} t={t2(m['t'])} "
              f"med={pct(ct['med'])} win={pct(ct['win'])} n={m['n']}")
    for c in ("pre_open", "entry_gap", "r5", "r30", "run_high"):
        x = D[c].to_numpy(float) if len(D) else np.array([])
        f = x[np.isfinite(x)]
        mu = float(f.mean()) if f.size else float("nan")
        me = float(np.median(f)) if f.size else float("nan")
        print(f"[{c}] mean={pct(mu)} med={pct(me)} n={int(f.size)}")
    hx = D["halt_min"].to_numpy(float) if len(D) else np.array([])
    hf = hx[np.isfinite(hx)]
    hmu = float(hf.mean()) if hf.size else float("nan")
    hme = float(np.median(hf)) if hf.size else float("nan")
    print(f"[halt_min] mean={hmu:.1f} med={hme:.1f} n={int(hf.size)}")
    entry_timing(D, bars_map)


DIP_KS = (1, 2, 3, 5, 7, 10)


def dip_limit_px(o_r, k):
    """지정가 L = o_r×(1−k%) 호가내림. o_r 유효 전제."""
    raw = float(o_r) * (1.0 - float(k) / 100.0)
    t = ticks.tick_size(raw)
    return int(math.floor(raw / t + 1e-9)) * t


def timing_trade(bars, rule, entry_time, o_r, pc, p_close, d1_open):
    """(filled, entry_px, gross). 미체결/가드탈락 → (False, NaN, NaN).

    dip_close 대체매수는 (False, 종가, gross). 청산은 D+1 시가.
    """
    nan = float("nan")
    try:
        et = int(entry_time)
    except (TypeError, ValueError):
        return (False, nan, nan)
    if rule == "resume":
        if not ticks.buyable(o_r, pc):
            return (False, nan, nan)
        return (True, float(o_r), div(d1_open, o_r))
    if rule == "close":
        if not ticks.buyable(p_close, pc):
            return (False, nan, nan)
        return (True, float(p_close), div(d1_open, p_close))
    if rule in ("t5", "t15", "t30"):
        tt = add_min(et, int(rule[1:]))
        if tt > 152000:
            return (False, nan, nan)
        px = mdata.snapshot(bars, tt)
        try:
            px = float(px)
        except (TypeError, ValueError):
            return (False, nan, nan)
        if not np.isfinite(px) or not ticks.buyable(px, pc):
            return (False, nan, nan)
        return (True, px, div(d1_open, px))
    k, _, variant = rule[3:].partition("_")
    try:
        o_f = float(o_r)
    except (TypeError, ValueError):
        return (False, nan, nan)
    if not np.isfinite(o_f):
        return (False, nan, nan)
    L = dip_limit_px(o_f, int(k))
    hit = None
    if ticks.buyable(L, pc):
        hit = fills.limit_buy_fill(bars, L, pc, pre_open=False,
                                   after=et, before=152000)
    if hit is not None:
        fp = float(hit[1])
        return (True, fp, div(d1_open, fp))
    if variant == "close":
        if not ticks.buyable(p_close, pc):
            return (False, nan, nan)
        return (False, float(p_close), div(d1_open, p_close))
    return (False, nan, nan)


def entry_timing(D, bars_map):
    """진입 타이밍 분기 비교. 콘솔 표 2개 + out/minute_entry_timing.csv."""
    rules = ["resume", "t5", "t15", "t30"] + \
        [f"dip{k}_{v}" for k in DIP_KS for v in ("close", "skip")] + ["close"]
    nan = float("nan")
    long_rows = []
    per = {r: {"filled": [], "gross": [], "D0": []} for r in rules}
    for r in D.to_dict("records"):
        d0, code = str(r["D0"]), str(r["code"])
        bars = bars_map.get((d0, code)) if bars_map else None
        for rule in rules:
            if bars is None:
                f, e, g = False, nan, nan
            else:
                f, e, g = timing_trade(bars, rule, r["entry_time"], r["entry"],
                                       r["pc"], r["p_close"], r["d1_open"])
            long_rows.append({"acptno": r["acptno"], "name": r["name"],
                              "D0": d0, "rule": rule, "filled": bool(f),
                              "entry_px": float(e), "gross": float(g)})
            per[rule]["filled"].append(bool(f))
            per[rule]["gross"].append(float(g))
            per[rule]["D0"].append(d0)
    T = pd.DataFrame(long_rows, columns=["acptno", "name", "D0", "rule",
                                         "filled", "entry_px", "gross"])
    OUT.mkdir(parents=True, exist_ok=True)
    T.to_csv(OUT / "minute_entry_timing.csv", index=False, encoding="utf-8")

    def pct(v):
        return "-" if pd.isna(v) else f"{float(v) * 100:.1f}%"

    def t2(v):
        return "-" if pd.isna(v) else f"{float(v):.2f}"

    def row_stat(rule, idx=None):
        x = np.asarray(per[rule]["gross"], dtype=float)
        dd = per[rule]["D0"]
        if idx is not None:
            x = x[idx]
            dd = [d for d, m in zip(dd, idx) if m]
        ct = stats.cost_table(x, stats.day_key(dd))
        m = ct["costs"]["0.0035"]
        fr = nan
        if rule.startswith("dip"):
            fl = per[rule]["filled"]
            if idx is not None:
                fl = [v for v, mm in zip(fl, idx) if mm]
            fr = float(np.mean(fl)) if fl else nan
        return m["n"], fr, m["mean"], m["t"], ct["med"], ct["win"]

    def show(rule, st):
        n, fr, mean, t, med, win = st
        return (rule.ljust(12) + str(int(n)).rjust(6)
                + pct(fr).rjust(8) + pct(mean).rjust(8) + t2(t).rjust(8)
                + pct(med).rjust(8) + pct(win).rjust(8))

    hdr = ("rule".ljust(12) + "n".rjust(6) + "fill".rjust(8)
           + "mean".rjust(8) + "t".rjust(8) + "med".rjust(8) + "win".rjust(8))
    print(f"[진입 타이밍] 전체 규칙 (대상 {len(D)}건, 청산 D+1 시가)")
    print(hdr)
    for rule in rules:
        print(show(rule, row_stat(rule)))
    five = ["resume", "t5", "t15", "t30", "close"]
    G = {r: np.asarray(per[r]["gross"], dtype=float) for r in five}
    mask = np.ones(len(D), dtype=bool)
    for r in five:
        mask &= np.isfinite(G[r])
    print(f"[진입 타이밍] 공통표본 n={int(mask.sum())} (5규칙 모두 거래)")
    print(hdr)
    for r in five:
        print(show(r, row_stat(r, mask)))
    cg = np.asarray(per["close"]["gross"], dtype=float)
    for k in DIP_KS:
        fl = np.asarray(per[f"dip{k}_close"]["filled"], dtype=bool)
        f = cg[fl]
        f = f[np.isfinite(f)]
        u = cg[~fl]
        u = u[np.isfinite(u)]
        mf = float(f.mean()) if f.size else nan
        mu = float(u.mean()) if u.size else nan
        print(f"[dip{k}] 체결건 close평균(비용전)={pct(mf)} n={int(f.size)} / "
              f"미체결건 close평균(비용전)={pct(mu)} n={int(u.size)}")
    print(f"→ {OUT / 'minute_entry_timing.csv'} {len(T)}행")


def selfcheck():
    assert t_of("10:19") == 101900
    assert add_min(101900, 1) == 102000
    assert t_of("10:59") == 105900
    assert add_min(105900, 1) == 110000
    assert path_time(102000, 60) == 112000
    assert path_time(143000, 60) == 153000
    assert path_time(143100, 60) is None
    assert np.isnan(div(float("nan"), 100.0))

    bars = {
        "time": np.array([101700, 101800, 101900, 102000, 102100]),
        "open": np.array([100., 101., 102., 103., 104.]),
        "high": np.array([101., 102., 103., 105., 104.]),
        "low": np.array([99., 100., 101., 102., 103.]),
        "close": np.array([101., 102., 103., 104., 103.]),
        "volume": np.array([10., 10., 10., 10., 10.]),
    }
    assert mdata.snapshot(bars, 101900) == 103.0
    assert mdata.snapshot(bars, 102000) == 104.0
    assert post_high_of(bars, 102000) == 104.0
    assert np.isnan(post_high_of(bars, 102100))
    assert np.isnan(post_high_of(None, 102000))

    assert first_bar_after(bars, 101900) == (3, 102000, 103.0)
    assert first_bar_after(bars, 102100) is None
    assert first_bar_after(None, 101900) is None
    assert halt_min_of(101900, 102000) == 1
    assert halt_min_of(105900, 110000) == 1
    assert halt_min_of(110600, 114600) == 40
    assert run_high_of(bars, 102000) == 105.0
    assert run_high_of(bars, 102100) == 104.0
    assert np.isnan(run_high_of(bars, 102200))
    assert np.isnan(run_high_of(None, 102000))

    gap = {
        "time": np.array([110500, 114600, 114700]),
        "open": np.array([72700., 86400., 87000.]),
        "high": np.array([72700., 87000., 87500.]),
        "low": np.array([72700., 86400., 87000.]),
        "close": np.array([72700., 87000., 87200.]),
        "volume": np.array([10., 10., 10.]),
    }
    fb = first_bar_after(gap, 110600)
    assert fb == (1, 114600, 86400.0)
    assert halt_min_of(110600, fb[1]) == 40
    assert run_high_of(gap, fb[1]) == 87500.0
    assert first_bar_after(gap, 114700) is None

    kw = dict(p_t=12000.0, d0_open=10000.0, pc=10000.0, d1_open=12100.0,
              paths={5: 13100.0, 15: 13200.0, 30: 13300.0, 60: float("nan")},
              post_high=13500.0)
    ind = indicators(entry=13000.0, p_close=12000.0, **kw)
    assert np.isnan(ind["A"]) and np.isnan(ind["B"])
    assert abs(ind["C"] - (12100 / 12000 - 1)) < 1e-12
    assert abs(ind["r5"] - (13100 / 13000 - 1)) < 1e-12
    assert np.isnan(ind["r60"])
    assert abs(ind["run_high"] - (13500 / 13000 - 1)) < 1e-12
    assert abs(ind["entry_gap"] - (13000 / 12000 - 1)) < 1e-12
    ind2 = indicators(entry=12000.0, p_close=13000.0, **kw)
    assert not np.isnan(ind2["A"]) and not np.isnan(ind2["B"])
    assert np.isnan(ind2["C"])
    assert abs(ind2["entry_gap"]) < 1e-12

    assert dip_limit_px(10000, 3) == 9700
    assert dip_limit_px(10000, 5) == 9500
    assert dip_limit_px(10123, 3) == 9810
    tb = {
        "time": np.array([100000, 100100, 100200]),
        "open": np.array([10000., 9900., 9800.]),
        "high": np.array([10050., 9950., 9850.]),
        "low": np.array([9950., 9690., 9750.]),
        "close": np.array([9900., 9800., 9800.]),
        "volume": np.array([10., 10., 10.]),
    }
    f, e, g = timing_trade(tb, "resume", 100000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert (f, e, g) == (True, 10000.0, 0.0)
    f, e, g = timing_trade(tb, "t5", 100000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert f is True and e == 9800.0
    f, e, g = timing_trade(tb, "dip3_close", 100000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert f is True and e == 9700.0
    assert abs(g - (10000 / 9700 - 1)) < 1e-12
    f, e, g = timing_trade(tb, "dip3_skip", 100000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert f is True and e == 9700.0
    f, e, g = timing_trade(tb, "dip5_close", 100000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert f is False and e == 9800.0
    assert abs(g - (10000 / 9800 - 1)) < 1e-12
    f, e, g = timing_trade(tb, "dip5_skip", 100000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert f is False and np.isnan(e) and np.isnan(g)
    tb2 = dict(tb)
    tb2["low"] = np.array([9950., 9691., 9750.])
    f, e, g = timing_trade(tb2, "dip3_skip", 100000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert f is False and np.isnan(e) and np.isnan(g)
    tb3 = dict(tb)
    tb3["low"] = np.array([9690., 9800., 9800.])
    f, e, g = timing_trade(tb3, "dip3_skip", 100000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert f is True and e == 9700.0
    f, e, g = timing_trade(tb, "t30", 150000, 10000.0, 10000.0,
                           9800.0, 10000.0)
    assert f is False and np.isnan(e) and np.isnan(g)


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        selfcheck()
        print("selfcheck passed")
    else:
        main()
