"""ΔCCC 스프레드 측정."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from research.ccc.factors import load_factors
from research.ccc.run import _decile_panel, _panel_md

ROOT = Path(__file__).resolve().parents[2]


def add_delta(cs: pd.DataFrame) -> pd.DataFrame:
    """전년 동기 CCC 매칭해 dccc·dccc_adj 추가."""
    out = cs.reset_index(drop=True).copy()
    out["_rowid"] = range(len(out))
    first = (
        out.sort_values("ym")
        .drop_duplicates(["cik", "qend"], keep="first")[["cik", "qend", "ccc"]]
        .rename(columns={"qend": "qend_lag", "ccc": "ccc_lag"})
    )
    m = out.merge(first, on="cik", how="left")
    days = (pd.to_datetime(m["qend"]) - pd.to_datetime(m["qend_lag"])).dt.days
    m = m.loc[(days >= 350) & (days <= 380)].copy()
    m = m.sort_values("qend_lag").drop_duplicates("_rowid", keep="last")
    m = m.drop(columns=["qend_lag", "_rowid"]).reset_index(drop=True)
    m["dccc"] = m["ccc"] - m["ccc_lag"]
    m["dccc"] = m.groupby("ym")["dccc"].transform(lambda s: s.clip(s.quantile(0.01), s.quantile(0.99)))
    m["dccc_adj"] = m["dccc"] - m.groupby(["ym", "ind"])["dccc"].transform("median")
    return m


def main(argv=None) -> None:
    """캐시 읽어 기간별 ΔCCC 패널 md 저장."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=ROOT / "research" / "private" / "ccc" / "cache" / "cs_48.pkl")
    args = ap.parse_args(argv)
    cs = pd.read_pickle(args.cache)
    print(f"읽기: n={len(cs)}", flush=True)
    df = add_delta(cs)
    factors = load_factors()
    rate = f"{len(df) / len(cs) * 100:.1f}%" if len(cs) else "-"
    print(f"표본: n={len(df)} (전년 매칭률 {rate})", flush=True)
    PERIODS = [("ALL", None, None), ("2010-07~2015-12", 201007, 201512), ("2016-01~", 201601, None)]
    months = sorted(df["ym"].unique()) if len(df) else []
    n_avg = float(df.groupby("ym").size().mean()) if len(df) else float("nan")
    dmed = float(df["dccc"].median()) if len(df) else float("nan")
    lines = [
        "# ΔCCC (전년 동기 대비 CCC 변화) 스프레드",
        "",
        "- D1 = ΔCCC 최소(개선 최대), D10 = ΔCCC 최대(악화 최대). 가설이 맞으면 D1−D10 > 0.",
        "- 신호: dccc = ccc(qend) − ccc(같은 cik, 350~380일 전 분기), dccc_adj = 월·FF48 업종 중앙값 차감, 월별 1% 윈저.",
        "- 생존편향·배당 미포함 등 한계는 월별 재현과 같음.",
        "",
        "## 표본",
        f"- 수익 월: {months[0] if months else '-'} ~ {months[-1] if months else '-'} ({len(months)}개월)",
        f"- 월평균 종목 수: {n_avg:.1f}",
        f"- dccc 중앙값: {dmed:.1f}일",
        "",
    ]
    for name, lo, hi in PERIODS:
        sub = df
        if lo is not None:
            sub = sub[sub["ym"] >= lo]
        if hi is not None:
            sub = sub[sub["ym"] <= hi]
        rows = _decile_panel(sub, "dccc", factors) + _decile_panel(sub, "dccc_adj", factors)
        lines += [f"## {name}", _panel_md(rows), ""]
    outp = ROOT / "research" / "private" / "ccc" / "DELTA_RESULTS.md"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text("\n".join(lines), encoding="utf-8")
    print(f"저장: {outp}", flush=True)


if __name__ == "__main__":
    main()
