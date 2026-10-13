"""AFA 한국 검증 — DART 주요계정 13개로 시가총액 OLS 적정가 (PLAN_AFA_KR)."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from research.afa.fair import mispricing, ols_fair
from research.afa.items import winsorize_by_assets
from research.afa.run import _quintile
from research.backtest_daily.adjust import adj_returns
from research.backtest_daily.data import DB, load_px, stock_names
from research.backtest_daily.guards import AVAIL, FIN_DB
from research.backtest_daily.stats import COSTS
from research.backtest_daily.universe import SPAC_RE
from research.ccc.sort import nw_tstat

ROOT = Path(__file__).resolve().parents[2]

ITEMS_BS = ["자산총계", "유동자산", "비유동자산", "부채총계", "유동부채",
            "비유동부채", "자본총계", "자본금", "이익잉여금"]
# "총포괄손익" 제외: 2023 이전 거의 수집 안 돼 전 기간 표본이 0이 됨.
ITEMS_IS = ["매출액", "영업이익", "법인세차감전 순이익", "당기순이익(손실)"]
ITEMS = ITEMS_BS + ITEMS_IS
FIN_MARKERS = ["예수부채", "보험계약부채", "순이자손익", "순수수료손익"]
QEND = {"11013": "0331", "11012": "0630", "11014": "0930", "11011": "1231"}
QNUM = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
QMONTH = {3: 1, 6: 2, 9: 3, 12: 4}
MIN_PRICE = 1000
MIN_N = 200
EXTREME = 10
STALE_DAYS = 400
START_YM = 201806
PERIODS = [("ALL", None, None), ("2018-07~2021-12", 201807, 202112),
           ("2022-01~", 202201, None)]
CS_COLS = ["ym", "ret_ym", "ticker", "me", "ret", "m", "q", "N"]


def _ro_con(db):
    p = str(Path(db).resolve()).replace("\\", "/")
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True)


def fin_corps(db=FIN_DB) -> set[str]:
    """FIN_MARKERS 계정이 하나라도 있는 stock_code 집합."""
    marks = list(FIN_MARKERS)
    con = _ro_con(db)
    try:
        q = ("SELECT DISTINCT stock_code FROM accounts WHERE account_nm IN ("
             + ",".join(["?"] * len(marks)) + ")")
        rows = con.execute(q, marks).fetchall()
    finally:
        con.close()
    return {str(r[0]) for r in rows if r[0] is not None and str(r[0]) != ""}


def load_kr_fin(db=FIN_DB) -> pd.DataFrame:
    """13계정 long (stock_code, item, qend, avail, val, kind)."""
    items = list(ITEMS)
    con = _ro_con(db)
    try:
        q = ("SELECT stock_code, bsns_year, reprt_code, fs_div, account_nm, amount "
             "FROM accounts WHERE account_nm IN (" + ",".join(["?"] * len(items)) + ")")
        df = pd.read_sql_query(q, con, params=items)
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=["stock_code", "item", "qend", "avail", "val", "kind"])
    df["bsns_year"] = df["bsns_year"].astype(str)
    df = df[df["reprt_code"].isin(QEND)].copy()
    if df.empty:
        return pd.DataFrame(columns=["stock_code", "item", "qend", "avail", "val", "kind"])
    # 회사·연도·보고서마다 CFS 자산총계 있으면 CFS만, 아니면 OFS만.
    flag = (df["fs_div"] == "CFS") & (df["account_nm"] == "자산총계")
    has_cfs = flag.groupby([df["stock_code"], df["bsns_year"],
                            df["reprt_code"]]).transform("any")
    keep_cfs = np.asarray(has_cfs, dtype=bool)
    keep = np.where(keep_cfs, df["fs_div"].to_numpy() == "CFS",
                    df["fs_div"].to_numpy() == "OFS")
    df = df[keep].copy()
    if df.empty:
        return pd.DataFrame(columns=["stock_code", "item", "qend", "avail", "val", "kind"])
    df = df.drop_duplicates(["stock_code", "bsns_year", "reprt_code", "account_nm"],
                            keep="last")
    df["qend"] = pd.to_datetime(df["bsns_year"] + df["reprt_code"].map(QEND),
                                format="%Y%m%d")
    addy = df["reprt_code"].map(lambda r: AVAIL[r][0])
    md = df["reprt_code"].map(lambda r: AVAIL[r][1])
    df["avail"] = pd.to_datetime((df["bsns_year"].astype(int) + addy).astype(str)
                                 + md, format="%Y%m%d")
    df["val"] = pd.to_numeric(df["amount"], errors="coerce")
    bs = df[df["account_nm"].isin(ITEMS_BS)].copy()
    bs_out = pd.DataFrame({"stock_code": bs["stock_code"].to_numpy(),
                           "item": bs["account_nm"].to_numpy(),
                           "qend": pd.to_datetime(bs["qend"]),
                           "avail": pd.to_datetime(bs["avail"]),
                           "val": bs["val"].to_numpy(float),
                           "kind": "bs"})
    isr = df[df["account_nm"].isin(ITEMS_IS)].copy()
    parts = []
    q123 = isr[isr["reprt_code"].isin(("11013", "11012", "11014"))].copy()
    if not q123.empty:
        parts.append(pd.DataFrame(
            {"stock_code": q123["stock_code"].to_numpy(),
             "item": q123["account_nm"].to_numpy(),
             "qend": pd.to_datetime(q123["qend"]),
             "avail": pd.to_datetime(q123["avail"]),
             "val": q123["val"].to_numpy(float),
             "kind": "is3m"}))
    # 4분기 3개월값 = 연간 − 1Q − 2Q − 3Q (네 값 모두 있을 때만).
    piv = isr.pivot_table(index=["stock_code", "bsns_year", "account_nm"],
                          columns="reprt_code", values="val", aggfunc="last")
    for c in ("11011", "11013", "11012", "11014"):
        if c not in piv.columns:
            piv[c] = np.nan
    need = piv[["11011", "11013", "11012", "11014"]]
    ok = need.notna().all(axis=1)
    if bool(ok.any()):
        sub = piv[ok]
        q4v = (sub["11011"] - sub["11013"] - sub["11012"] - sub["11014"])
        rec = q4v.reset_index()
        y = rec["bsns_year"].astype(int)
        parts.append(pd.DataFrame(
            {"stock_code": rec["stock_code"].astype(str).to_numpy(),
             "item": rec["account_nm"].astype(str).to_numpy(),
             "qend": pd.to_datetime(y.astype(str) + "1231", format="%Y%m%d"),
             "avail": pd.to_datetime((y + AVAIL["11011"][0]).astype(str)
                                     + AVAIL["11011"][1], format="%Y%m%d"),
             "val": q4v[ok].to_numpy(float),
             "kind": "is3m"}))
    is_out = (pd.concat(parts, ignore_index=True) if parts
              else pd.DataFrame(columns=["stock_code", "item", "qend",
                                         "avail", "val", "kind"]))
    out = pd.concat([bs_out, is_out], ignore_index=True)
    return out.sort_values(["stock_code", "item", "qend"]).reset_index(drop=True)


def kr_snapshot(fin: pd.DataFrame, t) -> pd.DataFrame:
    """형성일 스냅샷 (index=stock_code, 열=ITEMS)."""
    cols = list(ITEMS)
    empty = pd.DataFrame(columns=cols)
    empty.index.name = "stock_code"
    if fin is None or fin.empty:
        return empty
    t = pd.Timestamp(t)
    lo = t - pd.Timedelta(days=STALE_DAYS)
    f = fin[fin["avail"] <= t].copy()
    if f.empty:
        return empty
    # BS: 항목마다 가장 최근 qend 값 (qend ≥ t − 400일).
    b = f[f["kind"] == "bs"].sort_values("qend")
    wide_bs = pd.DataFrame()
    if not b.empty:
        b = b.drop_duplicates(["stock_code", "item"], keep="last")
        b = b[b["qend"] >= lo]
        if not b.empty:
            wide_bs = b.pivot(index="stock_code", columns="item", values="val")
    # IS TTM: 최근 분기 Q와 Q−1, Q−2, Q−3 네 개 모두 있으면 합.
    wide_is = pd.DataFrame()
    s = f[f["kind"] == "is3m"].copy()
    if not s.empty:
        s = s.drop_duplicates(["stock_code", "item", "qend"], keep="last")
        s["qn"] = s["qend"].dt.month.map(QMONTH)
        s = s[s["qn"].notna()].copy()
        s["p"] = s["qend"].dt.year * 4 + s["qn"].astype(int) - 1
        pmax = s.groupby("stock_code")["p"].max()
        qy = (pmax // 4).astype(int)
        qq = (pmax % 4).astype(int) + 1
        qm = qq.map({1: 3, 2: 6, 3: 9, 4: 12})
        qmax_qend = pd.to_datetime(qy.astype(str)
                                   + qm.astype(int).astype(str).str.zfill(2)
                                   + "01", format="%Y%m%d") \
            + pd.offsets.MonthEnd(0)
        ok_stocks = pmax[qmax_qend >= lo].index
        s = s[s["stock_code"].isin(set(ok_stocks))].copy()
        if not s.empty:
            s = s.merge(pmax.rename("P"), left_on="stock_code", right_index=True)
            s = s[(s["p"] <= s["P"]) & (s["p"] >= s["P"] - 3)]
            cnt = s.groupby(["stock_code", "item"])["p"].nunique()
            full = set(cnt[cnt == 4].index)
            s = s[s.set_index(["stock_code", "item"]).index.isin(full)]
            if not s.empty:
                wide_is = (s.groupby(["stock_code", "item"])["val"].sum()
                           .unstack("item"))
    if wide_bs.empty and wide_is.empty:
        return empty
    out = pd.concat([wide_bs, wide_is], axis=1)
    out = out[[c for c in cols if c in out.columns]]
    for c in cols:
        if c not in out.columns:
            out[c] = np.nan
    out = out[cols]
    out.index.name = "stock_code"
    return out


def _formation_map(px: pd.DataFrame) -> dict[int, str]:
    """형성월 → 형성일(그 달 마지막 거래일)."""
    fmap: dict[int, str] = {}
    for d in sorted(set(px["date"].astype(str))):
        fmap[int(d[:6])] = str(d)
    return fmap


def monthly_frame(px: pd.DataFrame) -> pd.DataFrame:
    """형성월별 다음 달 수익 (ym, ret_ym, ticker, close, me, market, ret, tv)."""
    cols = ["ym", "ret_ym", "ticker", "close", "me", "market", "ret", "tv"]
    if px is None or px.empty:
        return pd.DataFrame(columns=cols)
    s = px.copy()
    s["date"] = s["date"].astype(str)
    s = s.sort_values(["ticker", "date"]).reset_index(drop=True)
    r = np.asarray(adj_returns(s, use_krx_reference=True), dtype=float)
    s["_r"] = np.nan_to_num(r, nan=0.0)
    fmap = _formation_map(s)
    fdates = sorted(fmap.values())
    if len(fdates) < 2:
        return pd.DataFrame(columns=cols)
    farr = np.array(fdates)
    pos = np.searchsorted(farr, s["date"].to_numpy(), side="left")
    s["_iv"] = pos - 1
    hold = s[(s["_iv"] >= 0) & (s["_iv"] < len(farr) - 1)].copy()
    comp: dict = {}
    if not hold.empty:
        l = np.log1p(np.clip(hold["_r"].to_numpy(float), -0.999, None))
        gs = pd.Series(l).groupby([hold["_iv"].to_numpy(),
                                   hold["ticker"].astype(str).to_numpy()]).sum()
        comp = {k: float(np.expm1(v)) for k, v in gs.items()}
    rows = []
    for i in range(len(fdates) - 1):
        f0 = fdates[i]
        g0 = s[s["date"] == f0].drop_duplicates("ticker", keep="last")
        if g0.empty:
            continue
        ym, ret_ym = int(f0[:6]), int(fdates[i + 1][:6])
        for row in g0.itertuples():
            tk = str(row.ticker)
            rows.append({"ym": ym, "ret_ym": ret_ym, "ticker": tk,
                         "close": float(row.close),
                         "me": float(row.market_cap), "market": row.market,
                         "ret": float(comp.get((i, tk), 0.0)),
                         "tv": float(row.trading_value)})
    return pd.DataFrame(rows, columns=cols)


def _turnover(prev_set, cur_set) -> float:
    """전월 대비 새로 들어온 종목 비율."""
    cur = set(cur_set)
    if not cur:
        return 0.0
    prev = set(prev_set)
    if not prev:
        return 1.0
    return len(cur - prev) / len(cur)


def _nw(series) -> tuple[float, float]:
    """NW 평균·t (lags=6)."""
    v = np.asarray(series, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return (float("nan"), float("nan"))
    b, t = nw_tstat(v, lags=6)
    return (float(b[0]), float(t[0]))


def _ew_vw(g: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    ew = g.groupby("q")["ret"].mean()
    w = g.assign(_w=g["ret"] * g["me"]).groupby("q")[["_w", "me"]].sum()
    vw = (w["_w"] / w["me"]).replace([float("inf"), float("-inf")], np.nan)
    return ew.reindex([1, 2, 3, 4, 5]), vw.reindex([1, 2, 3, 4, 5])


def _aggregate_kr(cs: pd.DataFrame) -> dict:
    """수익월 기준 PERIODS 집계 (순수 함수)."""
    out: dict = {}
    for pname, p0, p1 in PERIODS:
        df = cs
        if p0 is not None:
            df = df[df["ret_ym"] >= p0]
        if p1 is not None:
            df = df[df["ret_ym"] <= p1]
        rec: dict = {"n_months": 0, "n_avg": float("nan"),
                     "q_ew": {}, "q_vw": {}, "sp_ew": (float("nan"), float("nan")),
                     "sp_vw": (float("nan"), float("nan")),
                     "ex5": (float("nan"), float("nan")),
                     "ex1": (float("nan"), float("nan")),
                     "q5_to": float("nan"), "q5_net": float("nan"),
                     "q1_to": float("nan"), "q1_net": float("nan"),
                     "ext_lo": {}, "ext_hi": {}, "freq_lo": [],
                     "freq_hi": [], "med_me_lo": float("nan"),
                     "med_me_hi": float("nan")}
        if df is None or df.empty:
            out[pname] = rec
            continue
        months = sorted(df["ret_ym"].unique())
        rec["n_months"] = len(months)
        rec["n_avg"] = float(df.groupby("ret_ym").size().mean())
        mkt = df.groupby("ret_ym")["ret"].mean()
        ew_rows, vw_rows, mk = [], [], []
        sp_ew, sp_vw, ex5, ex1 = [], [], [], []
        q5_sets: list[set] = []
        q1_sets: list[set] = []
        q5_rets, q1_rets = [], []
        lo_rets, hi_rets, lo_ex, hi_ex = [], [], [], []
        lo_sets: list[set] = []
        hi_sets: list[set] = []
        lo_me, hi_me = [], []
        lo_cnt: dict = {}
        hi_cnt: dict = {}
        for ym, g in df.groupby("ret_ym"):
            if {1, 5} <= set(g["q"].to_numpy(int)):
                ew, vw = _ew_vw(g)
                if not ew.isna().any() and not vw.isna().any():
                    ew_rows.append(ew.to_numpy(float))
                    vw_rows.append(vw.to_numpy(float))
                    mk.append(float(mkt.loc[ym]))
                    sp_ew.append(float(ew.loc[5] - ew.loc[1]))
                    sp_vw.append(float(vw.loc[5] - vw.loc[1]))
                    ex5.append(float(ew.loc[5] - mkt.loc[ym]))
                    ex1.append(float(ew.loc[1] - mkt.loc[ym]))
                    q5 = set(g[g["q"] == 5]["ticker"].astype(str))
                    q1 = set(g[g["q"] == 1]["ticker"].astype(str))
                    q5_sets.append(q5)
                    q1_sets.append(q1)
                    q5_rets.append(float(ew.loc[5]))
                    q1_rets.append(float(ew.loc[1]))
            gm = g.sort_values("m")
            if len(gm) >= 2 * EXTREME:
                lo = gm.iloc[:EXTREME]
                hi = gm.iloc[-EXTREME:]
                lo_sets.append(set(lo["ticker"].astype(str)))
                hi_sets.append(set(hi["ticker"].astype(str)))
                lo_rets.append(float(lo["ret"].mean()))
                hi_rets.append(float(hi["ret"].mean()))
                lo_ex.append(float(lo["ret"].mean() - mkt.loc[ym]))
                hi_ex.append(float(hi["ret"].mean() - mkt.loc[ym]))
                lo_me.extend(lo["me"].to_numpy(float).tolist())
                hi_me.extend(hi["me"].to_numpy(float).tolist())
                for tk in lo["ticker"].astype(str):
                    lo_cnt[tk] = lo_cnt.get(tk, 0) + 1
                for tk in hi["ticker"].astype(str):
                    hi_cnt[tk] = hi_cnt.get(tk, 0) + 1
        if ew_rows:
            rec["q_ew"] = {q: float(v) for q, v in
                           zip([1, 2, 3, 4, 5], np.nanmean(np.asarray(ew_rows),
                                                           axis=0))}
            rec["q_vw"] = {q: float(v) for q, v in
                           zip([1, 2, 3, 4, 5], np.nanmean(np.asarray(vw_rows),
                                                           axis=0))}
            rec["sp_ew"] = _nw(sp_ew)
            rec["sp_vw"] = _nw(sp_vw)
            rec["ex5"] = _nw(ex5)
            rec["ex1"] = _nw(ex1)

            def _to_net(sets, rets):
                if len(sets) <= 1:
                    return float("nan"), float("nan")
                to = [_turnover(sets[i - 1], sets[i])
                      for i in range(1, len(sets))]
                net = [rets[i] - t * COSTS[1]
                       for i, t in enumerate(to, start=1)]
                return float(np.mean(to)), float(np.mean(net))

            rec["q5_to"], rec["q5_net"] = _to_net(q5_sets, q5_rets)
            rec["q1_to"], rec["q1_net"] = _to_net(q1_sets, q1_rets)

            def _ext(sets, rets, ex, me_all, cnt):
                if len(sets) <= 1:
                    to_m = float("nan")
                    net_m = float("nan")
                else:
                    to = [_turnover(sets[i - 1], sets[i])
                          for i in range(1, len(sets))]
                    net = [rets[i] - t * COSTS[1]
                           for i, t in enumerate(to, start=1)]
                    to_m = float(np.mean(to))
                    net_m = float(np.mean(net))
                cum = float(np.prod([1 + r for r in rets]) - 1) if rets else float("nan")
                freq = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
                return {"mean": float(np.mean(rets)), "ex": _nw(ex),
                        "cum": cum, "to": to_m,
                        "net": net_m, "freq": freq,
                        "med_me": float(np.median(me_all)) if me_all else float("nan")}

            rec["ext_lo"] = _ext(lo_sets, lo_rets, lo_ex, lo_me, lo_cnt)
            rec["ext_hi"] = _ext(hi_sets, hi_rets, hi_ex, hi_me, hi_cnt)
            rec["freq_lo"] = rec["ext_lo"]["freq"]
            rec["freq_hi"] = rec["ext_hi"]["freq"]
            rec["med_me_lo"] = rec["ext_lo"]["med_me"]
            rec["med_me_hi"] = rec["ext_hi"]["med_me"]
        out[pname] = rec
    return out


def _f_pct(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v * 100:.2f}" if v == v else "nan"


def _f_t(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v:.2f}" if v == v else "nan"


OLD_FILTER_LINE = "필터: 옛 코드(시총>10억, 거래대금>1천만, 순이익>0, 매출>0)"


def _results_md(cs: pd.DataFrame, agg: dict, names: dict,
               old_filter: bool = False) -> str:
    months = sorted(cs["ret_ym"].unique()) if len(cs) else []
    n_avg = float(cs.groupby("ret_ym").size().mean()) if len(cs) else float("nan")
    lines = ([OLD_FILTER_LINE, ""] if old_filter else [])
    lines += ["# AFA 한국 검증 (PLAN_AFA_KR)", "",
             "- 한계: 업종·팩터 조정 없음 — 같은 달 표본 동일가중 대비 초과만 본다.",
             "- 한계: 12월 결산 가정 — 3/31·6/30·9/30·12/31 분기 끝 기준.",
             "- 한계: 공시 시점은 법정 기한 근사(AVAIL) — 실제 공시일보다 늦을 수 있다.",
             "",
             "## 표본",
             (f"- 수익월: {months[0]} ~ {months[-1]} ({len(months)}개월)"
              if months else "- 수익월: 없음"),
             f"- 월평균 종목 수: {n_avg:.1f}" if n_avg == n_avg else "- 월평균 종목 수: nan",
             f"- 회귀 항목: {len(ITEMS)} (상태표 {len(ITEMS_BS)} + 손익 TTM {len(ITEMS_IS)})", "",
             "## 5분위 월평균 수익률 (%)",
             "| 기간 | EW Q1 | Q2 | Q3 | Q4 | Q5 | Q5-Q1 | t |"
             " VW Q1 | Q2 | Q3 | Q4 | Q5 | Q5-Q1 | t | Q5-mkt | t | Q1-mkt | t |",
             "|" + "|".join(["---"] * 20) + "|"]
    for pname, _, _ in PERIODS:
        r = agg[pname]
        qe = [r["q_ew"].get(q, float("nan")) for q in (1, 2, 3, 4, 5)]
        qv = [r["q_vw"].get(q, float("nan")) for q in (1, 2, 3, 4, 5)]
        cells = ([pname, *[_f_pct(v) for v in qe], _f_pct(r["sp_ew"][0]),
                  _f_t(r["sp_ew"][1]), *[_f_pct(v) for v in qv],
                  _f_pct(r["sp_vw"][0]), _f_t(r["sp_vw"][1]),
                  _f_pct(r["ex5"][0]), _f_t(r["ex5"][1]),
                  _f_pct(r["ex1"][0]), _f_t(r["ex1"][1])])
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## 극단 10종목 EW (%)",
              "| 기간 | 저평가 top10 | t(초과) | 누적 | 회전율 | 비용후 |"
              " 고평가 bot10 | t(초과) | 누적 | 회전율 | 비용후 |"
              " Q5 회전율 | Q5 비용후 | Q1 회전율 | Q1 비용후 |",
              "|" + "|".join(["---"] * 15) + "|"]
    for pname, _, _ in PERIODS:
        r = agg[pname]
        lo, hi = r["ext_lo"], r["ext_hi"]
        cells = [pname, _f_pct(hi.get("mean")), _f_t(hi.get("ex", (0, 0))[1]),
                 _f_pct(hi.get("cum")), _f_pct(hi.get("to")), _f_pct(hi.get("net")),
                 _f_pct(lo.get("mean")), _f_t(lo.get("ex", (0, 0))[1]),
                 _f_pct(lo.get("cum")), _f_pct(lo.get("to")), _f_pct(lo.get("net")),
                 _f_pct(r["q5_to"]), _f_pct(r["q5_net"]),
                 _f_pct(r["q1_to"]), _f_pct(r["q1_net"])]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## 극단 자주 등장 (ALL)"]
    for label, key, med in (("저평가 top10", "freq_hi", "med_me_hi"),
                            ("고평가 bot10", "freq_lo", "med_me_lo")):
        r = agg["ALL"]
        lines.append(f"- {label} 시총 중앙값: {r[med]:.0f}원")
        lines.append("| ticker | 이름 | 횟수 |")
        lines.append("|---|---|---|")
        for tk, c in r[key]:
            lines.append(f"| {tk} | {names.get(str(tk), '')} | {int(c)} |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> None:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=START_YM)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--px-db", default=DB)
    ap.add_argument("--fin-db", default=FIN_DB)
    ap.add_argument("--old-filter", action="store_true")
    args = ap.parse_args(argv)
    cache_dir = ROOT / "research" / "private" / "afa" / "cache"
    cs_path = cache_dir / ("cs_kr_old.pkl" if args.old_filter else "cs_kr.pkl")
    if (not args.no_cache) and cs_path.exists():
        cs = pd.read_pickle(cs_path)
        names = stock_names(db=args.px_db)
    else:
        px = load_px(db=args.px_db)
        names = stock_names(db=args.px_db)
        fin = load_kr_fin(db=args.fin_db)
        corps = fin_corps(db=args.fin_db)
        frame = monthly_frame(px)
        dmax = pd.Timestamp(str(px["date"].max()))
        last_bday = (dmax + pd.offsets.BMonthEnd(0)).normalize()
        if dmax.normalize() < last_bday:
            ym_last = dmax.year * 100 + dmax.month
            n0 = len(frame)
            frame = frame[(frame["ret_ym"] != ym_last)
                          & (frame["ym"] != ym_last)].copy()
            print(f"[incomplete] dmax={dmax.date()} < 월말영업일={last_bday.date()}"
                  f" → ym/ret_ym={ym_last} {n0 - len(frame)}행 제외", flush=True)
        fmap = _formation_map(px)
        mask = (~frame["market"].astype(str).str.contains("KONEX", na=False))
        mask &= frame["ticker"].str.endswith("0")
        mask &= frame["ticker"].map(
            lambda t: not SPAC_RE.search(str(names.get(str(t), ""))))
        mask &= ~frame["ticker"].isin(corps)
        if args.old_filter:
            mask &= frame["me"] > 1e9
            mask &= frame["tv"] > 1e7
        else:
            mask &= frame["close"] >= MIN_PRICE
        mask &= frame["me"] > 0
        frame = frame[mask].copy()
        yms = sorted(frame["ym"].unique())
        end = args.end if args.end is not None else (max(yms) if yms else None)
        yms = [y for y in yms if y >= args.start and (end is None or y <= end)]
        parts: list[pd.DataFrame] = []
        for i, ym in enumerate(yms):
            g = frame[frame["ym"] == ym].copy()
            if g.empty:
                continue
            t = pd.Timestamp(str(fmap[ym]))
            snap = kr_snapshot(fin, t)
            pre = snap.index.intersection(g["ticker"].astype(str).unique())
            sub = snap.loc[pre].dropna(subset=ITEMS) if len(pre) else snap.iloc[0:0]
            sub = sub[sub["자산총계"] > 0]
            if args.old_filter:
                sub = sub[(sub["당기순이익(손실)"] > 0) & (sub["매출액"] > 0)]
            N = len(sub)
            if N < MIN_N:
                print(f"[{ym}] skip: N={N}", flush=True)
                continue
            X = winsorize_by_assets(sub[ITEMS], sub["자산총계"]).to_numpy(float)
            gi = g.drop_duplicates("ticker", keep="last").set_index("ticker")
            v = gi.reindex(sub.index)["me"].to_numpy(float)
            okv = np.isfinite(v) & (v > 0)
            sub = sub[okv]
            if len(sub) < MIN_N:
                print(f"[{ym}] skip: N={len(sub)}", flush=True)
                continue
            X = X[okv]
            v = v[okv]
            N = len(sub)
            m = mispricing(ols_fair(X, v), v)
            q = _quintile(pd.Series(m, index=sub.index)).to_numpy(int)
            out = pd.DataFrame({"ym": int(ym),
                                "ret_ym": gi.reindex(sub.index)["ret_ym"].to_numpy(int),
                                "ticker": sub.index.astype(str).to_numpy(),
                                "me": v,
                                "ret": gi.reindex(sub.index)["ret"].to_numpy(float),
                                "m": m, "q": q, "N": int(N)})
            parts.append(out[CS_COLS])
            if i % 12 == 0:
                print(f"[{ym}] N={N}", flush=True)
        cs = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=CS_COLS)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cs.to_pickle(cs_path)
    agg = _aggregate_kr(cs)
    outp = ROOT / "research" / "private" / "afa" / (
        "RESULTS_KR_old.md" if args.old_filter else "RESULTS_KR.md")
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(_results_md(cs, agg, names,
                               old_filter=args.old_filter), encoding="utf-8")


if __name__ == "__main__":
    main()
