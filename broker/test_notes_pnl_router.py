"""투자노트 손익 API(GET /notes/{uid}, GET /notes/pnl-summary) 라우터 테스트.

    .venv/Scripts/python.exe -m unittest test_notes_pnl_router

kiwoom.quotes.get_quote/get_watchlist_quotes를 monkeypatch해 HTTP 없이 검증한다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiwoom import quotes
from notes import db as notes_db
from notes.models import EventCreate, EventType, NoteCreate
from notes import store
from routers import notes as notes_router


def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return Path(path)


class _Base(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        notes_db.NOTES_DB_PATH = self.db
        notes_db._conn = None
        notes_db.init()

        self._orig_resolve = store.resolve_name
        store.resolve_name = lambda symbol: None

        self._orig_get_quote = quotes.get_quote
        self._orig_batch = quotes.get_watchlist_quotes

        app = FastAPI()
        app.include_router(notes_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        store.resolve_name = self._orig_resolve
        quotes.get_quote = self._orig_get_quote
        quotes.get_watchlist_quotes = self._orig_batch
        if notes_db._conn is not None:
            notes_db._conn.close()
            notes_db._conn = None
        self.db.unlink(missing_ok=True)


class GetNoteIncludesPnl(_Base):
    def test_open_note_gets_unrealized_pnl_from_live_quote(self):
        note = store.create_note(NoteCreate(symbol="005930"))
        store.add_event(
            note.uid,
            EventCreate(event_type=EventType.buy, price=10000, qty=10, executed_at="2026-07-01"),
        )
        quotes.get_quote = lambda symbol: type("Q", (), {"price": 11000})()

        resp = self.client.get(f"/notes/{note.uid}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["pnl"]["remaining_qty"], 10)
        self.assertEqual(body["pnl"]["net_pnl"], 10000)
        self.assertFalse(body["pnl"]["needs_price"])

    def test_fully_sold_note_skips_quote_call(self):
        """수동 add_note_event 경로는 status를 자동 전이하지 않는다(autolink만 함).
        그래서 게이팅은 status가 아니라 이벤트로 계산한 remaining_qty로 한다."""
        note = store.create_note(NoteCreate(symbol="005930"))
        store.add_event(
            note.uid,
            EventCreate(event_type=EventType.buy, price=10000, qty=10, executed_at="2026-07-01"),
        )
        store.add_event(
            note.uid,
            EventCreate(event_type=EventType.sell, price=12000, qty=10, executed_at="2026-07-02"),
        )

        called = []
        quotes.get_quote = lambda symbol: called.append(symbol) or type("Q", (), {"price": 99999})()

        resp = self.client.get(f"/notes/{note.uid}")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["pnl"]["remaining_qty"], 0)
        self.assertEqual(body["pnl"]["net_pnl"], 20000)
        self.assertEqual(called, [])  # 전량매도 노트는 현재가 조회 안 함


class PnlSummaryBatches(_Base):
    def test_batches_live_symbols_once_and_skips_fully_sold(self):
        open_note = store.create_note(NoteCreate(symbol="005930"))
        store.add_event(
            open_note.uid,
            EventCreate(event_type=EventType.buy, price=10000, qty=10, executed_at="2026-07-01"),
        )
        sold_note = store.create_note(NoteCreate(symbol="000660"))
        store.add_event(
            sold_note.uid,
            EventCreate(event_type=EventType.buy, price=5000, qty=5, executed_at="2026-07-01"),
        )
        store.add_event(
            sold_note.uid,
            EventCreate(event_type=EventType.sell, price=6000, qty=5, executed_at="2026-07-02"),
        )

        calls = []

        def fake_batch(codes):
            calls.append(list(codes))
            return [{"stk_cd": "005930", "cur_prc": 11000}]

        quotes.get_watchlist_quotes = fake_batch

        resp = self.client.get("/notes/pnl-summary")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(calls, [["005930"]])  # 전량매도 종목은 배치에 안 들어감

        by_uid = {item["uid"]: item for item in body}
        self.assertEqual(by_uid[open_note.uid]["pnl"]["net_pnl"], 10000)
        self.assertEqual(by_uid[sold_note.uid]["pnl"]["net_pnl"], 5000)

    def test_quote_failure_does_not_500(self):
        note = store.create_note(NoteCreate(symbol="005930"))
        store.add_event(
            note.uid,
            EventCreate(event_type=EventType.buy, price=10000, qty=10, executed_at="2026-07-01"),
        )

        def boom(codes):
            raise RuntimeError("kiwoom down")

        quotes.get_watchlist_quotes = boom

        resp = self.client.get("/notes/pnl-summary")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body[0]["pnl"]["needs_price"])


if __name__ == "__main__":
    unittest.main()
