"""IS 드라이버 — 주간 클러스터링 + 일간 선정/매매 + 벤치마크.

일봉 대리 단계: 진입 당일 종가, 청산 익일 시가.
실행형(15:15/15:19)은 분봉 구간에서 진입가만 교체한다.
"""
from __future__ import annotations

import random
import sys
import time

from .cluster import cluster_labels, standardize
from .data import (
    build_weekly_matrix,
    load_daily,
    matrix_from_series,
    weekly_series,
)
from .runner import (
    ROUNDTRIP_COST,
    SL_PCT,
    TP_PCT,
    PriorHighIndex,
    build_prior_high,
    build_volume_profile,
    close_position,
    distance_high_60,
    entry_allowed,
    exit_allowed,
    exit_tpsl,
    net_return,
    prior_max_high,
    summarize,
    vp_overhead_ratio,
)
from .select import cluster_counts, pick_cluster, pick_stocks, top_by_turnover
from .weekly import week_key

BENCHES = ("strategy", "bench_a", "bench_b")

HORIZON = {"open1": 1, "h1": 1, "h2": 2}

VARIANT_NAMES = ("base", "LM1", "LM2", "CP1", "CP2", "CP3",
                 "HP1", "HP2", "HP3", "HP4",
                 "CSR50", "CSR60", "CSR70", "CSR75", "CSR80",
                 "CSN05", "CSN10",
                 "CSM1", "CSM2", "CSM3", "CSS30", "CSS50",
                 "VP05", "VP10", "VP20", "VPB")

_CP_TH = {"CP1": 0.6, "CP2": 0.7, "CP3": 0.8}
_VP_TH = {"VP05": 0.05, "VP10": 0.10, "VP20": 0.20}
_HP_TH = {"HP1": -0.10, "HP2": -0.05, "HP3": -0.03}
_CSR_TH = {"CSR50": 0.50, "CSR60": 0.60, "CSR70": 0.70,
           "CSR75": 0.75, "CSR80": 0.80}
_CSN_TH = {"CSN05": 5, "CSN10": 10}
_CSM_TH = {"CSM1": 0.01, "CSM2": 0.02, "CSM3": 0.03}
_CSS_TH = {"CSS30": 0.30, "CSS50": 0.50}
CS_VARIANTS = tuple(list(_CSR_TH) + list(_CSN_TH)
                    + list(_CSM_TH) + list(_CSS_TH))


def cluster_stats(assignment: dict[str, int], cluster: int,
                    day_rows_by_ticker, prev_close_lookup) -> dict:
    """선정 클러스터의 당일 수익률 통계 → dict.

    members = 해당 클러스터에 배정되고 당일 행이 있으며 ms-1 직전봉이
    있는 종목. ret = close / prev_close - 1, [-0.30, +0.30] 클리핑.
    up = ret > 0, strong_up = ret >= 0.03. members 0이면 mean 0.0.
    """
    if isinstance(day_rows_by_ticker, list):
        rows = {r["ticker"]: r for r in day_rows_by_ticker}
    else:
        rows = day_rows_by_ticker
    up, members, strong_up = 0, 0, 0
    ret_sum = 0.0
    for ticker, c in assignment.items():
        if c != cluster:
            continue
        row = rows.get(ticker)
        if row is None:
            continue
        ms, close = row.get("ms"), row.get("close")
        if ms is None or close is None:
            continue
        if callable(prev_close_lookup):
            prev = prev_close_lookup(ticker, ms)
        else:
            prev = prev_close_lookup.get((ticker, ms - 1))
        if prev is None:
            continue
        members += 1
        ret = close / prev - 1 if prev else 0.0
        ret = max(-0.30, min(0.30, ret))
        ret_sum += ret
        if ret > 0:
            up += 1
        if ret >= 0.03:
            strong_up += 1
    return {"up": up, "members": members,
            "mean_ret": (ret_sum / members if members else 0.0),
            "strong_up": strong_up}


def cluster_breadth(assignment: dict[str, int], cluster: int,
                    day_rows_by_ticker, prev_close_lookup) -> tuple[int, int]:
    """선정 클러스터의 당일 상승 breadth → (up_count, member_count).

    members = 해당 클러스터에 배정되고 당일 행이 있으며 ms-1 직전봉이
    있는 종목. up = 당일 close > 직전 close (strict). 직전봉 없으면
    양쪽 카운트에서 제외. prev_close_lookup(ticker, ms) → 직전 close.
    """
    s = cluster_stats(assignment, cluster,
                      day_rows_by_ticker, prev_close_lookup)
    return (s["up"], s["members"])


