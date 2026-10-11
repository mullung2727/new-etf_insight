"""공시일 전후 CAR 이벤트 스터디."""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from research.ccc.panel import FACTS_DB, ccc_table, load_facts
from research.ccc.returns import OHLCV_DB, ticker_key
from research.ccc.run import _load_companies, _load_ind_map
from research.ccc.sort import nw_tstat

ROOT = Path(__file__).resolve().parents[2]
BUCKETS = [(-5, -1), (0, 1), (2, 5), (6, 20), (21, 60), (0, 60)]
PERIODS = [("ALL", None, None),
           ("2010-2015", pd.Timestamp("2010-01-01"), pd.Timestamp("2015-12-31")),
           ("2016-", pd.Timestamp("2016-01-01"), None)]
CURVE_KS = [-5, -1, 0, 1, 2, 5, 10, 20, 40, 60]


def first_reported(facts: pd.DataFrame) -> pd.DataFrame:
    """(cik,tag,start,end)별 최초 공시 행."""
    if facts.empty:
        return facts.copy()
    return (facts.sort_values("filed")
            .drop_duplicates(["cik", "tag", "start", "end"], keep="first")
            .reset_index(drop=True))


def build_events(facts: pd.DataFrame, companies: pd.DataFrame, ind_map: dict,
                 scheme: int = 48, max_lag_days: int = 120, rank_within_ind: bool = False) -> pd.DataFrame:
    """최초공시 CCC → 분기별 10분위 이벤트."""
    cols = ["event_id", "cik", "ticker", "qend", "avail", "aq", "ccc_adj", "dec"]
    ccc = ccc_table(first_reported(facts))
    if ccc.empty:
        return pd.DataFrame(columns=cols)
    ccc = ccc[(ccc["avail"] - ccc["qend"]).dt.days <= max_lag_days].copy()
    if ccc.empty:
        return pd.DataFrame(columns=cols)
    ccc = ccc.sort_values("avail").drop_duplicates(["cik", "qend"], keep="first")
    comp = companies[["cik", "ticker", "sic"]].copy()
    comp = comp[comp["ticker"].notna() & comp["sic"].notna()].copy()
    if comp.empty:
        return pd.DataFrame(columns=cols)
    comp["sic"] = comp["sic"].astype(int)
    comp = comp[(comp["sic"] < 6000) | (comp["sic"] > 6999)]
    if comp.empty:
        return pd.DataFrame(columns=cols)
    comp["ticker"] = comp["ticker"].astype(str).map(ticker_key)
    comp["ind"] = comp["sic"].map(lambda s: ind_map.get((scheme, int(s))))
    comp = comp[comp["ind"].notna()]
    if comp.empty:
        return pd.DataFrame(columns=cols)
    m = ccc.merge(comp, on="cik", how="inner")
    if m.empty:
        return pd.DataFrame(columns=cols)
    m["aq"] = m["avail"].dt.year * 10 + m["avail"].dt.quarter
    parts = []
    for _, g in m.groupby("aq"):
        if len(g) < 10:
            continue
        g = g.copy()
        lo1, hi99 = g["ccc"].quantile(0.01), g["ccc"].quantile(0.99)
        g["ccc"] = g["ccc"].clip(lo1, hi99)
        g["ccc_adj"] = g["ccc"] - g.groupby("ind")["ccc"].transform("median")
        if rank_within_ind:  # 업종 분산 차이 제거: 업종 내 순위로 10분위
            r = g.groupby("ind")["ccc"].rank(method="average")
            n = g.groupby("ind")["ccc"].transform("size")
            g["dec"] = (np.floor((r - 0.5) / n * 10) + 1).clip(1, 10).astype(int)
        else:
            g["dec"] = pd.qcut(g["ccc_adj"].rank(method="first"), 10,
                               labels=range(1, 11)).astype(int)
                           
        parts.append(g)
    if not parts:
        return pd.DataFrame(columns=cols)
    out = pd.concat(parts, ignore_index=True)
    out = out.sort_values(["aq", "dec"]).reset_index(drop=True)
    out["event_id"] = range(len(out))
    return out[cols]


def event_windows(events: pd.DataFrame, con, lo: int = -5, hi: int = 60,
                  clip: tuple = (-0.5, 1.0)) -> pd.DataFrame:
    """이벤트별 거래일 창 초과수익 (전부 SQL)."""
    cols = ["event_id", "k", "ar"]
    if events.empty:
        return pd.DataFrame(columns=cols)
    ev = pd.DataFrame({"event_id": events["event_id"].to_numpy(),
                       "ticker": events["ticker"].astype(str).to_numpy(),
                       "avail": pd.to_datetime(events["avail"]).dt.strftime("%Y%m%d")})
    key = "upper(replace(replace(ticker, '.', '-'), '/', '-'))"
    q = f"""
    WITH b AS (
      SELECT {key} AS tkey, date, close,
             ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY date) AS tidx,
             LAG(close) OVER (PARTITION BY {key} ORDER BY date) AS prev
      FROM ohlcv
    ),
    d AS (
      SELECT tkey, date, tidx,
             CASE WHEN prev IS NULL OR prev <= 0 THEN NULL
                  ELSE LEAST(GREATEST(close / prev - 1, ?), ?) END AS ret
      FROM b
    ),
    mkt AS (
      SELECT date, AVG(ret) AS mret FROM d WHERE ret IS NOT NULL GROUP BY date
    ),
    day0 AS (
      SELECT e.event_id AS event_id, MIN(d.tidx) AS tidx0
      FROM _ev e JOIN d ON d.tkey = e.ticker AND d.date >= e.avail
      GROUP BY e.event_id
    )
    SELECT day0.event_id AS event_id, (d.tidx - day0.tidx0) AS k,
           (d.ret - mkt.mret) AS ar
    FROM day0
    JOIN _ev e ON e.event_id = day0.event_id
    JOIN d ON d.tkey = e.ticker AND d.tidx BETWEEN day0.tidx0 + ? AND day0.tidx0 + ?
    JOIN mkt ON mkt.date = d.date
    WHERE d.ret IS NOT NULL
    """
    con.register("_ev", ev)
    try:
        out = con.execute(q, [clip[0], clip[1], lo, hi]).fetchdf()
    finally:
        con.unregister("_ev")
    if out.empty:
        return pd.DataFrame(columns=cols)
    return out[cols]


