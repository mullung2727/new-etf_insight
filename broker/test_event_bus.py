"""EventBus sticky system-state 재생 검증.

늦게 붙은 SSE 구독자가 직전 연결상태(system/connected)를 즉시 받아야 "끊김" 오표시가
안 난다. broker는 pytest 없음 → stdlib unittest.
    .venv/Scripts/python.exe -m unittest test_event_bus

asyncio.Queue는 put_nowait/get_nowait가 동기라 이벤트루프 없이 검증한다.
"""
from __future__ import annotations

import asyncio
import unittest

from kiwoom.ws.event_bus import EventBus


def _drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return out


class TestStickySystemState(unittest.TestCase):
    def test_late_subscriber_gets_current_state(self):
        bus = EventBus()
        bus.publish("system", {"type": "connected"})   # 구독 전에 이미 연결됨
        q: asyncio.Queue = asyncio.Queue()
        bus.subscribe("*", q)                           # 나중에 붙음
        events = _drain(q)
        self.assertEqual(events, [{"channel": "system", "payload": {"type": "connected"}}])

    def test_sticky_reflects_latest(self):
        bus = EventBus()
        bus.publish("system", {"type": "connected"})
        bus.publish("system", {"type": "disconnected"})  # 최신=끊김
        q: asyncio.Queue = asyncio.Queue()
        bus.subscribe("system", q)
        self.assertEqual(_drain(q), [{"channel": "system", "payload": {"type": "disconnected"}}])

    def test_non_system_not_replayed(self):
        bus = EventBus()
        bus.publish("00", {"913": "체결"})              # transient 체결 — 재생 안 함
        q: asyncio.Queue = asyncio.Queue()
        bus.subscribe("*", q)
        self.assertEqual(_drain(q), [])

    def test_no_state_yet_no_replay(self):
        bus = EventBus()
        q: asyncio.Queue = asyncio.Queue()
        bus.subscribe("*", q)                           # 아직 system 없음
        self.assertEqual(_drain(q), [])

    def test_live_fanout_still_works(self):
        bus = EventBus()
        q: asyncio.Queue = asyncio.Queue()
        bus.subscribe("*", q)
        bus.publish("0B", {"10": "37000"})              # 구독 후 발생 이벤트
        self.assertEqual(_drain(q), [{"channel": "0B", "payload": {"10": "37000"}}])


if __name__ == "__main__":
    unittest.main()
