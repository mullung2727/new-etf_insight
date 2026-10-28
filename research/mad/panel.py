"""MAD 리서치 패널 — 사양은 research/private/mad/SPEC.md.

일봉 계산은 research.backtest_daily 재사용(재구현 금지).
t 값은 research.ccc.sort.nw_tstat(lags=6).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from research.backtest_daily import adjust
from research.backtest_daily import data as kr_data
from research.backtest_daily.data_us import US_OHLCV_DB
from research.backtest_daily.universe import SPAC_RE

ROOT = Path(__file__).resolve().parent
PRIVATE_DIR = ROOT.parent / "private" / "mad"
CACHE_DIR = PRIVATE_DIR / "cache"
KR_CACHE = CACHE_DIR / "kr_panel.pkl"
US_CACHE = CACHE_DIR / "us_panel.pkl"
US_META = CACHE_DIR / "us_panel.meta.json"

_MA_SPAN = {5: 5, 20: 22, 21: 22, 60: 65, 200: 219}
_HI252_SPAN = 275
_US_NAME_RE = re.compile(r"acquisition|spac|warrant|\bunits?\b|\brights?\b",
                         re.IGNORECASE)


def _block_parts(v: np.ndarray, start: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """종목 블록 안 누적합(NaN은 0으로) + NaN 개수 누적."""
    vv = np.where(np.isfinite(v), v, 0.0)
    g = np.cumsum(vv)
    b = g - (g[start] - vv[start])
    m = (~np.isfinite(v)).astype(np.float64)
    gm = np.cumsum(m)
    nb = gm - (gm[start] - m[start])
    return b, nb


def _roll_mean(b: np.ndarray, nb: np.ndarray, pos: np.ndarray, ms: np.ndarray,
               n: int, span: int | None) -> np.ndarray:
    """n행 평균. 창 미충족·창 안 NaN·ms 범위 초과는 NaN."""
    nrows = len(b)
    out = np.full(nrows, np.nan)
    ok = pos >= n - 1
    if not bool(np.any(ok)):
        return out
    idx = np.arange(nrows)
    back = np.where(pos >= n, idx - n, 0)
    has_pre = pos >= n
    sums = np.where(has_pre, b - b[back], b)
    wnan = np.where(has_pre, nb - nb[back], nb)
    good = ok & (wnan == 0)
    if span is not None:
        first = np.where(ok, idx - n + 1, 0)
        wide = ms - ms[first]
        good &= np.isfinite(wide) & (wide <= span)
    out[good] = sums[good] / float(n)
    return out


_FEATURE_REQ = ("ticker", "date", "ms", "r", "raw_close", "tv", "me")
_FEATURE_NEW = ("P", "MA5", "MA20", "MA21", "MA60", "MA200", "hi252", "tv20",
                "age", "mad", "align", "above200", "mds", "hi52")


def features(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 → 이평·신호. 입력 행 순서 유지, 열만 추가.

    신호(mad·align·above200·mds·hi52)는 종목 내 한 행 전 값(전일 기준).
    align·above200·mds는 1.0/0.0/NaN float.
    """
    missing = [c for c in _FEATURE_REQ if c not in df.columns]
    if missing:
        raise KeyError(f"need columns: {missing}")
    out = df.copy()
    n = len(out)
    if n == 0:
        for c in _FEATURE_NEW:
            out[c] = pd.Series(dtype=float)
        return out
    tic = df["ticker"].astype(str).to_numpy()
    dat = df["date"].astype(str).to_numpy()
    tc, _ = pd.factorize(tic, sort=True)
    dc, _ = pd.factorize(dat, sort=True)
    order = np.lexsort((np.arange(n), dc, tc))
    st = tic[order]
    ms = pd.to_numeric(df["ms"], errors="coerce").to_numpy(dtype=float)[order]
    r = pd.to_numeric(df["r"], errors="coerce").to_numpy(dtype=float)[order]
    tv = pd.to_numeric(df["tv"], errors="coerce").to_numpy(dtype=float)[order]

    P = np.asarray(adjust.adj_price(st, r), dtype=float)
    new_block = np.ones(n, dtype=bool)
    new_block[1:] = st[1:] != st[:-1]
    start = np.maximum.accumulate(np.where(new_block, np.arange(n), 0))
    pos = np.arange(n) - start

    bP, nP = _block_parts(P, start)
    ma = {w: _roll_mean(bP, nP, pos, ms, w, _MA_SPAN[w])
          for w in (5, 20, 21, 60, 200)}
    btv, ntv = _block_parts(tv, start)
    tv20 = _roll_mean(btv, ntv, pos, ms, 20, None)
    # 연결 시리즈 rolling max + 종목 경계·커버리지 마스크 (groupby 없음).
    hi = pd.Series(P).rolling(252, min_periods=252).max().to_numpy(dtype=float)
    ok252 = pos >= 251
    hi[~ok252] = np.nan
    f252 = np.where(ok252, np.arange(n) - 251, 0)
    wide252 = ms - ms[f252]
    hi[ok252 & (~np.isfinite(wide252) | (wide252 > _HI252_SPAN))] = np.nan

    ma5, ma20, ma21, ma60, ma200 = (ma[5], ma[20], ma[21], ma[60], ma[200])
    with np.errstate(divide="ignore", invalid="ignore"):
        mad_raw = ma21 / ma200
        hi52_raw = P / hi
    al_ok = np.isfinite(ma5) & np.isfinite(ma20) & np.isfinite(ma60)
    align_raw = np.where(al_ok, ((ma5 > ma20) & (ma20 > ma60)).astype(float),
                         np.nan)
    above_raw = np.where(np.isfinite(ma200), (P > ma200).astype(float), np.nan)
    mds_raw = np.where(np.isfinite(ma21) & np.isfinite(ma200),
                       (ma21 > ma200).astype(float), np.nan)

    same = np.zeros(n, dtype=bool)
    same[1:] = st[1:] == st[:-1]

    def _shift(v: np.ndarray) -> np.ndarray:
        s = np.full(n, np.nan)
        s[1:] = np.where(same[1:], v[:-1], np.nan)
        return s

    inv = np.empty(n, dtype=int)
    inv[order] = np.arange(n)
    vals = {"P": P, "MA5": ma5, "MA20": ma20, "MA21": ma21, "MA60": ma60,
            "MA200": ma200, "hi252": hi, "tv20": tv20,
            "mad": _shift(mad_raw), "align": _shift(align_raw),
            "above200": _shift(above_raw), "mds": _shift(mds_raw),
            "hi52": _shift(hi52_raw)}
    for c, v in vals.items():
        out[c] = v[inv]
    out["age"] = pos[inv].astype(int)
    return out


