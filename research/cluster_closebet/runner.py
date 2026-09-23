"""백테스트 실행기 — 진입/청산/비용/지표 (PLAN §13·§15 + 확정값).

진입 15:19가(일봉 대리 시 종가), 청산 익일 시가, 왕복 0.6% 차감.
상한가 진입·권리락 갭은 BACKTEST_DATA.md §2b·§2c 기준 제외.
"""
from __future__ import annotations

import bisect
import math
import statistics

ROUNDTRIP_COST = 0.006
TP_PCT = 0.03
SL_PCT = 0.03
LIMIT_UP_CUT = 0.295
GAP_CUT = 0.31


def exit_tpsl(entry: float, bars: list[dict], tp: float = 0.03,
              sl: float = 0.03) -> tuple[float, str]:
    """TP/SL 다일 청산. bars는 D+1부터 순서대로, 각 dict는
    open_price/high_price/low_price/close 키를 가진다."""
    tp_lv = entry * (1 + tp)
    sl_lv = entry * (1 - sl)
    last = len(bars) - 1
    for i, b in enumerate(bars):
        o, h, low = b["open_price"], b["high_price"], b["low_price"]
        if o >= tp_lv:
            return o, "tp_open"
        if o <= sl_lv:
            return o, "sl_open"
        if low <= sl_lv:
            return sl_lv, "sl"
        if h >= tp_lv:
            return tp_lv, "tp"
        if i == last:
            return b["close"], "time"
    return bars[-1]["close"], "time"


def net_return(entry: float, exit: float) -> float:
    """비용 차감 순수익률."""
    return exit / entry - 1 - ROUNDTRIP_COST


def entry_allowed(entry: float, prev_close: float | None) -> bool:
    """상한가 묶임은 매수 불가. 전일종가 없으면 허용(건수는 호출측 보고)."""
    if prev_close is None:
        return True
    return entry / prev_close - 1 < LIMIT_UP_CUT


def exit_allowed(next_open: float, close: float) -> bool:
    """±31% 밖 갭은 권리락·오류로 제외."""
    return abs(next_open / close - 1) < GAP_CUT


def t_stat(nets: list[float]) -> float:
    """평균 / (표본표준편차 / sqrt(n)). n < 2 또는 표준편차 0이면 0.0."""
    n = len(nets)
    if n < 2:
        return 0.0
    sd = statistics.stdev(nets)
    if sd == 0:
        return 0.0
    return (sum(nets) / n) / (sd / math.sqrt(n))


def close_position(row: dict) -> float | None:
    """(close - low) / (high - low). high == low이면 None."""
    hi, lo, close = row.get("high_price"), row.get("low_price"), row.get("close")
    if hi is None or lo is None or close is None:
        return None
    if hi == lo:
        return None
    return (close - lo) / (hi - lo)


def distance_high_60(close: float | None, prior_max_high: float | None,
                     ) -> float | None:
    """close / prior_max_high - 1. prior 없으면 None."""
    if close is None or prior_max_high is None or prior_max_high == 0:
        return None
    return close / prior_max_high - 1


def build_prior_high(rows: list[dict]):
    """티커별 정렬 (ms, high) 리스트를 미리 만들고 bisect로 조회하는
    prior_max_high(ticker, entry_ms, window=60, min_obs=40)를 반환한다."""
    pairs: dict[str, list[tuple]] = {}
    for r in rows:
        ms, hi = r.get("ms"), r.get("high_price")
        if ms is None or hi is None:
            continue
        pairs.setdefault(r["ticker"], []).append((ms, hi))
    index = {}
    for t, ps in pairs.items():
        ps.sort()
        index[t] = ([m for m, _ in ps], [h for _, h in ps])

    def prior_max_high(ticker: str, entry_ms, window: int = 60,
                       min_obs: int = 40) -> float | None:
        if entry_ms is None:
            return None
        pair = index.get(ticker)
        if pair is None:
            return None
        mss, his = pair
        left = bisect.bisect_left(mss, entry_ms - window)
        right = bisect.bisect_right(mss, entry_ms - 1)
        if right - left < min_obs:
            return None
        return max(his[left:right])

    return prior_max_high


def prior_max_high(rows: list[dict], ticker: str, entry_ms,
                   window: int = 60, min_obs: int = 40) -> float | None:
    """편의 래퍼: 행 리스트에서 직접 조회한다.
    반복 조회는 build_prior_high로 lookup을 미리 만들 것."""
    return build_prior_high(rows)(ticker, entry_ms, window, min_obs)


class PriorHighIndex:
    """build_prior_high lookup의 클래스형 래퍼."""

    def __init__(self, rows: list[dict]):
        self._lookup = build_prior_high(rows)

    def prior_max_high(self, ticker: str, entry_ms, window: int = 60,
                       min_obs: int = 40) -> float | None:
        return self._lookup(ticker, entry_ms, window, min_obs)

    __call__ = prior_max_high


