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
    "rv_10", "rv_30", "ret_open", "hl_pos", "ret_prev",
    "ma20_gap", "ret5", "vol20", "vwap_gap", "ma30_gap",
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


def day_paths(day: str, root: Path | None = None) -> tuple[Path, Path, Path]:
    """그날 trade/book/gaps parquet 경로. root 기본 = orderbook_dir()."""
    if root is None:
        try:
            from new_etf_insight.data_paths import orderbook_dir
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "src"))
            from new_etf_insight.data_paths import orderbook_dir
        root = orderbook_dir()
    sub = Path(root) / day[:4] / day[4:6]
    return sub / f"trade_{day}.parquet", sub / f"book_{day}.parquet", sub / f"gaps_{day}.parquet"


def load_day(day: str, root: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(trade, book, gaps). root 기본 = orderbook_dir(). gaps 파일 없으면 빈 DF."""
    trade_p, book_p, gaps_path = day_paths(day, root)
    trade = _read_parquet(trade_p)
    book = _read_parquet(book_p)
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
    """(n, 4, 20, 20) float32. 행 = 매도10~1·매수1~10, 시간축 끝 = 현재 칸. 채널2·3 = 체결/그 칸 잔량합(매수→매도1행, 매도→매수1행)."""
    n = aq.shape[0]
    qty = np.concatenate([aq[:, ::-1], bq], axis=1).astype(np.float64)
    tot = qty.sum(axis=1)
    buy = buy_vol.astype(np.float64)
    sell = sell_vol.astype(np.float64)
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
        bb = np.zeros(n)
        bb[valid] = buy[cv]
        ss = np.zeros(n)
        ss[valid] = sell[cv]
        out[nz, 2, 9, tau] = bb[nz] / s[nz]
        out[nz, 3, 10, tau] = ss[nz] / s[nz]
    return out


def build(day: str, trade: pd.DataFrame, book: pd.DataFrame, gaps: pd.DataFrame,
          planes: bool = False, daily: dict | None = None) -> dict[str, dict]:
    """종목별 칸 데이터 {code: {...}}. _excluded=[(code, 이유)], _reasons={이유: 건수}. daily = for_day 결과."""
    bounds = grid(day)
    n = len(bounds)
    bad_cell = _gap_bad(bounds, gaps)
    codes = set()
    if "code" in book.columns:
        codes.update(book["code"].dropna().unique().tolist())
    if "code" in trade.columns:
        codes.update(trade["code"].dropna().unique().tolist())
    if book.empty or book["rt"].to_numpy(dtype=np.int64).max() < bounds[-1]:
        exc = [(c, "short_day") for c in sorted(codes)]
        return {"_excluded": exc, "_reasons": {"short_day": len(exc)} if exc else {}}
    out: dict[str, dict] = {}
    excluded = []
    for code in sorted(codes):
        bsub = book[book["code"] == code].sort_values("rt")
        tsub = trade[trade["code"] == code].sort_values("rt")
        brt = bsub["rt"].to_numpy(dtype=np.int64)
        bmat = bsub[_SNAP_COLS].to_numpy(dtype=np.int64)
        trt = tsub["rt"].to_numpy(dtype=np.int64)
        tq = tsub["qty"].to_numpy(dtype=np.int64)
        tpr = tsub["price"].to_numpy(dtype=np.float64)
        ask1 = np.empty(n)
        bid1 = np.empty(n)
        ap = np.empty((n, 10), dtype=np.int64)
        bp = np.empty((n, 10), dtype=np.int64)
        aq = np.empty((n, 10), dtype=np.int64)
        bq = np.empty((n, 10), dtype=np.int64)
        ex_ask1 = np.zeros(n, dtype=np.float64)
        ex_bid1 = np.zeros(n, dtype=np.float64)
        ex_aq1 = np.zeros(n, dtype=np.int64)
        ex_bq1 = np.zeros(n, dtype=np.int64)
        buy_vol = np.zeros(n, dtype=np.int64)
        sell_vol = np.zeros(n, dtype=np.int64)
        bin_pq = np.zeros(n, dtype=np.float64)
        bin_q = np.zeros(n, dtype=np.float64)
        stale_cells = 0
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
            a1, b1 = int(row[0]), int(row[10])
            ask_ok = a1 > 0 and int(row[20]) > 0
            bid_ok = b1 > 0 and int(row[30]) > 0
            if ask_ok and bid_ok and a1 <= b1:
                ask_ok = bid_ok = False
            if not ask_ok and not bid_ok:
                if i == 0:
                    bad = "no_book"
                    break
                ask1[i], bid1[i] = ask1[i - 1], bid1[i - 1]
                ap[i], bp[i], aq[i], bq[i] = ap[i - 1], bp[i - 1], aq[i - 1], bq[i - 1]
                stale_cells += 1
            else:
                ask1[i] = float(a1) if ask_ok else 0.0
                bid1[i] = float(b1) if bid_ok else 0.0
                ap[i], bp[i], aq[i], bq[i] = row[0:10], row[10:20], row[20:30], row[30:40]
            k = int(np.searchsorted(brt, b, side="left"))
            if k < len(brt) and brt[k] < b + STEP_NS:
                erow = bmat[k]
                ea1, eb1 = float(erow[0]), float(erow[10])
                if not (ea1 > 0 and eb1 > 0 and ea1 <= eb1):
                    ex_ask1[i], ex_bid1[i] = ea1, eb1
                    ex_aq1[i], ex_bq1[i] = int(erow[20]), int(erow[30])
            l = int(np.searchsorted(trt, b - STEP_NS, side="left"))
            r = int(np.searchsorted(trt, b, side="left"))
            if r > l:
                seg = tq[l:r]
                buy_vol[i] = seg[seg > 0].sum()
                sell_vol[i] = -seg[seg < 0].sum()
                aq_ = np.abs(seg)
                bin_q[i] = aq_.sum()
                bin_pq[i] = (tpr[l:r] * aq_).sum()
        if bad is not None:
            excluded.append((code, bad))
            continue
        both = (ask1 > 0) & (bid1 > 0)
        mid = np.where(both, (ask1 + bid1) / 2.0, np.where(ask1 > 0, ask1, bid1))
        logmid = np.log(mid)
        cols = []
        for k in (1, 2, 10, 30):
            r = np.zeros(n)
            if k < n:
                r[k:] = logmid[k:] - logmid[:-k]
            cols.append(r)
        spread = np.zeros(n)
        spread[both] = (ask1[both] - bid1[both]) / mid[both] * 1e4
        cols.append(spread)
        for w in (1, 5):
            sb = bq[:, :w].sum(axis=1)
            sa = aq[:, :w].sum(axis=1)
            den = sb + sa
            imb = np.zeros(n)
            m = den != 0
            imb[m] = (sb[m] - sa[m]) / den[m]
            cols.append(imb)
        tot = (buy_vol + sell_vol).astype(np.float64)
        # ponytail: 당일 직전 칸 평균 기준. 전일까지 20일 기준은 P2 일봉 지표와 함께 (PLAN §5)
        mean = np.zeros(n)
        if n > 1:
            mean[1:] = np.cumsum(tot)[:-1] / np.arange(1, n)
        vol_rel = np.ones(n)
        m = mean != 0
        vol_rel[m] = tot[m] / mean[m]
        cols.append(vol_rel)
        br = np.full(n, 0.5)
        m = tot > 0
        br[m] = buy_vol[m].astype(np.float64) / tot[m]
        cols.append(br)
        cols.append(np.zeros(n) if n <= 1 else np.arange(n) / (n - 1))
        r1 = np.zeros(n)
        r1[1:] = logmid[1:] - logmid[:-1]
        csq = np.zeros(n + 1)
        csq[1:] = np.cumsum(r1 ** 2)
        for k in (10, 30):
            lo = np.maximum(0, np.arange(n) - k + 1)
            cols.append(np.sqrt(csq[1:] - csq[lo]))
        cols.append(logmid - logmid[0])
        cumlo, cumhi = np.minimum.accumulate(mid), np.maximum.accumulate(mid)
        hl = np.full(n, 0.5)
        m = cumhi != cumlo
        hl[m] = (mid[m] - cumlo[m]) / (cumhi[m] - cumlo[m])
        cols.append(hl)
        dd = (daily or {}).get(code, {})
        try:
            pc = float(dd.get("prev_close", np.nan))
        except (TypeError, ValueError):
            pc = np.nan
        cols.append(np.log(mid / pc) if np.isfinite(pc) and pc > 0 else np.full(n, np.nan))
        for key in ("ma20_gap", "ret5", "vol20"):
            try:
                v = float(dd.get(key, np.nan))
            except (TypeError, ValueError):
                v = np.nan
            cols.append(np.full(n, v))
        cum_pq, cum_q = np.cumsum(bin_pq), np.cumsum(bin_q)
        vg = np.zeros(n)
        m = cum_q > 0
        vg[m] = np.log(mid[m] / (cum_pq[m] / cum_q[m]))
        cols.append(vg)
        cm = np.zeros(n + 1)
        cm[1:] = np.cumsum(mid)
        lo = np.maximum(0, np.arange(n) - 29)
        cols.append(logmid - np.log((cm[1:] - cm[lo]) / (np.arange(n) - lo + 1)))
        entry = {
            "ask1": ask1, "bid1": bid1,
            "ap": ap, "bp": bp, "aq": aq, "bq": bq,
            "ex_ask1": ex_ask1, "ex_bid1": ex_bid1,
            "ex_aq1": ex_aq1, "ex_bq1": ex_bq1,
            "buy_vol": buy_vol, "sell_vol": sell_vol,
            "feat": np.column_stack(cols).astype(np.float32),
            "stale_cells": int(stale_cells),
        }
        if planes:
            entry["planes"] = _planes(aq, bq, buy_vol, sell_vol)
        out[code] = entry
    out["_excluded"] = excluded
    reasons: dict[str, int] = {}
    for _, rsn in excluded:
        reasons[rsn] = reasons.get(rsn, 0) + 1
    out["_reasons"] = reasons
    return out