def passes_breadth(variant: str, up_count: int, member_count: int,
                   mean_ret: float = 0.0, strong_up: int = 0) -> bool:
    """CS 게이트 통과 여부. member 0이면 실패. 비-CS 변형은 True."""
    if variant in _CSR_TH:
        if member_count <= 0:
            return False
        return up_count / member_count >= _CSR_TH[variant]
    if variant in _CSN_TH:
        if member_count <= 0:
            return False
        return up_count >= _CSN_TH[variant]
    if variant in _CSM_TH:
        if member_count <= 0:
            return False
        return mean_ret >= _CSM_TH[variant]
    if variant in _CSS_TH:
        if member_count <= 0:
            return False
        return strong_up / member_count >= _CSS_TH[variant]
    return True


def passes_variant(variant: str, row: dict, feats: dict) -> bool:
    """후보 행 + feature에 대한 변형별 통과 여부. 결측(None)은 탈락."""
    if variant == "base":
        return True
    if variant in CS_VARIANTS:
        return True
    if variant == "LM1":
        a, b = feats.get("p1520"), feats.get("p1500")
        return a is not None and b is not None and a > b
    if variant == "LM2":
        a, b, c = feats.get("p1520"), feats.get("p1500"), feats.get("p1430")
        return (a is not None and b is not None and c is not None
                and a > b > c)
    if variant in _CP_TH:
        cp = feats.get("close_position")
        return cp is not None and cp >= _CP_TH[variant]
    if variant in _HP_TH:
        d = feats.get("distance_high_60")
        return d is not None and d >= _HP_TH[variant]
    if variant == "HP4":
        prior = feats.get("prior_max_high")
        close = row.get("close")
        return (prior is not None and close is not None
                and close >= prior)
    if variant in _VP_TH:
        ov = feats.get("vp_overhead")
        return ov is not None and ov <= _VP_TH[variant]
    if variant == "VPB":
        poc = feats.get("vp_poc_high")
        prev = feats.get("vp_prev_close")
        close = row.get("close")
        return (poc is not None and prev is not None
                and close is not None and prev < poc and close >= poc)
    raise ValueError(f"unknown variant: {variant}")


def make_variant_predicate(variant: str, date: str, prior_lookup,
                           lm_features: dict | None = None,
                           vp_lookup=None, vp_cache=None,
                           prev_close_lookup=None):
    """해당 날짜용 pick_stocks predicate. base/CS면 None(필터 없음)."""
    if variant == "base" or variant in CS_VARIANTS:
        return None
    lm_features = lm_features or {}

    def pred(row: dict) -> bool:
        prior = None
        ms = row.get("ms")
        ticker = row.get("ticker")
        if ms is not None:
            prior = prior_lookup(ticker, ms)
        feats = {
            "close_position": close_position(row),
            "prior_max_high": prior,
            "distance_high_60": distance_high_60(row.get("close"), prior),
            "p1430": None, "p1500": None, "p1520": None,
        }
        lm = (lm_features or {}).get(f"{row['ticker']}|{date}") or {}
        for key in ("p1430", "p1500", "p1520"):
            feats[key] = lm.get(key)
        if variant in _VP_TH or variant == "VPB":
            vp = None
            if vp_lookup is not None and ms is not None and ticker is not None:
                key = (ticker, ms)
                if vp_cache is not None and key in vp_cache:
                    vp = vp_cache[key]
                else:
                    vp = vp_lookup(ticker, ms)
                    if vp_cache is not None:
                        vp_cache[key] = vp
            feats["vp_overhead"] = (vp_overhead_ratio(vp, row.get("close"))
                                    if vp is not None else None)
            feats["vp_poc_high"] = vp.get("poc_high") if vp is not None else None
            if callable(prev_close_lookup):
                feats["vp_prev_close"] = prev_close_lookup(ticker, ms)
            elif prev_close_lookup is not None:
                feats["vp_prev_close"] = prev_close_lookup.get(
                    (ticker, ms - 1 if ms is not None else None))
            else:
                feats["vp_prev_close"] = None
        return passes_variant(variant, row, feats)

    return pred


def _bar(row: dict) -> dict:
    return {"open_price": row["open_price"], "high_price": row["high_price"],
            "low_price": row["low_price"], "close": row["close"]}


