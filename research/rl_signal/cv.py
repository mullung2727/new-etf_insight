"""교차검증 진단 CLI: 코드/날짜 그룹 k-fold. 진단용 — 합격 판정은 walkforward."""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

from research.backtest_daily.data import load_px

from . import bars
from . import daily as daily_mod
from . import walkforward as wf
from .costs import FEE, SELL_TAX
from .dataset import EXTRA_COLS, day_cells, samples
from .evaluate import eval_codes, summarize
from .models import make
from .oracle import K_MAX
from .scale import Scaler


def folds(units: list[tuple[str, str]], by: str, k: int, seed: int) -> list[tuple[list, list]]:
    """그룹(by code|day)을 섞어 k 묶음 → [(train_units, test_units)]. 같은 그룹이 train·test 양쪽에 없음."""
    if by == "code":
        def key(u):
            return u[1]
    elif by == "day":
        def key(u):
            return u[0]
    else:
        raise ValueError(f"unknown by: {by}")
    if k < 1:
        raise ValueError(f"k must be >= 1: {k}")
    groups = sorted({key(u) for u in units})
    order = list(np.random.default_rng(seed).permutation(groups)) if groups else []
    bundles: list[list] = [[] for _ in range(k)]
    for i, g in enumerate(order):
        bundles[i % k].append(g)
    out = []
    for i in range(k):
        tg = set(bundles[i])
        out.append(([u for u in units if key(u) not in tg],
                    [u for u in units if key(u) in tg]))
    return out


def _nanmean(xs) -> float:
    a = np.asarray(list(xs), dtype=float)
    a = a[~np.isnan(a)]
    return float(a.mean()) if a.size else float("nan")


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=["logit", "hgb", "hgb_adv"])
    ap.add_argument("--by", required=True, choices=["code", "day"])
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--reps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    print("진단용 — 합격 판정은 walkforward (미래 날짜·같은 날 시장 정보가 학습에 섞임)")
    print(f"model={args.model} by={args.by} folds={args.folds} reps={args.reps} seed={args.seed} "
          f"FEE={FEE} SELL_TAX={SELL_TAX} K_MAX={K_MAX}")
    days = wf._trade_days(wf._orderbook_dir())
    print(f"days: {len(days)}")
    tbl = daily_mod.daily_table(load_px(), daily_mod.load_etf_px())
    rng = np.random.default_rng(args.seed)
    names = list(bars.FEATURES) + list(EXTRA_COLS)
    cells_by_day = {}
    for d in days:
        cells = day_cells(d, tbl)
        if cells:
            cells_by_day[d] = cells
    units = [(d, c) for d in sorted(cells_by_day) for c in sorted(cells_by_day[d])]
    print(f"units: {len(units)}")
    recs = []
    for i, (train_u, test_u) in enumerate(folds(units, args.by, args.folds, args.seed)):
        if not test_u or not train_u:
            print(f"fold {i} skip: no {'test' if not test_u else 'train'} units")
            continue
        tr_by_day: dict[str, list] = {}
        for d, c in train_u:
            tr_by_day.setdefault(d, []).append(c)
        Xtr, ytr, atr = [], [], []
        for d in sorted(tr_by_day):
            sub = {c: cells_by_day[d][c] for c in tr_by_day[d]}
            Xd, yd, _, advd = samples(sub, rng)
            Xtr.append(Xd)
            ytr.append(yd)
            atr.append(advd)
        if not Xtr:
            print(f"fold {i} skip: no train samples")
            continue
        Xtr = np.concatenate(Xtr)
        ytr = np.concatenate(ytr)
        atr = np.concatenate(atr)
        scaler = Scaler().fit(Xtr, names)
        model = make(args.model)
        model.fit(scaler.transform(Xtr, names), ytr, atr, names.index("pos"))
        te_by_day: dict[str, list] = {}
        for d, c in test_u:
            te_by_day.setdefault(d, []).append(c)
        ds = []
        for d in sorted(te_by_day):
            day_codes = sorted(te_by_day[d])
            per = eval_codes(model, scaler, bars.FEATURES, cells_by_day[d],
                             day_codes, args.reps, rng)
            ds.append(summarize(per))
        rec = {"fold": i, "n_test": len(test_u), "n_train": len(train_u), "n_dates": len(ds),
               "model": float(np.mean([s["model"] for s in ds])),
               "bh": float(np.mean([s["bh"] for s in ds])),
               "oracle": float(np.mean([s["oracle"] for s in ds])),
               "capture": _nanmean([s["diag_capture"] for s in ds]),
               "pct_random": float(np.mean([s["diag_pct_random"] for s in ds])),
               "pct_timing": float(np.mean([s["pct_timing"] for s in ds]))}
        recs.append(rec)
        print(f"fold {i} n={rec['n_test']} model={rec['model']:+.4%} bh={rec['bh']:+.4%} "
              f"oracle={rec['oracle']:+.4%} cap={rec['capture']:.2f} "
              f"pct_r={rec['pct_random']:.2f} pct_t={rec['pct_timing']:.2f}")
    avg: dict = {}
    if recs:
        avg = {k: float(np.mean([r[k] for r in recs]))
               for k in ("model", "bh", "oracle", "pct_random", "pct_timing")}
        avg["capture"] = _nanmean([r["capture"] for r in recs])
        print(f"avg model={avg['model']:+.4%} bh={avg['bh']:+.4%} oracle={avg['oracle']:+.4%} "
              f"cap={avg['capture']:.2f} pct_r={avg['pct_random']:.2f} pct_t={avg['pct_timing']:.2f}")
    wf.RESULTS.mkdir(parents=True, exist_ok=True)
    (wf.RESULTS / f"cv_{args.model}_{args.by}.json").write_text(
        json.dumps({"model": args.model, "by": args.by, "folds": args.folds,
                    "reps": args.reps, "seed": args.seed, "n_units": len(units),
                    "fold_list": recs, "avg": avg},
                   ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
