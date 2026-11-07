"""녹화 parquet → 30초 칸. 경계 b 의 칸 값은 rt < b 데이터만 쓴다(미래 금지)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

STEP_S = 30
STEP_NS = STEP_S * 1_000_000_000

FEATURES = [
    "ret_1", "ret_2", "ret_10", "ret_30",
    "spread_bp", "imb_1", "imb_5",
    "vol_rel", "buy_ratio", "t_frac",
]

_AP = [f"ap{i}" for i in range(1, 11)]
_BP = [f"bp{i}" for i in range(1, 11)]
_AQ = [f"aq{i}" for i in range(1, 11)]
_BQ = [f"bq{i}" for i in range(1, 11)]
_SNAP_COLS = _AP + _BP + _AQ + _BQ


def _load_market_session():
    try:
        from etl.scripts import market_session
        return market_session
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "scripts"))
        import market_session
        return market_session


def grid(day: str) -> np.ndarray:
    """그날 칸 경계 epoch ns 배열. 첫 경계 = 09:00:30, 마지막 = 15:19:30, 30초 간격(양끝 포함)."""
    ms = _load_market_session()
    start = ms.at(day, "09:00:30")
    end = ms.at(day, "15:19:30")
    start_ns = int(start.timestamp()) * 1_000_000_000 + start.microsecond * 1000
    end_ns = int(end.timestamp()) * 1_000_000_000 + end.microsecond * 1000
    count = (end_ns - start_ns) // STEP_NS + 1
    return (start_ns + STEP_NS * np.arange(count)).astype(np.int64)


def _read_parquet(path: Path) -> pd.DataFrame:
    import duckdb
    con = duckdb.connect()
    try:
        return con.execute("SELECT * FROM read_parquet(?)", [str(path)]).fetchdf()
    finally:
        con.close()


def load_day(day: str, root: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(trade, book, gaps). root 기본 = orderbook_dir(). gaps 파일 없으면 빈 DF."""
    if root is None:
        try:
            from new_etf_insight.data_paths import orderbook_dir
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "src"))
            from new_etf_insight.data_paths import orderbook_dir
        root = orderbook_dir()
    sub = Path(root) / day[:4] / day[4:6]
    trade = _read_parquet(sub / f"trade_{day}.parquet")
    book = _read_parquet(sub / f"book_{day}.parquet")
    gaps_path = sub / f"gaps_{day}.parquet"
    gaps = _read_parquet(gaps_path) if gaps_path.exists() else pd.DataFrame()
    return trade, book, gaps


def _gap_bad(bounds: np.ndarray, gaps: pd.DataFrame) -> np.ndarray:
    n = len(bounds)
    if gaps is None or gaps.empty or "start_rt" not in gaps or "end_rt" not in gaps:
        return np.zeros(n, dtype=bool)
    gs = gaps["start_rt"].to_numpy(dtype=np.int64)
    ge = gaps["end_rt"].to_numpy(dtype=np.int64)
    return ((gs[:, None] < bounds[None, :]) & (ge[:, None] > bounds[None, :] - STEP_NS)).any(axis=0)


def _planes(aq: np.ndarray, bq: np.ndarray, buy_vol: np.ndarray, sell_vol: np.ndarray) -> np.ndarray:
    """(n, 4, 20, 20) float32. 행 = 매도10~1·매수1~10, 시간축 끝 = 현재 칸."""
    n = aq.shape[0]
    qty = np.concatenate([aq[:, ::-1], bq], axis=1).astype(np.float64)
    tot = qty.sum(axis=1)
    buy = buy_vol.astype(np.float64)
    sell = sell_vol.astype(np.float64)
    vol = buy + sell
    out = np.zeros((n, 4, 20, 20), dtype=np.float32)
    for tau in range(20):
        c = np.arange(n) - (19 - tau)
        valid = c >= 0
        cv = c[valid]
        q = np.zeros((n, 20))
        q[valid] = qty[cv]
        p = np.zeros((n, 20))
        hp = valid & (c >= 1)
        p[hp] = qty[c[hp] - 1]
        s = np.zeros(n)
        s[valid] = tot[cv]
        nz = valid & (s != 0)
        out[nz, 0, :, tau] = q[nz] / s[nz, None]
        out[nz, 1, :, tau] = (q[nz] - p[nz]) / s[nz, None]
        v = np.zeros(n)
        v[valid] = vol[cv]
        nt = v != 0
        bb = np.zeros(n)
        bb[valid] = buy[cv]
        ss = np.zeros(n)
        ss[valid] = sell[cv]
        out[nt, 2, 9, tau] = bb[nt] / v[nt]
        out[nt, 3, 10, tau] = ss[nt] / v[nt]
    return out


