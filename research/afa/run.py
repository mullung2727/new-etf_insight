"""AFA 재현 실행."""

from __future__ import annotations

import argparse
import datetime
import gc
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.afa.fair import mispricing, ols_fair, ts_fair
from research.afa.items import winsorize_by_assets
from research.ccc.factors import load_factors
from research.ccc.panel import FACTS_DB, SHARES_TAG, load_facts
from research.ccc.returns import OHLCV_DB, month_end_prices, monthly_returns, ticker_key
from research.ccc.run import _load_companies, _load_ind_map, _ym_range
from research.ccc.sort import formation_window, spread_stats
from research.us_fin_std.build import load_std, std_snapshot

ROOT = Path(__file__).resolve().parents[2]
PERIODS = [("ALL", None, None), ("2010-07~2012-12", 201007, 201212),
           ("2013-01~2017-12", 201301, 201712), ("2018-01~", 201801, None)]
CS_COLS = ["ym", "cik", "ticker", "ind", "ret", "ret_ia", "me", "microcap",
           "m_ols", "m_ts", "q_ols", "q_ts", "K", "N"]
AFA_ITEMS = ["ATQ", "LTQ", "SEQQ", "TEQQ", "CEQQ", "PSTKQ", "PSTKRQ",
             "PPENTQ", "DLTTQ", "CHEQ", "APQ", "ACOQ", "AOQ", "LCOQ",
             "LOQ", "ICAPTQ", "SALEQ", "IBQ", "IBADJQ", "IBCOMQ", "NIQ",
             "XIDOQ", "DOQ", "PIQ", "TXTQ", "NOPIQ", "DVPQ", "DVQ"]
SHARES_MAX_AGE_DAYS = 400
SHARES_RATIO_MAX = 3.0
MIN_SHARES = 500_000  # 주가 $5 이상 상장사가 50만주 미만인 경우는 거의 없음, 1000배 단위 오류 차단
WAVG_TAG = "WeightedAverageNumberOfSharesOutstandingBasic"
CSO_TAG = "CommonStockSharesOutstanding"