def _ms_gap(entry_ms, day_ms, lag: int) -> bool:
    """lag일 뒤 봉 ms 불연속이면 True. ms 없으면 검사 생략(기존 패턴)."""
    if entry_ms is None or day_ms is None:
        return False
    return day_ms != entry_ms + lag


def build_index(rows: list[dict]):
    idx = {(r["ticker"], r["date"]): r for r in rows}
    by_t: dict[str, list[str]] = {}
    for r in rows:
        by_t.setdefault(r["ticker"], []).append(r["date"])
    for t in by_t:
        by_t[t] = sorted(set(by_t[t]))
    return idx, by_t


def price_nets(picks: list[str], idx: dict, by_t: dict, date: str,
               exit: str = "open1"):
    """진입가/청산가/가드 적용 순수익률 + 제외 수 + 청산사유 집계."""
    if exit not in HORIZON:
        raise ValueError(f"unknown exit: {exit}")
    nets, excluded, reasons = [], 0, {}

    def _count(reason: str, px: float, entry: float):
        nets.append(net_return(entry, px))
        reasons[reason] = reasons.get(reason, 0) + 1

    for t in picks:
        row = idx[(t, date)]
        ds = by_t[t]
        i = ds.index(date)
        entry = row["close"]
        ms = row.get("ms")
        if i + 1 >= len(ds):
            excluded += 1
            continue
        nrow = idx[(t, ds[i + 1])]
        if _ms_gap(ms, nrow.get("ms"), 1):  # 거래정지 갭 → 청산 불가
            excluded += 1
            continue
        prev_close = None
        if i > 0:
            prow = idx[(t, ds[i - 1])]
            pms = prow.get("ms")
            if ms is not None and pms is not None:
                if pms == ms - 1:
                    prev_close = prow["close"]
            else:
                prev_close = prow["close"]
        if not entry_allowed(entry, prev_close):
            excluded += 1
            continue
        if not exit_allowed(nrow["open_price"], entry):
            excluded += 1
            continue
        if exit == "open1":
            _count("open", nrow["open_price"], entry)
        elif exit in ("h1", "h2"):
            bar1 = _bar(nrow)
            px, reason = exit_tpsl(entry, [bar1], TP_PCT, SL_PCT)
            if exit == "h1" or reason != "time":
                _count(reason, px, entry)
            else:
                if i + 2 >= len(ds):
                    excluded += 1
                    continue
                nrow2 = idx[(t, ds[i + 2])]
                if _ms_gap(ms, nrow2.get("ms"), 2):
                    excluded += 1
                    continue
                if not exit_allowed(nrow2["open_price"], nrow["close"]):
                    excluded += 1
                    continue
                px, reason = exit_tpsl(entry, [bar1, _bar(nrow2)],
                                       TP_PCT, SL_PCT)
                _count(reason, px, entry)
    return nets, excluded, reasons


def run_day(day_rows: list[dict], idx: dict, by_t: dict, date: str,
            assignment: dict[str, int], n_min: int, seed: int = 42,
            exit: str = "open1", predicate=None, gate_pass: bool = True) -> dict:
    top30 = top_by_turnover(day_rows)
    names = [r["ticker"] for r in top30]
    strat_picks: list[str] = []
    picked = pick_cluster(cluster_counts(top30, assignment), n_min)
    if picked is not None and gate_pass:
        strat_picks = pick_stocks(top30, picked, assignment,
                                  predicate=predicate)
    rng = random.Random(seed + int(date))
    bench_b = rng.sample(names, min(3, len(names)))
    out = {}
    for key, picks in (("strategy", strat_picks),
                       ("bench_a", names[:3]), ("bench_b", bench_b)):
        nets, excluded, reasons = price_nets(picks, idx, by_t, date,
                                             exit=exit)
        out[key] = {"picks": picks, "nets": nets, "excluded": excluded,
                    "reasons": reasons}
    return out


def assign_weekly(rows: list[dict], week_end: str, k: int, weeks: int = 52):
    grid, mat = build_weekly_matrix(
        [r for r in rows if r["date"] <= week_end], weeks=weeks, end=week_end)
    items = [(t, standardize(v)) for t, v in mat.items()]
    items = [(t, z) for t, z in items if z is not None]
    if not items:
        return {}
    labels = cluster_labels([z for _, z in items], k)
    return {t: int(lab) for (t, _), lab in zip(items, labels)}