def month_ends(dates) -> list[str]:
    """거래일 목록 → 연월별 마지막 거래일, 오름차순."""
    if dates is None:
        return []
    last: dict[str, str] = {}
    for d in sorted({str(x) for x in dates}):
        last[d[:6]] = d
    return [last[k] for k in sorted(last)]


def monthly_panel(feat: pd.DataFrame, fdates, H: int = 6) -> pd.DataFrame:
    """형성일 스냅샷 + 보유월 수익 ret_1..H, mom(t-12~t-1), ret_1m.

    일수익 NaN은 0(상폐·정지 후 가격 고정). 구간에 행 없어도 0,
    fdates 끝을 넘으면 NaN.
    """
    ret_cols = [f"ret_{k}" for k in range(1, H + 1)]
    base = ["ym", "ticker", "raw_close", "tv20", "me", "mad", "align",
            "above200", "mds", "hi52", "mom", "ret_1m", "age", *ret_cols]
    cols = base + (["market"] if feat is not None and "market" in feat.columns
                   else [])
    farr = np.array(sorted({str(d) for d in (fdates or [])}))
    m = len(farr)
    if feat is None or len(feat) == 0 or m == 0:
        return pd.DataFrame(columns=cols)
    s = feat.copy()
    s["ticker"] = s["ticker"].astype(str)
    s["date"] = s["date"].astype(str)
    d = s["date"].to_numpy()
    tk_all = s["ticker"].to_numpy()
    iv = np.searchsorted(farr, d, side="left") - 1
    r = pd.to_numeric(s["r"], errors="coerce").to_numpy(dtype=float)
    l = np.log1p(np.clip(np.nan_to_num(r, nan=0.0), -0.999, None))
    comp: dict = {}
    hold = (iv >= 0) & (iv < m - 1)
    if bool(np.any(hold)):
        gs = pd.Series(l[hold]).groupby([iv[hold], tk_all[hold]]).sum()
        comp = {(int(k[0]), str(k[1])): float(v) for k, v in gs.items()}
    f = s[np.isin(d, farr)].copy()
    if f.empty:
        return pd.DataFrame(columns=cols)
    f["_fi"] = np.searchsorted(farr, f["date"].to_numpy(), side="left")
    f = f.sort_values(["_fi", "ticker"], kind="mergesort")
    f = f.drop_duplicates(["_fi", "ticker"], keep="last").reset_index(drop=True)
    fi = f["_fi"].to_numpy()
    tk = f["ticker"].astype(str).to_numpy()
    get = comp.get
    n = len(f)
    rets = np.full((n, H), np.nan)
    for k in range(1, H + 1):
        jj = fi + k - 1
        ok = jj <= m - 2
        rets[:, k - 1] = np.array(
            [np.expm1(get((int(j), t), 0.0)) if o else np.nan
             for j, t, o in zip(jj, tk, ok)])
    mom = np.full(n, np.nan)
    m1 = np.full(n, np.nan)
    for i in range(n):
        if fi[i] >= 1:
            m1[i] = np.expm1(get((int(fi[i]) - 1, tk[i]), 0.0))
        if fi[i] >= 12:
            ssum = 0.0
            for j in range(int(fi[i]) - 12, int(fi[i]) - 1):
                ssum += get((j, tk[i]), 0.0)
            mom[i] = np.expm1(ssum)
    out = pd.DataFrame({"ym": np.array([int(farr[i][:6]) for i in fi]),
                        "ticker": tk, "mom": mom, "ret_1m": m1})
    for c in ("raw_close", "tv20", "me", "mad", "align", "above200", "mds",
              "hi52", "age"):
        out[c] = f[c].to_numpy()
    if "market" in f.columns:
        out["market"] = f["market"].to_numpy()
    for k in range(1, H + 1):
        out[f"ret_{k}"] = rets[:, k - 1]
    return out[cols].reset_index(drop=True)


