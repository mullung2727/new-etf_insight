"""공용 평가: walkforward·cv 둘 다 사용. 종목별 모델/플라시보/bh/오라클 + 동일가중 요약."""

from __future__ import annotations

import numpy as np

from .costs import ETF_CODES
from .oracle import BUY, HOLD, K_MAX
from .replay import placebo_random, placebo_shift, placebo_timing, policy_run, run


def _buy_and_hold(n: int) -> np.ndarray:
    """첫 칸 BUY, 나머지 HOLD. 장중 보유(bh)용."""
    a = np.full(n, HOLD, dtype=int)
    if n > 0:
        a[0] = BUY
    return a


def strata(codes: list[str]) -> dict[str, list[str]]:
    """ETF_CODES 면 etf, 나머지 stock. 입력 순서 유지."""
    s: dict[str, list[str]] = {"stock": [], "etf": []}
    for c in codes:
        s["etf" if c in ETF_CODES else "stock"].append(c)
    return s


def eval_codes(model, scaler, names, cells: dict, codes: list[str], reps: int, rng) -> dict:
    """codes 각각 policy_run(model.decide_fn) → 총 로그보상·매수 수. 같은 종목에 placebo_random,
    placebo_timing(같은 매수 수), placebo_shift(모델 행동열 구간 이동) reps 개.

    장중 보유 bh = 첫 칸 BUY 만 넣은 run (마지막 칸 강제 청산).
    oracle = cells 의 q 에서 q[0,0,K_MAX].max().
    model_slip1 = 모델 행동열을 slip=1 로 다시 재생한 수익.
    반환 {code: {"model": r, "model_slip1": r, "trades": n, "log_total": lr, "log_total_slip1": lr,
    "bh": r, "oracle": r, "pr": array(reps), "pt": array(reps), "ps": array(reps)}}
    """
    per = {}
    for c in codes:
        e = cells[c]
        ask, bid, aq, bq = e["ex_ask1"], e["ex_bid1"], e["ex_aq1"], e["ex_bq1"]
        m0 = float(e.get("mark0", 0.0))
        tot, nb, act = policy_run(model.decide_fn(scaler, e["feat"], names), ask, bid, aq, bq, c,
                                  mark0=m0)
        tot_s1, _, _ = run(act, ask, bid, aq, bq, c, slip=1, mark0=m0)
        bh_log, _, _ = run(_buy_and_hold(len(np.asarray(ask))), ask, bid, aq, bq, c, mark0=m0)
        per[c] = {
            "model": float(np.exp(tot) - 1),
            "model_slip1": float(np.exp(tot_s1) - 1),
            "trades": int(nb),
            "log_total": float(tot),
            "log_total_slip1": float(tot_s1),
            "bh": float(np.exp(bh_log) - 1),
            "oracle": float(np.exp(float(e["q"][0, 0, K_MAX].max())) - 1),
            "pr": np.exp(placebo_random(ask, bid, aq, bq, c, reps, rng, mark0=m0)) - 1,
            "pt": np.exp(placebo_timing(nb, ask, bid, aq, bq, c, reps, rng, mark0=m0)) - 1,
            "ps": np.exp(placebo_shift(act, ask, bid, aq, bq, c, reps, rng, mark0=m0)) - 1,
        }
    return per


def _pct_half(value: float, dist: np.ndarray) -> float:
    """분위(동률 ½): (less + 0.5*equal) / reps."""
    dist = np.asarray(dist, dtype=float)
    return float(((dist < value).sum() + 0.5 * (dist == value).sum()) / dist.size)


def summarize(per_code: dict, codes: list[str] | None = None) -> dict:
    """codes(기본 전체) 동일가중 평균 + 층 json 재료: model_slip1 평균, log_sum/log_sum_slip1 합,
    ps_day = rep 별 종목 평균 배열.

    분위(동률 ½): diag_pct_random. pct_timing·pr_med/pt_med·diag_capture = 진단.
    diag_capture = (model − pr_med) / (oracle − pr_med), 분모 ≤ 0 이면 NaN.
    codes 가 비면 n=0, ps_day 빈 배열, 나머지 NaN.
    """
    names = sorted(per_code) if codes is None else list(codes)
    nan = float("nan")
    if not names:
        return {"n": 0, "model": nan, "model_slip1": nan, "bh": nan, "oracle": nan,
                "trades": nan, "log_sum": nan, "log_sum_slip1": nan,
                "ps_day": np.zeros(0),
                "diag_pct_random": nan, "pct_timing": nan,
                "pr_med": nan, "pt_med": nan, "diag_capture": nan}
    m = float(np.mean([per_code[c]["model"] for c in names]))
    m1 = float(np.mean([per_code[c]["model_slip1"] for c in names]))
    bh = float(np.mean([per_code[c]["bh"] for c in names]))
    o = float(np.mean([per_code[c]["oracle"] for c in names]))
    tr = int(sum(int(per_code[c]["trades"]) for c in names))
    ls = float(sum(float(per_code[c]["log_total"]) for c in names))
    ls1 = float(sum(float(per_code[c]["log_total_slip1"]) for c in names))
    pr_day = np.mean([np.asarray(per_code[c]["pr"], dtype=float) for c in names], axis=0)
    pt_day = np.mean([np.asarray(per_code[c]["pt"], dtype=float) for c in names], axis=0)
    ps_day = np.mean([np.asarray(per_code[c]["ps"], dtype=float) for c in names], axis=0)
    pr_med = float(np.median(pr_day))
    pt_med = float(np.median(pt_day))
    den = o - pr_med
    cap = (m - pr_med) / den if den > 0 else nan
    return {"n": len(names), "model": m, "model_slip1": m1, "bh": bh, "oracle": o, "trades": tr,
            "log_sum": ls, "log_sum_slip1": ls1, "ps_day": ps_day,
            "diag_pct_random": _pct_half(m, pr_day), "pct_timing": _pct_half(m, pt_day),
            "pr_med": pr_med, "pt_med": pt_med, "diag_capture": float(cap)}