def summarize(events: pd.DataFrame, ar: pd.DataFrame) -> dict:
    """구간 CAR·분기 시계열 t·CAR 곡선."""
    ev_cols = ["event_id", "aq", "dec", "avail"]
    m = ar.merge(events[ev_cols], on="event_id", how="left")
    rows = []
    for name, p0, p1 in PERIODS:
        for a, b in BUCKETS:
            sub = m[(m["k"] >= a) & (m["k"] <= b)]
            car = sub.groupby("event_id")["ar"].sum().rename("car").reset_index()
            car = car.merge(events[ev_cols], on="event_id", how="left")
            if p0 is not None:
                car = car[car["avail"] >= p0]
            if p1 is not None:
                car = car[car["avail"] <= p1]
            d1m = car[car["dec"] == 1].groupby("aq")["car"].mean()
            d10m = car[car["dec"] == 10].groupby("aq")["car"].mean()
            sp = (d1m - d10m).dropna()
            d1 = float(d1m.reindex(sp.index).mean()) if len(sp) else float("nan")
            d10 = float(d10m.reindex(sp.index).mean()) if len(sp) else float("nan")
            spread = float(sp.mean()) if len(sp) else float("nan")
            t = float(nw_tstat(sp.to_numpy(), lags=4)[1][0])
            n_ev = int(((car["dec"] == 1) | (car["dec"] == 10)).sum())
            rows.append({"period": name, "bucket": f"({a},{b})", "d1": d1, "d10": d10,
                         "spread": spread, "t": t, "nq": len(sp), "n_ev": n_ev})
    table = pd.DataFrame(rows, columns=["period", "bucket", "d1", "d10", "spread",
                                        "t", "nq", "n_ev"])
    ks = sorted(m["k"].dropna().unique().tolist()) if len(m) else []
    if ks:
        g1 = m[m["dec"] == 1].groupby("k")["ar"].mean()
        g10 = m[m["dec"] == 10].groupby("k")["ar"].mean()
        curve = pd.DataFrame({"k": ks, "d1": g1.reindex(ks).cumsum().to_numpy(),
                              "d10": g10.reindex(ks).cumsum().to_numpy()})
        curve["spread"] = curve["d1"] - curve["d10"]
    else:
        curve = pd.DataFrame(columns=["k", "d1", "d10", "spread"])
    return {"table": table, "curve": curve}


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


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts-db", type=Path, default=FACTS_DB)
    ap.add_argument("--ohlcv-db", type=Path, default=OHLCV_DB)
    args = ap.parse_args(argv)
    facts = load_facts(db_path=args.facts_db)
    print(f"facts: {len(facts)}", flush=True)
    companies = _load_companies(args.facts_db)
    sics = sorted(int(s) for s in companies["sic"].dropna().unique())
    ind_map = _load_ind_map(args.facts_db, sics)
    events = build_events(facts, companies, ind_map)
    aqr = f"{int(events['aq'].min())}~{int(events['aq'].max())}" if len(events) else "-"
    print(f"events: {len(events)} (aq {aqr})", flush=True)
    con = duckdb.connect(str(args.ohlcv_db), read_only=True)
    try:
        ar = event_windows(events, con)
    finally:
        con.close()
    print(f"windows: {len(ar)} rows", flush=True)
    res = summarize(events, ar)
    lines = ["# CCC 이벤트 스터디 (공시일 전후 CAR)", "",
             "- 시점: (cik,tag,start,end) 최초 공시값 → avail = 구성항목 최대 filed",
             "- 시장: 같은 날 ohlcv 전 종목 동일가중(EW) 평균",
             "- 클립: 일수익 [-50%, +100%]", "",
             "## 표본",
             f"- 이벤트: {len(events)}건, aq {aqr}", "",
             "## 구간 CAR",
             "| 기간 | 구간 | D1 | D10 | D1-D10 | t | 분기수 | 이벤트수 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in res["table"].itertuples():
        lines.append(f"| {r.period} | {r.bucket} | {_fp(r.d1)} | {_fp(r.d10)} | "
                     f"{_fp(r.spread)} | {_ft(r.t)} | {int(r.nq)} | {int(r.n_ev)} |")
    lines += ["", "## CAR 곡선 (전체 기간)",
              "| k | D1 | D10 | D1-D10 |", "|---|---|---|---|"]
    curve = res["curve"]
    show = curve[curve["k"].isin(CURVE_KS)].sort_values("k")
    for r in show.itertuples():
        lines.append(f"| {int(r.k)} | {_fp(r.d1)} | {_fp(r.d10)} | {_fp(r.spread)} |")
    lines.append("")
    outp = ROOT / "research" / "private" / "ccc" / "EVENT_RESULTS.md"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {outp}", flush=True)


if __name__ == "__main__":
    main()
