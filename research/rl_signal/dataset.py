"""하루 칸 캐시·학습 표본."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import bars
from . import daily as daily_mod
from . import oracle
from .oracle import BUY, HOLD, K_MAX, SELL

CACHE = Path(__file__).resolve().parents[1] / "private" / "rl_signal" / "cache"
EXTRA_COLS = ["pos", "k5"]


def day_cells(day: str, daily_tbl, root=None, refresh: bool = False) -> dict:
    """bars.load_day + daily.for_day + bars.build → 종목별 {feat, ask1, bid1} + oracle.solve 의 q.

    npz 캐시 CACHE/cells_YYYYMMDD.npz (codes 배열, feat (C,n,F), ask1/bid1 (C,n), q (C,n,2,6,3)).
    있으면 읽기.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"cells_{day}.npz"
    if not refresh and p.exists():
        z = np.load(str(p))
        codes = [str(c) for c in z["codes"].tolist()]
        if not codes:
            return {}
        return {c: {"feat": z["feat"][i], "ask1": z["ask1"][i],
                    "bid1": z["bid1"][i], "q": z["q"][i]}
                for i, c in enumerate(codes)}
    trade, book, gaps = bars.load_day(day, root)
    built = bars.build(day, trade, book, gaps, daily=daily_mod.for_day(daily_tbl, day))
    codes = sorted(c for c in built if c != "_excluded")
    cells = {}
    for c in codes:
        e = built[c]
        cells[c] = {"feat": e["feat"], "ask1": e["ask1"], "bid1": e["bid1"],
                    "q": oracle.solve(e["ask1"], e["bid1"], c)["q"]}
    if codes:
        np.savez_compressed(str(p),
                            codes=np.array(codes),
                            feat=np.stack([cells[c]["feat"] for c in codes]),
                            ask1=np.stack([cells[c]["ask1"] for c in codes]),
                            bid1=np.stack([cells[c]["bid1"] for c in codes]),
                            q=np.stack([cells[c]["q"] for c in codes]))
    else:
        np.savez_compressed(str(p), codes=np.array([]))
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


def samples(cells: dict, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, list]:
    """종목·칸(마지막 칸 제외)마다 2개: pos=0 (k ~ U{1..5}), pos=1 (k ~ U{0..4}).

    X = feat + [pos, k/5], y = argmax q[t,pos,k] (동률 HOLD>SELL>BUY, -inf 제외). meta = (code, t).
    """
    Xs, ys, meta = [], [], []
    for c in sorted(cells):
        feat = np.asarray(cells[c]["feat"], dtype=np.float64)
        q = np.asarray(cells[c]["q"])
        for t in range(feat.shape[0] - 1):
            for pos, k in ((0, int(rng.integers(1, K_MAX + 1))),
                           (1, int(rng.integers(0, K_MAX)))):
                Xs.append(np.concatenate([feat[t], [pos, k / 5]]))
                ys.append(_best_action(q[t, pos, k]))
                meta.append((c, t))
    if not Xs:
        return (np.zeros((0, len(bars.FEATURES) + 2), dtype=np.float32),
                np.zeros(0, dtype=int), [])
    return np.array(Xs, dtype=np.float32), np.array(ys, dtype=int), meta
