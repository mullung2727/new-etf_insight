"""표준 항목 long·스냅샷 (재사용: ccc.panel, ccc.event, afa.items)."""

from __future__ import annotations

import gc
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from research.afa.items import bal_obs, snapshot, ttm_obs
from research.ccc.event import first_reported
from research.ccc.panel import FACTS_DB
from research.us_fin_std.mapping import FALLBACK_DAYS, GROUPS, tag_to_item

PICK_COLS = ["cik", "item", "src_tag", "start", "end", "filed", "val"]
LONG_COLS = ["cik", "tag", "end", "avail", "val"]


def _read_tags(db_path=FACTS_DB, tags=()) -> pd.DataFrame:
    """요청 us-gaap 태그만 USD로 읽기."""
    cols = ["cik", "tag", "start", "end", "filed", "val"]
    uniq = list(dict.fromkeys(tags))
    if not uniq:
        return pd.DataFrame(columns=cols)
    p = str(Path(db_path).resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        q = (f"SELECT concept_id, tag FROM concepts WHERE taxonomy='us-gaap' "
             f"AND tag IN ({','.join('?' * len(uniq))})")
        cmap = {cid: tag for cid, tag in con.execute(q, (*uniq,)).fetchall()}
        if not cmap:
            return pd.DataFrame(columns=cols)
        ids = sorted(cmap)
        q = (f"SELECT cik, concept_id, start, end, filed, val FROM facts "
             f"WHERE concept_id IN ({','.join('?' * len(ids))}) "
             f"AND unit='USD' AND end >= '2008-01-01'")
        df = pd.read_sql_query(q, con, params=[*ids])
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["tag"] = df["concept_id"].map(cmap)
    has_start = df["start"].notna() & (df["start"] != "")
    df["start"] = pd.to_datetime(df["start"].where(df["start"] != "", None), errors="coerce")
    df["end"] = pd.to_datetime(df["end"], errors="coerce")
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df = df[~((has_start & df["start"].isna()) | df["end"].isna() | df["filed"].isna())].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["val"] = df["val"].astype(float)
    return df[cols].reset_index(drop=True)


def pick_priority(f: pd.DataFrame) -> pd.DataFrame:
    """(cik, 항목, start, end)마다 filed 가장 이른 행 1개."""
    if f is None or f.empty:
        return pd.DataFrame(columns=PICK_COLS)
    m = tag_to_item()
    df = f[f["tag"].isin(m)].copy()
    if df.empty:
        return pd.DataFrame(columns=PICK_COLS)
    df["item"] = df["tag"].map(lambda t: m[t][0])
    df["prio"] = df["tag"].map(lambda t: m[t][1])
    df["src_tag"] = df["tag"]
    # NaT도 같은 키로 묶이게 문자열 임시열 (groupby는 NaN 키를 버림).
    df["_sk"] = df["start"].astype(str)
    df = df.sort_values(["filed", "prio"]).drop_duplicates(
        ["cik", "item", "_sk", "end"], keep="first")
    return df[PICK_COLS].reset_index(drop=True)


def load_std(db_path=FACTS_DB, items=None, chunk=20) -> tuple[pd.DataFrame, pd.DataFrame]:
    """표준 항목 long (bal, ttm). 열 cik, tag(=항목), end, avail, val."""
    empty = lambda: pd.DataFrame(columns=LONG_COLS)  # noqa: E731
    names = list(GROUPS) if items is None else list(items)
    chunk = max(1, int(chunk))
    bundles: list[tuple[list[str], list[str]]] = []
    cur_names: list[str] = []
    cur_tags: list[str] = []
    for name in names:
        ts = list(GROUPS[name]["tags"])
        if cur_names and len(cur_tags) + len(ts) > chunk:
            bundles.append((cur_names, cur_tags))
            cur_names = []
            cur_tags = []
        cur_names.append(name)
        for t in ts:
            if t not in cur_tags:
                cur_tags.append(t)
    if cur_names:
        bundles.append((cur_names, cur_tags))
    n = len(bundles)
    if n == 0:
        return empty(), empty()
    bal_names = {k for k, v in GROUPS.items() if v["kind"] == "bal"}
    bal_parts: list[pd.DataFrame] = []
    ttm_parts: list[pd.DataFrame] = []
    for i, (bnames, btags) in enumerate(bundles):
        b = empty()
        w = empty()
        f = _read_tags(db_path, btags)
        if f is not None and not f.empty:
            f = f[f["tag"].isin(btags)]
            if not f.empty:
                picked = pick_priority(first_reported(f))
                if not picked.empty:
                    fb = picked[picked["item"].isin(bal_names)].rename(
                        columns={"item": "tag"})
                    fw = picked[~picked["item"].isin(bal_names)].rename(
                        columns={"item": "tag"})
                    if not fb.empty:
                        b = bal_obs(fb)
                        if not b.empty:
                            b["tag"] = b["tag"].astype("category")
                            bal_parts.append(b)
                    if not fw.empty:
                        w = ttm_obs(fw)
                        if not w.empty:
                            w["tag"] = w["tag"].astype("category")
                            ttm_parts.append(w)
        print(f"[std] {i + 1}/{n} bal_rows={len(b)} ttm_rows={len(w)}", flush=True)
        del b, w, f
        gc.collect()
    bal = (pd.concat(bal_parts, ignore_index=True)
           if bal_parts else empty())
    ttm = (pd.concat(ttm_parts, ignore_index=True)
           if ttm_parts else empty())
    del bal_parts, ttm_parts
    gc.collect()
    if not bal.empty:
        bal["tag"] = bal["tag"].astype("category")
    if not ttm.empty:
        ttm["tag"] = ttm["tag"].astype("category")
    return bal.reset_index(drop=True), ttm.reset_index(drop=True)


def std_snapshot(bal: pd.DataFrame, ttm: pd.DataFrame, t, items=None) -> pd.DataFrame:
    """형성일 표준 항목 표 (계산 채움 → 0 채움)."""
    all_items = list(GROUPS)
    s = snapshot(bal, ttm, t, all_items)
    s2 = snapshot(bal, ttm, t, all_items, window_days=FALLBACK_DAYS)
    s = s.combine_first(s2)
    for c in all_items:
        if c not in s.columns:
            s[c] = np.nan
    s = s[all_items]
    # 계산 채움 (원래 NaN일 때만).
    s["total_equity"] = s["total_equity"].fillna(
        s["stockholders_equity"] + s["minority_interest"].fillna(0))
    s["total_liabilities"] = s["total_liabilities"].fillna(
        s["liabilities_and_equity"] - s["total_equity"]
        - s["redeemable_preferred"].fillna(0))
    v = (s["general_admin"].fillna(0) + s["selling_marketing"].fillna(0)).where(
        s["general_admin"].notna() | s["selling_marketing"].notna())
    s["sga_expense"] = s["sga_expense"].fillna(v)
    v = (s["short_term_borrowings"].fillna(0) + s["long_term_debt_current"].fillna(0)).where(
        s["short_term_borrowings"].notna() | s["long_term_debt_current"].notna())
    s["short_term_debt"] = s["short_term_debt"].fillna(v)
    v = (s["long_term_debt_total"] - s["long_term_debt_current"].fillna(0)).where(
        s["long_term_debt_total"].notna())
    s["long_term_debt"] = s["long_term_debt"].fillna(v)
    v = (s["cash_and_equivalents"] + s["short_term_investments"].fillna(0)).where(
        s["cash_and_equivalents"].notna())
    s["cash_and_sti"] = s["cash_and_sti"].fillna(v)
    v = (s["income_before_xo"] + s["income_tax_expense"]).where(
        s["income_before_xo"].notna() & s["income_tax_expense"].notna())
    s["income_before_tax"] = s["income_before_tax"].fillna(v)
    v = (s["income_before_tax"] - s["income_before_xo"]).where(
        s["income_before_tax"].notna() & s["income_before_xo"].notna())
    s["income_tax_expense"] = s["income_tax_expense"].fillna(v)
    # 0 채움 (위 계산에서 읽은 항목은 명시적 NaN→0이라 순서 무관).
    bal_zero = [k for k, v in GROUPS.items() if v["zero"] and v["kind"] == "bal"]
    flow_zero = [k for k, v in GROUPS.items() if v["zero"] and v["kind"] == "flow"]
    has_a = s["total_assets"].notna()
    for c in bal_zero:
        s.loc[has_a, c] = s.loc[has_a, c].fillna(0)
    has_f = s["revenue"].notna() | s["net_income"].notna()
    for c in flow_zero:
        s.loc[has_f, c] = s.loc[has_f, c].fillna(0)
    if items is not None:
        s = s[list(items)]
    s.index.name = "cik"
    return s
