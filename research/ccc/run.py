"""CCC 재현 실행."""

from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from research.ccc.factors import load_factors
from research.ccc.panel import FACTS_DB, asof, ccc_table, load_facts
from research.ccc.returns import OHLCV_DB, month_end_prices, monthly_returns, ticker_key
from research.ccc.sort import cross_section, decile_returns, formation_window, nw_tstat, size_double_sort, spread_stats

ROOT = Path(__file__).resolve().parents[2]


def _ym_range(start: int, end: int) -> list[int]:
    y, m = divmod(start, 100)
    ey, em = divmod(end, 100)
    out = []
    while (y, m) <= (ey, em):
        out.append(y * 100 + m)
        m += 1
        if m > 12:
            y += 1
            m = 1
    return out


def _load_companies(db_path) -> pd.DataFrame:
    p = str(Path(db_path).resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        return pd.read_sql_query("SELECT cik, ticker, sic FROM companies", con)
    finally:
        con.close()


def _load_ind_map(db_path, sics) -> dict:
    sys.path.insert(0, str(ROOT / "etl" / "scripts"))
    from build_us_facts import ff_industry

    p = str(Path(db_path).resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        out = {}
        for scheme in (48, 5):
            for s in sics:
                r = ff_industry(con, scheme, int(s))
                if r is not None:
                    out[(scheme, int(s))] = r[0]
        return out
    finally:
        con.close()


def _mapping_diag(ym: int, facts: pd.DataFrame, rets: pd.DataFrame, companies: pd.DataFrame) -> tuple[int, int]:
    """CCC cik 수·가격 매칭 실패 수."""
    d, lo, hi = formation_window(ym)
    pre = facts[facts["end"] >= lo - pd.Timedelta(days=400)]
    if pre.empty:
        return (0, 0)
    f = asof(pre, d)
    if f.empty:
        return (0, 0)
    ccc = ccc_table(f)
    if ccc.empty:
        return (0, 0)
    ccc = ccc[(ccc["qend"] >= lo) & (ccc["qend"] <= hi)]
    if ccc.empty:
        return (0, 0)
    ccc = ccc.sort_values("qend").drop_duplicates("cik", keep="last")
    comp = companies[companies["ticker"].notna()][["cik", "ticker"]].copy()
    comp["key"] = comp["ticker"].astype(str).map(ticker_key)
    rym = rets[(rets["ym"] == ym) & rets["ret"].notna()]
    rkeys = set(rym["ticker"].astype(str).map(ticker_key)) if not rym.empty else set()
    m = ccc[["cik"]].merge(comp[["cik", "key"]], on="cik", how="left")
    return (len(ccc), int((~m["key"].isin(rkeys)).sum()))


def _fp(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v * 100:.2f}" if v == v else "nan"


def _ft(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v:.2f}" if v == v else "nan"


def _decile_panel(cs_all: pd.DataFrame, col: str, factors: pd.DataFrame) -> list[dict]:
    rows = []
    for univ, ex in (("ALL", False), ("exMIC", True)):
        for w in ("EW", "VW"):
            decs = {}
            for ym, g in cs_all.groupby("ym"):
                if ex:
                    g = g[~g["microcap"]]
                if len(g) < 10:
                    continue
                try:
                    decs[ym] = decile_returns(g, col, w.lower())
                except ValueError:
                    continue
            if not decs:
                rows.append({"signal": col, "univ": univ, "w": w, "d": [float("nan")] * 10,
                             "st": {k: float("nan") for k in
                                    ("mean", "t", "capm_a", "capm_t", "ff3_a", "ff3_t", "ff5m_a", "ff5m_t")}})
                continue
            dd = pd.DataFrame(decs).T.sort_index()
            means = [float(dd[c].mean()) for c in range(1, 11)]
            st = spread_stats(dd[1] - dd[10], factors)
            rows.append({"signal": col, "univ": univ, "w": w, "d": means, "st": st})
    return rows


def _panel_md(rows: list[dict]) -> str:
    head = "| signal | univ | w | " + " | ".join(f"D{i}" for i in range(1, 11)) + \
        " | mean | t | CAPM a | t | FF3 a | t | FF5M a | t |"
    sep = "|" + "|".join(["---"] * 21) + "|"
    out = [head, sep]
    for r in rows:
        cells = [r["signal"], r["univ"], r["w"], *[_fp(v) for v in r["d"]],
                 _fp(r["st"]["mean"]), _ft(r["st"]["t"]),
                 _fp(r["st"]["capm_a"]), _ft(r["st"]["capm_t"]),
                 _fp(r["st"]["ff3_a"]), _ft(r["st"]["ff3_t"]),
                 _fp(r["st"]["ff5m_a"]), _ft(r["st"]["ff5m_t"])]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=201007)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--facts-db", type=Path, default=FACTS_DB)
    ap.add_argument("--ohlcv-db", type=Path, default=OHLCV_DB)
    args = ap.parse_args(argv)
    facts = load_facts(db_path=args.facts_db)
    rets = monthly_returns(month_end_prices(db_path=args.ohlcv_db))
    companies = _load_companies(args.facts_db)
    sics = sorted(int(s) for s in companies["sic"].dropna().unique())
    ind_map = _load_ind_map(args.facts_db, sics)
    factors = load_factors()
    t = datetime.date.today()
    prev_ym = t.year * 100 + t.month - 1 if t.month > 1 else (t.year - 1) * 100 + 12
    ret_max = int(rets.loc[rets["ret"].notna(), "ym"].max())
    end = args.end if args.end is not None else min(ret_max, prev_ym)
    yms = _ym_range(args.start, end)
    all48, all5 = [], []
    for i, ym in enumerate(yms):
        c48 = cross_section(ym, facts, rets, companies, ind_map, 48)
        c5 = cross_section(ym, facts, rets, companies, ind_map, 5)
        all48.append(c48)
        all5.append(c5)
        if i % 12 == 0:
            nm = int(c48["microcap"].sum()) if len(c48) else 0
            print(f"[{ym}] n={len(c48)} microcap={nm}", flush=True)
    cs48 = pd.concat(all48, ignore_index=True) if all48 else pd.DataFrame()
    cs5 = pd.concat(all5, ignore_index=True) if all5 else pd.DataFrame()
    cache = ROOT / "research" / "private" / "ccc" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    cs48.to_pickle(cache / "cs_48.pkl")
    cs5.to_pickle(cache / "cs_5.pkl")
    months = sorted(cs48["ym"].unique()) if len(cs48) else []
    n_all = float(cs48.groupby("ym").size().mean()) if len(cs48) else float("nan")
    if len(cs48):
        cnt = cs48.groupby("ym")["microcap"].apply(lambda s: int((~s).sum())).reindex(months, fill_value=0)
        n_ex = float(cnt.mean())
    else:
        n_ex = float("nan")
    f0 = _mapping_diag(yms[0], facts, rets, companies) if yms else (0, 0)
    f1 = _mapping_diag(yms[-1], facts, rets, companies) if yms else (0, 0)
    rows_a = _decile_panel(cs48, "ccc", factors) + _decile_panel(cs48, "ccc_adj", factors)
    rows_b = _decile_panel(cs5, "ccc_adj", factors)
    dbl = {}
    for ym, g in (cs48.groupby("ym") if len(cs48) else []):
        dbl[ym] = size_double_sort(g, "ccc_adj")
    dd = pd.DataFrame(dbl).T.sort_index() if dbl else pd.DataFrame()
    lines = ["# CCC 스프레드 재현 (Wang 2019)", "",
             "- 생존편향: 현재 상장 종목만 있어 상폐(주로 고CCC·부실) 수익이 빠진다.",
             "- 배당 미포함: 분할 조정 종가 기준, 배당 수익 빠진다.",
             "- 현재 SIC·현재 티커 기준: 과거 업종·티커 변경 미반영.", "",
             "## 표본",
             f"- 수익 월: {yms[0] if yms else '-'} ~ {yms[-1] if yms else '-'} ({len(months)}개월)",
             f"- 월평균 종목 수: 전체 {n_all:.1f}, 초소형 제외 {n_ex:.1f}",
             f"- 매핑 진단: 첫 달 CCC {f0[0]} cik 중 {f0[1]} 실패, 마지막 달 CCC {f1[0]} cik 중 {f1[1]} 실패.",
             "", "## 패널 A (FF48)", _panel_md(rows_a), "",
             "## 패널 B (FF5 업종조정)", _panel_md(rows_b), "",
             "## 사이즈 이중정렬 (EW, ccc_adj FF48, 전체)",
             "| size | mean | t |", "|---|---|---|"]
    for g in range(1, 6):
        if len(dd) and g in dd.columns:
            b, t = nw_tstat(dd[g].to_numpy(), lags=6)
            lines.append(f"| {g} | {_fp(b[0])} | {_ft(t[0])} |")
        else:
            lines.append(f"| {g} | nan | nan |")
    lines.append("")
    outp = ROOT / "research" / "private" / "ccc" / "RESULTS.md"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
