"""AFA 회계 항목 스냅샷."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from research.ccc.panel import FACTS_DB

POOL_N = 100
BAL_WINDOW_DAYS = 200
COVER_FRAC = 0.6


def candidate_tags(db_path=FACTS_DB, n: int = POOL_N) -> list[str]:
    """us-gaap·USD 태그를 보유 기업 수 상위 n개."""
    p = str(Path(db_path).resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        q = ("SELECT c.tag AS tag, COUNT(DISTINCT f.cik) AS n "
             "FROM facts f JOIN concepts c ON f.concept_id = c.concept_id "
             "WHERE c.taxonomy = 'us-gaap' AND f.unit = 'USD' "
             "GROUP BY c.tag ORDER BY n DESC, c.tag ASC LIMIT ?")
        rows = con.execute(q, (int(n),)).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def tag_kind(f: pd.DataFrame) -> dict:
    """태그별 start 결측 비율 0.5 이상이면 bal."""
    if f.empty:
        return {}
    r = f.groupby("tag")["start"].apply(lambda s: float(s.isna().mean()))
    return {t: ("bal" if v >= 0.5 else "flow") for t, v in r.items()}


def bal_obs(f: pd.DataFrame) -> pd.DataFrame:
    """start 없는 잔액 행."""
    cols = ["cik", "tag", "end", "avail", "val"]
    if f.empty:
        return pd.DataFrame(columns=cols)
    df = f[f["start"].isna() & f["end"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["avail"] = pd.to_datetime(df["filed"])
    return df[cols].reset_index(drop=True)


def ttm_obs(f: pd.DataFrame) -> pd.DataFrame:
    """손익 TTM (연간 직접값 + YTD 재구성, 벡터화)."""
    cols = ["cik", "tag", "end", "avail", "val"]
    if f.empty:
        return pd.DataFrame(columns=cols)
    df = f[f["start"].notna() & f["end"].notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["days"] = (df["end"] - df["start"]).dt.days
    ann = df[(df["days"] >= 350) & (df["days"] <= 380)].copy()
    b = pd.Series(pd.NA, index=df.index, dtype="string")
    b = b.mask((df["days"] >= 80) & (df["days"] <= 100), "q1")
    b = b.mask((df["days"] >= 170) & (df["days"] <= 190), "q2")
    b = b.mask((df["days"] >= 260) & (df["days"] <= 285), "q3")
    ytd = df[b.notna()].copy()
    ytd["bucket"] = b[b.notna()].to_numpy()
    direct = pd.DataFrame(columns=cols)
    if not ann.empty:
        direct = pd.DataFrame({
            "cik": ann["cik"].to_numpy(),
            "tag": ann["tag"].to_numpy(),
            "end": pd.to_datetime(ann["end"]),
            "avail": pd.to_datetime(ann["filed"]),
            "val": ann["val"].to_numpy(float),
        })
    if ytd.empty or ann.empty:
        out = direct
    else:
        yl = ytd[["cik", "tag", "bucket", "start", "end", "filed", "val"]].copy()
        yl["key_a"] = yl["start"] - pd.to_timedelta(1, unit="D")
        yl["key_b"] = yl["end"] - pd.to_timedelta(365, unit="D")
        ar = ann[["cik", "tag", "end", "filed", "val"]].copy()
        ar = ar.rename(columns={"end": "a_end", "filed": "a_filed", "val": "a_val"})
        yl = yl.sort_values("key_a").reset_index(drop=True)
        ar = ar.sort_values("a_end").reset_index(drop=True)
        m1 = pd.merge_asof(yl, ar, left_on="key_a", right_on="a_end",
                           by=["cik", "tag"], direction="nearest",
                           tolerance=pd.Timedelta(days=10))
        pr = ytd[["cik", "tag", "bucket", "end", "filed", "val"]].copy()
        pr = pr.rename(columns={"end": "p_end", "filed": "p_filed", "val": "p_val"})
        m1 = m1.sort_values("key_b").reset_index(drop=True)
        pr = pr.sort_values("p_end").reset_index(drop=True)
        m2 = pd.merge_asof(m1, pr, left_on="key_b", right_on="p_end",
                           by=["cik", "tag", "bucket"], direction="nearest",
                           tolerance=pd.Timedelta(days=10))
        ok = m2["a_val"].notna() & m2["p_val"].notna()
        m2 = m2[ok]
        if m2.empty:
            out = direct
        else:
            comp = pd.DataFrame({
                "cik": m2["cik"].to_numpy(),
                "tag": m2["tag"].to_numpy(),
                "end": pd.to_datetime(m2["end"]),
                "avail": pd.to_datetime(pd.concat(
                    [m2["filed"], m2["a_filed"], m2["p_filed"]], axis=1).max(axis=1)),
                "val": (m2["a_val"].to_numpy(float) + m2["val"].to_numpy(float)
                        - m2["p_val"].to_numpy(float)),
            })
            out = pd.concat([direct, comp], ignore_index=True)
    if out.empty:
        return pd.DataFrame(columns=cols)
    if not direct.empty:
        dkey = set(zip(direct["cik"].astype(str), direct["tag"].astype(str),
                       direct["end"].astype(str)))
        out["is_direct"] = [k in dkey for k in
                            zip(out["cik"].astype(str), out["tag"].astype(str),
                                out["end"].astype(str))]
    else:
        out["is_direct"] = False
    out = out.sort_values(["is_direct", "avail"], ascending=[False, True])
    out = out.drop_duplicates(["cik", "tag", "end"], keep="first")
    return out[cols].reset_index(drop=True)


def snapshot(bal: pd.DataFrame, ttm: pd.DataFrame, t, tags,
             window_days: int = BAL_WINDOW_DAYS) -> pd.DataFrame:
    """형성일 항목 행렬 (index=cik, columns=tags)."""
    t = pd.Timestamp(t)
    lo = t - pd.Timedelta(days=window_days)
    tags = list(tags)

    def _wide(long: pd.DataFrame) -> pd.DataFrame:
        if long is None or long.empty:
            return pd.DataFrame()
        df = long[(long["avail"] <= t) & (long["end"] >= lo)
                  & long["tag"].isin(tags)].copy()
        if df.empty:
            return pd.DataFrame()
        df = df.sort_values(["end", "avail"]).drop_duplicates(["cik", "tag"], keep="last")
        return df.pivot(index="cik", columns="tag", values="val")

    wb, wt = _wide(bal), _wide(ttm)
    if not wb.empty and not wt.empty:
        both = set(wb.columns) & set(wt.columns)
        if both:
            nb = bal["tag"].value_counts() if not bal.empty else pd.Series(dtype=int)
            nt = ttm["tag"].value_counts() if not ttm.empty else pd.Series(dtype=int)
            for tg in both:
                if float(nb.get(tg, 0)) >= float(nt.get(tg, 0)):
                    wt = wt.drop(columns=[tg])
                else:
                    wb = wb.drop(columns=[tg])
    if wb.empty and wt.empty:
        return pd.DataFrame(columns=tags)
    if wb.empty:
        out = wt
    elif wt.empty:
        out = wb
    else:
        out = pd.concat([wb, wt], axis=1)
    keep = [c for c in tags if c in out.columns]
    out = out[keep]
    out.index.name = "cik"
    return out


def select_items(snap: pd.DataFrame, sample_ciks, frac: float = COVER_FRAC) -> list[str]:
    """전부 보유 기업이 frac 이상 유지되는 최대 태그 집합."""
    if snap is None or snap.empty or len(snap.columns) == 0:
        return []
    ciks = list(sample_ciks)
    if not ciks:
        return []
    sub = snap.reindex(ciks)
    cov = sub.notna().sum(axis=0)
    order = sorted(sub.columns, key=lambda c: (-int(cov[c]), str(c)))
    need = float(frac) * len(ciks)
    keep: list[str] = []
    for c in order:
        trial = keep + [c]
        n = int(sub[trial].notna().all(axis=1).sum())
        if n >= need:
            keep = trial
        else:
            break
    return keep


def winsorize_by_assets(X: pd.DataFrame, assets: pd.Series, p: float = 0.05) -> pd.DataFrame:
    """자산 대비 비율 횡단면 clip 후 자산 환원."""
    r = X.div(assets, axis=0)
    lo = r.quantile(p)
    hi = r.quantile(1 - p)
    return r.clip(lower=lo, upper=hi, axis=1).mul(assets, axis=0)
