"""하루 칸 캐시·학습 표본."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from . import bars
from . import costs
from . import daily as daily_mod
from . import oracle
from .oracle import BUY, HOLD, K_MAX, SELL

CACHE = Path(__file__).resolve().parents[1] / "private" / "rl_signal" / "cache"
EXTRA_COLS = ["pos", "k5"]
CACHE_VERSION = "v2"


def cache_tag() -> str:
    """sha1(FEATURES·FEE·SELL_TAX·K_MAX·UNIT_KRW·"v2") 앞 8자리."""
    payload = repr((bars.FEATURES, costs.FEE, costs.SELL_TAX,
                    oracle.K_MAX, costs.UNIT_KRW, CACHE_VERSION)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:8]


def _cache_path(day: str) -> Path:
    return CACHE / f"cells_{day}_{cache_tag()}.npz"


def day_reasons(day: str) -> dict[str, int]:
    """캐시에서 _reasons 읽기. 캐시 없으면 {}."""
    p = _cache_path(day)
    if not p.exists():
        return {}
    try:
        z = np.load(str(p))
    except Exception:
        return {}
    if "reasons_keys" not in z or "reasons_vals" not in z:
        return {}
    keys = [str(k) for k in z["reasons_keys"].tolist()]
    vals = z["reasons_vals"].tolist()
    return {k: int(v) for k, v in zip(keys, vals)}


def day_cells(day: str, daily_tbl, root=None, refresh: bool = False) -> dict:
    """bars.load_day + daily.for_day + bars.build → 종목별 {feat, ask1, bid1, ex_*, q, q_slip1}.

    npz 캐시 CACHE/cells_{day}_{tag}.npz (tag=cache_tag()). 있으면 읽기.
    ex_dead 종목은 제외(사유 no_exec). _reasons는 캐시에 저장, day_reasons()로 읽기.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    p = _cache_path(day)
    if not refresh and p.exists():
        z = np.load(str(p))
        codes = [str(c) for c in z["codes"].tolist()]
        if not codes:
            return {}
        req = ("feat", "ask1", "bid1", "ex_ask1", "ex_bid1", "ex_aq1", "ex_bq1", "q", "q_slip1")
        if all(k in z for k in req):
            return {c: {"feat": z["feat"][i], "ask1": z["ask1"][i],
                        "bid1": z["bid1"][i], "ex_ask1": z["ex_ask1"][i],
                        "ex_bid1": z["ex_bid1"][i], "ex_aq1": z["ex_aq1"][i],
                        "ex_bq1": z["ex_bq1"][i], "q": z["q"][i], "q_slip1": z["q_slip1"][i]}
                    for i, c in enumerate(codes)}
    trade, book, gaps = bars.load_day(day, root)
    built = bars.build(day, trade, book, gaps, daily=daily_mod.for_day(daily_tbl, day))
    reasons = dict(built.get("_reasons", {}))
    codes: list[str] = []
    cells: dict = {}
    no_exec = 0
    for c in sorted(k for k in built if not k.startswith("_")):
        e = built[c]
        if e.get("ex_dead"):
            no_exec += 1
            continue
        codes.append(c)
        q0 = oracle.solve(e["ex_ask1"], e["ex_bid1"], e["ex_aq1"], e["ex_bq1"], c)["q"]
        q1 = oracle.solve(e["ex_ask1"], e["ex_bid1"], e["ex_aq1"], e["ex_bq1"], c, slip=1)["q"]
        cells[c] = {"feat": e["feat"], "ask1": e["ask1"], "bid1": e["bid1"],
                    "ex_ask1": e["ex_ask1"], "ex_bid1": e["ex_bid1"],
                    "ex_aq1": e["ex_aq1"], "ex_bq1": e["ex_bq1"],
                    "q": q0, "q_slip1": q1}
    if no_exec:
        reasons["no_exec"] = reasons.get("no_exec", 0) + no_exec
    rk = sorted(reasons.keys())
    rkeys = np.array(rk)
    rvals = np.array([reasons[k] for k in rk], dtype=np.int64)
    if codes:
        np.savez_compressed(str(p),
                            codes=np.array(codes),
                            feat=np.stack([cells[c]["feat"] for c in codes]),
                            ask1=np.stack([cells[c]["ask1"] for c in codes]),
                            bid1=np.stack([cells[c]["bid1"] for c in codes]),
                            ex_ask1=np.stack([cells[c]["ex_ask1"] for c in codes]),
                            ex_bid1=np.stack([cells[c]["ex_bid1"] for c in codes]),
                            ex_aq1=np.stack([cells[c]["ex_aq1"] for c in codes]),
                            ex_bq1=np.stack([cells[c]["ex_bq1"] for c in codes]),
                            q=np.stack([cells[c]["q"] for c in codes]),
                            q_slip1=np.stack([cells[c]["q_slip1"] for c in codes]),
                            reasons_keys=rkeys, reasons_vals=rvals)
    else:
        np.savez_compressed(str(p), codes=np.array([]), reasons_keys=rkeys, reasons_vals=rvals)
    return cells


def _best_action(q3: np.ndarray) -> int:
    best, a_best = -np.inf, HOLD
    for a in (HOLD, SELL, BUY):
        v = float(q3[a])
        if v == -np.inf:
            continue
        if v > best:
            best, a_best = v, a
    return a_best


def samples(cells: dict, rng: np.random.Generator,
            shuffle_rng: np.random.Generator | None = None
            ) -> tuple[np.ndarray, np.ndarray, list, np.ndarray]:
    """종목·칸(마지막 칸 제외)마다 2개: pos=0 (k ~ U{1..5}), pos=1 (k ~ U{0..4}).

    X = feat + [pos, k/5], y = argmax q[t,pos,k] (동률 HOLD>SELL>BUY, -inf 제외). meta = (code, t).
    adv = pos=0: q[t,0,k,BUY]−q[t,0,k,HOLD], pos=1: q[t,1,k,SELL]−q[t,1,k,HOLD] (-inf 포함이면 NaN).
    q는 slip 0. shuffle_rng 가 있으면 종목마다 q 를 칸 축으로 np.roll (X 그대로, y·adv 만 바뀜).
    """
    Xs, ys, meta, advs = [], [], [], []
    for c in sorted(cells):
        feat = np.asarray(cells[c]["feat"], dtype=np.float64)
        q = np.asarray(cells[c]["q"])
        n = feat.shape[0]
        if shuffle_rng is not None:
            q = np.roll(q, int(shuffle_rng.integers(n // 4, 3 * n // 4 + 1)), axis=0)
        for t in range(n - 1):
            for pos, k in ((0, int(rng.integers(1, K_MAX + 1))),
                           (1, int(rng.integers(0, K_MAX)))):
                Xs.append(np.concatenate([feat[t], [pos, k / 5]]))
                ys.append(_best_action(q[t, pos, k]))
                meta.append((c, t))
                a = BUY if pos == 0 else SELL
                va, vh = float(q[t, pos, k, a]), float(q[t, pos, k, HOLD])
                advs.append(va - vh if (np.isfinite(va) and np.isfinite(vh)) else np.nan)
    if not Xs:
        return (np.zeros((0, len(bars.FEATURES) + 2), dtype=np.float32),
                np.zeros(0, dtype=int), [], np.zeros(0, dtype=np.float64))
    return (np.array(Xs, dtype=np.float32), np.array(ys, dtype=int), meta,
            np.array(advs, dtype=np.float64))
