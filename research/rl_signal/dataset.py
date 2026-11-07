"""하루 칸 캐시·학습 표본."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

from . import bars
from . import costs
from . import daily as daily_mod
from . import oracle
from .oracle import BUY, HOLD, K_MAX, SELL

CACHE = Path(__file__).resolve().parents[1] / "private" / "rl_signal" / "cache"
EXTRA_COLS = ["pos", "k5"]
CACHE_VERSION = "v5"


def cache_tag() -> str:
    """sha1(FEATURES·FEE·SELL_TAX·K_MAX·UNIT_KRW·LABEL_COST_MULT·"v5") 앞 8자리."""
    payload = repr((bars.FEATURES, costs.FEE, costs.SELL_TAX,
                    oracle.K_MAX, costs.UNIT_KRW, costs.LABEL_COST_MULT, CACHE_VERSION)).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:8]


def cache_key(day: str, daily_tbl=None, root=None) -> str:
    """tag + 일자 h 8자리. h = sha1(그날 trade/book/gaps 크기·mtime_ns + repr(for_day)) 앞 8자리."""
    parts: list = []
    for p in bars.day_paths(day, root):
        try:
            st = os.stat(p)
        except OSError:
            parts.append(None)
        else:
            parts.append((st.st_size, st.st_mtime_ns))
    parts.append(None if daily_tbl is None else repr(daily_mod.for_day(daily_tbl, day)))
    h = hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:8]
    return cache_tag() + h


def _cache_path(day: str, key: str) -> Path:
    return CACHE / f"cells_{day}_{key}.npz"


def day_reasons(day: str) -> dict[str, int]:
    """캐시에서 _reasons 읽기. 캐시 없으면 {}. 같은 날 파일이 여러 개면 최신."""
    cands = [p for p in CACHE.glob(f"cells_{day}_*.npz") if not p.name.endswith(".tmp.npz")]
    if not cands:
        return {}
    p = max(cands, key=lambda q: q.stat().st_mtime_ns)
    try:
        with np.load(str(p)) as z:
            if "reasons_keys" not in z or "reasons_vals" not in z:
                return {}
            keys = [str(k) for k in z["reasons_keys"].tolist()]
            vals = z["reasons_vals"].tolist()
    except Exception:
        return {}
    return {k: int(v) for k, v in zip(keys, vals)}


def day_cells(day: str, daily_tbl, root=None, refresh: bool = False) -> dict:
    """bars.load_day + daily.for_day + bars.build → 종목별 {feat, ask1, bid1, ex_*, q, q_slip1, q_train, mark0}. q_train=비용 2배 학습 정답지.

    npz 캐시 CACHE/cells_{day}_{key}.npz (key=cache_key()). 있으면 읽기, 읽기 실패면 지우고 다시 만듦.
    하루 내내 체결 스냅샷이 하나도 없는 종목은 제외(사유 no_exec). _reasons는 캐시에 저장, day_reasons()로 읽기.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    p = _cache_path(day, cache_key(day, daily_tbl, root))
    if not refresh and p.exists():
        try:
            with np.load(str(p)) as z:
                codes = [str(c) for c in z["codes"].tolist()]
                req = ("feat", "ask1", "bid1", "ex_ask1", "ex_bid1", "ex_aq1", "ex_bq1",
                       "q", "q_slip1", "q_train", "mark0")
                if codes and all(k in z for k in req):
                    arr = {k: z[k] for k in req}
                    return {c: {"feat": arr["feat"][i], "ask1": arr["ask1"][i],
                                "bid1": arr["bid1"][i], "ex_ask1": arr["ex_ask1"][i],
                                "ex_bid1": arr["ex_bid1"][i], "ex_aq1": arr["ex_aq1"][i],
                                "ex_bq1": arr["ex_bq1"][i], "q": arr["q"][i],
                                "q_slip1": arr["q_slip1"][i], "q_train": arr["q_train"][i], "mark0": float(arr["mark0"][i])}
                            for i, c in enumerate(codes)}
                if not codes:
                    return {}
        except Exception:
            pass
        p.unlink(missing_ok=True)
    trade, book, gaps = bars.load_day(day, root)
    built = bars.build(day, trade, book, gaps, daily=daily_mod.for_day(daily_tbl, day))
    reasons = dict(built.get("_reasons", {}))
    codes: list[str] = []
    cells: dict = {}
    no_exec = 0
    for c in sorted(k for k in built if not k.startswith("_")):
        e = built[c]
        if not ((np.asarray(e["ex_ask1"]) > 0) | (np.asarray(e["ex_bid1"]) > 0)).any():
            no_exec += 1
            continue
        codes.append(c)
        a0, b0 = float(e["ask1"][0]), float(e["bid1"][0])
        mark0 = (a0 + b0) / 2 if (a0 > 0 and b0 > 0) else (a0 if a0 > 0 else b0)
        q0 = oracle.solve(e["ex_ask1"], e["ex_bid1"], e["ex_aq1"], e["ex_bq1"], c, mark0=mark0)["q"]
        qt = oracle.solve(e["ex_ask1"], e["ex_bid1"], e["ex_aq1"], e["ex_bq1"], c, mark0=mark0, cost_mult=costs.LABEL_COST_MULT)["q"]
        q1 = oracle.solve(e["ex_ask1"], e["ex_bid1"], e["ex_aq1"], e["ex_bq1"], c,
                          slip=1, mark0=mark0)["q"]
        cells[c] = {"feat": e["feat"], "ask1": e["ask1"], "bid1": e["bid1"],
                    "ex_ask1": e["ex_ask1"], "ex_bid1": e["ex_bid1"],
                    "ex_aq1": e["ex_aq1"], "ex_bq1": e["ex_bq1"],
                    "q": q0, "q_slip1": q1, "q_train": qt, "mark0": float(mark0)}
    if no_exec:
        reasons["no_exec"] = reasons.get("no_exec", 0) + no_exec
    rk = sorted(reasons.keys())
    payload: dict = {"codes": np.array(codes),
                     "reasons_keys": np.array(rk),
                     "reasons_vals": np.array([reasons[k] for k in rk], dtype=np.int64)}
    if codes:
        for k in ("feat", "ask1", "bid1", "ex_ask1", "ex_bid1", "ex_aq1", "ex_bq1", "q", "q_slip1", "q_train"):
            payload[k] = np.stack([cells[c][k] for c in codes])
        payload["mark0"] = np.array([cells[c]["mark0"] for c in codes], dtype=np.float64)
    tmp = p.with_name(p.stem + ".tmp.npz")
    np.savez_compressed(str(tmp), **payload)
    os.replace(tmp, p)
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

    X = feat + [pos, k/5], y = argmax q_train[t,pos,k] (동률 HOLD>SELL>BUY, -inf 제외). meta = (code, t).
    adv = pos=0: q_train[t,0,k,BUY]−q_train[t,0,k,HOLD], pos=1: q_train[t,1,k,SELL]−q_train[t,1,k,HOLD] (-inf 포함이면 NaN).
    q_train(비용 LABEL_COST_MULT배) 기준. q_train 없으면 KeyError(q 폴백 없음, 캐시 v5 필요). shuffle_rng 가 있으면 종목마다 q_train 을 칸 축으로 np.roll (X 그대로, y·adv 만 바뀜).
    """
    Xs, ys, meta, advs = [], [], [], []
    for c in sorted(cells):
        feat = np.asarray(cells[c]["feat"], dtype=np.float64)
        q = np.asarray(cells[c]["q_train"])
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
