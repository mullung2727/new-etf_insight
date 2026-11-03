"""미국 안정배당 고배당률 백테스트 (PLAN_US_DIVIDEND 1·2·3·5절)."""

from __future__ import annotations

import sqlite3
import sys
import warnings
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from research.afa.items import snapshot
from research.backtest_daily.data_us import US_OHLCV_DB, load_etf_tr
from research.backtest_daily.perf import summary, yearly
from research.backtest_daily.portfolio import run as portfolio_run
from research.backtest_daily.validate import placebo_percentile
from research.ccc.panel import FACTS_DB
from research.us_fin_std.build import load_std

DPS_DECLARED = "CommonStockDividendsPerShareDeclared"
DPS_PAID = "CommonStockDividendsPerShareCashPaid"
FIN_TAGS = ["operating_cash_flow", "capex", "dividends_paid", "net_income"]
N_PICKS = 30
START_YEAR = 2013
MIN_DAY_TICKERS = 3000
COST = 0.00175
P0_DRAWS = 200
BUCKETS = ["0~-20%", "-20~-50%", "-50%초과"]
H1_BUCKETS = ["-20~-50%", "-50%초과"]
H1_GROUPS = ["①안정+현금감당", "②안정+현금미감당", "③불안정배당", "④무배당"]
PAYOUT_ORDER = ["<50%", "50~100%", ">100%/적자", "무배당"]
TAX_RATE = 0.15
DIV_MULT_TAX = 1.0 - TAX_RATE
RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "RESULTS.md"
_CAND_COLS = ["ticker", "cik", "name", "dps3", "yld", "px", "adv20", "r1y",
              "ocf", "capex", "divpaid", "fy3_end"]


def stable(v, tol: float = 0.95) -> bool:
    """3년 DPS 안정 (모두 >0, 매년 직전 해 tol 이상)."""
    try:
        a, b, c = (float(x) for x in v)
    except (TypeError, ValueError):
        return False
    if not (a > 0 and b > 0 and c > 0):
        return False
    return b >= tol * a and c >= tol * b


def fresh_consecutive(ends: list, t) -> bool:
    """최근 3개 FY end 신선·연속 (최신 end >= t-550일, 인접 간격 330~400일)."""
    try:
        if ends is None or len(ends) != 3:
            return False
        ts = pd.Timestamp(t)
        es = [pd.Timestamp(e) for e in ends]
    except (TypeError, ValueError):
        return False
    if pd.isna(ts) or any(pd.isna(e) for e in es):
        return False
    if es[-1] < ts - pd.Timedelta(days=550):
        return False
    for a, b in zip(es, es[1:]):
        d = (b - a).days
        if d < 330 or d > 400:
            return False
    return True


def excluded_sic(sic) -> bool:
    """SIC 없음·0·67xx 제외."""
    if sic is None:
        return True
    try:
        v = float(sic)
    except (TypeError, ValueError):
        return True
    if not np.isfinite(v) or v == 0:
        return True
    return int(v) // 100 == 67


def dd_bucket(r1y) -> str:
    """리밸런싱 전 1년 낙폭 구간."""
    if r1y >= -0.20:
        return "0~-20%"
    if r1y >= -0.50:
        return "-20~-50%"
    return "-50%초과"


def unit_ok(dps3, px) -> bool:
    """단위 오류 가드 (max DPS <= 종가 절반)."""
    try:
        p = float(px)
        m = max(float(x) for x in dps3)
    except (TypeError, ValueError):
        return False
    if not (np.isfinite(p) and np.isfinite(m)) or p <= 0:
        return False
    return m <= p * 0.5


def cash_covered(ocf, capex, divpaid) -> bool:
    """V1 현금 감당 (OCF - |capex| >= divpaid, NaN이면 탈락)."""
    try:
        o, c, d = float(ocf), float(capex), float(divpaid)
    except (TypeError, ValueError):
        return False
    if not (np.isfinite(o) and np.isfinite(c) and np.isfinite(d)):
        return False
    return o - abs(c) >= d


def div_group(dps3_fresh_ok, dps3, has_recent_dps, divpaid, covered) -> str:
    """H1 배당 그룹 (①② 안정, ③ 불안정, ④ 무배당)."""
    if dps3_fresh_ok and stable(dps3):
        return "①안정+현금감당" if covered else "②안정+현금미감당"
    try:
        dp = float(divpaid)
    except (TypeError, ValueError):
        dp = float("nan")
    if has_recent_dps or (np.isfinite(dp) and dp > 0):
        return "③불안정배당"
    return "④무배당"


def payout_bucket(divpaid, net_income):
    """배당성향 구간 (무배당이면 None)."""
    try:
        d = float(divpaid)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(d) or d <= 0:
        return None
    try:
        ni = float(net_income)
    except (TypeError, ValueError):
        return ">100%/적자"
    if not np.isfinite(ni) or ni <= 0:
        return ">100%/적자"
    r = d / ni
    if r < 0.5:
        return "<50%"
    if r <= 1.0:
        return "50~100%"
    return ">100%/적자"


def split_factor(splits_one: pd.DataFrame, filed) -> float:
    """filed 이후 분할 비율의 곱 (DPS를 이 값으로 나눔)."""
    if splits_one is None or splits_one.empty:
        return 1.0
    f = pd.Timestamp(filed)
    d = pd.to_datetime(splits_one["date"], errors="coerce")
    r = pd.to_numeric(splits_one["ratio"], errors="coerce")
    m = (d > f) & r.notna() & (r > 0)
    if not bool(m.any()):
        return 1.0
    return float(r[m].prod())


_TAG_PRIO = {DPS_DECLARED: 0, DPS_PAID: 1}