def kr_panel(cache: bool = True) -> pd.DataFrame:
    """한국 월별 패널 (+univ·liq). 캐시 있으면 읽기."""
    if cache and KR_CACHE.exists():
        return pd.read_pickle(KR_CACHE)
    px = kr_data.load_px()
    r = np.asarray(adjust.adj_returns(px, use_krx_reference=True), dtype=float)
    df = pd.DataFrame({
        "ticker": px["ticker"].astype(str).to_numpy(),
        "date": px["date"].astype(str).to_numpy(),
        "ms": px["ms"].to_numpy(),
        "r": r,
        "raw_close": pd.to_numeric(px["close"], errors="coerce").to_numpy(
            dtype=float),
        "tv": pd.to_numeric(px["trading_value"], errors="coerce").to_numpy(
            dtype=float),
        "me": pd.to_numeric(px["market_cap"], errors="coerce").to_numpy(
            dtype=float),
        "market": px["market"].astype(str).to_numpy(),
    })
    feat = features(df)
    panel = monthly_panel(feat, month_ends(kr_data.market_dates()), H=6)
    names = kr_data.stock_names()
    univ = (~panel["market"].astype(str).str.contains("KONEX", na=False)
            & panel["ticker"].astype(str).str.endswith("0")
            & ~panel["ticker"].astype(str).map(
                lambda t: bool(SPAC_RE.search(str(names.get(t, "")))))
            & (panel["raw_close"] >= 1000)
            & (panel["me"] > 0)
            & (panel["age"] >= 252)
            & panel["mad"].notna())
    panel["univ"] = univ.to_numpy(dtype=bool)
    panel["liq"] = (univ & (panel["tv20"] >= 1e9)).to_numpy(dtype=bool)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_pickle(KR_CACHE)
    return panel


def _us_split_factors(tic: np.ndarray, dat: np.ndarray,
                      splits: pd.DataFrame) -> np.ndarray:
    """행별 분할 팩터 = split.date > 행 date 인 ratio 곱. (ticker,date) 정렬 가정."""
    n = len(tic)
    factor = np.ones(n)
    if splits is None or len(splits) == 0:
        return factor
    sp = splits.copy()
    sp["ticker"] = sp["ticker"].astype(str)
    sp["date"] = sp["date"].astype(str)
    sp["ratio"] = pd.to_numeric(sp["ratio"], errors="coerce")
    sp = sp.dropna(subset=["ratio"])
    sp = sp[sp["ratio"] > 0]
    if sp.empty:
        return factor
    ch = np.flatnonzero(tic[1:] != tic[:-1]) + 1 if n else np.empty(0, int)
    starts = np.concatenate(([0], ch)).astype(int)
    blk = {t: k for k, t in enumerate(tic[starts])}
    for t, g in sp.groupby("ticker", sort=False):
        k = blk.get(str(t))
        if k is None:
            continue
        lo = starts[k]
        hi = starts[k + 1] if k + 1 < len(starts) else n
        sd = g["date"].to_numpy()
        sr = g["ratio"].to_numpy(dtype=float)
        o = np.argsort(sd, kind="mergesort")
        sd, sr = sd[o], sr[o]
        suf = np.cumprod(sr[::-1])[::-1]
        j = np.searchsorted(sd, dat[lo:hi], side="right")
        jj = np.minimum(j, len(sd) - 1)
        factor[lo:hi] = np.where(j < len(sd), suf[jj], 1.0)
    return factor


def _read_us_clips():
    try:
        return int(json.loads(US_META.read_text(encoding="utf-8")).get("clips"))
    except (OSError, ValueError, TypeError):
        return None


