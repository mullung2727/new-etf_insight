"""전진 검증 CLI: D까지 학습 → D+1 평가. 테스트 날은 학습에 절대 없음."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from research.backtest_daily.data import load_px

from . import bars
from . import daily as daily_mod
from .costs import FEE, SELL_TAX
from .dataset import EXTRA_COLS, cache_tag, day_cells, day_reasons, samples
from .evaluate import eval_codes, strata, summarize
from .models import make
from .oracle import K_MAX
from .scale import Scaler

RESULTS = Path(__file__).resolve().parents[1] / "private" / "rl_signal" / "results"
CONFIG_NAME = "_config.json"


def splits(days: list[str], min_train: int) -> list[tuple[list[str], str]]:
    """i ≥ min_train 인 i 마다 (days[:i], days[i])."""
    days = list(days)
    return [(days[:i], days[i]) for i in range(len(days)) if i >= min_train]


def eval_rng(seed: int, test_day: str) -> np.random.Generator:
    """테스트일별 평가 난수: [seed + 1, int(test_day)]. 그날 eval_codes·라벨섞기 재생이 공유."""
    return np.random.default_rng([seed + 1, int(test_day)])


def src_hash(base: Path | None = None) -> str:
    """research/rl_signal 아래 tests 제외 *.py 내용 sha1 (파일명 정렬)."""
    b = Path(__file__).resolve().parent if base is None else Path(base)
    files = sorted(
        (p for p in b.rglob("*.py") if "tests" not in p.relative_to(b).parts),
        key=lambda p: p.relative_to(b).as_posix(),
    )
    h = hashlib.sha1()
    for p in files:
        h.update(p.read_bytes())
    return h.hexdigest()


def _current_config(args) -> dict:
    return {"src_hash": src_hash(), "reps": args.reps, "shuffle_reps": args.shuffle_reps,
            "seed": args.seed, "min_train": args.min_train, "cache_tag": cache_tag()}


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
    ap.add_argument("--model", default="hgb_adv", choices=["logit", "hgb", "hgb_adv"])
    ap.add_argument("--version", default="dev")
    ap.add_argument("--min-train", type=int, default=5)
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--shuffle-reps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--incremental", action="store_true",
                    help="기존 json 유지 + 있는 테스트일 건너뛰기 (기본: 폴더 비우고 전체 실행)")
    args = ap.parse_args(argv)
    print(f"model={args.model} version={args.version} min_train={args.min_train} reps={args.reps} "
          f"shuffle_reps={args.shuffle_reps} seed={args.seed} incremental={args.incremental} "
          f"FEE={FEE} SELL_TAX={SELL_TAX} K_MAX={K_MAX}")
    days = _trade_days(_orderbook_dir())
    print(f"days: {len(days)}")
    if not days:
        return
    tbl = daily_mod.daily_table(load_px(), daily_mod.load_etf_px())
    # 학습 상태 표본은 일자별 시드 [seed, day] 로 직접 뽑아 섞기가 같은 X 를 재현 (아래 day_rng)
    rng_train = np.random.default_rng(args.seed)
    # (평가는 일자별로 아래 day 루프에서 eval_rng 생성)
    # 구 rng_eval 순차 스트림 제거 (전체·증분 실행 동일 결과용)
    names = list(bars.FEATURES) + list(EXTRA_COLS)
    pos_col = names.index("pos")
    outdir = RESULTS / args.version / args.model
    outdir.mkdir(parents=True, exist_ok=True)
    if args.version != "dev":
        cfg_path = outdir / CONFIG_NAME
        cur = _current_config(args)
        if not args.incremental:
            for p in outdir.glob("*.json"):
                p.unlink()
            cfg_path.write_text(json.dumps(cur, ensure_ascii=False, indent=1, sort_keys=True),
                                encoding="utf-8")
        elif not cfg_path.exists():
            cfg_path.write_text(json.dumps(cur, ensure_ascii=False, indent=1, sort_keys=True),
                                encoding="utf-8")
        else:
            old = json.loads(cfg_path.read_text(encoding="utf-8"))
            if old != cur:
                for k in sorted(set(old) | set(cur)):
                    ov, cv = old.get(k), cur.get(k)
                    if ov != cv:
                        print(f"{k}: {ov!r} != {cv!r}")
                print("코드·설정이 바뀜 — 새 판정 버전 필요")
                sys.exit(2)
    elif not args.incremental:
        for p in outdir.glob("*.json"):
            p.unlink()
    rows = []
    for train_days, test_day in splits(days, args.min_train):
        if args.incremental and (outdir / f"{test_day}.json").exists():
            print(f"{test_day} skip: exists (incremental)")
            continue
        Xtr, ytr, atr = [], [], []
        seen: set[str] = set()
        n_train_days = 0
        for d in train_days:
            cells = day_cells(d, tbl)
            if not cells:
                continue
            n_train_days += 1
            seen.update(cells)
            day_rng = np.random.default_rng([args.seed, int(d)])
            Xd, yd, _, advd = samples(cells, day_rng)
            Xtr.append(Xd)
            ytr.append(yd)
            atr.append(advd)
        if not Xtr:
            print(f"{test_day} skip: no train samples")
            continue
        Xtr = np.concatenate(Xtr)
        ytr = np.concatenate(ytr)
        atr = np.concatenate(atr)
        scaler = Scaler().fit(Xtr, names)
        model = make(args.model)
        model.fit(scaler.transform(Xtr, names), ytr, atr, pos_col)
        cells = day_cells(test_day, tbl)
        codes = sorted(cells)
        if not codes:
            print(f"{test_day} skip: no test codes")
            continue
        rng_eval = eval_rng(args.seed, test_day)
        per = eval_codes(model, scaler, bars.FEATURES, cells, codes, args.reps, rng_eval)
        st = strata(codes)
        sh: dict[str, list] = {"stock": [], "etf": []}
        for r in range(args.shuffle_reps):
            Xs, ys, av = [], [], []
            for d in train_days:
                tcells = day_cells(d, tbl)
                if not tcells:
                    continue
                Xd, yd, _, advd = samples(tcells, np.random.default_rng([args.seed, int(d)]),
                                          np.random.default_rng([args.seed, int(d), 1000 + r]))
                Xs.append(Xd)
                ys.append(yd)
                av.append(advd)
            Xsh = np.concatenate(Xs)
            ysh = np.concatenate(ys)
            ash = np.concatenate(av)
            sc_sh = Scaler().fit(Xsh, names)
            m_sh = make(args.model)
            m_sh.fit(sc_sh.transform(Xsh, names), ysh, ash, pos_col)
            per_sh = eval_codes(m_sh, sc_sh, bars.FEATURES, cells, codes, 0, rng_eval)
            for sname, sc in st.items():
                if sc:
                    sh[sname].append(float(np.mean([per_sh[c]["model"] for c in sc])))
        rec: dict = {"day": test_day, "version": args.version, "model": args.model,
                     "n_train_days": n_train_days, "reasons": day_reasons(test_day)}
        for sname, sc in st.items():
            s = summarize(per, sc)
            sn = [c for c in sc if c in seen]
            un = [c for c in sc if c not in seen]
            rec[sname] = {
                "n": s["n"], "codes": sc, "model": s["model"], "model_slip1": s["model_slip1"],
                "trades": s["trades"], "log_sum": s["log_sum"],
                "log_sum_slip1": s["log_sum_slip1"], "bh": s["bh"],
                "ps_day": [float(v) for v in s["ps_day"]], "sh_day": sh[sname],
                "diag_capture": s["diag_capture"], "diag_pct_random": s["diag_pct_random"],
                "seen_n": len(sn), "unseen_n": len(un),
                "seen_model": float(np.mean([per[c]["model"] for c in sn])) if sn else float("nan"),
                "unseen_model": float(np.mean([per[c]["model"] for c in un])) if un else float("nan"),
            }
        (outdir / f"{test_day}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append(rec)
        for sname in ("stock", "etf"):
            e = rec[sname]
            print(f"{test_day} {sname} n={e['n']} train_days={n_train_days} model={e['model']:+.4%} "
                  f"slip1={e['model_slip1']:+.4%} bh={e['bh']:+.4%} trades={e['trades']} "
                  f"diag_cap={e['diag_capture']:.2f} diag_pr={e['diag_pct_random']:.2f} "
                  f"seen={e['seen_model']:+.4%}(n={e['seen_n']}) "
                  f"unseen={e['unseen_model']:+.4%}(n={e['unseen_n']})")
    for sname in ("stock", "etf"):
        have = [r for r in rows if r[sname]["n"] > 0]
        vals = [r[sname]["model"] for r in have]
        trd = int(sum(r[sname]["trades"] for r in have))
        ls0 = float(sum(r[sname]["log_sum"] for r in have))
        ls1 = float(sum(r[sname]["log_sum_slip1"] for r in have))
        bp0 = ls0 / trd * 1e4 if trd else float("nan")
        bp1 = ls1 / trd * 1e4 if trd else float("nan")
        med = float(np.median(vals)) if vals else float("nan")
        if len(vals) >= 2:
            cut = int(np.argmax(np.abs(vals)))
            drop = _nanmean([v for i, v in enumerate(vals) if i != cut])
        else:
            drop = float("nan")
        print(f"total {sname} days={len(vals)} avg={_nanmean(vals):+.4%} trades={trd} "
              f"bp_slip0={bp0:+.1f} bp_slip1={bp1:+.1f} med={med:+.4%} dropmax={drop:+.4%}")


if __name__ == "__main__":
    main()
