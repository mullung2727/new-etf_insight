"""P5 평가 (OPEN 트랙, 콤보는 제네릭) — PLAN_JEV_CANDIDATE_JUDGE §2·§7·§9-3·§9-4.

입력: decisions (run_id backtest-open-{D}-{ver}), outcomes (open 트랙),
states (price_pct, n_passed), jev_answers, krx_ohlcv.duckdb.

주 수익률 = ret_open_close, 보조 = ret_open_0930.
보조 지표는 전종목 분봉이 없어 사이즈 벤치 대신 당일 E 평균 대비 초과로 본다.
COMBOS 에 close 트랙 조합을 붙이면 같은 코드로 확장된다.

Usage (repo root, CLI 실행 금지 — 리포트만):
    etl\\.venv\\Scripts\\python.exe -m unittest discover -s research/jev_candidate/tests -t .
"""
from __future__ import annotations

import argparse
import bisect
import math
import sqlite3
import statistics
import sys
import warnings
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import duckdb
import numpy as np
import pandas as pd

from research.high52_strategy.backtest import CAP_EDGES, CAP_LABELS
from research.jev_candidate import questions, store
from research.jev_candidate.questions import QUESTION_SET_VER
from research.jev_candidate.state import price_sentence

COSTS = (0.0023, 0.0035, 0.0060)
SUCCESS_COST = 0.0035
PLACEBO_DRAWS = 1000
PLACEBO_SEED = 20260923
QUESTION_SHUFFLES = 100
QUESTION_SEED = 20260923
CLIP = 0.3
# 성공 판정 대상 — close 트랙 조합을 뒤에 붙이면 확장 (§9-4 4개 조합)
COMBOS = [("open", "A"), ("open", "B")]
ARMS = ("A", "B", "G", "H", "I_A", "I_B", "E")
DROP_REASONS = ("gap_artifact", "no_trade")


def _bucket_case() -> str:
    """CAP_EDGES/CAP_LABELS → duckdb CASE (억, right=False 와 동일)."""
    whens = [
        f"WHEN cap < {edge} THEN '{label}'"
        for edge, label in zip(CAP_EDGES[1:-1], CAP_LABELS[:-1])
    ]
    return "CASE " + " ".join(whens) + f" ELSE '{CAP_LABELS[-1]}' END"


def cap_bucket(cap_eok: float) -> str:
    """억 시총 → 구간 라벨 (CASE 와 같은 경계)."""
    i = bisect.bisect_right(list(CAP_EDGES), cap_eok) - 1
    return CAP_LABELS[max(0, min(i, len(CAP_LABELS) - 1))]


