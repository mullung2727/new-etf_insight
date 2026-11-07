"""판정 CLI (PLAN §10): results/<version>/<model>/*.json 층별 판정. 통과 기준 4개."""

from __future__ import annotations

import argparse
import json
import math
import sys

import numpy as np

from .walkforward import RESULTS

MAIN_MODEL = "hgb_adv"


def pct_half(value: float, dist) -> float:
    """분위(동률 ½): (less + 0.5*equal) / n."""
    d = np.asarray(dist, dtype=float)
    return float(((d < value).sum() + 0.5 * (d == value).sum()) / d.size)


def block_boot_means(x, block: int, boot: int, rng: np.random.Generator) -> np.ndarray:
    """이동 블록 부트스트랩(길이 block) 평균 분포."""
    x = np.asarray(x, dtype=float)
    T = x.shape[0]
    b = min(int(block), T)
    if b < 1:
        return np.zeros(0)
    nb = -(-T // b)
    starts = rng.integers(0, T - b + 1, size=(boot, nb))
    idx = (starts[:, :, None] + np.arange(b)).reshape(boot, nb * b)[:, :T]
    return x[idx].mean(axis=1)


def block_boot_lower(x, block: int, boot: int, rng: np.random.Generator) -> float:
    """이동 블록 부트스트랩(길이 block) 평균 분포의 5분위 = 단측 95% 하한."""
    m = block_boot_means(x, block, boot, rng)
    return float(np.quantile(m, 0.05)) if m.size else float("nan")


def required_days(sd: float) -> int:
    """n* = ((1.645+0.8416)·SD / 0.001)² 올림."""
    return int(math.ceil(((1.645 + 0.8416) * float(sd) / 0.001) ** 2))


def holm2(pa: float, pb: float, alpha: float = 0.05) -> tuple[bool, bool]:
    """2가설 Holm: 작은 p ≤ α/2, 그다음 큰 p ≤ α. 첫 실패면 둘 다 실패."""
    small_first = pa <= pb
    ps, pl = (pa, pb) if small_first else (pb, pa)
    if ps > alpha / 2:
        return False, False
    oks, okl = True, pl <= alpha
    return (oks, okl) if small_first else (okl, oks)


def verdict(n_days: int, c1: bool, c2: bool, c3: bool, c4: bool, required: int) -> str:
    """평가일 < 필요 일수 → "보류", 4개 충족 → "통과", 아니면 "불통과: 기준 .." (실패 번호)."""
    if n_days < required:
        return "보류"
    failed = [str(i + 1) for i, c in enumerate((c1, c2, c3, c4)) if not c]
    if not failed:
        return "통과"
    return "불통과: 기준 " + ", ".join(failed)


def _stats(recs: list, block: int, boot: int, rng: np.random.Generator) -> dict | None:
    """일별 rec 목록 → 평균·분위·p값·매매 요약. 없으면 None. p1=부트 평균 ≤0 비율, p2·p3=1−분위."""
    if not recs:
        return None
    vals = np.array([e["model"] for e in recs])
    mean = float(vals.mean())
    sd = float(np.std(vals))
    med = float(np.median(vals))
    n = len(recs)
    if n > 1:
        cut = int(np.argmax(np.abs(vals)))
        drop = float(vals[[i for i in range(n) if i != cut]].mean())
    else:
        drop = float("nan")
    means = block_boot_means(vals, block, boot, rng)
    lower = float(np.quantile(means, 0.05))
    p1 = float((means <= 0).mean())
    sh = np.array([e["sh_day"] for e in recs])
    q2 = pct_half(mean, sh.mean(axis=0))
    ps = np.array([e["ps_day"] for e in recs])
    q3 = pct_half(mean, ps.mean(axis=0))
    trades = int(sum(e["trades"] for e in recs))
    ls1 = float(sum(e["log_sum_slip1"] for e in recs))
    bp1 = ls1 / trades * 1e4 if trades else float("nan")
    return {"n": n, "mean": mean, "sd": sd, "med": med, "drop": drop,
            "lower": lower, "p1": p1, "q2": q2, "p2": 1 - q2, "q3": q3, "p3": 1 - q3,
            "trades": trades, "bp1": bp1, "c4": bool(trades >= 100 and bp1 >= 5),
            "n_star": required_days(sd)}


def stratum_summary(version: str, model: str, stratum: str, start: str | None) -> dict:
    """층별 확인 구간 요약 + 기준 1~4 충족 여부 (단일 층 기준, report CLI 공용).

    judge CLI 와 같은 _stats·verdict 사용. boot/block/seed/min_days 는 CLI 기본값.
    결과 없으면 n=0, c1~c4 False, verdict "보류".
    """
    recs = []
    for p in sorted((RESULTS / version / model).glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if start is not None and str(d.get("day", p.stem)) < start:
            continue
        e = d[stratum]
        if e["n"] > 0:
            recs.append(e)
    if not recs:
        return {"n": 0, "mean": float("nan"), "c1": False, "c2": False, "c3": False,
                "c4": False, "verdict": "보류"}
    st = _stats(recs, 5, 10000, np.random.default_rng(0))
    assert st is not None
    c1, c2, c3, c4 = st["p1"] < 0.05, st["p2"] < 0.05, st["p3"] < 0.05, st["c4"]
    required = max(60, st["n_star"])
    return {"n": st["n"], "mean": st["mean"], "c1": c1, "c2": c2, "c3": c3, "c4": c4,
            "verdict": verdict(st["n"], c1, c2, c3, c4, required)}


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--stratum", default="both", choices=["stock", "etf", "both"])
    ap.add_argument("--min-days", type=int, default=60)
    ap.add_argument("--start", default=None, help="YYYYMMDD: 이 날짜 이상 결과만 (dev 외 필수)")
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--block", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    if args.min_days < 60:
        ap.error("--min-days must be >= 60")
    if args.version != "dev" and not args.start:
        ap.error("--start YYYYMMDD required unless version is dev")
    if args.start is not None and (len(args.start) != 8 or not args.start.isdigit()):
        ap.error("--start must be YYYYMMDD")
    strata = ["stock", "etf"] if args.stratum == "both" else [args.stratum]
    recs: dict[str, list] = {s: [] for s in strata}
    for p in sorted((RESULTS / args.version / args.model).glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        if args.start is not None and str(d.get("day", p.stem)) < args.start:
            continue
        for s in strata:
            e = d[s]
            if e["n"] > 0:
                recs[s].append(e)
    diag = args.model != MAIN_MODEL
    rng = np.random.default_rng(args.seed)
    stats = {s: _stats(recs[s], args.block, args.boot, rng) for s in strata}
    ok: dict[str, tuple[bool, bool, bool, bool] | None] = {}
    if not diag:
        if len(strata) == 2 and all(stats[s] is not None for s in strata):
            for s in strata:
                st = stats[s]
                assert st is not None
                o = [holm2(stats["stock"][f"p{i}"], stats["etf"][f"p{i}"])[0 if s == "stock" else 1]
                     for i in (1, 2, 3)]
                ok[s] = (o[0], o[1], o[2], st["c4"])
        else:
            for s in strata:
                st = stats[s]
                ok[s] = None if st is None else (st["p1"] < 0.05, st["p2"] < 0.05,
                                                 st["p3"] < 0.05, st["c4"])
    for s in strata:
        st = stats[s]
        n_days = 0 if st is None else st["n"]
        print(f"version={args.version} model={args.model} stratum={s} days={n_days} start={args.start}")
        if st is None:
            if not diag:
                print(f"판정 보류 (0/{args.min_days}일)")
            continue
        print(f"mean={st['mean']:+.4%} sd={st['sd']:.4%} med={st['med']:+.4%} dropmax={st['drop']:+.4%}")
        if diag:
            print(f"기준 1 블록부트 하한={st['lower']:+.4%} p={st['p1']:.4f}")
            print(f"기준 2 라벨섞기 분위={st['q2']:.4f} p={st['p2']:.4f}")
            print(f"기준 3 경로이동 분위={st['q3']:.4f} p={st['p3']:.4f}")
            print(f"기준 4 매매={st['trades']} 건당순익_slip1={st['bp1']:+.1f}bp")
            print(f"필요 추정={st['n_star']}일")
            continue
        c1, c2, c3, c4 = ok[s] or (False, False, False, False)
        print(f"기준 1 블록부트 하한={st['lower']:+.4%} p={st['p1']:.4f} {'충족' if c1 else '미달'}")
        print(f"기준 2 라벨섞기 분위={st['q2']:.4f} p={st['p2']:.4f} {'충족' if c2 else '미달'}")
        print(f"기준 3 경로이동 분위={st['q3']:.4f} p={st['p3']:.4f} {'충족' if c3 else '미달'}")
        print(f"기준 4 매매={st['trades']} 건당순익_slip1={st['bp1']:+.1f}bp {'충족' if c4 else '미달'}")
        print(f"필요 추정={st['n_star']}일")
        required = max(args.min_days, st["n_star"])
        v = verdict(n_days, c1, c2, c3, c4, required)
        if v == "보류":
            print(f"판정 보류 ({n_days}/{required}일, 필요 추정 {st['n_star']}일)")
        else:
            print(f"판정 {v}")
    if diag:
        print("진단 (주 모델 아님)")


if __name__ == "__main__":
    main()
