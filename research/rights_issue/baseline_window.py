"""유상 "발표후~권리락전" 불기둥 발생률의 같은 종목 평소 기준선.

실행: cd etl && PYTHONPATH=.. uv run python ../research/rights_issue/baseline_window.py
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

sys.path.insert(0, str(Path(__file__).parent))
import analyze as A

from research.backtest_daily import data

ROOT = Path(__file__).parent
EVENTS = ROOT / "out" / "events.csv"
THS = (0.03, 0.05, 0.07, 0.10)
METRICS = ("B", "C")
APPLIED = ("적용값: 유상만(events.csv) · 실제=[D0+2,X-1]ms 안 종목행, 길이L(0이면 제외) · "
           "기준선=[D0-260,D0-30)ms 안 종목행 뒤에서 길이L씩(앞 나머지 버림, 0개면 제외) · "
           "B=고가/직전종목행종가-1 · C=종가/시가-1 · hit=구간 내 th이상 1일+ · "
           "대응t=mean/std*sqrt(n), std=표본(ddof=1)")


def load_events():
    """유상 행 → [(code, D0ms, Xms)], (유상행수, 날짜결측수)."""
    e = pd.read_csv(EVENTS, dtype=str).fillna("")
    e = e[e["유형"] == "유상"]
    pos = {d: i for i, d in enumerate(data.market_dates())}
    recs, bad = [], 0
    for r in e.to_dict("records"):
        d0 = A.norm_date(r.get("D0"))
        x = A.norm_date(r.get("X"))
        if r.get("code") and d0 in pos and x in pos:
            recs.append((r["code"], pos[d0], pos[x]))
        else:
            bad += 1
    return recs, len(e), bad


def stock_frame(px, tk, code):
    """종목 행 + B/C. B 분모=같은 종목 직전 행 close. 없으면 None."""
    lo = int(np.searchsorted(tk, code, side="left"))
    hi = int(np.searchsorted(tk, code, side="right"))
    if lo == hi:
        return None
    df = px.iloc[lo:hi].sort_values("ms").reset_index(drop=True)
    prev = df["close"].shift(1).to_numpy(float)
    op = df["open"].to_numpy(float)
    B = np.full(len(df), np.nan)
    ok = prev > 0
    B[ok] = df["high"].to_numpy(float)[ok] / prev[ok] - 1
    return df.assign(B=B, C=df["close"].to_numpy(float) / op - 1)


def hit(vals, th):
    """NaN 제외, 1개라도 th 이상이면 1."""
    v = np.asarray(vals, float)
    v = v[~np.isnan(v)]
    return 1 if len(v) and (v >= th).any() else 0


def baseline_groups(n, L):
    """n개 행을 뒤에서 길이 L씩 → 구간 위치 리스트. 앞 나머지 버림."""
    n_win = n // L
    off = n - n_win * L
    return [np.arange(off + i * L, off + (i + 1) * L) for i in range(n_win)]


def main():
    print(APPLIED)
    px = data.load_px()
    tk = px["ticker"].to_numpy()
    evs, n_raw, n_bad = load_events()
    skip = {"D0/X 결측": n_bad} if n_bad else {}

    def drop(reason):
        skip[reason] = skip.get(reason, 0) + 1

    rows, n_base_total = [], 0
    for code, d0, x in evs:
        df = stock_frame(px, tk, code)
        if df is None:
            drop("종목 행 없음")
            continue
        ms = df["ms"].to_numpy()
        ai = np.where((ms >= d0 + 2) & (ms <= x - 1))[0]
        if len(ai) == 0:
            drop("실제 L=0")
            continue
        bi = np.where((ms >= d0 - 260) & (ms < d0 - 30))[0]
        groups = baseline_groups(len(bi), len(ai))
        if not groups:
            drop("기준선 0개")
            continue
        rec = {}
        for metric in METRICS:
            v = df[metric].to_numpy(float)
            ah = {th: hit(v[ai], th) for th in THS}
            for th in THS:
                rec[(metric, th)] = (ah[th], float(np.mean([hit(v[bi[g]], th) for g in groups])))
        rows.append(rec)
        n_base_total += len(groups)

    n = len(rows)
    print(f"유상={n_raw} → 포함={n}"
          + (" (" + ", ".join(f"{k}={v}" for k, v in skip.items()) + ")" if skip else ""))
    print(f"평균 기준선 구간 수={n_base_total / n:.1f}" if n else "평균 기준선 구간 수=-")
    print("metric th     실제   기준선  차이%p  대응평균       t")
    if not n:
        print("(없음)")
        return
    for metric in METRICS:
        for th in THS:
            a = np.array([r[(metric, th)][0] for r in rows], float)
            b = np.array([r[(metric, th)][1] for r in rows], float)
            d = a - b
            md = float(d.mean())
            sd = float(d.std(ddof=1)) if n >= 2 else math.nan
            t = md / sd * math.sqrt(n) if sd and math.isfinite(sd) else math.nan
            ts = "-" if not math.isfinite(t) else f"{t:.2f}"
            print(f"{metric:<6}{int(th * 100):>3}%  {a.mean():.3f}   {b.mean():.3f}  "
                  f"{(a.mean() - b.mean()) * 100:+.1f}  {md:+.3f}  {ts:>7}")


if __name__ == "__main__":
    main()