def load_bench(krx_con: duckdb.DuckDBPyConnection, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """한 쿼리로 전 종목 행 추출 — (entry_day, ticker, bucket, r).

    pairs = [(신호일 D, 진입일 E)]. bucket 은 D 시총, r 은 E일
    clip(close/open-1, ±0.3). E일 거래·스팩(스팩/기업인수목적) 제외.
    동일가중 평균은 bench_means() 가 낸다.
    """
    # pairs=(신호일 D, 진입일 E) — INSERT 순서 그대로 bucket_day=D, entry_day=E
    krx_con.execute("CREATE OR REPLACE TEMP TABLE _pairs(bucket_day VARCHAR, entry_day VARCHAR)")
    krx_con.executemany("INSERT INTO _pairs VALUES (?, ?)", pairs)
    return krx_con.execute(f"""
    SELECT p.entry_day AS eday, e.ticker AS ticker,
           {_bucket_case().replace("cap", "b.market_cap/1e8")} AS "bucket",
           GREATEST(-{CLIP}, LEAST({CLIP}, e.close * 1.0 / e.open - 1)) AS r
    FROM _pairs p
    JOIN ohlcv e ON e.date = p.entry_day
    JOIN ohlcv b ON b.ticker = e.ticker AND b.date = p.bucket_day
    LEFT JOIN stock_names s ON s.code = e.ticker
    WHERE e.volume > 0 AND e.open > 0 AND e.close > 0 AND b.market_cap > 0
      AND (s.name IS NULL
           OR (s.name NOT LIKE '%스팩%' AND s.name NOT LIKE '%기업인수목적%'))
    """).df()


def bench_means(df: pd.DataFrame) -> tuple[dict, dict, dict]:
    """행 → (bench[(E,bucket)], tickbucket[(E,ticker)], overall[E])."""
    bench = {(e, b): float(g["r"].mean()) for (e, b), g in df.groupby(["eday", "bucket"])}
    tickbucket = {(r.eday, r.ticker): r.bucket for r in df.itertuples()}
    overall = {e: float(s.mean()) for e, s in df.groupby("eday")["r"]}
    return bench, tickbucket, overall


def load_picks(con: sqlite3.Connection, run_id: str) -> dict[str, list[str]]:
    """decisions → arm → ticker 목록."""
    out: dict[str, list[str]] = {}
    for arm, ticker in con.execute(
        "SELECT arm, ticker FROM decisions WHERE run_id = ? ORDER BY arm, rank, ticker", (run_id,)
    ).fetchall():
        out.setdefault(arm, []).append(ticker)
    return out


def score_day(
    picks_by_arm: dict[str, list[str]],
    universe_tickers: list[str],
    outcomes: dict[str, dict],
    bench_of: dict[str, float],
    default_bench: float = 0.0,
    ret_col: str = "ret_open_close",
) -> dict:
    """하루치 초과수익 — arm별 excess 목록 + 우주 배열 + 드롭.

    limit_up → 0.0 (현금, 벤치 미차감). gap_artifact/no_trade → 전 arm·우주 제외.
    outcomes 없음·수익률 None → 제외. bench 없음 → default_bench.
    """
    drops = {
        t for t, o in outcomes.items() if (o.get("excluded_open") or "") in DROP_REASONS
    }
    missing = {
        t
        for ts in picks_by_arm.values()
        for t in ts
        if t not in drops and t not in outcomes
    } | {t for t in universe_tickers if t not in drops and t not in outcomes}

    def xs(tickers: list[str]) -> tuple[list[float], int]:
        out, noret = [], 0
        for t in tickers:
            if t in drops or t in missing:
                continue
            o = outcomes[t]
            if o.get("excluded_open") == "limit_up":
                out.append(0.0)
                continue
            r = o.get(ret_col)
            if r is None:
                noret += 1
                continue
            out.append(float(r) - bench_of.get(t, default_bench))
        return out, noret

    arm_xs, noret = {}, 0
    for arm, tickers in picks_by_arm.items():
        arm_xs[arm], n = xs(tickers)
        noret += n
    uni, n = xs(list(dict.fromkeys(universe_tickers)))
    return {"arm": arm_xs, "universe": uni, "drops": drops, "missing": missing, "noret": noret + n}


def summarize(values: list[float], n_days_total: int, n_picks: int) -> dict:
    """일별 가중 지표 — values 는 거래일 day value (시간순)."""
    n = len(values)
    no_trade = n_days_total - n
    if n == 0:
        return {"n_days": 0, "no_trade": no_trade, "no_trade_ratio": 0.0,
                "n_picks": 0, "mean": 0.0, "win_rate": 0.0,
                "profit_factor": 0.0, "mdd": 0.0, "t": 0.0}
    mean = sum(values) / n
    pos = sum(v for v in values if v > 0)
    neg = sum(-v for v in values if v < 0)
    pf = pos / neg if neg > 0 else (math.inf if pos > 0 else 0.0)
    peak, mdd = 0.0, 0.0  # 누적 0 에서 출발 — 첫날 손실도 낙폭
    cum = 0.0
    for v in values:
        cum += v
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    sd = statistics.pstdev(values) if n >= 2 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    return {"n_days": n, "no_trade": no_trade,
            "no_trade_ratio": no_trade / n_days_total if n_days_total else 0.0,
            "n_picks": n_picks, "mean": mean,
            "win_rate": sum(1 for v in values if v > 0) / n,
            "profit_factor": pf, "mdd": mdd, "t": t}


def placebo_maxstat(
    universe: dict[str, list[float]],
    picks: dict[str, dict[str, int]],
    cost: float = SUCCESS_COST,
    draws: int = PLACEBO_DRAWS,
    seed: int = PLACEBO_SEED,
) -> tuple[float, np.ndarray]:
    """max 통계량 placebo — 콤보별 독립 추출, 콤보 중 최댓값 분포 → (p95, 분포).

    universe: 날짜 → excess 배열(전처리済み, limit_up 은 0.0 포함).
    picks: 콤보 라벨 → {날짜: k}. k=0 → NO-TRADE.
    """
    rng = np.random.default_rng(seed)
    days = sorted(universe)
    arrs = [np.asarray(universe[d], dtype=float) for d in days]
    combo_means = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for label in picks:
            ks = [picks[label].get(d, 0) for d in days]
            mat = np.full((draws, len(days)), np.nan)
            for j, (a, k) in enumerate(zip(arrs, ks)):
                if k <= 0 or len(a) == 0:
                    continue
                if k >= len(a):
                    mat[:, j] = a.mean() - cost
                else:
                    idx = np.argpartition(rng.random((draws, len(a))), k, axis=1)[:, :k]
                    mat[:, j] = a[idx].mean(axis=1) - cost
            m = np.nanmean(mat, axis=1)
            m[np.isnan(m)] = -np.inf
            combo_means.append(m)
    dist = np.max(np.stack(combo_means), axis=0)
    return float(np.percentile(dist, 95)), dist


def price_controlled_day(groups: dict[str, list[tuple[float, float]]]) -> float | None:
    """하루치 가격 통제 diff — 버킷별 (상위 절반 − 하위 절반) 평균.

    groups: 가격 버킷 → [(jev_score, excess)]. 버킷당 2개 미만 제외.
    홀수는 가운데 1개 버림 (top=n//2, bottom=뒤 n//2).
    """
    diffs = []
    for grp in groups.values():
        n = len(grp)
        if n < 2:
            continue
        s = sorted(grp, key=lambda p: -p[0])
        h = n // 2
        top = [e for _, e in s[:h]]
        bot = [e for _, e in s[n - h:]]
        diffs.append(sum(top) / len(top) - sum(bot) / len(bot))
    return sum(diffs) / len(diffs) if diffs else None


def _ranks(xs: list[float]) -> list[float]:
    """동률 평균 순위 (1-base)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """순위상관 — 분산 0이면 0.0."""
    if len(xs) < 2:
        return 0.0
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else 0.0


def modal_share(qtype: str, values: list[float]) -> float:
    """몰림 점검 — score 는 [0,.5)/[.5,1.5)/[1.5,] 중 최대 비중, noul 은 ≥0.5 비중."""
    if not values:
        return 0.0
    if qtype == "score":
        b = [0, 0, 0]
        for x in values:
            b[0 if x < 0.5 else 1 if x < 1.5 else 2] += 1
        return max(b) / len(values)
    return sum(1 for x in values if x >= 0.5) / len(values)


def question_stats(
    rows: list[tuple[str, str, float, float]],
    shuffles: int = QUESTION_SHUFFLES,
    seed: int = QUESTION_SEED,
) -> dict[str, dict]:
    """문항별 기술 통계 — rows: (day, qid, 답값, excess).

    rho = 답값-excess Spearman. p = 일자 내 셔플 placebo 중 |ρ| ≥ |실측| 비중.
    """
    rng = np.random.default_rng(seed)
    by_qid: dict[str, list[tuple[str, float, float]]] = {}
    for day, qid, v, e in rows:
        by_qid.setdefault(qid, []).append((day, v, e))
    out = {}
    for qid, rs in by_qid.items():
        days = [d for d, _, _ in rs]
        vals = [v for _, v, _ in rs]
        excs = [e for _, _, e in rs]
        rho = spearman(vals, excs)
        idx_by_day: dict[str, list[int]] = {}
        for i, d in enumerate(days):
            idx_by_day.setdefault(d, []).append(i)
        groups = list(idx_by_day.values())
        hit = 0
        for _ in range(shuffles):
            sv = list(vals)
            for g in groups:
                perm = rng.permutation(len(g))
                tmp = [sv[g[k]] for k in perm]
                for k, v in zip(g, tmp):
                    sv[k] = v
            if abs(spearman(sv, excs)) >= abs(rho):
                hit += 1
        qtype = "noul" if qid in ("q4", "q5", "q6", "q10") else "score"
        out[qid] = {"n": len(rs), "rho": rho, "p": hit / shuffles,
                    "modal": modal_share(qtype, vals)}
    return out


def _period(day: str) -> str:
    """D(YYYYMMDD) → train(01~05)/test(06~09)/other."""
    m = day[:6]
    if "202601" <= m <= "202605":
        return "train"
    if "202606" <= m <= "202609":
        return "test"
    return "other"


def run_eval(
    con: sqlite3.Connection, krx_con: duckdb.DuckDBPyConnection,
    d_from: str, d_to: str, ver: str = QUESTION_SET_VER,
) -> dict:
    """범위 내 일자 평가 — 일자별 arm 초과·우주·k·가격·문항 행 조립."""
    run_like = f"backtest-open-%-{ver}"
    days = sorted(
        r[0] for r in con.execute(
            "SELECT DISTINCT date FROM decisions WHERE run_id LIKE ? AND date >= ? AND date <= ?",
            (run_like, d_from, d_to),
        ).fetchall()
    )
    trading = [r[0] for r in krx_con.execute("SELECT DISTINCT date FROM ohlcv ORDER BY date").fetchall()]
    pos = {d: i for i, d in enumerate(trading)}
    res: dict = {"days": [], "skipped_no_outcomes": 0, "skipped_no_entry": 0,
                 "drops": Counter(), "missing": 0, "noret": 0, "fallbacks": 0}
    wanted: list[tuple[str, str]] = []
    for day in days:
        if day in pos and pos[day] + 1 < len(trading):
            wanted.append((day, trading[pos[day] + 1]))
        else:
            res["skipped_no_entry"] += 1
    bench, tickbucket, overall = bench_means(load_bench(krx_con, wanted)) if wanted else ({}, {}, {})
    for day, entry in wanted:
        outcomes = {(d, t): o for (d, t), o in store.load_outcomes(con, [day]).items()}
        out = {t: o for (d, t), o in outcomes.items()}
        if not out:
            res["skipped_no_outcomes"] += 1
            continue
        run_id = f"backtest-open-{day}-{ver}"
        picks = load_picks(con, run_id)
        states = {s["ticker"]: s for s in store.load_states(con, run_id)}
        answers = store.load_answers(con, run_id)
        e_base = picks.get("E", [s for s in states])
        day_drops = {
            t for t, o in out.items() if (o.get("excluded_open") or "") in DROP_REASONS
        }
        bench_of: dict[str, float] = {}
        for t in (set(e_base) | {t for ts in picks.values() for t in ts}) - day_drops:
            b = bench.get((entry, tickbucket.get((entry, t), "")))
            if b is None:
                b = overall.get(entry, 0.0)
                res["fallbacks"] += 1
            bench_of[t] = b
        day_r = score_day(picks, e_base, out, bench_of, default_bench=overall.get(entry, 0.0))
        res["drops"].update(
            out[t].get("excluded_open") for t in day_r["drops"] if out[t].get("excluded_open")
        )
        res["missing"] += len(day_r["missing"])
        sec = score_day(picks, e_base, out, {}, ret_col="ret_open_0930")
        res["noret"] += sec["noret"]
        e_sec = sec["arm"].get("E", sec["universe"])
        e_mean = sum(e_sec) / len(e_sec) if e_sec else 0.0
        sec_xs = {a: [x - e_mean for x in xs] for a, xs in sec["arm"].items()}
        price_groups: dict[str, list[tuple[float, float]]] = {}
        q_rows: list[tuple[str, str, float, float]] = []
        ans_score = {t: questions.jev_score(rs) for t, rs in answers.items()}
        for t, s in states.items():
            if t in day_r["drops"] or t not in out or t not in answers:
                continue
            o = out[t]
            if o.get("excluded_open") == "limit_up":
                e = 0.0
            elif o.get("ret_open_close") is None:
                continue
            else:
                e = float(o["ret_open_close"]) - bench_of.get(t, overall.get(entry, 0.0))
            bucket = price_sentence(float(s["price_pct"]))
            price_groups.setdefault(bucket, []).append((ans_score[t], e))
            for r in answers[t]:
                if r["type"] in ("score", "noul"):
                    q_rows.append((day, r["qid"], float(r["value"]), e))
        k = {f"{t}/{a}": len(day_r["arm"].get(a, [])) for t, a in COMBOS}
        res["days"].append({"day": day, "period": _period(day), "arm": day_r["arm"],
                            "universe": day_r["universe"], "k": k,
                            "sec": sec_xs, "price": price_groups, "q": q_rows})
    return res


def period_metrics(res: dict, period: str, ret_key: str = "arm") -> dict[str, dict[float, dict]]:
    """기간·arm·비용별 지표 — ret_key arm(주)/sec(보조)."""
    arms = sorted({a for d in res["days"] if d["period"] == period for a in d[ret_key]})
    out: dict[str, dict[float, dict]] = {}
    for arm in arms:
        means, n_picks, n_total = [], 0, 0
        for d in res["days"]:
            if d["period"] != period:
                continue
            n_total += 1
            xs = d[ret_key].get(arm, [])
            if xs:
                means.append(sum(xs) / len(xs))
                n_picks += len(xs)
        out[arm] = {c: summarize([m - c for m in means], n_total, n_picks) for c in COSTS}
    return out


def success_table(res: dict) -> tuple[dict, float, dict]:
    """TEST · 비용 0.0035 · §9-4 ①~⑤ 판정 → (행, placebo p95, 가격통제)."""
    test = [d for d in res["days"] if d["period"] == "test"]
    pm = period_metrics(res, "test")
    mean = lambda arm: pm.get(arm, {}).get(SUCCESS_COST, {}).get("mean", 0.0)
    uni = {d["day"]: d["universe"] for d in test}
    ks = {f"{t}/{a}": {d["day"]: d["k"].get(f"{t}/{a}", 0) for d in test} for t, a in COMBOS}
    p95, _ = placebo_maxstat(uni, ks) if test else (0.0, np.array([]))
    diffs = [v for d in test if (v := price_controlled_day(d["price"])) is not None]
    pc = {"n_days": len(diffs), "mean": sum(diffs) / len(diffs) if diffs else 0.0,
          "t": summarize(diffs, len(diffs), 0)["t"] if diffs else 0.0}
    rows = {}
    for track, arm in COMBOS:
        m = mean(arm)
        i_arm = f"I_{arm}"
        checks = {
            "1>0": m > 0,
            "2>placebo": m > p95,
            "3>E": m > mean("E"),
            "4>G,H,I": m > mean("G") and m > mean("H") and m > mean(i_arm),
            "5>가격통제": pc["mean"] > 0,
        }
        rows[f"{track}/{arm}"] = {"mean": m, "checks": checks, "pass": all(checks.values())}
    return rows, p95, pc


def _f(x: float) -> str:
    """지표 포맷 — inf·nan 그대로."""
    if isinstance(x, float) and math.isinf(x):
        return "inf"
    if isinstance(x, float) and math.isnan(x):
        return "nan"
    return f"{x:.4f}"


def render(res: dict, d_from: str, d_to: str) -> str:
    """마크다운 리포트 조립."""
    lines = [f"# Jev 후보 평가 (open) — {d_from}~{d_to} (v{QUESTION_SET_VER})", ""]
    n_eval = len(res["days"])
    lines.append(f"- 평가일 {n_eval}, outcomes 없음 {res['skipped_no_outcomes']}, "
                 f"진입일 없음 {res['skipped_no_entry']}, outcomes 누락 티커 {res['missing']}, "
                 f"0930 없음 {res['noret']}, 벤치 폴백 {res['fallbacks']}")
    drop_s = ", ".join(f"{k} {v}" for k, v in sorted(res["drops"].items())) or "없음"
    lines.append(f"- 드롭 사유: {drop_s}")
    for period in ("train", "test"):
        lines += ["", f"## 주 지표 (ret_open_close 초과, {period})", "",
                  "| arm | cost | n_days | no_trade | ratio | n_picks | mean | win | pf | mdd | t |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for arm, by_cost in sorted(period_metrics(res, period).items()):
            for c in COSTS:
                m = by_cost[c]
                lines.append(f"| {arm} | {c:.4f} | {m['n_days']} | {m['no_trade']} | "
                             f"{m['no_trade_ratio']:.2f} | {m['n_picks']} | {_f(m['mean'])} | "
                             f"{m['win_rate']:.2f} | {_f(m['profit_factor'])} | {_f(m['mdd'])} | {_f(m['t'])} |")
    lines += ["", "## 보조 지표 (ret_open_0930, 당일 E 대비, test)", "",
              "| arm | cost | n_days | no_trade | n_picks | mean | win | pf | mdd | t |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for arm, by_cost in sorted(period_metrics(res, "test", ret_key="sec").items()):
        for c in COSTS:
            m = by_cost[c]
            lines.append(f"| {arm} | {c:.4f} | {m['n_days']} | {m['no_trade']} | {m['n_picks']} | "
                         f"{_f(m['mean'])} | {m['win_rate']:.2f} | {_f(m['profit_factor'])} | "
                         f"{_f(m['mdd'])} | {_f(m['t'])} |")
    rows, p95, pc = success_table(res)
    lines += ["", f"## placebo F (max 통계량, {PLACEBO_DRAWS}회, seed={PLACEBO_SEED})", "",
              f"- p95 = {_f(p95)} (콤보: {', '.join(f'{t}/{a}' for t, a in COMBOS)})", "",
              "## 성공 판정 (§9-4, TEST, cost 0.0035)", ""]
    for label, r in rows.items():
        marks = " ".join(f"{k}={'✓' if v else '✗'}" for k, v in r["checks"].items())
        lines.append(f"- {label}: mean={_f(r['mean'])} {marks} → {'통과' if r['pass'] else '탈락'}")
    lines += ["", "## 가격 통제 비교 ⑤ (test)", "",
              f"- 일별 버킷 diff 평균 = {_f(pc['mean'])}, t = {_f(pc['t'])}, n_days = {pc['n_days']}", "",
              "## 문항 분석 (test, Spearman vs 주 초과)", "",
              "| qid | n | rho | p(셔플) | 몰림 |",
              "|---|---|---|---|---|"]
    q_all = [row for d in res["days"] if d["period"] == "test" for row in d["q"]]
    for qid, s in sorted(question_stats(q_all).items()):
        lines.append(f"| {qid} | {s['n']} | {s['rho']:.3f} | {s['p']:.2f} | {s['modal']:.2f} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI — 범위 평가 → stdout + md 저장."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="20260102")
    ap.add_argument("--to", dest="date_to", default="20260921")
    ap.add_argument("--out", default="temp/jev_review/eval_open.md")
    ap.add_argument("--db", default=str(store.DEFAULT_DB))
    ap.add_argument("--krx", default=str(ROOT / "etl" / "db" / "krx_ohlcv.duckdb"))
    args = ap.parse_args(argv)
    con = store.connect(args.db)
    store.ensure_schema(con)
    krx_con = duckdb.connect(str(args.krx), read_only=True)
    try:
        res = run_eval(con, krx_con, args.date_from, args.date_to)
    finally:
        krx_con.close()
    text = render(res, args.date_from, args.date_to)
    print(text, end="")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
