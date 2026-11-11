"""RSI 쌍바닥(60분봉) 백테스트 — 유니버스·신호·체결·청산·집계·RESULTS.md 출력."""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import statistics
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily.data import load_px, market_dates
from research.backtest_daily.stats import (
    COSTS, cost_table, day_key, day_median, day_weighted_mean,
    day_win_rate, month_key, weighted,
)
from research.backtest_daily.validate import placebo_percentile
from research.backtest_minute.data import FULL_SINCE, day_bars, universe
from research.backtest_minute.exits import tp_sl_exit
from research.backtest_minute.ticks import buyable
from research.rsi_double_bottom.signal import (
    add_indicators, find_entries, find_naive, hourly_bars,
)

REF_COST = 0.0035
HOLD_DAYS = 5
RR = 3.0
PLACEBO_SEED = 20261110
CHUNK_KEYS = 2000
CHUNK_TICKERS = 300
PLACEBO_BATCH = 10

OUT_DIR = Path(__file__).resolve().parent.parent / "private" / "rsi_double_bottom"

SKIP_ORDER = ["universe", "no_bars", "limit_up", "sl_above_entry", "overlap", "open"]
SKIP_KO = {
    "universe": "유니버스 불허",
    "no_bars": "분봉 없음",
    "limit_up": "상한가",
    "sl_above_entry": "sl_above_entry",
    "overlap": "보유중 중복",
    "open": "open",
}
CSV_COLS = ["kind", "ticker", "sig_date", "sig_hb", "date", "entry_date",
            "entry_time", "entry_px", "sl", "tp", "width_pct", "exit_date",
            "exit_time", "exit_px", "reason", "ret", "net", "first_low",
            "bottom2_date", "bottom2_hb", "setup_date"]


def batched_day_bars(keys, chunk=CHUNK_KEYS):
    out = {}
    keys = sorted(set((str(d), str(t)) for d, t in keys))
    for i in range(0, len(keys), chunk):
        out.update(day_bars(keys[i:i + chunk]))
    return out


def build_universe(since):
    """umap: (D, ticker) → (진입 허용, pc). allowed: D → 허용 ticker 정렬.
    breaks: ok_jump==False (ticker, f_date) — 점프일 첫 봉부터 새 구간."""
    u = universe(since=since)
    dd = u["date"].astype(str).to_numpy()
    tt = u["ticker"].astype(str).to_numpy()
    ok = (u["ok_listing"] & u["ok_halt"] & u["ok_jump"] & u["ok_liq"]).to_numpy()
    pc = u["pc"].to_numpy(float)
    umap = {(d, t): (bool(o), float(p)) for d, t, o, p in zip(dd, tt, ok, pc)}
    allowed: dict = {}
    for (d, t), (o, _) in umap.items():
        if o:
            allowed.setdefault(d, []).append(t)
    for d in allowed:
        allowed[d].sort()
    fd = u["f_date"].astype(str).to_numpy()
    oj = u["ok_jump"].to_numpy()
    breaks = {(t, f) for t, f, o in zip(tt, fd, oj) if not o}
    return umap, allowed, breaks


def build_px(mdates, midx, since):
    """(ticker, date) → (open, high, low, close). since 직전일부터만."""
    px = load_px()
    if str(since) in midx and midx[str(since)] > 0:
        cutoff = mdates[midx[str(since)] - 1]
    else:
        cutoff = str(since)
    px = px[px["date"].astype(str) >= cutoff]
    tk = px["ticker"].astype(str).to_numpy()
    dt = px["date"].astype(str).to_numpy()
    o = px["open"].to_numpy(float)
    h = px["high"].to_numpy(float)
    low = px["low"].to_numpy(float)
    c = px["close"].to_numpy(float)
    return {(t, d): (oo, hh, ll, cc)
            for t, d, oo, hh, ll, cc in zip(tk, dt, o, h, low, c)}


