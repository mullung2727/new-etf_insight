
"""Parse Kiwoom realtime (REAL) WS messages into (channel, values) pairs.

Only ``trnm == "REAL"`` carries realtime data; LOGIN/PING/REG replies are
handled by the WS manager and yield nothing here. One REAL message may bundle
several items, so this returns a list.
"""

from __future__ import annotations

def parse_message(raw:dict) -> list[tuple[str, dict]]:
    """Return ``[(channel, values), ...]`` from a Kiwoom WS message.

    Non-REAL messages (LOGIN, PING, REG replies) return an empty list. Each
    REAL ``data`` entry maps its ``type`` to the channel and ``values`` to the
    payload that gets published on the bus.
    """
    if raw.get("trnm") != "REAL":
        return []
    
    out: list[tuple[str,dict]] = []
    for entry in raw.get("data", []):
        channel = entry.get("type")
        values = entry.get("values")
        if channel and isinstance(values, dict):
            # 0D 는 한 프레임에 여러 종목이 온다 — 종목 식별자 item 을 payload 에 싣는다.
            out.append((channel, {**values, "item": entry.get("item", "")}))
    return out