def us_panel(cache: bool = True) -> pd.DataFrame:
    """미국 월별 패널 (+univ·liq). df.attrs["us_clips"]에 일수익 클립 건수."""
    if cache and US_CACHE.exists():
        df = pd.read_pickle(US_CACHE)
        df.attrs["us_clips"] = _read_us_clips()
        return df
    CHUNK = 1000
    con = duckdb.connect(str(US_OHLCV_DB), read_only=True)
    try:
        mkt_dates = [str(r[0]) for r in con.execute(
            "SELECT DISTINCT date FROM ohlcv ORDER BY date").fetchall()]
        splits = con.execute("SELECT ticker, date, ratio FROM splits").df()
        splits["ticker"] = splits["ticker"].astype(str)
        names = {str(c): str(nm) for c, nm in
                 con.execute("SELECT code, name FROM stock_names").fetchall()}
        tickers = [str(r[0]) for r in con.execute(
            """
            SELECT DISTINCT ticker FROM ohlcv
            WHERE close > 0 AND volume > 0
              AND market IN ('NASDAQ', 'NYSE', 'AMEX')
            ORDER BY ticker
            """).fetchall()]
        fdates = month_ends(mkt_dates)
        panels = []
        clips = 0
        n_chunk = (len(tickers) + CHUNK - 1) // CHUNK
        for i in range(n_chunk):
            tk = tickers[i * CHUNK:(i + 1) * CHUNK]
            o = con.execute(
                """
                WITH mkt AS (SELECT DISTINCT date FROM ohlcv),
                     m AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS ms
                           FROM mkt)
                SELECT o.date, o.ticker, o.market, o.close, o.volume, m.ms
                FROM ohlcv o JOIN m USING (date)
                WHERE o.close > 0 AND o.volume > 0
                  AND o.market IN ('NASDAQ', 'NYSE', 'AMEX')
                  AND o.ticker IN (SELECT * FROM UNNEST(?))
                ORDER BY o.ticker, o.date
                """,
                [tk],
            ).df()
            o["ticker"] = o["ticker"].astype(str)
            o["date"] = o["date"].astype(str)
            o = o.sort_values(["ticker", "date"],
                              kind="mergesort").reset_index(drop=True)
            tic = o["ticker"].to_numpy()
            dat = o["date"].to_numpy()
            close = o["close"].to_numpy(dtype=float)
            vol = o["volume"].to_numpy(dtype=float)
            ms = o["ms"].to_numpy()
            sp = splits[splits["ticker"].isin(tk)]
            raw_close = close * _us_split_factors(tic, dat, sp)
            n = len(o)
            contig = np.zeros(n, dtype=bool)
            if n:
                contig[1:] = ((tic[1:] == tic[:-1])
                              & (ms[1:] == ms[:-1] + 1))
                stick = np.zeros(n, dtype=bool)
                stick[1:] = tic[1:] == tic[:-1]
                prev = np.where(stick, np.concatenate(([np.nan], close[:-1])),
                                np.nan)
            else:
                prev = np.full(0, np.nan)
            with np.errstate(divide="ignore", invalid="ignore"):
                r = np.where(contig & (prev > 0), close / prev - 1, np.nan)
            clips += int(np.sum(np.isfinite(r) & ((r < -0.95) | (r > 3.0))))
            r = np.clip(r, -0.95, 3.0)
            print(f"[us_panel] chunk {i + 1}/{n_chunk} rows={n} "
                  f"clips={clips}", flush=True)
            df = pd.DataFrame({
                "ticker": tic, "date": dat, "ms": ms, "r": r,
                "raw_close": raw_close, "tv": close * vol,
                "me": np.full(n, np.nan),
                "market": o["market"].astype(str).to_numpy(),
            })
            feat = features(df)
            panels.append(monthly_panel(feat, fdates, H=6))
            del o, tic, dat, close, vol, ms, sp, raw_close, prev, r, contig
            del df, feat
        if panels:
            panel = pd.concat(panels, ignore_index=True)
            panel = panel.sort_values(["ym", "ticker"],
                                      kind="mergesort").reset_index(drop=True)
        else:
            panel = monthly_panel(
                pd.DataFrame(columns=["ticker", "date", "ms", "r", "raw_close",
                                      "tv", "me", "mad", "align", "above200",
                                      "mds", "hi52", "age", "market"]),
                fdates, H=6)
    finally:
        con.close()
    ok_name = ~panel["ticker"].map(
        lambda t: bool(_US_NAME_RE.search(names.get(str(t), ""))))
    univ = (ok_name & (panel["raw_close"] >= 5) & (panel["age"] >= 252)
            & panel["mad"].notna())
    panel["univ"] = univ.to_numpy(dtype=bool)
    panel["liq"] = (univ & (panel["tv20"] >= 5e6)).to_numpy(dtype=bool)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_pickle(US_CACHE)
    US_META.write_text(json.dumps({"clips": clips}), encoding="utf-8")
    panel.attrs["us_clips"] = clips
    return panel