def collect_signals(hb, breaks, tickers):
    pats, naivs = [], []
    all_t = sorted(set(hb["ticker"].astype(str))) if len(hb) else []
    if tickers is not None:
        all_t = [t for t in all_t if t in tickers]
    for i in range(0, len(all_t), CHUNK_TICKERS):
        ts = set(all_t[i:i + CHUNK_TICKERS])
        sub = add_indicators(hb[hb["ticker"].isin(ts)], breaks=breaks)
        for _, grp in sub.groupby(["ticker", "seg"], sort=False):
            pats.extend(find_entries(grp))
            naivs.extend(find_naive(grp))
        del sub
    return pats, naivs


def entry_day(sig, mdates, midx):
    if sig["sig_hb"] < 1500:
        return sig["sig_date"]
    i = midx.get(sig["sig_date"])
    if i is None or i + 1 >= len(mdates):
        return None
    return mdates[i + 1]


def load_bars_for(signals, mdates, midx):
    keys = set()
    for s in signals:
        eday = entry_day(s, mdates, midx)
        if eday is None:
            continue
        ei = midx.get(eday)
        if ei is None:
            continue
        for d in mdates[ei:ei + HOLD_DAYS + 1]:
            keys.add((d, s["ticker"]))
    return batched_day_bars(keys)


def _has_bars(b):
    try:
        return b is not None and len(b["time"]) > 0
    except (KeyError, TypeError):
        return False


def _first_after(b, last_time):
    if not _has_bars(b):
        return None
    tm = np.asarray(b["time"]).astype(int)
    j = int(np.searchsorted(tm, int(last_time), side="right"))
    return j if j < tm.size else None


def _first_at_or_after(b, etime):
    if not _has_bars(b):
        return None
    tm = np.asarray(b["time"]).astype(int)
    j = int(np.searchsorted(tm, int(etime), side="left"))
    return j if j < tm.size else None


def _day_row(d, t, px, bars, mdates, midx):
    o, _h, low, c = px.get((t, d), (np.nan, np.nan, np.nan, np.nan))
    pi = midx[d] - 1
    prev = mdates[pi] if pi >= 0 else None
    pc = px.get((t, prev), (np.nan,) * 4)[3] if prev else np.nan
    return {"date": d, "pc": pc, "open": o, "low": low, "close": c,
            "bars": bars.get((d, t))}


def _row(s, eday, entry_time, entry_px, sl, tp, r):
    row = {
        "ticker": s["ticker"], "sig_date": s["sig_date"],
        "sig_hb": int(s["sig_hb"]), "date": eday, "entry_date": eday,
        "entry_time": entry_time, "entry_px": entry_px, "sl": sl, "tp": tp,
        "width_pct": (entry_px - sl) / entry_px * 100,
        "first_low": s.get("first_low"), "bottom2_date": s.get("bottom2_date"),
        "bottom2_hb": s.get("bottom2_hb"), "setup_date": s.get("setup_date"),
    }
    if r is None:
        row.update({"exit_date": None, "exit_time": None, "exit_px": np.nan,
                    "reason": "open", "ret": np.nan, "net": np.nan})
    else:
        ret = float(r["ret"])
        row.update({"exit_date": r["exit_date"], "exit_time": r["exit_time"],
                    "exit_px": r["exit_px"], "reason": r["reason"], "ret": ret,
                    "net": ret - REF_COST if np.isfinite(ret) else np.nan})
    return row


def run_pipeline(signals, umap, bars, px, mdates, midx):
    skips = dict.fromkeys(SKIP_ORDER, 0)
    rows = []
    held: dict = {}
    key = lambda s: (s["ticker"], s["sig_date"], int(s["sig_hb"]))
    for s in sorted(signals, key=key):
        t = s["ticker"]
        eday = entry_day(s, mdates, midx)
        if eday is None:
            skips["no_bars"] += 1
            continue
        u = umap.get((eday, t))
        if u is None or not u[0]:
            skips["universe"] += 1
            continue
        b = bars.get((eday, t))
        if s["sig_hb"] < 1500:
            j = _first_after(b, s["sig_last_time"])
        else:
            j = 0 if _has_bars(b) else None
        if j is None:
            skips["no_bars"] += 1
            continue
        entry_px = float(np.asarray(b["open"], dtype=float)[j])
        if not np.isfinite(entry_px) or entry_px <= 0:
            skips["no_bars"] += 1
            continue
        entry_time = int(np.asarray(b["time"]).astype(int)[j])
        if eday <= held.get(t, ""):
            skips["overlap"] += 1
            continue
        if not buyable(entry_px, u[1]):
            skips["limit_up"] += 1
            continue
        sl = float(s["sl"])
        if not entry_px > sl:
            skips["sl_above_entry"] += 1
            continue
        tp = entry_px + RR * (entry_px - sl)
        ei = midx.get(eday)
        if ei is None or ei + HOLD_DAYS >= len(mdates):
            skips["open"] += 1
            rows.append(_row(s, eday, entry_time, entry_px, sl, tp, None))
            continue
        days = [_day_row(d, t, px, bars, mdates, midx)
                for d in mdates[ei:ei + HOLD_DAYS + 1]]
        r = tp_sl_exit(days, j, entry_px, tp_px=tp, sl_px=sl,
                       hold_days=HOLD_DAYS)
        if r["exit_date"] is not None:
            held[t] = str(r["exit_date"])
        rows.append(_row(s, eday, entry_time, entry_px, sl, tp, r))
    return rows, skips


