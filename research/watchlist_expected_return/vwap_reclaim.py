"""당일 누적 VWAP 재돌파 전략.

당일 1분봉 저가가 전일 저가 아래로 내려간 뒤, 1분봉 종가가 당일 누적
VWAP 아래에서 위로 처음 재돌파하는 시점의 종가를 매수가로 사용한다.
VWAP은 각 1분봉의 전형가격과 거래량으로 계산하며 거래량 0인 봉은 제외한다.
"""
from __future__ import annotations

from typing import Any


def find_entry(day_bars: list[dict[str, Any]], prior_low: float) -> dict[str, Any] | None:
    lower_low_seen = False
    cumulative_value = 0.0
    cumulative_volume = 0
    previous_below = False
    for bar in day_bars:
        lower_low_seen = lower_low_seen or bar["low"] < prior_low
        volume = bar.get("volume") or 0
        typical_price = (bar["high"] + bar["low"] + bar["close"]) / 3
        cumulative_value += typical_price * volume
        cumulative_volume += volume
        if not cumulative_volume:
            continue
        vwap = cumulative_value / cumulative_volume
        below = bar["close"] < vwap
        if lower_low_seen and previous_below and not below:
            return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
        previous_below = below
    return None
