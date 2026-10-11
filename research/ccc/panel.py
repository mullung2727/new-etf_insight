"""재무 facts → 분기 CCC."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

FACTS_DB = Path(__file__).resolve().parents[2] / "etl" / "db" / "us_facts.sqlite3"
SALES_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
              "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet")
COGS_TAGS = ("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
             "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization")
# 잔액: 항목별 대체 태그(앞이 우선). (cik, end) 마다 값 있는 첫 태그.
BAL_TAGS = {
    "inv": ("InventoryNet",),
    "ar": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent"),
    "ap": ("AccountsPayableCurrent", "AccountsPayableTradeCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"),
    "assets": ("Assets",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
}
SHARES_TAG = "EntityCommonStockSharesOutstanding"


def _concept_ids(con) -> dict[int, str]:
    """load 대상 concept_id → tag (us-gaap + dei 주식수만)."""
    gaap: list[str] = []
    bal_tags = [t for tags in BAL_TAGS.values() for t in tags]
    for t in (*SALES_TAGS, *COGS_TAGS, *bal_tags):
        if t not in gaap:
            gaap.append(t)
    q = (f"SELECT concept_id, tag FROM concepts WHERE (taxonomy='us-gaap' AND tag IN "
         f"({','.join('?' * len(gaap))})) OR (taxonomy='dei' AND tag=?)")
    return {cid: tag for cid, tag in con.execute(q, (*gaap, SHARES_TAG)).fetchall()}


def load_facts(db_path=FACTS_DB, end_from: str = "2008-01-01") -> pd.DataFrame:
    """us-gaap 재무 + dei 주식수 읽기."""
    cols = ["cik", "tag", "start", "end", "filed", "val"]
    p = str(Path(db_path).resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        cmap = _concept_ids(con)
        if not cmap:
            return pd.DataFrame(columns=cols)
        ids = sorted(cmap)
        q = (f"SELECT cik, concept_id, unit, start, end, filed, val FROM facts "
             f"WHERE concept_id IN ({','.join('?' * len(ids))}) AND end >= ?")
        df = pd.read_sql_query(q, con, params=[*ids, end_from])
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["tag"] = df["concept_id"].map(cmap)
    is_sh = df["tag"] == SHARES_TAG
    keep = (is_sh & (df["unit"] == "shares")) | ((~is_sh) & (df["unit"] == "USD"))
    df = df[keep].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["start"] = pd.to_datetime(df["start"].where(df["start"] != "", None))
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df["val"] = df["val"].astype(float)
    return df[cols].reset_index(drop=True)


def asof(facts: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """filed<=as_of 중 (cik,tag,start,end)별 최신."""
    if facts.empty:
        return facts.copy()
    df = facts[facts["filed"] <= as_of].copy()
    if df.empty:
        return df
    return df.sort_values("filed").drop_duplicates(["cik", "tag", "start", "end"], keep="last")


def _flows_with_filed(f: pd.DataFrame) -> pd.DataFrame:
    """cik,qend,sales,cogs + filed (Q4는 연간−9개월, 직접 우선)."""
    cols = ["cik", "qend", "sales", "cogs", "sales_filed", "cogs_filed"]
    if f.empty:
        return pd.DataFrame(columns=cols)
    want = set(SALES_TAGS) | set(COGS_TAGS)
    df = f[f["tag"].isin(want) & f["start"].notna() & f["end"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["days"] = (df["end"] - df["start"]).dt.days
    df = df.sort_values("filed")
    direct: dict[tuple, tuple[float, pd.Timestamp]] = {}
    for r in df.itertuples():
        if 80 <= r.days <= 100:
            direct[(r.cik, r.tag, r.end)] = (float(r.val), r.filed)
    annual: dict[tuple, tuple] = {}
    nine: dict[tuple, tuple] = {}
    for r in df.itertuples():
        if 350 <= r.days <= 380:
            annual[(r.cik, r.tag, r.start)] = (r.end, float(r.val), r.filed)
        elif 260 <= r.days <= 285:
            nine[(r.cik, r.tag, r.start)] = (float(r.val), r.filed)
    vals: dict[tuple, tuple[float, pd.Timestamp]] = dict(direct)
    for k, (qend, aval, afiled) in annual.items():
        if k in nine:
            nval, nfiled = nine[k]
            diff = aval - nval
            ck = (k[0], k[1], qend)
            if diff > 0 and ck not in vals:
                vals[ck] = (diff, afiled if afiled >= nfiled else nfiled)
    per: dict[tuple, dict] = {}
    for (cik, tag, qend), (val, filed) in vals.items():
        per.setdefault((cik, qend), {})[tag] = (val, filed)
    rows = []
    for (cik, qend), d in per.items():
        s = next((d[t] for t in SALES_TAGS if t in d), None)
        c = next((d[t] for t in COGS_TAGS if t in d), None)
        if s is not None and c is not None:
            rows.append((cik, qend, s[0], c[0], s[1], c[1]))
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def quarter_flows(f: pd.DataFrame) -> pd.DataFrame:
    """분기 매출·원가."""
    df = _flows_with_filed(f)
    if df.empty:
        return pd.DataFrame(columns=["cik", "qend", "sales", "cogs"])
    return df[["cik", "qend", "sales", "cogs"]].copy()


def ccc_table(f: pd.DataFrame) -> pd.DataFrame:
    """분기 CCC. 분기 흐름에 365를 곱해 스케일만 다르고 순위는 동일."""
    cols = ["cik", "qend", "sales", "cogs", "inv", "inv_beg", "ar", "ar_beg",
            "ap", "ap_beg", "assets_lag", "equity", "ccc", "avail"]
    if f.empty:
        return pd.DataFrame(columns=cols)
    flows = _flows_with_filed(f)
    if flows.empty:
        return pd.DataFrame(columns=cols)
    flows = flows[(flows["sales"] > 0) & (flows["cogs"] > 0)].copy()
    if flows.empty:
        return pd.DataFrame(columns=cols)
    tag_to_item = {t: item for item, tags in BAL_TAGS.items() for t in tags}
    tag_to_prio = {t: i for tags in BAL_TAGS.values() for i, t in enumerate(tags)}
    bal = f[f["tag"].isin(tag_to_item) & f["start"].isna()
            & f["end"].notna()][["cik", "tag", "end", "filed", "val"]].copy()
    if bal.empty:
        return pd.DataFrame(columns=cols)
    bal["item"] = bal["tag"].map(tag_to_item)
    bal["prio"] = bal["tag"].map(tag_to_prio)
    bal = bal.sort_values("prio").drop_duplicates(["cik", "item", "end"], keep="first")
    out = flows.copy()
    for col, item in (("inv", "inv"), ("ar", "ar"),
                      ("ap", "ap"), ("equity", "equity")):
        t = bal[bal["item"] == item][["cik", "end", "val", "filed"]].rename(
            columns={"end": "qend", "val": col, "filed": f"{col}_filed"})
        out = out.merge(t, on=["cik", "qend"], how="left")
    for col, item in (("inv_beg", "inv"), ("ar_beg", "ar"),
                      ("ap_beg", "ap"), ("assets_lag", "assets")):
        t = bal[bal["item"] == item][["cik", "end", "val", "filed"]]
        if t.empty:
            out[col] = float("nan")
            out[f"{col}_filed"] = pd.NaT
            continue
        m = out[["cik", "qend"]].merge(t, on="cik", how="left")
        lo = m["qend"] - pd.to_timedelta(100, unit="D")
        hi = m["qend"] - pd.to_timedelta(80, unit="D")
        m = m[(m["end"] >= lo) & (m["end"] <= hi)]
        if m.empty:
            out[col] = float("nan")
            out[f"{col}_filed"] = pd.NaT
            continue
        m = m.sort_values("end").drop_duplicates(["cik", "qend"], keep="last")
        m = m.rename(columns={"val": col, "filed": f"{col}_filed"})[["cik", "qend", col, f"{col}_filed"]]
        out = out.merge(m, on=["cik", "qend"], how="left")
    out = out.dropna(subset=["inv", "inv_beg", "ar", "ar_beg", "ap", "ap_beg", "assets_lag"])
    if out.empty:
        return pd.DataFrame(columns=cols)
    out = out[(out["sales"] / out["assets_lag"]) >= 0.025].copy()
    if out.empty:
        return pd.DataFrame(columns=cols)
    out["ccc"] = 365 * ((out["inv"] + out["inv_beg"]) / 2 / out["cogs"]
                        + (out["ar"] + out["ar_beg"]) / 2 / out["sales"]
                        - (out["ap"] + out["ap_beg"]) / 2 / out["cogs"])
    fcols = ["sales_filed", "cogs_filed", "inv_filed", "inv_beg_filed", "ar_filed",
             "ar_beg_filed", "ap_filed", "ap_beg_filed", "assets_lag_filed", "equity_filed"]
    out["avail"] = out[fcols].max(axis=1)
    return out[cols].reset_index(drop=True)


def shares_asof(f: pd.DataFrame) -> pd.Series:
    """cik별 최신 주식수."""
    if f.empty:
        return pd.Series(dtype=float, name="val")
    df = f[f["tag"] == SHARES_TAG].copy()
    if df.empty:
        return pd.Series(dtype=float, name="val")
    df = df.sort_values(["end", "filed"]).drop_duplicates("cik", keep="last")
    return pd.Series(df["val"].to_numpy(float), index=pd.Index(df["cik"], name="cik"), name="val")
