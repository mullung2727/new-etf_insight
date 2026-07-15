"""전일 저가 재돌파 전략.

당일 1분봉 저가가 전일 저가 아래로 내려간 뒤, 1분봉 종가가 전일 저가
위로 다시 올라오는 최초 시점의 종가를 매수가로 사용한다. 이탈과 복귀가
같은 1분봉 안에서 발생한 경우도 신호로 인정한다.
"""
from __future__ import annotations

from typing import Any


def find_entry(day_bars: list[dict[str, Any]], prior_low: float) -> dict[str, Any] | None:
    lower_low_seen = False
    previous_close: float | None = None
    for bar in day_bars:
        breached_this_bar = bar["low"] < prior_low
        lower_low_seen = lower_low_seen or breached_this_bar
        reclaimed = bar["close"] > prior_low and (
            breached_this_bar or (previous_close is not None and previous_close <= prior_low)
        )
        if lower_low_seen and reclaimed:
            return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
        previous_close = bar["close"]
    return None