def run_kind(signals, umap, px, mdates, midx):
    by_t: dict = {}
    for s in signals:
        by_t.setdefault(s["ticker"], []).append(s)
    ts = sorted(by_t)
    rows, skips = [], dict.fromkeys(SKIP_ORDER, 0)
    for i in range(0, len(ts), CHUNK_TICKERS):
        chunk = [s for t in ts[i:i + CHUNK_TICKERS] for s in by_t[t]]
        bars = load_bars_for(chunk, mdates, midx)
        r, sk = run_pipeline(chunk, umap, bars, px, mdates, midx)
        rows.extend(r)
        for k in SKIP_ORDER:
            skips[k] += sk[k]
        del bars
    return rows, skips


def run_placebo(ptrades, allowed, px, mdates, midx, n):
    """같은 진입일·시각, 허용 유니버스 무작위 1종목, 동일 손절폭%, 3R."""
    if not ptrades or n <= 0:
        return [], []
    rng = np.random.default_rng(PLACEBO_SEED)
    by_draw: list = [[] for _ in range(n)]
    for bi in range(n):
        for r in ptrades:
            cands = allowed.get(r["entry_date"], [])
            if not cands:
                continue
            t = cands[int(rng.integers(len(cands)))]
            w = (r["entry_px"] - r["sl"]) / r["entry_px"]
            by_draw[bi].append((t, r["entry_date"], int(r["entry_time"]), w))
    draws, oks = [], []
    for b0 in range(0, n, PLACEBO_BATCH):
        bd = by_draw[b0:b0 + PLACEBO_BATCH]
        keys = set()
        for picks in bd:
            for t, eday, _, _ in picks:
                ei = midx.get(eday)
                if ei is None:
                    continue
                for d in mdates[ei:ei + HOLD_DAYS + 1]:
                    keys.add((d, t))
        bars = batched_day_bars(keys)
        for picks in bd:
            vals = []
            for t, eday, etime, w in picks:
                j = _first_at_or_after(bars.get((eday, t)), etime)
                if j is None:
                    continue
                b = bars[(eday, t)]
                e = float(np.asarray(b["open"], dtype=float)[j])
                if not np.isfinite(e) or e <= 0:
                    continue
                sl = e * (1 - w)
                tp = e + RR * (e - sl)
                ei = midx.get(eday)
                if ei is None or ei + HOLD_DAYS >= len(mdates):
                    continue
                days = [_day_row(d, t, px, bars, mdates, midx)
                        for d in mdates[ei:ei + HOLD_DAYS + 1]]
                r = tp_sl_exit(days, j, e, tp_px=tp, sl_px=sl,
                               hold_days=HOLD_DAYS)
                if not np.isfinite(r["ret"]):
                    continue
                vals.append((eday, float(r["ret"]) - REF_COST))
            oks.append(len(vals))
            if not vals:
                draws.append(np.nan)
                continue
            draws.append(day_weighted_mean(
                pd.DataFrame(vals, columns=["date", "net"]), "net"))
        del bars
    return draws, oks


def closed_df(rows):
    df = pd.DataFrame(rows)
    if not len(df):
        return df
    return df[np.isfinite(df["ret"].to_numpy(dtype=float))].reset_index(drop=True)


