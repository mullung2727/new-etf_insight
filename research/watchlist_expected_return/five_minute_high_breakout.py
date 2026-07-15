"""직전 5분 고점 돌파 전략.

당일 1분봉 저가가 전일 저가 아래로 내려간 뒤, 현재 1분봉 종가가 직전
5개 1분봉의 고가 중 최댓값을 처음 돌파하는 시점의 종가를 매수가로 쓴다.
미래 봉이나 당일 종가 조건은 사용하지 않는다.
"""
from __future__ import annotations

from typing import Any


def find_entry(day_bars: list[dict[str, Any]], prior_low: float) -> dict[str, Any] | None:
    lower_low_seen = False
    for index, bar in enumerate(day_bars):
        lower_low_seen = lower_low_seen or bar["low"] < prior_low
        if index < 5 or not lower_low_seen:
            continue
        previous_high = max(item["high"] for item in day_bars[index - 5:index])
        if bar["close"] > previous_high:
            return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
    return None
