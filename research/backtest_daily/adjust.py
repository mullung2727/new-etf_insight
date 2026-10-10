"""일봉 전용 기업행위 보정·보정가격·이동평균."""
from __future__ import annotations

import numpy as np
import pandas as pd

SH_TH, PX_TH, MATCH = 0.10, 0.30, 30


def _blocks(tic: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """종목 연속 블록의 시작/끝 행번호."""
    n = len(tic)
    ch = np.flatnonzero(tic[1:] != tic[:-1]) + 1 if n else np.empty(0, int)
    starts = np.concatenate(([0], ch)).astype(int)
    ends = np.concatenate((ch, [n])).astype(int)
    return starts, ends


def adj_returns(px: pd.DataFrame, *, use_krx_reference: bool = False) -> np.ndarray:
    """px 행 순서 그대로 보정 일별 수익률. 필요 컬럼: ticker, ms, close, market_cap.

    주식수(시총/종가) ±10% 초과 변화일 j마다 같은 종목 [j-30, j+30]행에서
    |pr·sr_j - 1| < 0.05 이고 |pr - 1| > 0.03인 날 중 j에 가장 가까운 날(동점은 이른 날)
    수익률을 pr·sr_j로 바꾼다. 출력은 pr - 1을 ±30% 클립, 비연속 거래일은 NaN.

    use_krx_reference=True 면 KRX 기준가 모드: 필요 컬럼 ticker, ms, close, cmp_prev
    (market_cap 불필요, 기존 주식수 매칭 코드 실행 안 함). 행별 reference = close - cmp_prev,
    수익률 = close / reference - 1. reference <= 0·결측·무효는 NaN. 종목 경계·비연속 ms·
    종목 첫 행 NaN, ±30% 클립은 기존과 동일. cmp_prev 컬럼이 없으면 ValueError.
    기본 False 는 기존 결과와 완전히 동일하다.
    """
    if use_krx_reference and "cmp_prev" not in px.columns:
        raise ValueError("need 'cmp_prev' column for use_krx_reference=True")
    n = len(px)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    if use_krx_reference:
        tic = px["ticker"].to_numpy()
        ms = np.asarray(px["ms"])
        close = px["close"].to_numpy(dtype=float)
        cmp_prev = pd.to_numeric(px["cmp_prev"], errors="coerce").to_numpy(dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ref = close - cmp_prev
            r = close / ref - 1
        bad = (~np.isfinite(close) | (close <= 0) | ~np.isfinite(ref) | (ref <= 0))
        r[bad] = np.nan
        same = np.zeros(n, dtype=bool)
        same[1:] = tic[1:] == tic[:-1]
        contig = np.zeros(n, dtype=bool)
        contig[1:] = same[1:] & (ms[1:] == ms[:-1] + 1)
        r[~contig] = np.nan
        return np.clip(r, -0.30, 0.30)
    tic = px["ticker"].to_numpy()
    ms = np.asarray(px["ms"])
    close = px["close"].to_numpy(dtype=float)
    cap = px["market_cap"].to_numpy(dtype=float)
    same = np.zeros(n, dtype=bool)
    same[1:] = tic[1:] == tic[:-1]
    idx = np.flatnonzero(same)
    pr = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        pr[idx] = close[idx] / close[idx - 1]
    raw = np.where((cap > 0) & (close > 0), cap / close, np.nan)
    sh = pd.Series(raw).groupby(tic).ffill().to_numpy(dtype=float)
    fin = np.isfinite(sh)
    m = same & fin
    m[1:] &= fin[:-1]
    sr = np.full(n, np.nan)
    mi = np.flatnonzero(m)
    sr[mi] = sh[mi] / sh[mi - 1]
    ev = mi[np.abs(sr[mi] - 1) > 0.10]
    starts, ends = _blocks(tic)
    fixed = np.zeros(n, dtype=bool)
    for j in ev:
        b = int(np.searchsorted(starts, j, side="right")) - 1
        lo, hi = max(int(starts[b]), j - 30), min(int(ends[b]), j + 31)
        w = pr[lo:hi]
        okw = (np.isfinite(w) & (np.abs(w * sr[j] - 1) < 0.05)
               & (np.abs(w - 1) > 0.03) & ~fixed[lo:hi])
        cand = np.flatnonzero(okw) + lo
        if cand.size == 0:
            continue
        i = int(cand[np.argmin(np.abs(cand - j))])
        pr[i] *= sr[j]
        fixed[i] = True
    contig = np.zeros(n, dtype=bool)
    contig[1:] = same[1:] & (ms[1:] == ms[:-1] + 1)
    r = pr - 1
    r[~contig] = np.nan
    return np.clip(r, -0.30, 0.30)


def adj_price(tickers, r) -> np.ndarray:
    """종목별 보정가격 cumprod(1 + nan_to_num(r)). 종목 경계에서 새로 시작."""
    factor = 1.0 + np.nan_to_num(np.asarray(r, dtype=float))
    return (pd.Series(factor)
            .groupby(pd.Series(np.asarray(tickers)), sort=False)
            .cumprod().to_numpy())


def corp_action_events(px: pd.DataFrame, cap: np.ndarray):
    """px 정렬(ticker,date) 기준 행마다 주식수 이벤트·가격 이벤트와 분류.

    반환 (E, sh_ev, first, last): E(이벤트 표 i/trigger/kind/sr/pr/ticker/date),
    sh_ev(주식수 이벤트 행 마스크), first/last(종목 블록 경계).
    """
    tk = px["ticker"].to_numpy()
    close = px["close"].to_numpy(float)
    n = len(px)
    start = np.r_[True, tk[1:] != tk[:-1]]
    valid = (close > 0) & (cap > 0)
    sh = pd.Series(np.where(valid, cap / np.where(close > 0, close, np.nan), np.nan))
    sh = sh.groupby(tk).ffill().to_numpy()
    cl = pd.Series(np.where(close > 0, close, np.nan)).groupby(tk).ffill().to_numpy()
    sr = np.full(n, np.nan)
    pr = np.full(n, np.nan)
    sr[1:] = sh[1:] / sh[:-1]
    pr[1:] = cl[1:] / cl[:-1]
    sr[start] = np.nan
    pr[start] = np.nan
    sh_ev = np.isfinite(sr) & (np.abs(sr - 1) > SH_TH)
    px_ev = np.isfinite(pr) & (np.abs(pr - 1) >= PX_TH)

    # 종목 시작 인덱스 (창이 다른 종목으로 넘어가지 않게)
    first = np.maximum.accumulate(np.where(start, np.arange(n), 0))
    last = np.minimum.accumulate(np.where(np.r_[start[1:], True], np.arange(n), n)[::-1])[::-1]

    ev = []
    sh_idx = np.where(sh_ev)[0]
    used_sh = set()
    for i in np.where(px_ev)[0]:
        lo, hi = max(first[i], i - MATCH), min(last[i], i + MATCH)
        cand = sh_idx[(sh_idx >= lo) & (sh_idx <= hi)]
        kind, s = "실제 가격 변동", np.nan
        if len(cand):
            j = cand[np.argmin(np.abs(cand - i))]
            s = sr[j]
            if abs(np.log(pr[i] * s)) < 0.5 * abs(np.log(pr[i])):
                kind = "분할·무상증자" if s > 1 else "병합·감자"
                used_sh.add(j)
        ev.append((i, "가격", kind, s, pr[i]))
    for j in sh_idx:
        if j in used_sh:
            continue
        lo = max(first[j], j - MATCH)
        win = pr[lo:j + 1]
        hit = np.isfinite(win) & (np.abs(win * sr[j] - 1) < 0.05) & (np.abs(win - 1) > 0.03)
        if sr[j] > 1:
            kind = "무상증자(권리락 매칭)" if hit.any() else "유상증자·전환 등"
        else:
            kind = "감자(권리락 매칭)" if hit.any() else "주식수 감소(가격 무영향)"
        ev.append((j, "주식수", kind, sr[j], pr[j]))
    E = pd.DataFrame(ev, columns=["i", "trigger", "kind", "sr", "pr"])
    E["ticker"] = tk[E["i"]]
    E["date"] = px["date"].to_numpy()[E["i"]]
    return E.sort_values(["ticker", "date"]).reset_index(drop=True), sh_ev, first, last


def ma_n(ms, P, n=20) -> np.ndarray:
    """직전 n행(당일 포함) P 평균. n행 ms 폭이 정확히 n-1이 아니면 NaN."""
    ms = np.asarray(ms)
    P = np.asarray(P, dtype=float)
    m = len(ms)
    out = np.full(m, np.nan)
    if m >= n:
        c = np.zeros(m + 1)
        c[1:] = np.cumsum(P)
        ok = ms[n - 1:] - ms[:m - n + 1] == n - 1
        sums = c[n:] - c[:m - n + 1]
        idx = np.flatnonzero(ok) + n - 1
        out[idx] = sums[ok] / float(n)
    return out
