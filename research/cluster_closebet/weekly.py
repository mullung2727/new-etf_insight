"""주간 수익률 — 수정주가(상장주식수 밴드) + 일별 ±30% 클립 + 주간 복리.

보정식은 research/BACKTEST_DATA.md §(c) (high52 _ADJ와 동일):
밴드(0.67~1.5) 밖 주식수 변동 시 시총 비율로 일수익률을 계산한다.
작은 변동(CB 전환·소액 증자)은 실제 희석이라 건드리지 않는다.
"""
from __future__ import annotations

from datetime import datetime

SPLIT_LO = 0.67
SPLIT_HI = 1.5
DAILY_CLIP = 0.30


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def daily_returns(bars: list[dict]) -> list[tuple[str, float]]:
    """bars: [{'date': 'YYYYMMDD', 'close': n, 'list_shrs': n|None}] 오래된 순.
    반환: [(date, ret)] — 첫 봉 제외, close<=0 행은 건너뛴다."""
    out: list[tuple[str, float]] = []
    prev: tuple[float, float | None] | None = None
    for b in bars:
        close = _num(b.get("close"))
        if close is None or close <= 0:
            continue
        shrs = _num(b.get("list_shrs"))
        if prev is not None:
            pc, ps = prev
            ret = close / pc - 1
            if shrs and shrs > 0 and ps and ps > 0:
                ratio = shrs / ps
                if ratio < SPLIT_LO or ratio > SPLIT_HI:
                    ret = (close * shrs) / (pc * ps) - 1
            out.append((b["date"], max(-DAILY_CLIP, min(DAILY_CLIP, ret))))
        prev = (close, shrs)
    return out


def week_key(yyyymmdd: str) -> str:
    y, w, _ = datetime.strptime(yyyymmdd, "%Y%m%d").date().isocalendar()
    return f"{y}W{w:02d}"


def weekly_returns(daily: list[tuple[str, float]]) -> dict[str, float]:
    """[(date, ret)] → {주키: 복리수익률}."""
    acc: dict[str, float] = {}
    for d, r in daily:
        k = week_key(d)
        acc[k] = (1 + acc[k]) * (1 + r) - 1 if k in acc else r
    return acc
