"""전진 검증 CLI: D까지 학습 → D+1 평가. 테스트 날은 학습에 절대 없음."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from research.backtest_daily.data import load_px

from . import bars
from . import daily as daily_mod
from .costs import FEE, SELL_TAX
from .dataset import EXTRA_COLS, day_cells, samples
from .models import decide_fn, make
from .oracle import K_MAX
from .replay import placebo_random, placebo_timing, policy_run
from .scale import Scaler

RESULTS = Path(__file__).resolve().parents[1] / "private" / "rl_signal" / "results"


def splits(days: list[str], min_train: int) -> list[tuple[list[str], str]]:
    """i ≥ min_train 인 i 마다 (days[:i], days[i])."""
    days = list(days)
    return [(days[:i], days[i]) for i in range(len(days)) if i >= min_train]


def _orderbook_dir() -> Path:
    try:
        from new_etf_insight.data_paths import orderbook_dir
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "src"))
        from new_etf_insight.data_paths import orderbook_dir
    return orderbook_dir()


def _trade_days(root: Path) -> list[str]:
    days = set()
    for p in Path(root).rglob("trade_*.parquet"):
        day = p.stem.split("_", 1)[1]
        if len(day) == 8 and day.isdigit():
            days.add(day)
    return sorted(days)


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=["logit", "hgb"])
    ap.add_argument("--min-train", type=int, default=5)
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    print(f"model={args.model} min_train={args.min_train} reps={args.reps} seed={args.seed} "
          f"FEE={FEE} SELL_TAX={SELL_TAX} K_MAX={K_MAX}")
    days = _trade_days(_orderbook_dir())
    print(f"days: {len(days)}")
    if not days:
        return
    tbl = daily_mod.daily_table(load_px(), daily_mod.load_etf_px())
    rng = np.random.default_rng(args.seed)
    names = list(bars.FEATURES) + list(EXTRA_COLS)
    outdir = RESULTS / args.model
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for train_days, test_day in splits(days, args.min_train):
        Xtr, ytr = [], []
        for d in train_days:
            cells = day_cells(d, tbl)
            if not cells:
                continue
            Xd, yd, _ = samples(cells, rng)
            Xtr.append(Xd)
            ytr.append(yd)
        if not Xtr:
            print(f"{test_day} skip: no train samples")
            continue
        Xtr = np.concatenate(Xtr)
        ytr = np.concatenate(ytr)
        scaler = Scaler().fit(Xtr, names)
        model = make(args.model).fit(scaler.transform(Xtr, names), ytr)
        cells = day_cells(test_day, tbl)
        codes = sorted(cells)
        if not codes:
            print(f"{test_day} skip: no test codes")
            continue
        rets, orets, pr_cols, pt_cols, trades = [], [], [], [], 0
        for c in codes:
            e = cells[c]
            tot, nb, _ = policy_run(decide_fn(model, scaler, e["feat"], bars.FEATURES),
                                    e["ask1"], e["bid1"], c)
            rets.append(np.exp(tot) - 1)
            trades += nb
            orets.append(np.exp(float(e["q"][0, 0, K_MAX].max())) - 1)
            pr_cols.append(np.exp(placebo_random(e["ask1"], e["bid1"], c, args.reps, rng)) - 1)
            pt_cols.append(np.exp(placebo_timing(nb, e["ask1"], e["bid1"], c, args.reps, rng)) - 1)
        model_ret = float(np.mean(rets))
        pr_day = np.mean(pr_cols, axis=0)
        pt_day = np.mean(pt_cols, axis=0)
        rec = {"day": test_day, "train_days": train_days, "n_codes": len(codes),
               "model_ret": model_ret, "model_trades": int(trades),
               "oracle_ret": float(np.mean(orets)),
               "pct_random": float((pr_day < model_ret).mean()),
               "pct_timing": float((pt_day < model_ret).mean()),
               "placebo_random_median": float(np.median(pr_day)),
               "placebo_timing_median": float(np.median(pt_day))}
        (outdir / f"{test_day}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append(rec)
        print(f"{test_day} n={len(codes)} train={len(train_days)} model={model_ret:+.4%} "
              f"trades={trades} oracle={rec['oracle_ret']:+.4%} "
              f"pct_r={rec['pct_random']:.2f} pct_t={rec['pct_timing']:.2f}")
    if rows:
        mr = np.mean([r["model_ret"] for r in rows])
        pr = np.mean([r["pct_random"] for r in rows])
        pt = np.mean([r["pct_timing"] for r in rows])
        br = np.mean([r["model_ret"] > r["placebo_random_median"] for r in rows])
        bt = np.mean([r["model_ret"] > r["placebo_timing_median"] for r in rows])
        print(f"total days={len(rows)} avg_ret={mr:+.4%} avg_pct_r={pr:.2f} avg_pct_t={pt:.2f} "
              f"beat_r={br:.2f} beat_t={bt:.2f}")


if __name__ == "__main__":
    main()
