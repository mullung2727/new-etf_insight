"""공용 평가: walkforward·cv 둘 다 사용. 종목별 모델/플라시보/bh/오라클 + 동일가중 요약."""

from __future__ import annotations

import numpy as np

from .models import decide_fn
from .oracle import BUY, HOLD, K_MAX
from .replay import placebo_random, placebo_timing, policy_run, run


def _buy_and_hold(n: int) -> np.ndarray:
    """첫 칸 BUY, 나머지 HOLD. 장중 보유(bh)용."""
    a = np.full(n, HOLD, dtype=int)
    if n > 0:
        a[0] = BUY
    return a


def eval_codes(model, scaler, names, cells: dict, codes: list[str], reps: int, rng) -> dict:
    """codes 각각 policy_run(decide_fn) → 총 로그보상·매수 수. 같은 종목에 placebo_random, placebo_timing(같은 매수 수) reps 개.

    장중 보유 bh = 첫 칸 BUY 만 넣은 run (마지막 칸 강제 청산).
    oracle = cells 의 q 에서 q[0,0,K_MAX].max().
    반환 {code: {"model": r, "trades": n, "bh": r, "oracle": r, "pr": array(reps), "pt": array(reps)}} (r 은 exp(로그)−1).
    """
    per = {}
    for c in codes:
        e = cells[c]
        ask, bid = e["ask1"], e["bid1"]
        tot, nb, _ = policy_run(decide_fn(model, scaler, e["feat"], names), ask, bid, c)
        bh_log, _, _ = run(_buy_and_hold(len(np.asarray(ask))), ask, bid, c)
        per[c] = {
            "model": float(np.exp(tot) - 1),
            "trades": int(nb),
            "bh": float(np.exp(bh_log) - 1),
            "oracle": float(np.exp(float(e["q"][0, 0, K_MAX].max())) - 1),
            "pr": np.exp(placebo_random(ask, bid, c, reps, rng)) - 1,
            "pt": np.exp(placebo_timing(nb, ask, bid, c, reps, rng)) - 1,
        }
    return per


def summarize(per_code: dict, codes: list[str] | None = None) -> dict:
    """codes(기본 전체) 동일가중 평균: model, bh, oracle, trades 합, n.

    플라시보는 rep 별 종목 평균 → pct_random/pct_timing = 플라시보 중 모델보다 작은 비율, 중앙값 pr_med/pt_med.
    capture = (model − pr_med) / (oracle − pr_med), 분모 ≤ 0 이면 NaN.
    codes 가 비면 n=0 과 나머지 NaN.
    """
    names = sorted(per_code) if codes is None else list(codes)
    nan = float("nan")
    if not names:
        return {"n": 0, "model": nan, "bh": nan, "oracle": nan, "trades": nan,
                "pct_random": nan, "pct_timing": nan,
                "pr_med": nan, "pt_med": nan, "capture": nan}
    m = float(np.mean([per_code[c]["model"] for c in names]))
    bh = float(np.mean([per_code[c]["bh"] for c in names]))
    o = float(np.mean([per_code[c]["oracle"] for c in names]))
    tr = int(sum(int(per_code[c]["trades"]) for c in names))
    pr_day = np.mean([np.asarray(per_code[c]["pr"], dtype=float) for c in names], axis=0)
    pt_day = np.mean([np.asarray(per_code[c]["pt"], dtype=float) for c in names], axis=0)
    pr_med = float(np.median(pr_day))
    pt_med = float(np.median(pt_day))
    den = o - pr_med
    cap = (m - pr_med) / den if den > 0 else nan
    return {"n": len(names), "model": m, "bh": bh, "oracle": o, "trades": tr,
            "pct_random": float((pr_day < m).mean()),
            "pct_timing": float((pt_day < m).mean()),
            "pr_med": pr_med, "pt_med": pt_med, "capture": float(cap)}