VP_WINDOW = 120
VP_MIN_OBS = 80
VP_BINS = 40


def volume_profile(closes: list, volumes: list, n_bins: int = VP_BINS,
                   min_obs: int = VP_MIN_OBS) -> dict | None:
    """과거 윈도우 종가/거래량 → 매물대 히스토그램.

    len < min_obs → None. lo=min(close), hi=max(close),
    hi == lo → None. [lo, hi]를 n_bins 등폭 분할하고 각 행의
    volume을 close가 속한 빈에 가산(마지막 빈은 hi 포함).
    poc 동점은 가장 낮은 빈. 반환: lo/hi/width/n_bins/
    bin_volumes/total/poc_high(최대 빈의 상단 edge).
    """
    if closes is None or volumes is None:
        return None
    n = len(closes)
    if n != len(volumes) or n < min_obs:
        return None
    lo = min(closes)
    hi = max(closes)
    if hi == lo:
        return None
    width = (hi - lo) / n_bins
    bins = [0] * n_bins
    for c, v in zip(closes, volumes):
        if c >= hi:
            bins[n_bins - 1] += v
        else:
            bins[int((c - lo) / width)] += v
    total = sum(bins)
    best = 0
    for i in range(1, n_bins):
        if bins[i] > bins[best]:
            best = i
    return {"lo": lo, "hi": hi, "width": width, "n_bins": n_bins,
            "bin_volumes": bins, "total": total,
            "poc_high": lo + (best + 1) * width}


def vp_overhead_ratio(vp: dict | None, close_today) -> float | None:
    """bin 하단 edge >= close_today인 빈들의 volume 비율.

    vp None·거래량 0이면 None."""
    if vp is None or close_today is None:
        return None
    total = vp.get("total")
    if not total:
        return None
    lo, width, bins = vp["lo"], vp["width"], vp["bin_volumes"]
    over = 0
    for i, b in enumerate(bins):
        if lo + i * width >= close_today:
            over += b
    return over / total


def build_volume_profile(rows: list[dict], window: int = VP_WINDOW,
                         min_obs: int = VP_MIN_OBS,
                         n_bins: int = VP_BINS):
    """티커별 정렬 (ms, close, volume) 배열을 한 번만 만들고 bisect로
    [entry_ms-window, entry_ms-1] 윈도우를 잘라 volume_profile을
    만드는 lookup(ticker, entry_ms)를 반환한다."""
    triples: dict[str, list[tuple]] = {}
    for r in rows:
        ms, close, vol = r.get("ms"), r.get("close"), r.get("volume")
        if ms is None or close is None or vol is None:
            continue
        triples.setdefault(r["ticker"], []).append((ms, close, vol))
    index = {}
    for t, ps in triples.items():
        ps.sort()
        index[t] = ([m for m, _, _ in ps], [c for _, c, _ in ps],
                    [v for _, _, v in ps])

    def lookup(ticker: str, entry_ms):
        if entry_ms is None:
            return None
        trio = index.get(ticker)
        if trio is None:
            return None
        mss, closes, vols = trio
        left = bisect.bisect_left(mss, entry_ms - window)
        right = bisect.bisect_right(mss, entry_ms - 1)
        if right - left < min_obs:
            return None
        return volume_profile(closes[left:right], vols[left:right],
                              n_bins=n_bins, min_obs=min_obs)

    return lookup


def summarize(nets: list[float]) -> dict:
    """PLAN §15 최소 지표 + 분포 버킷."""
    n = len(nets)
    wins = [r for r in nets if r > 0]
    losses = [r for r in nets if r < 0]
    cum, peak, mdd = 1.0, 1.0, 0.0
    for r in nets:
        cum *= 1 + r
        peak = max(peak, cum)
        mdd = max(mdd, 1 - cum / peak)
    return {
        "n": n,
        "mean": sum(nets) / n if n else 0.0,
        "median": statistics.median(nets) if n else 0.0,
        "win_rate": len(wins) / n if n else 0.0,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": (sum(wins) / -sum(losses)) if losses else 0.0,
        "t_stat": t_stat(nets),
        "cum": cum - 1,
        "mdd": mdd,
        "buckets": {
            "ge_3pct": sum(1 for r in nets if r >= 0.03),
            "ge_2pct": sum(1 for r in nets if r >= 0.02),
            "ge_1pct": sum(1 for r in nets if r >= 0.01),
            "lt_0": sum(1 for r in nets if r < 0),
            "le_neg1pct": sum(1 for r in nets if r <= -0.01),
            "le_neg2pct": sum(1 for r in nets if r <= -0.02),
            "le_neg3pct": sum(1 for r in nets if r <= -0.03),
        },
    }
