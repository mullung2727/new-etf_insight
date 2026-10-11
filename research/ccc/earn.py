"""Wang(2019) Table 12(식 5) 간소화 재현: CCC 분위가 다음 분기 이익을 예측하는지."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from research.ccc.event import build_events, first_reported
from research.ccc.panel import FACTS_DB, load_facts, quarter_flow
from research.ccc.run import _load_companies, _load_ind_map
from research.ccc.sort import nw_tstat

ROOT = Path(__file__).resolve().parents[2]
INCOME_TAGS = ("NetIncomeLoss", "ProfitLoss")
COLS = ("low", "high", "ln_at_lag", "e_lag", "neg_lag")
PERIODS = [("ALL", None, None),
           ("2010-2015", pd.Timestamp("2010-01-01"), pd.Timestamp("2015-12-31")),
           ("2016-", pd.Timestamp("2016-01-01"), None)]


def earnings_panel(f: pd.DataFrame) -> pd.DataFrame:
    """분기 순이익/직전자산 패널."""
    cols = ["cik", "qend", "e", "ln_at", "neg"]
    if f.empty:
        return pd.DataFrame(columns=cols)
    ni = quarter_flow(f, INCOME_TAGS, allow_negative=True)
    if ni.empty:
        return pd.DataFrame(columns=cols)
    at = f[(f["tag"] == "Assets") & f["start"].isna()
           & f["end"].notna()][["cik", "end", "val"]].copy()
    if at.empty:
        return pd.DataFrame(columns=cols)
    at = at.sort_values("end").drop_duplicates(["cik", "end"], keep="last")
    m = ni[["cik", "qend"]].merge(at, on="cik", how="left")
    lo = m["qend"] - pd.to_timedelta(100, unit="D")
    hi = m["qend"] - pd.to_timedelta(80, unit="D")
    m = m[(m["end"] >= lo) & (m["end"] <= hi)]
    if m.empty:
        return pd.DataFrame(columns=cols)
    m = m.sort_values("end").drop_duplicates(["cik", "qend"], keep="last")
    m = m.rename(columns={"val": "at_lag"})[["cik", "qend", "at_lag"]]
    out = ni.merge(m, on=["cik", "qend"], how="left")
    out = out[out["at_lag"] > 0].copy()
    if out.empty:
        return pd.DataFrame(columns=cols)
    out["e"] = out["val"] / out["at_lag"]
    cur = at.rename(columns={"end": "qend", "val": "at_cur"})[["cik", "qend", "at_cur"]]
    out = out.merge(cur, on=["cik", "qend"], how="left")
    out["ln_at"] = np.where(out["at_cur"] > 0, np.log(out["at_cur"]), np.nan)
    out["neg"] = out["val"] < 0
    return out[cols].reset_index(drop=True)


def build_reg(events: pd.DataFrame, ep: pd.DataFrame) -> pd.DataFrame:
    """t-1 이벤트 + t 이익 결합 회귀 패널."""
    cols = ["cik", "q0", "q1", "cq", "y", "low", "high",
            "e_lag", "neg_lag", "ln_at_lag"]
    if events.empty or ep.empty:
        return pd.DataFrame(columns=cols)
    ev = events[["cik", "qend", "dec"]].rename(columns={"qend": "q0"}).copy()
    e0 = ep.rename(columns={"qend": "q0", "e": "e_lag", "neg": "neg_lag",
                            "ln_at": "ln_at_lag"})[["cik", "q0", "e_lag", "neg_lag", "ln_at_lag"]]
    base = ev.merge(e0, on=["cik", "q0"], how="left")
    cand = ev[["cik", "q0"]].merge(
        ep.rename(columns={"qend": "q1", "e": "y"})[["cik", "q1", "y"]],
        on="cik", how="left")
    lo = cand["q0"] + pd.to_timedelta(80, unit="D")
    hi = cand["q0"] + pd.to_timedelta(100, unit="D")
    cand = cand[(cand["q1"] >= lo) & (cand["q1"] <= hi)]
    if cand.empty:
        return pd.DataFrame(columns=cols)
    cand = cand.sort_values("q1").drop_duplicates(["cik", "q0"], keep="first")
    out = base.merge(cand[["cik", "q0", "q1", "y"]], on=["cik", "q0"], how="inner")
    out["low"] = out["dec"] == 1
    out["high"] = out["dec"] == 10
    out = out.dropna(subset=["y", "e_lag", "neg_lag", "ln_at_lag"]).copy()
    if out.empty:
        return pd.DataFrame(columns=cols)
    out["q1"] = pd.to_datetime(out["q1"])
    out["cq"] = out["q1"].dt.year * 10 + out["q1"].dt.quarter
    for c in ("y", "e_lag"):
        out[c] = out.groupby("cq")[c].transform(
            lambda s: s.clip(s.quantile(0.01), s.quantile(0.99)))
    return out[cols].reset_index(drop=True)


def fama_macbeth(reg: pd.DataFrame, cols=COLS) -> pd.DataFrame:
    """달력분기별 OLS → 계수 평균, NW t(lags=4)."""
    out_cols = ["term", "coef", "t", "nq"]
    coefs: dict[str, list[float]] = {c: [] for c in cols}
    if reg is not None and len(reg):
        for _, g in reg.groupby("cq"):
            if len(g) < 30:
                continue
            if float(g["low"].sum()) == 0 or float(g["high"].sum()) == 0:
                continue
            X = np.column_stack([np.ones(len(g)), g[list(cols)].to_numpy(float)])
            y = g["y"].to_numpy(float)
            ok = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
            X, y = X[ok], y[ok]
            if len(y) < 30:
                continue
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            for j, c in enumerate(cols):
                coefs[c].append(float(beta[j + 1]))
    rows = []
    for c in cols:
        s = np.asarray(coefs[c], dtype=float)
        if len(s):
            rows.append((c, float(s.mean()), float(nw_tstat(s, lags=4)[1][0]), len(s)))
        else:
            rows.append((c, float("nan"), float("nan"), 0))
    return pd.DataFrame(rows, columns=out_cols)


def _fc(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v:.5f}" if v == v else "nan"


def _ft(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v:.2f}" if v == v else "nan"


def _fm(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v:.5f}" if v == v else "nan"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts-db", type=Path, default=FACTS_DB)
    args = ap.parse_args(argv)
    facts = load_facts(db_path=args.facts_db, extra_tags=INCOME_TAGS)
    print(f"facts: {len(facts)}", flush=True)
    f1 = first_reported(facts)
    companies = _load_companies(args.facts_db)
    sics = sorted(int(s) for s in companies["sic"].dropna().unique())
    ind_map = _load_ind_map(args.facts_db, sics)
    events = build_events(facts, companies, ind_map)
    ep = earnings_panel(f1)
    reg = build_reg(events, ep)
    print(f"events: {len(events)} reg: {len(reg)}", flush=True)
    lines = ["# CCC 이익 예측 (Wang 2019 Table 12 간소화)", "",
             "- E = 분기 순이익 / 직전 분기말 Assets",
             "- 생략 통제: Dividend, DDiv, Accruals (1차 한계, 계수 크기 직접 비교 불가)",
             "- 값: (cik,tag,start,end) 최초 공시값", ""]
    nq_all = int(reg["cq"].nunique()) if len(reg) else 0
    lines += ["## 표본",
              f"- 관측수: {len(reg)}, 분기수: {nq_all}", ""]
    aux_rows = []
    for name, p0, p1 in PERIODS:
        sub = reg
        if p0 is not None:
            sub = sub[sub["q1"] >= p0]
        if p1 is not None:
            sub = sub[sub["q1"] <= p1]
        res = fama_macbeth(sub)
        nq = int(res["nq"].max()) if len(res) else 0
        lines += [f"## {name} (관측 {len(sub)}, 분기 {nq})",
                  "| term | coef | t | 분기수 |",
                  "|---|---|---|---|"]
        for r in res.itertuples():
            lines.append(f"| {r.term} | {_fc(r.coef)} | {_ft(r.t)} | {int(r.nq)} |")
        lines.append("")
        for gname, g in (("low", sub[sub["low"]]),
                         ("high", sub[sub["high"]]),
                         ("rest", sub[~sub["low"] & ~sub["high"]])):
            my = float(g["y"].mean()) if len(g) else float("nan")
            md = float((g["y"] - g["e_lag"]).mean()) if len(g) else float("nan")
            aux_rows.append((name, gname, my, md, len(g)))
    lines += ["## 기술통계 (평균 y, 평균 (y - e_lag))",
              "| 기간 | 그룹 | 평균 y | 평균 (y - e_lag) | n |",
              "|---|---|---|---|---|"]
    for name, gname, my, md, n in aux_rows:
        lines.append(f"| {name} | {gname} | {_fm(my)} | {_fm(md)} | {n} |")
    lines.append("")
    outp = ROOT / "research" / "private" / "ccc" / "EARN_RESULTS.md"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {outp}", flush=True)


if __name__ == "__main__":
    main()
