"""호가 수집기 1단계 — REAL payload·SSE 채널 필터·큐 유실 계측 (SPEC §6, T01/T05/T06/T07).

    $env:PYTHONPATH="broker"; .\\broker\\.venv\\Scripts\\python.exe -m unittest discover -s broker -p "test_orderbook_*.py"
"""
from __future__ import annotations

import asyncio
import re
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiwoom.ws import channels
from kiwoom.ws.event_bus import EventBus
from kiwoom.ws.manager import KiwoomWSManager
from routers import events


def _real(*entries):
    return {"trnm": "REAL", "data": [
        {"type": t, "item": item, "values": values} for t, item, values in entries]}


class TestRealPayload(unittest.TestCase):
    def test_t01_two_tickers_keep_item(self):
        msg = _real(("0D", "KRX:005930", {"41": "+70000"}), ("0D", "KRX:000660", {"41": "-200000"}))
        self.assertEqual(channels.parse_message(msg), [
            ("0D", {"41": "+70000", "item": "KRX:005930"}),
            ("0D", {"41": "-200000", "item": "KRX:000660"}),
        ])

    def test_t05_fill_payload_keeps_fids(self):
        """_fill_sync_loop 은 payload["913"] == "체결" 로 판정한다 — item 추가가 그 키를 건드리면 안 된다."""
        [(channel, payload)] = channels.parse_message(_real(("00", "005930", {"913": "체결", "9001": "A005930"})))
        self.assertEqual(channel, "00")
        self.assertEqual(payload["913"], "체결")
        self.assertEqual(payload["9001"], "A005930")

    def test_manager_stamps_one_kst_ms_recv_ts_per_frame(self):
        published = []
        msg = _real(("0D", "KRX:005930", {"41": "1"}), ("0D", "KRX:000660", {"41": "2"}))
        with patch("kiwoom.ws.manager.bus.publish", lambda ch, p: published.append((ch, p))):
            KiwoomWSManager()._on_real(msg)
        stamps = {p["_recv_ts"] for _, p in published}
        self.assertEqual(len(published), 2)
        self.assertEqual(len(stamps), 1)                   # 한 프레임 = 한 수신 시각
        self.assertRegex(stamps.pop(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+09:00$")


class TestChannelFilter(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(events.router)
        self.client = TestClient(app)

    def test_t06_default_excludes_0d(self):
        self.assertEqual(events.parse_channels(None), ("system", "00"))

    def test_t06_explicit_filter_dedupes(self):
        self.assertEqual(events.parse_channels("0D,system,0D"), ("0D", "system"))

    def test_t06_bad_values_are_422(self):
        for bad in ("*", "", "0D,,system", "0B", "system,*"):
            with self.subTest(ch=bad):
                self.assertEqual(self.client.get("/events", params={"ch": bad}).status_code, 422)


class TestQueueOverflow(unittest.TestCase):
    def test_t07_full_queue_drops_without_blocking_and_counts(self):
        bus = EventBus()
        queue: asyncio.Queue = asyncio.Queue(maxsize=events.QUEUE_MAX)
        bus.subscribe("0D", queue)
        for i in range(2001):
            bus.publish("0D", {"i": i})
        self.assertEqual(events.QUEUE_MAX, 2000)
        self.assertEqual(queue.qsize(), 2000)
        self.assertEqual(bus.dropped, {"0D": 1})

    def test_system_event_survives_full_queue_by_evicting_oldest(self):
        """connected/disconnected 를 잃으면 수집기가 슬롯을 못 비우거나 복구 POST 를 못 한다."""
        bus = EventBus()
        queue: asyncio.Queue = asyncio.Queue(maxsize=3)
        bus.subscribe("0D", queue)
        bus.subscribe("system", queue)
        for i in range(3):
            bus.publish("0D", {"i": i})
        bus.publish("system", {"type": "disconnected"})
        drained = [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertEqual([e["channel"] for e in drained], ["0D", "0D", "system"])
        self.assertEqual(drained[0]["payload"], {"i": 1})      # 가장 오래된 0D 를 버림
        self.assertEqual(bus.dropped, {"0D": 1})


if __name__ == "__main__":
    unittest.main()