def load_share_tag(db_path=FACTS_DB, tag=WAVG_TAG) -> pd.DataFrame:
    """us-gaap 주식수 태그 읽기 (unit='shares')."""
    cols = ["cik", "end", "filed", "val"]
    p = str(Path(db_path).resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        q = ("SELECT f.cik AS cik, f.end AS end, f.filed AS filed, f.val AS val "
             "FROM facts f JOIN concepts c ON f.concept_id = c.concept_id "
             "WHERE c.taxonomy = 'us-gaap' AND c.tag = ? AND f.unit = 'shares'")
        df = pd.read_sql_query(q, con, params=[tag])
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["end"] = pd.to_datetime(df["end"], errors="coerce")
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df = df[df["end"].notna() & df["filed"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["val"] = df["val"].astype(float)
    return df[cols].reset_index(drop=True)


def _latest_share(df: pd.DataFrame, t: pd.Timestamp, lo: pd.Timestamp) -> pd.Series:
    """filed≤t·end≥lo 중 cik별 (end, filed) 최신 1개, 값>0만."""
    empty = pd.Series(dtype=float, name="val")
    empty.index.name = "cik"
    if df is None or df.empty:
        return empty
    f = df[(df["filed"] <= t) & (df["end"] >= lo)].copy()
    if f.empty:
        return empty
    f = f.sort_values(["end", "filed"]).drop_duplicates("cik", keep="last")
    s = pd.Series(f["val"].to_numpy(float), index=pd.Index(f["cik"], name="cik"), name="val")
    s = s[s > 0]
    s.index.name = "cik"
    s.name = "val"
    return s


def pick_shares(dei: pd.DataFrame, wavg: pd.DataFrame, cso: pd.DataFrame, t) -> pd.Series:
    """dei·wavg·cso 중 cik별 주식수 선택 (wavg 있는 회사만, MIN_SHARES 미만은 NaN)."""
    t = pd.Timestamp(t)
    lo = t - pd.Timedelta(days=SHARES_MAX_AGE_DAYS)
    wraw = _latest_share(wavg, t, lo)
    if wraw.empty:
        return pd.Series(dtype=float, name="val")
    idx = wraw.index
    dser = _latest_share(dei, t, lo).where(lambda s: s >= MIN_SHARES)
    cser = _latest_share(cso, t, lo).where(lambda s: s >= MIN_SHARES)
    wser = wraw.where(lambda s: s >= MIN_SHARES)
    d = dser.reindex(idx).to_numpy(float)
    c = cser.reindex(idx).to_numpy(float)
    w = wser.reindex(idx).to_numpy(float)
    vals = np.column_stack([d, c, w])
    n = np.sum(~np.isnan(vals), axis=1)
    out = np.full(len(idx), np.nan)
    m3 = n == 3
    if m3.any():
        out[m3] = np.nanmedian(vals[m3], axis=1)
    m2 = n == 2
    if m2.any():
        v2 = vals[m2]
        mx = np.nanmax(v2, axis=1)
        mn = np.nanmin(v2, axis=1)
        close = mx / mn <= SHARES_RATIO_MAX
        prio = np.where(~np.isnan(v2[:, 0]), v2[:, 0],
                        np.where(~np.isnan(v2[:, 1]), v2[:, 1], v2[:, 2]))
        out[m2] = np.where(close, prio, mx)
    m1 = n == 1
    if m1.any():
        out[m1] = np.nanmax(vals[m1], axis=1)
    res = pd.Series(out, index=idx, name="val")
    res = res[res > 0]
    res.index.name = "cik"
    res.name = "val"
    return res


def afa_matrix(s: pd.DataFrame) -> pd.DataFrame:
    """std_snapshot 표 → 논문 28항목 (Compustat 이름)."""
    def c(name: str) -> pd.Series:
        if name in s.columns:
            return s[name]
        return pd.Series(np.nan, index=s.index)

    ibxo = c("income_before_xo").fillna(c("net_income") - c("discontinued_ops"))
    ap = c("accounts_payable").fillna(c("ap_and_accrued"))
    out = pd.DataFrame({
        "ATQ": c("total_assets"),
        "LTQ": c("total_liabilities"),
        "SEQQ": c("stockholders_equity"),
        "TEQQ": c("total_equity"),
        "CEQQ": c("stockholders_equity") - c("preferred_stock"),
        "PSTKQ": c("preferred_stock"),
        "PSTKRQ": c("redeemable_preferred"),
        "PPENTQ": c("property_plant_equipment"),
        "DLTTQ": c("long_term_debt"),
        "CHEQ": c("cash_and_sti"),
        "APQ": ap,
        "ACOQ": c("total_current_assets") - c("cash_and_sti")
        - c("accounts_receivable").fillna(0) - c("inventory"),
        "AOQ": c("total_assets") - c("total_current_assets")
        - c("property_plant_equipment") - c("goodwill")
        - c("intangible_assets") - c("long_term_investments"),
        "LCOQ": c("total_current_liabilities") - ap
        - c("short_term_debt"),
        "LOQ": c("total_liabilities") - c("total_current_liabilities")
        - c("long_term_debt") - c("deferred_tax_liabilities"),
        "ICAPTQ": c("long_term_debt") + c("stockholders_equity")
        + c("minority_interest"),
        "SALEQ": c("revenue"),
        "IBQ": ibxo,
        "IBADJQ": ibxo,
        "IBCOMQ": ibxo - c("preferred_dividends"),
        "NIQ": c("net_income"),
        "XIDOQ": c("discontinued_ops"),
        "DOQ": c("discontinued_ops"),
        "PIQ": c("income_before_tax"),
        "TXTQ": c("income_tax_expense"),
        "NOPIQ": c("nonoperating_income"),
        "DVPQ": c("preferred_dividends"),
        "DVQ": c("dividends_paid"),
    }, index=s.index)
    out.index.name = "cik"
    return out[AFA_ITEMS]


def _quintile(s: pd.Series) -> pd.Series:
    """5분위 (1=고평가, 5=저평가)."""
    try:
        return pd.qcut(s, 5, labels=[1, 2, 3, 4, 5]).astype(int)
    except ValueError:
        return pd.qcut(s.rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)


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


def _resolve_end(args, rets: pd.DataFrame) -> int:
    """수익 있는 최대 ym 과 전월 중 작은 값."""
    t = datetime.date.today()
    prev_ym = t.year * 100 + t.month - 1 if t.month > 1 else (t.year - 1) * 100 + 12
    ret_max = int(rets.loc[rets["ret"].notna(), "ym"].max())
    return args.end if args.end is not None else min(ret_max, prev_ym)


def _month_frame(ym: int, m: pd.DataFrame, cut) -> pd.DataFrame:
    """시총·초소형 플래그 부착 표본."""
    m = m.copy()
    m["me"] = m["prev_raw_close"] * m["shares"]
    m = m[(m["me"] > 0) & (m["prev_raw_close"] >= 5) & m["ret"].notna()]
    if m.empty:
        return m
    if cut is None or not np.isfinite(cut):
        m["microcap"] = False
    else:
        m["microcap"] = m["me"].to_numpy(float) < float(cut)
    m["microcap"] = m["microcap"].astype(bool)
    return m.reset_index(drop=True)


def _aggregate(cs: pd.DataFrame, factors: pd.DataFrame) -> list[dict]:
    """signal×univ×weight×기간 집계."""
    rows = []
    skip_ts = False
    if "q_ts" in cs.columns and bool((cs["q_ts"].to_numpy() == 0).all()):
        skip_ts = True
    if "m_ts" in cs.columns and bool(pd.isna(cs["m_ts"]).all()):
        skip_ts = True
    for sig, qcol in (("ols", "q_ols"), ("ts", "q_ts")):
        if sig == "ts" and skip_ts:
            continue
        for univ, ex in (("ALL", False), ("exMIC", True)):
            for w in ("ew", "vw"):
                for pname, p0, p1 in PERIODS:
                    df = cs
                    if ex:
                        df = df[~df["microcap"]]
                    if p0 is not None:
                        df = df[df["ym"] >= p0]
                    if p1 is not None:
                        df = df[df["ym"] <= p1]
                    rec = {"signal": sig, "univ": univ, "w": w, "period": pname,
                           "q": [float("nan")] * 5, "raw": float("nan"),
                           "mean": float("nan"), "t": float("nan"),
                           "ff5m_a": float("nan"), "ff5m_t": float("nan"),
                           "long_mean": float("nan"), "long_t": float("nan"),
                           "short_mean": float("nan"), "short_t": float("nan"),
                           "pos": float("nan"), "n": 0}
                    sp_ia, sp_raw, qm, lng, sht, yml = [], [], [], [], [], []
                    for y, g in df.groupby("ym"):
                        if len(g) < 10 or not {1, 5} <= set(g[qcol].to_numpy(int)):
                            continue
                        if w == "ew":
                            mia = g.groupby(qcol)["ret_ia"].mean()
                            mraw = g.groupby(qcol)["ret"].mean()
                        else:
                            wsum = g.assign(wia=g["ret_ia"] * g["me"],
                                            wr=g["ret"] * g["me"]).groupby(qcol)[["wia", "wr", "me"]].sum()
                            mia = wsum["wia"] / wsum["me"]
                            mraw = wsum["wr"] / wsum["me"]
                        mia = mia.reindex([1, 2, 3, 4, 5])
                        mraw = mraw.reindex([1, 2, 3, 4, 5])
                        if mia.isna().any() or mraw.isna().any():
                            continue
                        s_ia = float(mia.loc[5] - mia.loc[1])
                        s_raw = float(mraw.loc[5] - mraw.loc[1])
                        sp_ia.append(s_ia)
                        sp_raw.append(s_raw)
                        qm.append(mia.to_numpy(float))
                        base = float(mia.mean())
                        lng.append(float(mia.loc[5] - base))
                        sht.append(float(mia.loc[1] - base))
                        yml.append(int(y))
                    if sp_ia:
                        s = pd.Series(sp_ia, index=pd.Index(yml, name="ym"))
                        st = spread_stats(s, factors)
                        sl = spread_stats(pd.Series(lng, index=s.index), factors)
                        ss = spread_stats(pd.Series(sht, index=s.index), factors)
                        rec.update({
                            "q": [float(v) for v in np.nanmean(np.asarray(qm), axis=0)],
                            "raw": float(np.mean(sp_raw)),
                            "mean": float(st["mean"]), "t": float(st["t"]),
                            "ff5m_a": float(st["ff5m_a"]), "ff5m_t": float(st["ff5m_t"]),
                            "long_mean": float(sl["mean"]), "long_t": float(sl["t"]),
                            "short_mean": float(ss["mean"]), "short_t": float(ss["t"]),
                            "pos": float(np.mean(np.asarray(sp_ia) > 0)),
                            "n": int(len(sp_ia))})
                    rows.append(rec)
    return rows


def _results_md(cs: pd.DataFrame, items_map: dict, rows: list[dict]) -> str:
    """RESULTS.md 본문."""
    months = sorted(cs["ym"].unique()) if len(cs) else []
    n_avg = float(cs.groupby("ym").size().mean()) if len(cs) else float("nan")
    drops = [v["dropped"] for v in items_map.values()
             if isinstance(v, dict) and "dropped" in v]
    if drops:
        dline = f"- 월평균 결측 제외 회사 수: {float(np.mean(drops)):.1f}"
    else:
        dline = "- 월평균 결측 제외 회사 수: 없음"
    head = ("| signal | univ | w | 기간 | Q1 | Q2 | Q3 | Q4 | Q5 | Q5-Q1 원 | "
            "mean | t | FF5M 알파 | t | 롱 mean | t | 숏 mean | t | 양수비율 | 월수 |")
    sep = "|" + "|".join(["---"] * 20) + "|"
    lines = ["# AFA 재현 (Bartram·Grinblatt 2018)", "",
             "- 생존편향: 현재 상장 종목만 있어 상폐 종목 수익이 빠진다.",
             "- 배당 미포함: 분할 조정 종가 기준, 배당 수익 빠진다.",
             "- 현재 SIC·현재 티커 기준: 과거 업종·티커 변경 미반영.", "",
             "## 표본",
             f"- 수익 월: {months[0] if months else '-'} ~ {months[-1] if months else '-'} ({len(months)}개월)",
             f"- 월평균 종목 수: {n_avg:.1f}",
             "- 항목 수 K: 28 고정",
             dline, "",
             "## 집계 (업종조정 ret_ia 기준)", head, sep]
    for r in rows:
        cells = [r["signal"], r["univ"], r["w"], r["period"],
                 *[_fp(v) for v in r["q"]], _fp(r["raw"]),
                 _fp(r["mean"]), _ft(r["t"]), _fp(r["ff5m_a"]), _ft(r["ff5m_t"]),
                 _fp(r["long_mean"]), _ft(r["long_t"]),
                 _fp(r["short_mean"]), _ft(r["short_t"]),
                 _fp(r["pos"]), str(int(r["n"]))]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=201007)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--facts-db", type=Path, default=FACTS_DB)
    ap.add_argument("--ohlcv-db", type=Path, default=OHLCV_DB)
    ap.add_argument("--draws", type=int, default=5000)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)
    cache_dir = ROOT / "research" / "private" / "afa" / "cache"
    cs_path = cache_dir / "cs.pkl"
    items_path = cache_dir / "items.json"
    if (not args.no_cache) and cs_path.exists() and items_path.exists():
        cs = pd.read_pickle(cs_path)
        items_map = {int(k): v for k, v in
                     json.loads(items_path.read_text(encoding="utf-8")).items()}
        factors = load_factors()
    else:
        bal, ttm = load_std(args.facts_db)
        sh_all = load_facts(db_path=args.facts_db)
        if sh_all.empty:
            sh_facts = pd.DataFrame(columns=["cik", "end", "filed", "val", "tag"])
        else:
            sh_facts = sh_all[sh_all["tag"] == SHARES_TAG][
                ["cik", "end", "filed", "val", "tag"]].copy()
        del sh_all
        gc.collect()
        wavg = load_share_tag(args.facts_db, WAVG_TAG)
        cso = load_share_tag(args.facts_db, CSO_TAG)
        rets = monthly_returns(month_end_prices(db_path=args.ohlcv_db))
        companies = _load_companies(args.facts_db)
        sics = sorted(int(s) for s in companies["sic"].dropna().unique())
        ind_map = _load_ind_map(args.facts_db, sics)
        factors = load_factors()
        yms = _ym_range(args.start, _resolve_end(args, rets))
        comp = companies[["cik", "ticker", "sic"]].copy()
        comp = comp[comp["ticker"].notna() & comp["sic"].notna()].copy()
        comp["sic"] = comp["sic"].astype(int)
        comp = comp[(comp["sic"] < 6000) | (comp["sic"] > 6999)]
        comp["key"] = comp["ticker"].astype(str).map(ticker_key)
        comp["ind"] = comp["sic"].map(lambda s: ind_map.get((48, int(s))))
        comp = comp[comp["ind"].notna()]
        all_comp = companies[["cik", "ticker"]].copy()
        all_comp = all_comp[all_comp["ticker"].notna()].copy()
        all_comp["key"] = all_comp["ticker"].astype(str).map(ticker_key)
        parts: list[pd.DataFrame] = []
        items_map: dict = {}
        for i, ym in enumerate(yms):
            t = formation_window(ym)[0]
            sh = pick_shares(sh_facts, wavg, cso, t)
            rym = rets[rets["ym"] == ym].copy()
            if rym.empty or sh.empty:
                continue
            rym["key"] = rym["ticker"].astype(str).map(ticker_key)
            rym = rym.drop_duplicates("key", keep="last")
            uni = all_comp.merge(sh.rename("shares"), left_on="cik",
                                 right_index=True, how="inner")
            uni = uni.merge(rym[["key", "prev_raw_close", "market"]], on="key", how="inner")
            uni["me_all"] = uni["prev_raw_close"] * uni["shares"]
            ny = uni[(uni["me_all"] > 0) & (uni["market"] == "NYSE")]["me_all"]
            cut = float(ny.quantile(0.2)) if len(ny) else None
            m = comp.merge(sh.rename("shares"), left_on="cik",
                           right_index=True, how="inner")
            m = m.merge(rym[["key", "ret", "prev_raw_close", "market"]],
                        on="key", how="inner")
            m = _month_frame(ym, m, cut)
            if m.empty:
                continue
            m = m.drop_duplicates("cik", keep="last")
            sample_ciks = m["cik"].tolist()
            snap = std_snapshot(bal, ttm, t)
            X28 = afa_matrix(snap)
            X28 = X28[X28["ATQ"] > 0]
            if X28.empty:
                print(f"[{ym}] skip: no ATQ", flush=True)
                continue
            pre = X28.index.intersection(sample_ciks)
            sub = X28.loc[pre].dropna()
            K = 28
            N = len(sub)
            if N < 200:
                print(f"[{ym}] skip: N={N} K={K}", flush=True)
                continue
            X = winsorize_by_assets(sub[AFA_ITEMS], sub["ATQ"]).to_numpy(float)
            mi = m.set_index("cik")
            v = mi.reindex(sub.index)["me"].to_numpy(float)
            mo = mispricing(ols_fair(X, v), v)
            if args.draws == 0:
                mt = np.full_like(mo, np.nan, dtype=float)
                q_ts = np.zeros(len(sub), dtype=int)
            else:
                mt = mispricing(ts_fair(X, v, draws=args.draws, seed=int(ym)), v)
                q_ts = _quintile(pd.Series(mt, index=sub.index)).to_numpy(int)
            g = pd.DataFrame({"ret": mi.reindex(sub.index)["ret"].to_numpy(float),
                              "ind": mi.reindex(sub.index)["ind"].to_numpy()},
                             index=sub.index)
            g["ret_ia"] = g["ret"] - g.groupby("ind")["ret"].transform("mean")
            g["me"] = v
            g["microcap"] = mi.reindex(sub.index)["microcap"].to_numpy(bool)
            g["ticker"] = mi.reindex(sub.index)["ticker"].astype(str).to_numpy()
            g["m_ols"] = mo
            g["m_ts"] = mt
            g["q_ols"] = _quintile(pd.Series(mo, index=sub.index)).to_numpy(int)
            g["q_ts"] = q_ts
            g["ym"] = int(ym)
            g["cik"] = sub.index.to_numpy()
            g["K"] = int(K)
            g["N"] = int(N)
            parts.append(g.reset_index(drop=True)[CS_COLS])
            items_map[int(ym)] = {"dropped": int(len(pre) - len(sub))}
            if i % 12 == 0:
                print(f"[{ym}] N={N} K={K} sh_n={len(sh)}", flush=True)
        cs = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=CS_COLS)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cs.to_pickle(cs_path)
        items_path.write_text(json.dumps({str(k): v for k, v in items_map.items()},
                                         ensure_ascii=False), encoding="utf-8")
    rows = _aggregate(cs, factors)
    outp = ROOT / "research" / "private" / "afa" / "RESULTS.md"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(_results_md(cs, items_map, rows), encoding="utf-8")


if __name__ == "__main__":
    main()