def assign_many(series: dict[str, dict[str, float]], cutoff: str,
                ks: list[int], weeks: int = 52) -> dict[int, dict[str, int]]:
    """한 리밸런스 주의 행렬을 한 번만 만들고 모든 k에 재사용."""
    _, mat = matrix_from_series(series, weeks=weeks, end=cutoff)
    items = [(t, standardize(v)) for t, v in mat.items()]
    items = [(t, z) for t, z in items if z is not None]
    if not items:
        return {}
    feats = [z for _, z in items]
    return {k: {t: int(lab) for (t, _), lab in
                zip(items, cluster_labels(feats, k))} for k in ks}


def run_backtest(con, trade_start: str, trade_end: str, ks: list[int],
                 ns: list[int], weeks: int = 52, seed: int = 42,
                 warmup_start: str = "20210802",
                 exits: list[str] | None = None,
                 variants: list[str] | None = None,
                 lm_features: dict | None = None,
                 dump_daily: dict | None = None) -> dict:
    exits = list(exits) if exits else ["open1"]
    for e in exits:
        if e not in HORIZON:
            raise ValueError(f"unknown exit: {e}")
    variants = list(variants) if variants else ["base"]
    for v in variants:
        if v not in VARIANT_NAMES:
            raise ValueError(f"unknown variant: {v}")
    # retention 분모용 base는 항상 내부 계산한다.
    effective = ["base"] + [v for v in variants if v != "base"]
    rows = load_daily(con, warmup_start, trade_end)
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    days = sorted(by_date)
    idx, by_t = build_index(rows)
    series = weekly_series(rows)
    prior_lookup = build_prior_high(rows)
    vp_lookup = build_volume_profile(rows)
    vp_cache: dict = {}
    # breadth용 직전종가 캐시: (ticker, ms) → close, 한 번만 구축.
    close_by_ms: dict[tuple, float] = {}
    for r in rows:
        ms, close = r.get("ms"), r.get("close")
        if ms is not None and close is not None:
            close_by_ms[(r["ticker"], ms)] = close
    _prev_close_cache: dict[tuple, float | None] = {}

    def prev_close_for(ticker: str, ms) -> float | None:
        if ms is None:
            return None
        key = (ticker, ms)
        if key in _prev_close_cache:
            return _prev_close_cache[key]
        prev = close_by_ms.get((ticker, ms - 1))
        _prev_close_cache[key] = prev
        return prev

    stats_cache: dict[tuple, dict] = {}
    acc = {(k, n): {v: {e: ({s: [] for s in BENCHES} |
                            {f"{s}_daily": [] for s in BENCHES} |
                            {"bench_a_same": [], "bench_b_same": [],
                             "excluded": 0, "reasons": {}})
                        for e in exits}
                    for v in effective}
           for k in ks for n in ns}
    if dump_daily is not None:
        for k in ks:
            for n in ns:
                dump_daily.setdefault(f"K{k}_N{n}", {})
                for v in effective:
                    dump_daily[f"K{k}_N{n}"].setdefault(v, {})
                    for e in exits:
                        dump_daily[f"K{k}_N{n}"][v].setdefault(e, [])
    t0 = time.time()
    last_week, assign = None, {}
    for d in days:
        w = week_key(d)
        if w != last_week:
            last_week = w
            hist = [x for x in days if x < d and week_key(x) < w]
            assign = {}
            print(f"{w} {time.time() - t0:.1f}s", file=sys.stderr)
            if not hist:
                continue
            assign = assign_many(series, hist[-1], ks, weeks)
        if d < trade_start or d > trade_end:
            continue
        day_rows = by_date[d]
        rows_by_t = {r["ticker"]: r for r in day_rows}
        day_top30 = top_by_turnover(day_rows)
        for k in ks:
            assign_k = assign.get(k, {})
            counts_k = cluster_counts(day_top30, assign_k)
            for n in ns:
                picked = pick_cluster(counts_k, n)
                stats = None
                if picked is not None:
                    bkey = (d, k, picked)
                    stats = stats_cache.get(bkey)
                    if stats is None:
                        stats = cluster_stats(assign_k, picked,
                                              rows_by_t, prev_close_for)
                        stats_cache[bkey] = stats
                for v in effective:
                    if v in CS_VARIANTS and picked is not None:
                        gate_pass = passes_breadth(
                            v, stats["up"], stats["members"],
                            stats["mean_ret"], stats["strong_up"])
                    else:
                        gate_pass = True
                    pred = make_variant_predicate(v, d, prior_lookup,
                                                  lm_features, vp_lookup,
                                                  vp_cache, prev_close_for)
                    for e in exits:
                        out = run_day(day_rows, idx, by_t, d,
                                      assign_k, n, seed, e,
                                      predicate=pred, gate_pass=gate_pass)
                        a = acc[(k, n)][v][e]
                        # 벤치 픽은 변형과 무관하게 날마다 같다.
                        for s in BENCHES:
                            nets = out[s]["nets"]
                            a[s].extend(nets)
                            if nets:
                                day_mean = sum(nets) / len(nets)
                                a[f"{s}_daily"].append(day_mean)
                                if (s == "strategy"
                                        and dump_daily is not None):
                                    dump_daily[f"K{k}_N{n}"][v][e].append(
                                        [d, day_mean])
                        a["excluded"] += out["strategy"]["excluded"]
                        for r, c in out["strategy"]["reasons"].items():
                            a["reasons"][r] = a["reasons"].get(r, 0) + c
                        # same_days는 이 변형이 거래한 날만.
                        if out["strategy"]["nets"]:
                            for b in ("bench_a", "bench_b"):
                                bnets = out[b]["nets"]
                                if bnets:
                                    key = f"{b}_same"
                                    a[key].append(sum(bnets) / len(bnets))
    out_all = {}
    for k in ks:
        for n in ns:
            out_all[(k, n)] = {}
            for v in effective:
                out_all[(k, n)][v] = {}
                for e in exits:
                    a = acc[(k, n)][v][e]
                    out_all[(k, n)][v][e] = {
                        "daily": {s: summarize(a[f"{s}_daily"])
                                  for s in BENCHES},
                        "trades": {s: summarize(a[s]) for s in BENCHES},
                        "bench_a_same_days": summarize(a["bench_a_same"]),
                        "bench_b_same_days": summarize(a["bench_b_same"]),
                        "excluded": a["excluded"],
                        "reasons": dict(a["reasons"]),
                    }
            for v in effective:
                for e in exits:
                    base_n = out_all[(k, n)]["base"][e]["trades"][
                        "strategy"]["n"]
                    if v == "base":
                        out_all[(k, n)][v][e]["retention"] = 1.0
                    else:
                        n_tr = out_all[(k, n)][v][e]["trades"][
                            "strategy"]["n"]
                        out_all[(k, n)][v][e]["retention"] = (
                            n_tr / base_n if base_n else 0.0)
    return out_all


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import os

    ap = argparse.ArgumentParser()
    ap.add_argument("--trade-start", required=True)
    ap.add_argument("--trade-end", required=True)
    ap.add_argument("--ks", required=True)
    ap.add_argument("--ns", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exits", default="open1")
    ap.add_argument("--variants", default="base")
    ap.add_argument("--lm-features", default=None)
    ap.add_argument("--warmup-start", default="20210802")
    ap.add_argument("--dump-daily", default=None)
    args = ap.parse_args(argv)
    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    ns = [int(x) for x in args.ns.split(",") if x.strip()]
    exits = [x for x in args.exits.split(",") if x.strip()] or ["open1"]
    variants = [x for x in args.variants.split(",") if x.strip()] or ["base"]
    lm_features = None
    if args.lm_features:
        with open(args.lm_features) as f:
            lm_features = json.load(f)
    import duckdb

    con = duckdb.connect("etl/db/krx_ohlcv.duckdb", read_only=True)
    dump_daily: dict | None = {} if args.dump_daily else None
    try:
        out = run_backtest(con, args.trade_start, args.trade_end, ks, ns,
                           exits=exits, variants=variants,
                           lm_features=lm_features,
                           warmup_start=args.warmup_start,
                           dump_daily=dump_daily)
    finally:
        con.close()
    eff = ["base"] + [v for v in variants if v != "base"]
    res = {f"K{k}_N{n}": {v: {e: out[(k, n)][v][e] for e in exits}
                          for v in eff}
           for k in ks for n in ns}
    res["_spec"] = {"tp": TP_PCT, "sl": SL_PCT, "cost": ROUNDTRIP_COST,
                    "exits": exits, "variants": eff,
                    "trade_start": args.trade_start,
                    "trade_end": args.trade_end, "ks": ks, "ns": ns,
                    "warmup_start": args.warmup_start}
    d = os.path.dirname(args.out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(res, f)
    if args.dump_daily:
        dd = os.path.dirname(args.dump_daily)
        if dd:
            os.makedirs(dd, exist_ok=True)
        with open(args.dump_daily, "w") as f:
            json.dump(dump_daily, f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