def build(day: str, trade: pd.DataFrame, book: pd.DataFrame, gaps: pd.DataFrame,
          planes: bool = False) -> dict[str, dict]:
    """종목별 칸 데이터 {code: {...}}. 칸 하나라도 무효면 제외, "_excluded"에 [(code, 이유)]."""
    bounds = grid(day)
    n = len(bounds)
    bad_cell = _gap_bad(bounds, gaps)
    codes = set()
    if "code" in book.columns:
        codes.update(book["code"].dropna().unique().tolist())
    if "code" in trade.columns:
        codes.update(trade["code"].dropna().unique().tolist())
    out: dict[str, dict] = {}
    excluded = []
    for code in sorted(codes):
        bsub = book[book["code"] == code].sort_values("rt")
        tsub = trade[trade["code"] == code].sort_values("rt")
        brt = bsub["rt"].to_numpy(dtype=np.int64)
        bmat = bsub[_SNAP_COLS].to_numpy(dtype=np.int64)
        trt = tsub["rt"].to_numpy(dtype=np.int64)
        tq = tsub["qty"].to_numpy(dtype=np.int64)
        ask1 = np.empty(n)
        bid1 = np.empty(n)
        ap = np.empty((n, 10), dtype=np.int64)
        bp = np.empty((n, 10), dtype=np.int64)
        aq = np.empty((n, 10), dtype=np.int64)
        bq = np.empty((n, 10), dtype=np.int64)
        buy_vol = np.zeros(n, dtype=np.int64)
        sell_vol = np.zeros(n, dtype=np.int64)
        bad = None
        for i, b in enumerate(bounds):
            j = int(np.searchsorted(brt, b, side="left")) - 1
            if j < 0:
                bad = "no_book"
                break
            if bad_cell[i]:
                bad = "gap"
                break
            row = bmat[j]
            a1, b1 = row[0], row[10]
            if a1 <= 0 or b1 <= 0 or a1 <= b1:
                bad = "bad_quote"
                break
            ask1[i], bid1[i] = a1, b1
            ap[i], bp[i], aq[i], bq[i] = row[0:10], row[10:20], row[20:30], row[30:40]
            l = int(np.searchsorted(trt, b - STEP_NS, side="left"))
            r = int(np.searchsorted(trt, b, side="left"))
            if r > l:
                seg = tq[l:r]
                buy_vol[i] = seg[seg > 0].sum()
                sell_vol[i] = -seg[seg < 0].sum()
        if bad is not None:
            excluded.append((code, bad))
            continue
        mid = (ask1 + bid1) / 2.0
        logmid = np.log(mid)
        cols = []
        for k in (1, 2, 10, 30):
            r = np.zeros(n)
            if k < n:
                r[k:] = logmid[k:] - logmid[:-k]
            cols.append(r)
        cols.append((ask1 - bid1) / mid * 1e4)
        for w in (1, 5):
            sb = bq[:, :w].sum(axis=1)
            sa = aq[:, :w].sum(axis=1)
            den = sb + sa
            imb = np.zeros(n)
            m = den != 0
            imb[m] = (sb[m] - sa[m]) / den[m]
            cols.append(imb)
        tot = (buy_vol + sell_vol).astype(np.float64)
        mean = np.cumsum(tot) / np.arange(1, n + 1)
        vol_rel = np.ones(n)
        m = mean != 0
        vol_rel[m] = tot[m] / mean[m]
        cols.append(vol_rel)
        br = np.full(n, 0.5)
        m = tot > 0
        br[m] = buy_vol[m].astype(np.float64) / tot[m]
        cols.append(br)
        cols.append(np.zeros(n) if n <= 1 else np.arange(n) / (n - 1))
        entry = {
            "ask1": ask1, "bid1": bid1,
            "ap": ap, "bp": bp, "aq": aq, "bq": bq,
            "buy_vol": buy_vol, "sell_vol": sell_vol,
            "feat": np.column_stack(cols).astype(np.float32),
        }
        if planes:
            entry["planes"] = _planes(aq, bq, buy_vol, sell_vol)
        out[code] = entry
    out["_excluded"] = excluded
    return out
