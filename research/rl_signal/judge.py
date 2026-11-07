"""판정 CLI (PLAN §10): results/<version>/<model>/*.json 층별 판정. 통과 기준 4개."""

from __future__ import annotations

import argparse
import json
import math
import sys

import numpy as np

from .walkforward import RESULTS


def pct_half(value: float, dist) -> float:
    """분위(동률 ½): (less + 0.5*equal) / n."""
    d = np.asarray(dist, dtype=float)
    return float(((d < value).sum() + 0.5 * (d == value).sum()) / d.size)


def block_boot_lower(x, block: int, boot: int, rng: np.random.Generator) -> float:
    """이동 블록 부트스트랩(길이 block) 평균 분포의 5분위 = 단측 95% 하한."""
    x = np.asarray(x, dtype=float)
    T = x.shape[0]
    b = min(int(block), T)
    if b < 1:
        return float("nan")
    nb = -(-T // b)
    starts = rng.integers(0, T - b + 1, size=(boot, nb))
    idx = (starts[:, :, None] + np.arange(b)).reshape(boot, nb * b)[:, :T]
    return float(np.quantile(x[idx].mean(axis=1), 0.05))


def required_days(sd: float) -> int:
    """n* = ((1.645+0.8416)·SD / 0.001)² 올림."""
    return int(math.ceil(((1.645 + 0.8416) * float(sd) / 0.001) ** 2))


def verdict(n_days: int, c1: bool, c2: bool, c3: bool, c4: bool, min_days: int) -> str:
    """평가일 부족 → "보류", 4개 충족 → "통과", 아니면 "불통과: 기준 .." (실패 번호)."""
    if n_days < min_days:
        return "보류"
    failed = [str(i + 1) for i, c in enumerate((c1, c2, c3, c4)) if not c]
    if not failed:
        return "통과"
    return "불통과: 기준 " + ", ".join(failed)


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--stratum", required=True, choices=["stock", "etf"])
    ap.add_argument("--min-days", type=int, default=60)
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--block", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    recs = []
    for p in sorted((RESULTS / args.version / args.model).glob("*.json")):
        e = json.loads(p.read_text(encoding="utf-8"))[args.stratum]
        if e["n"] > 0:
            recs.append(e)
    n_days = len(recs)
    print(f"version={args.version} model={args.model} stratum={args.stratum} days={n_days}")
    if n_days == 0:
        print(f"판정 보류 (0/{args.min_days}일)")
        return
    vals = np.array([e["model"] for e in recs])
    mean = float(vals.mean())
    sd = float(np.std(vals))
    med = float(np.median(vals))
    if n_days > 1:
        cut = int(np.argmax(np.abs(vals)))
        drop = float(vals[[i for i in range(n_days) if i != cut]].mean())
    else:
        drop = float("nan")
    print(f"mean={mean:+.4%} sd={sd:.4%} med={med:+.4%} dropmax={drop:+.4%}")
    rng = np.random.default_rng(args.seed)
    lower = block_boot_lower(vals, args.block, args.boot, rng)
    c1 = bool(lower > 0)
    print(f"기준 1 블록부트 하한={lower:+.4%} {'충족' if c1 else '미달'}")
    sh = np.array([e["sh_day"] for e in recs])
    p2 = pct_half(mean, sh.mean(axis=0))
    c2 = bool(p2 > 0.95)
    print(f"기준 2 라벨섞기 분위={p2:.4f} {'충족' if c2 else '미달'}")
    ps = np.array([e["ps_day"] for e in recs])
    p3 = pct_half(mean, ps.mean(axis=0))
    c3 = bool(p3 > 0.95)
    print(f"기준 3 경로이동 분위={p3:.4f} {'충족' if c3 else '미달'}")
    trades = int(sum(e["trades"] for e in recs))
    ls1 = float(sum(e["log_sum_slip1"] for e in recs))
    bp1 = ls1 / trades * 1e4 if trades else float("nan")
    c4 = bool(trades >= 100 and bp1 >= 5)
    print(f"기준 4 매매={trades} 건당순익_slip1={bp1:+.1f}bp {'충족' if c4 else '미달'}")
    n_star = required_days(sd)
    print(f"필요 추정={n_star}일")
    v = verdict(n_days, c1, c2, c3, c4, args.min_days)
    if v == "보류":
        print(f"판정 보류 ({n_days}/{args.min_days}일, 필요 추정 {n_star}일)")
    else:
        print(f"판정 {v}")


if __name__ == "__main__":
    main()
