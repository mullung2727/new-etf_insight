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
from .evaluate import eval_codes, summarize
from .models import make
from .oracle import K_MAX
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


def _nanmean(xs) -> float:
    a = np.asarray(list(xs), dtype=float)
    a = a[~np.isnan(a)]
    return float(a.mean()) if a.size else float("nan")


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
        seen: set[str] = set()
        for d in train_days:
            cells = day_cells(d, tbl)
            if not cells:
                continue
            seen.update(cells)
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
        per = eval_codes(model, scaler, bars.FEATURES, cells, codes, args.reps, rng)
        tot = summarize(per)
        s_seen = summarize(per, [c for c in codes if c in seen])
        s_unseen = summarize(per, [c for c in codes if c not in seen])
        rec = {"day": test_day, "train_days": train_days, "n_codes": len(codes),
               "model_ret": tot["model"], "model_trades": int(tot["trades"]),
               "oracle_ret": tot["oracle"],
               "bh_ret": tot["bh"], "capture": tot["capture"],
               "pct_random": tot["pct_random"],
               "pct_timing": tot["pct_timing"],
               "placebo_random_median": tot["pr_med"],
               "placebo_timing_median": tot["pt_med"],
               "seen": {"n": s_seen["n"], "model": s_seen["model"],
                        "bh": s_seen["bh"], "capture": s_seen["capture"]},
               "unseen": {"n": s_unseen["n"], "model": s_unseen["model"],
                          "bh": s_unseen["bh"], "capture": s_unseen["capture"]}}
        (outdir / f"{test_day}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append(rec)
        print(f"{test_day} n={len(codes)} train={len(train_days)} model={tot['model']:+.4%} "
              f"bh={tot['bh']:+.4%} trades={int(tot['trades'])} oracle={tot['oracle']:+.4%} "
              f"cap={tot['capture']:.2f} pct_r={tot['pct_random']:.2f} pct_t={tot['pct_timing']:.2f} "
              f"seen={s_seen['model']:+.4%}(n={s_seen['n']}) "
              f"unseen={s_unseen['model']:+.4%}(n={s_unseen['n']})")
    if rows:
        mr = np.mean([r["model_ret"] for r in rows])
        bh = np.mean([r["bh_ret"] for r in rows])
        ora = np.mean([r["oracle_ret"] for r in rows])
        cap = _nanmean([r["capture"] for r in rows])
        beat_bh = np.mean([r["model_ret"] > r["bh_ret"] for r in rows])
        sn = [r["seen"]["model"] for r in rows if r["seen"]["n"] > 0]
        un = [r["unseen"]["model"] for r in rows if r["unseen"]["n"] > 0]
        sm = float(np.mean(sn)) if sn else float("nan")
        um = float(np.mean(un)) if un else float("nan")
        pr = np.mean([r["pct_random"] for r in rows])
        pt = np.mean([r["pct_timing"] for r in rows])
        br = np.mean([r["model_ret"] > r["placebo_random_median"] for r in rows])
        bt = np.mean([r["model_ret"] > r["placebo_timing_median"] for r in rows])
        print(f"total days={len(rows)} avg_model={mr:+.4%} avg_bh={bh:+.4%} "
              f"avg_oracle={ora:+.4%} avg_cap={cap:.2f} beat_bh={beat_bh:.2f} "
              f"seen={sm:+.4%} unseen={um:+.4%} avg_pct_r={pr:.2f} avg_pct_t={pt:.2f} "
              f"beat_r={br:.2f} beat_t={bt:.2f}")


if __name__ == "__main__":
    main()