def _fin(x):
    try:
        return x is not None and bool(np.isfinite(x))
    except TypeError:
        return False


def fp(x, digits=2):
    return f"{x * 100:+.{digits}f}%" if _fin(x) else "-"


def ft(x, digits=2):
    return f"{x:.{digits}f}" if _fin(x) else "-"


def summarize_kind(df):
    if not len(df):
        return {"n": 0, "days": 0, "mean": np.nan, "t": np.nan,
                "med": np.nan, "win": np.nan, "costs": {}}
    dk = day_key(df["date"])
    mean, t, ndays, _n = weighted(df["net"].to_numpy(float), dk)
    ct = cost_table(df["ret"].to_numpy(float), dk)
    return {"n": len(df), "days": ndays, "mean": mean, "t": t,
            "med": day_median(df, "net"), "win": day_win_rate(df, "net"),
            "costs": {str(c): ct["costs"][str(c)]["mean"] for c in COSTS}}


def build_report(a, mdates, sig_n, dfs, sums, skips, draws, oks,
                 p_mean, p_pct, n_pct):
    L = []
    L.append(f"# RSI 쌍바닥({a.bar_min}분봉) 백테스트 결과")
    L.append("")
    L.append(f"- 지표: RSI 14 (Wilder) / BB 20봉 ±2σ (ddof=0)")
    L.append("- 상태머신: 첫 바닥 후 21봉 유효 / 두 번째 바닥 전 저가 < 하단밴드면 재시작")
    L.append(f"- 손절: 두 번째 바닥 저가 / 목표: 진입 + {RR:g}R"
             f" / 보유: 최대 {HOLD_DAYS}거래일"
             f" ({HOLD_DAYS}번째 거래일 종가 청산)")
    L.append("- 방향: 롱만 / 오버나이트 허용 / 비용: 0.35% (비교 0.6/0.23%)")
    L.append(f"- 기간: {a.since} ~ {mdates[-1] if mdates else '-'}"
             f" / 유니버스: KOSPI·KOSDAQ 전종목 (진입일 D행 ok 4종)")
    lim = f" / limit-tickers {a.limit_tickers}" if a.limit_tickers else ""
    L.append(f"- 신호: pattern {sig_n[0]}건 / naive {sig_n[1]}건{lim}")
    L.append(f"- placebo: {a.placebo}회, seed {PLACEBO_SEED}"
             " (동일 진입일·시각, 허용 유니버스 무작위 1종목, 동일 손절폭%, 3R,"
             " 상한가 가드 없음)")
    L.append("- breaks: ok_jump==False (ticker, f_date)"
             " — ok_jump는 F행 기준이라 점프일 첫 봉부터 새 구간")
    L.append("")
    L.append("## 표1: 전략 비교 (net, 일 가중)")
    L.append("")
    L.append("| kind | n | 거래일수 | 일가중 평균 | t | 일 중앙값 | 일 승률"
             " | 비용0.6% | 비용0.35% | 비용0.23% | placebo pct |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for kind in ("pattern", "naive"):
        s = sums[kind]
        pct = p_pct if kind == "pattern" else n_pct
        L.append(f"| {kind} | {s['n']} | {s['days']} | {fp(s['mean'])}"
                 f" | {ft(s['t'])} | {fp(s['med'])} | {fp(s['win'])}"
                 f" | {fp(s['costs'].get('0.006'))} | {fp(s['costs'].get('0.0035'))}"
                 f" | {fp(s['costs'].get('0.0023'))} | {ft(pct, 1)} |")
    draws_fin = [d for d in draws if _fin(d)]
    mok = sum(oks) / len(oks) if oks else float("nan")
    med_d = statistics.median(draws_fin) if draws_fin else float("nan")
    L.append(f"| placebo | {ft(mok, 1)}/회 | {sums['pattern']['days']} | {fp(p_mean)}"
             f" | - | {fp(med_d)} | - | - | - | - | - |")
    L.append("")
    L.append("## 표2: 청산 사유 분포 (건수·평균 ret)")
    L.append("")
    L.append("| kind | reason | n | 평균 ret |")
    L.append("|---|---|---|---|")
    for kind in ("pattern", "naive"):
        d = dfs[kind]
        if not len(d):
            continue
        for reason, grp in d.groupby("reason", sort=True):
            L.append(f"| {kind} | {reason} | {len(grp)}"
                     f" | {fp(float(grp['ret'].mean()))} |")
    L.append("")
    L.append("## 표3: 월별 pattern (건수·일가중 net)")
    L.append("")
    L.append("| 월 | n | 일가중 net |")
    L.append("|---|---|---|")
    pat = dfs["pattern"]
    if len(pat):
        for m, grp in pat.groupby(month_key(pat["date"]), sort=True):
            L.append(f"| {m} | {len(grp)}"
                     f" | {fp(day_weighted_mean(grp, 'net'))} |")
    L.append("")
    L.append("## 표4: 평균 보유 거래일·평균 손절폭%")
    L.append("")
    L.append("| kind | n | 평균 보유일 | 평균 손절폭% |")
    L.append("|---|---|---|---|")
    midx = {d: i for i, d in enumerate(mdates)}
    for kind in ("pattern", "naive"):
        d = dfs[kind]
        if not len(d):
            L.append(f"| {kind} | 0 | - | - |")
            continue
        holds = [midx[e] - midx[s] for s, e in
                 zip(d["entry_date"].astype(str), d["exit_date"].astype(str))
                 if s in midx and e in midx]
        ah = sum(holds) / len(holds) if holds else float("nan")
        aw = float(d["width_pct"].mean())
        L.append(f"| {kind} | {len(d)} | {ft(ah)} | {ft(aw)} |")
    L.append("")
    L.append("## 표5: skip 사유 건수")
    L.append("")
    L.append("| 사유 | pattern | naive |")
    L.append("|---|---|---|")
    for k in SKIP_ORDER:
        L.append(f"| {SKIP_KO[k]} | {skips['pattern'][k]} | {skips['naive'][k]} |")
    L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="RSI 쌍바닥 60분봉 백테스트")
    ap.add_argument("--since", default=FULL_SINCE)
    ap.add_argument("--bar-min", type=int, default=60)
    ap.add_argument("--placebo", type=int, default=200)
    ap.add_argument("--limit-tickers", type=int, default=None)
    a = ap.parse_args(argv)

    mdates = [str(d) for d in market_dates()]
    midx = {d: i for i, d in enumerate(mdates)}
    umap, allowed, breaks = build_universe(a.since)
    tickers = None
    if a.limit_tickers:
        tickers = set(sorted({t for _, t in umap})[:a.limit_tickers])
    hb = hourly_bars(since=a.since, minutes=a.bar_min)
    pats, naivs = collect_signals(hb, breaks, tickers)
    del hb
    px = build_px(mdates, midx, a.since)

    rows_p, skips_p = run_kind(pats, umap, px, mdates, midx)
    rows_n, skips_n = run_kind(naivs, umap, px, mdates, midx)
    for r in rows_p:
        r["kind"] = "pattern"
    for r in rows_n:
        r["kind"] = "naive"
    dfs = {"pattern": closed_df(rows_p), "naive": closed_df(rows_n)}
    sums = {k: summarize_kind(dfs[k]) for k in dfs}
    skips = {"pattern": skips_p, "naive": skips_n}

    ptrades = [r for r in rows_p if _fin(r["ret"])]
    draws, oks = run_placebo(ptrades, allowed, px, mdates, midx, a.placebo)
    p_mean, p_pct = placebo_percentile(sums["pattern"]["mean"], draws)
    _nm, n_pct = placebo_percentile(sums["naive"]["mean"], draws)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = rows_p + rows_n
    trades = pd.DataFrame(all_rows, columns=CSV_COLS) if all_rows \
        else pd.DataFrame(columns=CSV_COLS)
    trades.to_csv(OUT_DIR / "trades.csv", index=False)
    text = build_report(a, mdates, (len(pats), len(naivs)), dfs, sums,
                        skips, draws, oks, p_mean, p_pct, n_pct)
    (OUT_DIR / "RESULTS.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {OUT_DIR / 'RESULTS.md'}")
    print(f"wrote {OUT_DIR / 'trades.csv'} ({len(trades)} rows)")


if __name__ == "__main__":
    main()