def _dps_asof_filed(dps: pd.DataFrame, t) -> pd.DataFrame:
    """filed<t 중 (cik,end)별 Declared 우선·filed 최신 1행."""
    cols = ["cik", "end", "filed", "val"]
    if dps is None or dps.empty:
        return pd.DataFrame(columns=cols)
    ts = pd.Timestamp(t)
    df = dps.copy()
    df["_filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df["_end"] = pd.to_datetime(df["end"], errors="coerce")
    df = df[df["_filed"] < ts]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["_prio"] = df["tag"].map(_TAG_PRIO).fillna(9).astype(int)
    df = df.sort_values(["_prio", "_filed"], ascending=[True, False])
    df = df.drop_duplicates(["cik", "_end"], keep="first").reset_index(drop=True)
    return pd.DataFrame({
        "cik": df["cik"].to_numpy(),
        "end": pd.to_datetime(df["_end"]),
        "filed": pd.to_datetime(df["_filed"]),
        "val": pd.to_numeric(df["val"], errors="coerce").to_numpy(dtype=float),
    })


def dps_asof(dps: pd.DataFrame, t) -> pd.DataFrame:
    """시점 t 이전 공시 DPS (cik, end, val)."""
    return _dps_asof_filed(dps, t)[["cik", "end", "val"]]


def load_dps(db=FACTS_DB) -> pd.DataFrame:
    """연간 DPS 원본 (cik, tag, end, filed, val)."""
    cols = ["cik", "tag", "end", "filed", "val"]
    tags = [DPS_DECLARED, DPS_PAID]
    p = str(Path(db).resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        cmap = {cid: tag for cid, tag in con.execute(
            "SELECT concept_id, tag FROM concepts WHERE taxonomy = 'us-gaap'"
            f" AND tag IN ({','.join('?' * len(tags))})", tags).fetchall()}
        if not cmap:
            return pd.DataFrame(columns=cols)
        ids = sorted(cmap)
        df = pd.read_sql_query(
            "SELECT cik, concept_id, unit, start, end, filed, val FROM facts"
            f" WHERE concept_id IN ({','.join('?' * len(ids))}) AND unit LIKE 'USD%'",
            con, params=[*ids])
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df[df["unit"] == "USD/shares"].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["tag"] = df["concept_id"].map(cmap)
    df["start"] = pd.to_datetime(df["start"], errors="coerce")
    df["end"] = pd.to_datetime(df["end"], errors="coerce")
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    days = (df["end"] - df["start"]).dt.days
    df = df[(days >= 350) & (days <= 380)].copy()
    df["val"] = pd.to_numeric(df["val"], errors="coerce")
    df = df[df["end"].notna() & df["filed"].notna() & df["val"].notna()].copy()
    return df[cols].reset_index(drop=True)


def _cik_str(s: pd.Series) -> pd.Series:
    """cik 정규화 (0 채움 달라도 조인되게)."""
    return pd.to_numeric(s, errors="coerce").astype("Int64").astype("string")


def _tkey(t) -> str:
    """티커 정규화 (BRK.B/BRK-B 같은 표기 흡수)."""
    return str(t).upper().replace(".", "-").replace("/", "-")


def _load_companies(db=FACTS_DB) -> pd.DataFrame:
    """현재 상장 기업 (cik, ticker, name, sic)."""
    p = str(Path(db).resolve()).replace("\\", "/")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(
            "SELECT cik, entity_name, ticker, sic FROM companies WHERE is_listed = 1", con)
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=["cik", "ticker", "name", "sic"])
    df = df[df["ticker"].notna() & (df["ticker"].astype(str).str.strip() != "")].copy()
    df["ticker"] = df["ticker"].astype(str).str.strip()
    df["cik"] = _cik_str(df["cik"])
    df = df[df["cik"].notna()].copy()
    df["name"] = df["entity_name"].astype(str)
    df = df.sort_values(["cik", "ticker"]).drop_duplicates("cik", keep="first")
    df = df.sort_values(["ticker", "cik"]).drop_duplicates("ticker", keep="first")
    return df[["cik", "ticker", "name", "sic"]].reset_index(drop=True)


def _trading_dates(db=US_OHLCV_DB, min_tickers: int = MIN_DAY_TICKERS) -> list[str]:
    """하루 종목 수 기준 이상인 날짜 축."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        df = con.execute(
            "SELECT date, COUNT(*) AS n FROM ohlcv GROUP BY date"
            " HAVING COUNT(*) >= ? ORDER BY date", [int(min_tickers)]).fetchdf()
    finally:
        con.close()
    return df["date"].astype(str).tolist()


def _rebalance_days(dates: list[str], start_year: int = START_YEAR) -> list[str]:
    """매년 4월 첫 거래일."""
    first_of_month = {}
    for d in dates:
        first_of_month.setdefault(d[:6], d)
    out = []
    for y in range(int(start_year), int(dates[-1][:4]) + 1):
        d = first_of_month.get(f"{y}04")
        if d is not None:
            out.append(d)
    return out


def _load_splits(db=US_OHLCV_DB) -> dict[str, pd.DataFrame]:
    """정규화 티커 → 분할표 (date, ratio)."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        df = con.execute("SELECT ticker, date, ratio FROM splits").fetchdf()
    finally:
        con.close()
    out: dict[str, pd.DataFrame] = {}
    if df.empty:
        return out
    df["date"] = df["date"].astype(str)
    df["ratio"] = pd.to_numeric(df["ratio"], errors="coerce")
    for k, g in df.groupby(df["ticker"].astype(str).map(_tkey)):
        out[k] = g[["date", "ratio"]].sort_values("date").reset_index(drop=True)
    return out


def _attach_market(m: pd.DataFrame, t, dates: list[str],
                   ohlcv_db=US_OHLCV_DB) -> pd.DataFrame:
    """티커→ohlcv 매핑 + px·adv20·r1y 부착 + 가격·유동성 필터 (candidates·H1 공용)."""
    t = str(t)
    if m is None or m.empty:
        return pd.DataFrame()
    m = m.copy()
    try:
        idx = dates.index(t)
    except ValueError:
        return m.head(0)
    if idx < 1:
        return m.head(0)
    prev = dates[idx - 1]
    win20 = dates[max(0, idx - 20):idx]
    need = sorted(set(win20 + [prev]
                        + ([dates[idx - 1 - 252]] if idx - 1 - 252 >= 0 else [])))
    con = duckdb.connect(str(ohlcv_db), read_only=True)
    try:
        px = con.execute(
            f"SELECT date, ticker, close, trading_value FROM ohlcv"
            f" WHERE date IN ({','.join('?' * len(need))})", need).fetchdf()
    finally:
        con.close()
    if px.empty:
        return m.head(0)
    px["ticker"] = px["ticker"].astype(str)
    px["date"] = px["date"].astype(str)
    px = px.drop_duplicates(["date", "ticker"], keep="last")
    keymap: dict[str, str] = {}
    for tk in sorted(px["ticker"].unique()):
        keymap.setdefault(_tkey(tk), tk)
    before = len(m)
    m["ticker"] = m["ticker"].map(lambda x: keymap.get(_tkey(x)))
    m = m[m["ticker"].notna()].copy()
    if len(m) < before:
        print(f"[info] {t} no-price-ticker: {before - len(m)}", flush=True)
    if m.empty:
        return m
    m = m.sort_values(["ticker", "cik"]).drop_duplicates("ticker", keep="first")
    px["close"] = pd.to_numeric(px["close"], errors="coerce")
    px["trading_value"] = pd.to_numeric(px["trading_value"], errors="coerce")
    cp = px[px["date"] == prev].set_index("ticker")["close"]
    adv = px[px["date"].isin(win20)].groupby("ticker")["trading_value"].mean()
    m["px"] = pd.to_numeric(m["ticker"].map(cp), errors="coerce")
    m["adv20"] = pd.to_numeric(m["ticker"].map(adv), errors="coerce")
    if idx - 1 - 252 >= 0:
        bpx = pd.to_numeric(m["ticker"].map(
            px[px["date"] == dates[idx - 1 - 252]].set_index("ticker")["close"]),
            errors="coerce")
        m["r1y"] = (m["px"] / bpx - 1).where(bpx > 0)
    else:
        m["r1y"] = np.nan
    m = m[m["px"].notna() & (m["px"] >= 1)
          & m["adv20"].notna() & (m["adv20"] >= 1_000_000)].copy()
    return m


def _attach_fin(m: pd.DataFrame, bal, ttm, t) -> pd.DataFrame:
    """재무 스냅샷 병합 (ocf·capex·divpaid·net_income, candidates·H1 공용)."""
    m = m.copy()
    snap = snapshot(bal, ttm, pd.Timestamp(t) - pd.Timedelta(days=1), FIN_TAGS)
    if snap is None or snap.empty:
        m["ocf"] = np.nan
        m["capex"] = np.nan
        m["divpaid"] = np.nan
        m["net_income"] = np.nan
        return m
    fin = snap.reindex(columns=FIN_TAGS)
    fin.index.name = "cik"
    fin = fin.reset_index()
    fin["cik"] = _cik_str(fin["cik"])
    fin = fin.dropna(subset=["cik"]).drop_duplicates("cik", keep="last")
    return m.merge(fin, on="cik", how="left").rename(columns={
        "operating_cash_flow": "ocf", "capex": "capex",
        "dividends_paid": "divpaid"})


def candidates(t, dps, companies, dates, splits, bal, ttm,
               ohlcv_db=US_OHLCV_DB) -> pd.DataFrame:
    """리밸런싱일 t(YYYYMMDD) 후보 표."""
    empty = pd.DataFrame(columns=_CAND_COLS)
    t = str(t)
    avail = _dps_asof_filed(dps, t)
    if avail.empty:
        return empty
    last3 = avail.sort_values(["cik", "end"]).groupby("cik").tail(3)
    last3 = last3[last3.groupby("cik")["end"].transform("size") == 3]
    if last3.empty:
        return empty
    recs = []
    for cik, g in last3.groupby("cik", sort=True):
        g = g.sort_values("end")
        ends = list(g["end"])
        if not fresh_consecutive(ends, t):
            continue
        recs.append({"cik": cik, "vals": [float(v) for v in g["val"]],
                     "fileds": list(g["filed"]), "fy3_end": ends[-1]})
    if not recs:
        return empty
    m = pd.DataFrame(recs).merge(companies, on="cik", how="inner")
    if m.empty:
        return empty
    m = m[~m["sic"].map(excluded_sic)].copy()
    if m.empty:
        return empty
    m = _attach_market(m, t, dates, ohlcv_db)
    if m.empty:
        return empty
    fcache: dict = {}

    def _adj(tk, v, f):
        key = (_tkey(tk), str(pd.Timestamp(f).date()))
        fac = fcache.get(key)
        if fac is None:
            fac = split_factor(splits.get(key[0]), f)
            fcache[key] = fac
        return float(v) / fac

    m["dps3"] = [[_adj(tk, v, f) for v, f in zip(vs, fs)]
                 for tk, vs, fs in zip(m["ticker"], m["vals"], m["fileds"])]
    m = m[m["dps3"].map(stable)].copy()
    if m.empty:
        return empty
    keep = [unit_ok(v, p) for v, p in zip(m["dps3"], m["px"])]
    excl = sorted(m.loc[[not k for k in keep], "ticker"].tolist())
    if excl:
        print(f"[guard] {t} unit-excluded: {', '.join(excl)}", flush=True)
    m = m[keep].copy()
    if m.empty:
        return empty
    m = _attach_fin(m, bal, ttm, t)
    m["yld"] = [v[-1] / p for v, p in zip(m["dps3"], m["px"])]
    m = m.sort_values(["yld", "ticker"], ascending=[False, True])
    return m[_CAND_COLS].reset_index(drop=True)


def _select_top(cand: pd.DataFrame, n: int = N_PICKS) -> list[str]:
    """시가배당률 상위 n개."""
    if cand is None or cand.empty:
        return []
    df = cand.dropna(subset=["yld"]).sort_values(
        ["yld", "ticker"], ascending=[False, True]).head(int(n))
    return df["ticker"].tolist()


def select_v1(cand: pd.DataFrame, thr: float, n: int = N_PICKS) -> list[str]:
    """폭락(r1y < thr) 중 현금 미감당만 제외 후 yld 상위 n (r1y NaN은 유지)."""
    if cand is None or cand.empty:
        return []
    r = pd.to_numeric(cand["r1y"], errors="coerce")
    covered = cand.apply(
        lambda row: cash_covered(row["ocf"], row["capex"], row["divpaid"]),
        axis=1)
    drop = (r < float(thr)) & ~covered
    return _select_top(cand[~drop], n)


def _select_random(cand: pd.DataFrame, seed: int, n: int = N_PICKS) -> list[str]:
    """후보 중 무작위 n개."""
    if cand is None or cand.empty:
        return []
    pool = sorted(set(cand["ticker"].tolist()))
    k = min(int(n), len(pool))
    if k == 0:
        return []
    rng = np.random.default_rng(int(seed))
    return sorted(rng.choice(pool, size=k, replace=False).tolist())


def div_add(dps, prev_close, mult=1.0) -> float:
    """일별 배당 가산 = dps ÷ 252 ÷ 전일 종가 × mult."""
    try:
        d, p, m = float(dps), float(prev_close), float(mult)
    except (TypeError, ValueError):
        return 0.0
    if not (np.isfinite(d) and np.isfinite(p) and np.isfinite(m)) or p <= 0:
        return 0.0
    return d / 252.0 / p * m


def _returns(tickers, idx, rebs, dps_by_year, db=US_OHLCV_DB, div_mult=1.0):
    """종가 행렬·배당 가산(div_mult 배율) 일별 수익률·±50% 클립 건수."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        q = ("SELECT date, ticker, close FROM ohlcv WHERE date >= ?"
             f" AND ticker IN ({','.join('?' * len(tickers))})")
        df = con.execute(q, [idx[0], *tickers]).fetchdf()
    finally:
        con.close()
    df["date"] = df["date"].astype(str)
    df["ticker"] = df["ticker"].astype(str)
    df = df.drop_duplicates(["date", "ticker"], keep="last")
    close = df.pivot(index="date", columns="ticker", values="close")
    close = close.reindex(index=idx, columns=tickers)
    close = close.apply(pd.to_numeric, errors="coerce")
    prev = close.shift(1)
    div = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for yi, t in enumerate(rebs):
        hi = rebs[yi + 1] if yi + 1 < len(rebs) else None
        seg = [d for d in idx if d >= t and (hi is None or d < hi)]
        if not seg:
            continue
        dmap = dps_by_year.get(t, {})
        cols = [c for c in dmap if c in div.columns]
        if not cols:
            continue
        pv = prev.loc[seg, cols].to_numpy(dtype=float)
        mult = float(div_mult)
        # div_add 벡터화 (dps ÷ 252 ÷ 전일 종가 × mult).
        arr = np.array([float(dmap[c]) / 252.0 * mult for c in cols],
                       dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            add = arr / pv
        add[~(pv > 0)] = 0.0
        add[~np.isfinite(add)] = 0.0
        div.loc[seg, cols] = add
    with np.errstate(divide="ignore", invalid="ignore"):
        r = close / prev - 1 + div
    bad = r.abs() > 0.5
    n_clip = int(bad.sum().sum())
    return close, r.mask(bad).fillna(0.0), n_clip


def _weights(idx, cols, picks_by_reb):
    """리밸런싱일 목표 비중 행렬 (그 사이 ffill 유지)."""
    w = pd.DataFrame(np.nan, index=idx, columns=cols, dtype=float)
    have = set(cols)
    for t in sorted(picks_by_reb):
        picks = [c for c in picks_by_reb[t] if c in have]
        if not picks:
            continue
        w.loc[t, :] = 0.0
        w.loc[t, picks] = 1.0 / len(picks)
    return w.ffill().fillna(0.0)


def _a1(rebs, dates, v0_picks, cand_by_year, dps, splits, close):
    """A1 낙폭 구간별 다음 1년 총수익·삭감률 (pooled, 연도 가중, 판정불가수)."""
    pos = {d: i for i, d in enumerate(dates)}
    rows = []
    for yi in range(len(rebs) - 1):
        t1, t2 = rebs[yi], rebs[yi + 1]
        p1, p2 = dates[pos[t1] - 1], dates[pos[t2] - 1]
        cand = cand_by_year[t1]
        if cand.empty:
            continue
        crows = cand.set_index("ticker")
        a2 = _dps_asof_filed(dps, t2)
        latest = {}
        if not a2.empty:
            for r in a2.sort_values("end").groupby("cik").tail(1).itertuples():
                latest[r.cik] = (float(r.val), r.filed, r.end)
        for tk in v0_picks.get(t1, []):
            if tk not in crows.index:
                continue
            crow = crows.loc[tk]
            px1 = float(crow["px"])
            d3 = float(crow["dps3"][-1])
            px2 = float(close.at[p2, tk]) if (p2 in close.index
                                             and tk in close.columns) else np.nan
            tot = px2 / px1 - 1 + d3 / px1 if (np.isfinite(px2)
                                              and px1 > 0) else np.nan
            lv = latest.get(crow["cik"])
            if lv is None:
                cut = np.nan
            else:
                try:
                    fy3 = pd.Timestamp(crow["fy3_end"]) if "fy3_end" in crow else pd.NaT
                    lat_end = pd.Timestamp(lv[2])
                except (TypeError, ValueError):
                    cut = np.nan
                else:
                    if pd.isna(fy3) or pd.isna(lat_end) or lat_end <= fy3:
                        cut = np.nan
                    else:
                        cut = bool(lv[0] / split_factor(splits.get(_tkey(tk)), lv[1])
                                   < 0.95 * d3)
            r1y = crow["r1y"]
            dv = d3 / px1 if px1 > 0 else np.nan
            tot_tax = tot - TAX_RATE * dv if np.isfinite(tot) else np.nan
            rows.append({"year": t1[:4],
                         "bucket": dd_bucket(r1y) if pd.notna(r1y) else None,
                         "tot": tot, "tot_tax": tot_tax, "cut": cut})
    df = pd.DataFrame(rows, columns=["year", "bucket", "tot", "tot_tax", "cut"])
    n_no_r1y = int(df["bucket"].isna().sum()) if not df.empty else 0
    if n_no_r1y:
        print(f"[info] a1 r1y-missing: {n_no_r1y}", flush=True)
    d = df[df["bucket"].notna()].copy()
    if not d.empty:
        d = d[d["tot"].notna() & np.isfinite(d["tot"].to_numpy(dtype=float))]
    pooled = d.groupby("bucket").agg(n=("tot", "size"), mean=("tot", "mean"),
                                     med=("tot", "median"),
                                     mean_tax=("tot_tax", "mean"),
                                     cut_rate=("cut", "mean")).reindex(BUCKETS)
    g = d.groupby(["year", "bucket"]).agg(m=("tot", "mean"), mt=("tot_tax", "mean"),
                                          c=("cut", "mean"))
    yw = g.groupby("bucket").agg(years=("m", "size"), mean_return=("m", "mean"),
                                 mean_tax=("mt", "mean"),
                                 cut_rate=("c", "mean")).reindex(BUCKETS)
    n_unknown = int(d["cut"].isna().sum()) if not d.empty else 0
    return pooled, yw, n_unknown


def _pct(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v * 100:.2f}%" if np.isfinite(v) else "nan"


def _num(x, nd: int = 2) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v:.{nd}f}" if np.isfinite(v) else "nan"


def _int(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "0"
    return str(int(v)) if np.isfinite(v) else "0"


def _spy_div_yield(idx, db=US_OHLCV_DB) -> pd.Series:
    """SPY 일별 배당 ÷ 전일 종가 (idx 정렬, 없으면 0)."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        px = con.execute(
            "SELECT date, close FROM etf_ohlcv WHERE ticker = 'SPY'").fetchdf()
        dv = con.execute(
            "SELECT date, amount FROM etf_dividends WHERE ticker = 'SPY'").fetchdf()
    finally:
        con.close()
    if px.empty:
        return pd.Series(0.0, index=pd.Index(idx, dtype=str))
    c = px.copy()
    c["date"] = c["date"].astype(str)
    c["close"] = pd.to_numeric(c["close"], errors="coerce")
    c = c.sort_values("date").drop_duplicates("date", keep="last")
    c["prev"] = c["close"].shift(1)
    amt = {}
    if not dv.empty:
        dv["date"] = dv["date"].astype(str)
        dv["amount"] = pd.to_numeric(dv["amount"], errors="coerce")
        amt = dv.groupby("date")["amount"].sum().to_dict()
    c["amt"] = c["date"].map(amt).fillna(0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        c["yld"] = c["amt"] / c["prev"]
    c["yld"] = c["yld"].where(c["prev"] > 0, 0.0).fillna(0.0)
    return c.set_index("date")["yld"].reindex(
        pd.Index(idx, dtype=str)).fillna(0.0)


def _a2_bucket_picks(rebs, v0_picks, cand_by_year) -> dict:
    """버킷 → {리밸런싱일: V0 중 그 버킷 종목} (r1y NaN 제외)."""
    out = {b: {t: [] for t in rebs} for b in BUCKETS}
    for t in rebs:
        cand = cand_by_year.get(t)
        rmap = (dict(zip(cand["ticker"], cand["r1y"]))
                if cand is not None and not cand.empty else {})
        for tk in v0_picks.get(t, []):
            r = rmap.get(tk, np.nan)
            if pd.isna(r):
                continue
            out[dd_bucket(r)][t].append(tk)
    return out


def _a2_contrib(rebs, v0_picks, cand_by_year, rets) -> pd.DataFrame:
    """A2 연도×버킷 기여 (ΣR_i ÷ 그해 V0 수, 비용 무시, 마지막 연도 제외)."""
    cols = BUCKETS + ["미분류"]
    rows = []
    for yi in range(len(rebs) - 1):
        t, nxt = rebs[yi], rebs[yi + 1]
        seg = [d for d in rets.index if d >= t and d < nxt]
        picks = v0_picks.get(t, [])
        n = len(picks)
        cand = cand_by_year.get(t)
        rmap = (dict(zip(cand["ticker"], cand["r1y"]))
                if cand is not None and not cand.empty else {})
        bmap = {}
        for tk in picks:
            r = rmap.get(tk, np.nan)
            bmap[tk] = dd_bucket(r) if pd.notna(r) else "미분류"
        R = {}
        for tk in picks:
            if tk in rets.columns and seg:
                arr = rets.loc[seg, tk].to_numpy(dtype=float)
                R[tk] = float(np.prod(1.0 + arr) - 1.0)
            else:
                R[tk] = np.nan
        row = {"year": t[:4]}
        for b in cols:
            vals = [R[tk] for tk in picks
                    if bmap[tk] == b and np.isfinite(R[tk])]
            row[b] = sum(vals) / n if n else np.nan
        row["합계"] = sum(row[b] for b in BUCKETS)
        rows.append(row)
    return pd.DataFrame(rows, columns=["year"] + cols + ["합계"])


def _h1_year(t, nxt, dps, companies, dates, splits, bal, ttm,
             ohlcv_db=US_OHLCV_DB) -> list[dict]:
    """H1 한 해 행 (r1y < -20% 전 종목, 배당 무관)."""
    t, nxt = str(t), str(nxt)
    m0 = companies[~companies["sic"].map(excluded_sic)].copy()
    if m0.empty:
        return []
    m = _attach_market(m0, t, dates, ohlcv_db)
    if m.empty:
        return []
    m = m[pd.to_numeric(m["r1y"], errors="coerce") < -0.20].copy()
    if m.empty:
        return []
    m = _attach_fin(m, bal, ttm, t)
    avail = _dps_asof_filed(dps, t)
    hist = {}
    if not avail.empty:
        for cik, g in avail.sort_values("end").groupby("cik", sort=False):
            hist[cik] = g
    t550 = pd.Timestamp(t) - pd.Timedelta(days=550)
    seg = [d for d in dates if d >= t and d < nxt]
    if not seg:
        return []
    tickers = sorted(m["ticker"].unique())
    con = duckdb.connect(str(ohlcv_db), read_only=True)
    try:
        mn = con.execute(
            "SELECT ticker, MIN(close) AS mn FROM ohlcv WHERE date >= ?"
            f" AND date < ? AND ticker IN ({','.join('?' * len(tickers))})"
            " GROUP BY ticker", [t, nxt, *tickers]).fetchdf()
        last = con.execute(
            "SELECT ticker, close AS last FROM ohlcv WHERE date = ?"
            f" AND ticker IN ({','.join('?' * len(tickers))})",
            [seg[-1], *tickers]).fetchdf()
    finally:
        con.close()
    mn["ticker"] = mn["ticker"].astype(str)
    mn_map = dict(zip(mn["ticker"], pd.to_numeric(mn["mn"], errors="coerce")))
    last["ticker"] = last["ticker"].astype(str)
    last = last.drop_duplicates("ticker", keep="last")
    last_map = dict(zip(last["ticker"],
                        pd.to_numeric(last["last"], errors="coerce")))
    rows = []
    for r in m.itertuples():
        p1 = float(r.px) if pd.notna(r.px) else np.nan
        g = hist.get(r.cik)
        if g is not None and len(g) >= 3:
            g3 = g.tail(3)
            fresh_ok = fresh_consecutive(list(g3["end"]), t)
            sp = splits.get(_tkey(r.ticker))
            dps3 = [float(v) / split_factor(sp, f)
                    for v, f in zip(g3["val"], g3["filed"])]
            latest = g.iloc[-1]
        elif g is not None and len(g) > 0:
            fresh_ok, dps3, latest = False, None, g.iloc[-1]
        else:
            fresh_ok, dps3, latest = False, None, None
        has_recent = bool(((g["end"] >= t550) & (g["val"] > 0)).any()
                          if g is not None else False)
        covered = cash_covered(r.ocf, r.capex, r.divpaid)
        grp = div_group(fresh_ok, dps3, has_recent, r.divpaid, covered)
        pay = payout_bucket(r.divpaid, r.net_income)
        ladj = None
        if latest is not None:
            try:
                lv = float(latest["val"])
            except (TypeError, ValueError):
                lv = np.nan
            if np.isfinite(lv) and lv > 0:
                ladj = lv / split_factor(splits.get(_tkey(r.ticker)),
                                         latest["filed"])
        dy = ladj / p1 if (grp != "④무배당" and ladj and p1 > 0) else 0.0
        lo = mn_map.get(r.ticker, np.nan)
        endpx = last_map.get(r.ticker, np.nan)
        min_ret = (lo / p1 - 1 if (np.isfinite(lo) and lo > 0 and p1 > 0)
                   else np.nan)
        tot = (endpx / p1 - 1 + dy
               if (np.isfinite(endpx) and endpx > 0 and p1 > 0) else np.nan)
        tot_tax = tot - TAX_RATE * dy if np.isfinite(tot) else np.nan
        rows.append({"year": t[:4], "bucket": dd_bucket(r.r1y), "group": grp,
                     "payout": pay if pay is not None else "무배당",
                     "min_ret": min_ret,
                     "deep": ((1.0 if min_ret <= -0.30 else 0.0)
                              if np.isfinite(min_ret) else np.nan),
                     "tot": tot, "tot_tax": tot_tax})
    return rows


def _h1_agg(df: pd.DataFrame, col: str, order: list[str]) -> pd.DataFrame:
    """연도별 셀 통계의 연도 평균 (n 합, 연도 수 포함)."""
    idx = pd.MultiIndex.from_product([H1_BUCKETS, order], names=["bucket", col])
    cols = ["n", "years", "min_ret", "deep", "tot", "tot_med", "tot_p10",
            "tot_tax"]
    if df is None or df.empty:
        return pd.DataFrame(index=idx, columns=cols)
    y = df.groupby(["year", "bucket", col]).agg(
        n=("tot", "size"), min_ret=("min_ret", "mean"), deep=("deep", "mean"),
        tot=("tot", "mean"), tot_med=("tot", "median"),
        tot_p10=("tot", lambda s: s.quantile(0.10)),
        tot_tax=("tot_tax", "mean")).reset_index()
    a = y.groupby(["bucket", col]).agg(
        n=("n", "sum"), years=("n", "size"), min_ret=("min_ret", "mean"),
        deep=("deep", "mean"), tot=("tot", "mean"), tot_med=("tot_med", "mean"),
        tot_p10=("tot_p10", "mean"), tot_tax=("tot_tax", "mean"))
    return a.reindex(idx)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)
    dps = load_dps()
    dps["cik"] = _cik_str(dps["cik"])
    dps = dps[dps["cik"].notna()].reset_index(drop=True)
    companies = _load_companies()
    dates = _trading_dates()
    rebs = _rebalance_days(dates)
    splits = _load_splits()
    print(f"[info] dates {dates[0]}~{dates[-1]} n={len(dates)}"
          f" rebs={len(rebs)}", flush=True)
    bal, ttm = load_std(items=list(FIN_TAGS))
    cand_by_year, v0, v1, v1b = {}, {}, {}, {}
    for t in rebs:
        c = candidates(t, dps, companies, dates, splits, bal, ttm)
        cand_by_year[t] = c
        v0[t] = _select_top(c)
        v1[t] = select_v1(c, -0.50)
        v1b[t] = select_v1(c, -0.20)
        print(f"[info] {t} pool={len(c)} v0={len(v0[t])}"
              f" v1={len(v1[t])} v1b={len(v1b[t])}", flush=True)
    p0 = {s: {t: _select_random(cand_by_year[t], s) for t in rebs}
          for s in range(P0_DRAWS)}
    union = sorted({tk for d in [v0, v1, v1b, *p0.values()]
                    for picks in d.values() for tk in picks})
    start = dates[dates.index(rebs[0]) - 1]
    idx = [d for d in dates if d >= start]
    dps_by_year = {t: dict(zip(c["ticker"], [v[-1] for v in c["dps3"]]))
                   for t, c in cand_by_year.items()}
    close, rets, n_clip = _returns(union, idx, rebs, dps_by_year)
    _, rets_tax, n_clip_tax = _returns(union, idx, rebs, dps_by_year,
                                      div_mult=DIV_MULT_TAX)
    print(f"[info] clip |r|>50%: {n_clip} (세후 {n_clip_tax})", flush=True)
    w0, w1, w1b = (_weights(idx, union, v0), _weights(idx, union, v1),
                   _weights(idx, union, v1b))
    r0 = portfolio_run(w0, rets, COST)["ret"]
    r1 = portfolio_run(w1, rets, COST)["ret"]
    rb = portfolio_run(w1b, rets, COST)["ret"]
    r0t = portfolio_run(w0, rets_tax, COST)["ret"]
    r1t = portfolio_run(w1, rets_tax, COST)["ret"]
    rbt = portfolio_run(w1b, rets_tax, COST)["ret"]
    s0, s1, sb = summary(r0), summary(r1), summary(rb)
    s0t, s1t, sbt = summary(r0t), summary(r1t), summary(rbt)
    spy = load_etf_tr(["SPY"])["SPY"].reindex(idx).fillna(0.0)
    spyt = spy - TAX_RATE * _spy_div_yield(idx)
    ss, sst = summary(spy), summary(spyt)
    yt = pd.DataFrame({"V0": yearly(r0), "V1": yearly(r1), "V1b": yearly(rb),
                       "SPY": yearly(spy)})
    pooled, yw, n_a1_unknown = _a1(rebs, dates, v0, cand_by_year, dps, splits, close)
    a2_pre = _a2_contrib(rebs, v0, cand_by_year, rets)
    a2_tax = _a2_contrib(rebs, v0, cand_by_year, rets_tax)
    a2_picks = _a2_bucket_picks(rebs, v0, cand_by_year)
    a2_rows = []
    for b in BUCKETS:
        cols = sorted({tk for picks in a2_picks[b].values() for tk in picks})
        kavg = (sum(len(a2_picks[b].get(t, [])) for t in rebs) / len(rebs)
                if rebs else 0.0)
        wb = _weights(idx, cols, a2_picks[b])
        for yi, t in enumerate(rebs):
            if not a2_picks[b].get(t):
                nxt = rebs[yi + 1] if yi + 1 < len(rebs) else None
                seg = [d for d in idx
                       if d >= t and (nxt is None or d < nxt)]
                if seg:
                    wb.loc[seg, :] = 0.0
        a2_rows.append((b, summary(portfolio_run(wb, rets[cols], COST)["ret"]),
                        summary(portfolio_run(wb, rets_tax[cols], COST)["ret"]),
                        kavg))
    h1_rows = []
    for yi in range(len(rebs) - 1):
        h1_rows += _h1_year(rebs[yi], rebs[yi + 1], dps, companies, dates,
                            splits, bal, ttm)
    h1_df = pd.DataFrame(h1_rows)
    print(f"[info] h1 rows={len(h1_df)}", flush=True)
    h1_grp = _h1_agg(h1_df, "group", H1_GROUPS)
    h1_pay = _h1_agg(h1_df, "payout", PAYOUT_ORDER)
    draws = []
    for s in range(P0_DRAWS):
        cols = sorted({tk for picks in p0[s].values() for tk in picks})
        if not cols:
            draws.append(0.0)
        else:
            draws.append(summary(portfolio_run(
                _weights(idx, cols, p0[s]), rets[cols], COST)["ret"])["cagr"])
        if (s + 1) % 50 == 0:
            print(f"[info] p0 {s + 1}/{P0_DRAWS}", flush=True)
    pmean, ppct = placebo_percentile(s0["cagr"], draws)
    last = idx[-1]
    L = ["# 미국 안정배당 고배당률 백테스트 결과", "",
         "생존편향: 현재 상장 종목만 — 결과는 상한", "",
         "## 적용값 (PLAN 1절)", "", "| 항목 | 값 |", "|---|---|"]
    for k, v in [
            ("유니버스", "us_facts.companies 현재 상장(is_listed=1)"
                        " + us_ohlcv.ohlcv에 가격 있는 종목"),
            ("제외", "SIC 67xx, SIC 없음/0"),
            ("배당 안정 조건", "최근 3개 FY DPS 모두 >0,"
                              " FY2 ≥ 0.95·FY1, FY3 ≥ 0.95·FY2"),
            ("선정", "시가배당률(FY3 DPS ÷ 리밸런싱 전일 종가) 상위 30개"),
            ("폭락 종목", "제외하지 않음"),
            ("비중", "같은 비중(1/n)"),
            ("리밸런싱", "연 1회, 매년 4월 첫 거래일"),
            ("정보 시점", "DPS·재무 filed < 리밸런싱일만 사용"),
            ("비용", "편도 0.175% (cost=0.00175)"),
            ("비교", "SPY 총수익"),
            ("기간", f"2013-04 ~ {last[:4]}-{last[4:6]}"),
            ("유동성", "전 20거래일 평균 거래대금 ≥ $1M, 전일 종가 ≥ $1"),
            ("V1", "V0 후보 중 직전 1년 −50% 초과 낙폭이면서 현금 미감당 제외 후 상위 30"),
            ("V1b", "V1과 같고 낙폭 기준 −20%"),
            ("세후", "배당 15% 원천징수 (개별주 배당 가산 ×0.85,"
                     " SPY는 일별 배당÷전일 종가 ×0.15 차감 근사)")]:
        L.append(f"| {k} | {v} |")
    L += ["", "## 전략 요약", "",
          "| 전략 | CAGR | Vol | Sharpe | MDD | Calmar | Final |",
          "|---|---|---|---|---|---|---|"]
    for name, s in [("V0", s0), ("V0 세후", s0t), ("V1", s1),
                    ("V1 세후", s1t), ("V1b", sb), ("V1b 세후", sbt),
                    ("SPY", ss), ("SPY 세후", sst)]:
        L.append(f"| {name} | {_pct(s['cagr'])} | {_pct(s['vol'])}"
                 f" | {_num(s['sharpe'])} | {_pct(s['mdd'])}"
                 f" | {_num(s['calmar'])} | {_num(s['final'], 4)} |")
    L += ["", "## 연도별 수익률 (세전)", "", "| 연도 | V0 | V1 | V1b | SPY |",
          "|---|---|---|---|---|"]
    for y in sorted(yt.index):
        L.append(f"| {y} | {_pct(yt.at[y, 'V0'])} | {_pct(yt.at[y, 'V1'])}"
                 f" | {_pct(yt.at[y, 'V1b'])} | {_pct(yt.at[y, 'SPY'])} |")
    L += ["", "## 연도별 선정 30종목 (V0)"]
    for t in rebs:
        picks = v0[t]
        L += ["", f"### {t[:4]} (n={len(picks)})", "",
              "| ticker | yld | r1y |", "|---|---|---|"]
        crows = cand_by_year[t].set_index("ticker") if picks else None
        for tk in picks:
            r = crows.loc[tk]
            L.append(f"| {tk} | {_pct(r['yld'])} | {_pct(r['r1y'])} |")
    L += ["", "## A1: 낙폭 구간별 다음 1년 성과 (V0)", "",
          "개별 종목 배당락 데이터가 없어 보유 중 배당은 FY3 DPS ÷ 252 일할 근사.",
          "마지막 보유 연도는 다음 리밸런싱이 없어 제외.", "",
          "| bucket | n | 평균 총수익 | 세후 평균 | 중앙값 | cut 비율 |",
          "|---|---|---|---|---|---|"]
    for b in BUCKETS:
        L.append(f"| {b} | {_int(pooled.at[b, 'n'])}"
                 f" | {_pct(pooled.at[b, 'mean'])}"
                 f" | {_pct(pooled.at[b, 'mean_tax'])}"
                 f" | {_pct(pooled.at[b, 'med'])}"
                 f" | {_pct(pooled.at[b, 'cut_rate'])} |")
    L += ["", "### 연도 가중 (연도별 평균의 평균)", "",
          "| bucket | 연도 수 | 평균 총수익 | 세후 평균 | cut 비율 |",
          "|---|---|---|---|---|"]
    for b in BUCKETS:
        L.append(f"| {b} | {_int(yw.at[b, 'years'])}"
                 f" | {_pct(yw.at[b, 'mean_return'])}"
                 f" | {_pct(yw.at[b, 'mean_tax'])}"
                 f" | {_pct(yw.at[b, 'cut_rate'])} |")
    L += ["", f"cut 판정 불가(다음 FY 미공시) {n_a1_unknown}건"]
    L += ["", "## A2: V0 구간별 분해", "",
          "기여 = Σ종목 구간 총수익 ÷ 그해 V0 종목 수 (비용 무시 근사)."
          " 구간 = 리밸런싱일 ~ 다음 리밸런싱 전일, 마지막 보유 연도 제외.",
          "합계 = 세 구간 합 (미분류 제외).", ""]
    for title, adf in [("기여 (세전)", a2_pre), ("기여 (세후)", a2_tax)]:
        L += [f"### {title}", "",
              "| 연도 | 0~-20% | -20~-50% | -50%초과 | 미분류 | 합계 |",
              "|---|---|---|---|---|---|"]
        for _, r in adf.iterrows():
            L.append(f"| {r['year']} | {_pct(r['0~-20%'])}"
                     f" | {_pct(r['-20~-50%'])} | {_pct(r['-50%초과'])}"
                     f" | {_pct(r['미분류'])} | {_pct(r['합계'])} |")
        if not adf.empty:
            m = adf[BUCKETS + ["미분류", "합계"]].mean(numeric_only=True)
            L.append(f"| 평균 | {_pct(m['0~-20%'])} | {_pct(m['-20~-50%'])}"
                     f" | {_pct(m['-50%초과'])} | {_pct(m['미분류'])}"
                     f" | {_pct(m['합계'])} |")
        L.append("")
    L += ["### 구간별 하위 포트폴리오 (매년 V0 중 그 구간만 1/k, 비용 동일,"
          " 0개면 현금)", "",
          "| 구간 | | CAGR | Vol | Sharpe | MDD | 연평균 보유 수 |",
          "|---|---|---|---|---|---|---|"]
    for b, spre, stax, kavg in a2_rows:
        L.append(f"| {b} | 세전 | {_pct(spre['cagr'])} | {_pct(spre['vol'])}"
                 f" | {_num(spre['sharpe'])} | {_pct(spre['mdd'])}"
                 f" | {_num(kavg, 1)} |")
        L.append(f"| {b} | 세후 | {_pct(stax['cagr'])} | {_pct(stax['vol'])}"
                 f" | {_num(stax['sharpe'])} | {_pct(stax['mdd'])}"
                 f" | {_num(kavg, 1)} |")
    L += ["", "## H1: 폭락 후 하방 가설", "",
          "판정: ①이 ④보다 최저점·−30% 비율이 좋으면 배당 하방 지지,"
          " ①이 ②보다 좋으면 현금흐름 하방 지지.",
          "생존편향은 상폐가 많을 ④를 실제보다 좋게 만듦 → ①이 이겨도 보수적 결론.",
          "",
          "매년 4월 전 상장 종목(제외·유동성 조건 동일) 중 직전 1년 −20% 초과 낙폭 종목,"
          " 배당 무관. 진입 = 리밸런싱 전일 종가,"
          " 구간 = 리밸런싱일 ~ 다음 리밸런싱 전일 (마지막 리밸런싱 제외)."
          " 총수익 = 구간 말 종가 수익 + 최신 DPS(분할 보정) ÷ 진입가 (①②③만),"
          " 세후 = 총수익 − 0.15 × 배당분. 집계 = 연도별 그룹 평균 → 연도 평균,"
          " n = 전 기간 합.", ""]
    for title, htab, glabel, gorder in [
            ("낙폭 구간 × 배당 그룹", h1_grp, "그룹", H1_GROUPS),
            ("낙폭 구간 × 배당성향", h1_pay, "성향", PAYOUT_ORDER)]:
        L += [f"### {title}", "",
              f"| 낙폭 | {glabel} | n | 연도 수 | min_ret 평균 | deep 비율"
              " | tot 평균 | tot 중앙값 | tot 하위10% | tot_tax 평균 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for b in H1_BUCKETS:
            for g in gorder:
                h = htab.loc[(b, g)]
                L.append(f"| {b} | {g} | {_int(h['n'])} | {_int(h['years'])}"
                         f" | {_pct(h['min_ret'])} | {_pct(h['deep'])}"
                         f" | {_pct(h['tot'])} | {_pct(h['tot_med'])}"
                         f" | {_pct(h['tot_p10'])} | {_pct(h['tot_tax'])} |")
        L.append("")
    L += ["", "## P0: 무작위 대조 (세전)", "",
          f"- draws: {P0_DRAWS}회 (seed 0..{P0_DRAWS - 1}),"
          " 매년 후보 중 무작위 30개",
          f"- draws 평균 CAGR: {_pct(pmean)}",
          f"- V0 백분위: {_num(ppct, 1)}", "", "## 메모", "",
          f"- 일 수익 ±50% 초과 클립: 세전 {n_clip}건, 세후 {n_clip_tax}건 → 0", ""]
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(L), encoding="utf-8")
    print(f"[info] wrote {RESULTS_PATH}", flush=True)


if __name__ == "__main__":
    main()
