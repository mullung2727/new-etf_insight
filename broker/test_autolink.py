"""autolink reconciler 단위 테스트.

broker는 pytest가 없으므로 stdlib unittest로 돌린다.
    .venv/Scripts/python.exe -m unittest test_autolink

임시 NOTES_DB_PATH로 db를 격리하고, kiwoom.orders.get_order_history를
가짜 kt00007 행으로 monkeypatch한다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kiwoom import orders
from notes import autolink, db, store
from notes.models import NoteStatus


def _row(ord_no: str, stk_cd: str, cntr_qty: int, cntr_uv: int, ord_tm: str = "093000") -> dict:
    """kt00007 행 일부(reconciler가 읽는 필드만)."""
    return {
        "ord_no": ord_no,
        "stk_cd": stk_cd,
        "cntr_qty": str(cntr_qty),
        "cntr_uv": str(cntr_uv),
        "ord_tm": ord_tm,
    }


class _Base(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = Path(path)
        os.environ["NOTES_DB_PATH"] = str(self.db)
        # 모듈 캐시 리셋: 새 임시 경로로 연결을 다시 열도록.
        db._conn = None
        db.NOTES_DB_PATH = self.db
        db.init()
        # 매수/매도 응답을 테스트가 주입.
        self._buys: list[dict] = []
        self._sells: list[dict] = []
        self._orig = orders.get_order_history
        orders.get_order_history = self._fake_history

    def tearDown(self):
        orders.get_order_history = self._orig
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        os.environ.pop("NOTES_DB_PATH", None)
        self.db.unlink(missing_ok=True)

    def _fake_history(self, date: str, sell_tp: str = "2") -> list[dict]:
        return self._buys if sell_tp == "2" else self._sells

    def _only_note(self):
        notes = store.list_notes()
        self.assertEqual(len(notes), 1)
        return store.get_note(notes[0].uid)


class TestSyncTrades(_Base):
    def test_buy_creates_note_and_event(self):
        self._buys = [_row("0000001", "A005930", 10, 70000)]
        res = autolink.sync_trades("20260630")
        self.assertEqual(res, {"created": 1, "updated": 0, "notes": 1})
        note = self._only_note()
        self.assertEqual(note.symbol, "005930")
        self.assertEqual(note.status, NoteStatus.open)
        self.assertEqual(len(note.events), 1)
        self.assertEqual(note.events[0].event_type.value, "buy")
        self.assertEqual(note.events[0].qty, 10)
        self.assertEqual(note.events[0].order_no, "0000001")

    def test_resync_is_idempotent(self):
        self._buys = [_row("0000001", "A005930", 10, 70000)]
        autolink.sync_trades("20260630")
        res = autolink.sync_trades("20260630")
        self.assertEqual(res, {"created": 0, "updated": 1, "notes": 0})
        note = self._only_note()
        self.assertEqual(len(note.events), 1)  # 중복 행 없음

    def test_partial_to_full_updates_qty_in_place(self):
        self._buys = [_row("0000001", "A005930", 4, 70000)]
        autolink.sync_trades("20260630")
        # 같은 주문이 전량 체결되어 누적 qty 증가
        self._buys = [_row("0000001", "A005930", 10, 70000)]
        autolink.sync_trades("20260630")
        note = self._only_note()
        self.assertEqual(len(note.events), 1)
        self.assertEqual(note.events[0].qty, 10)

    def test_add_buy_then_full_sell_closes(self):
        self._buys = [
            _row("0000001", "A005930", 10, 70000),
            _row("0000002", "A005930", 5, 71000),
        ]
        autolink.sync_trades("20260630")
        note = self._only_note()
        types = sorted(e.event_type.value for e in note.events)
        self.assertEqual(types, ["add_buy", "buy"])
        self.assertEqual(note.status, NoteStatus.open)

        # 전량(15주) 매도 → closed
        self._sells = [_row("0000003", "A005930", 15, 72000, "143000")]
        autolink.sync_trades("20260630")
        note = self._only_note()
        self.assertEqual(note.status, NoteStatus.closed)
        sell = next(e for e in note.events if e.order_no == "0000003")
        self.assertEqual(sell.event_type.value, "sell")

    def test_partial_sell_sets_partial(self):
        self._buys = [_row("0000001", "A005930", 10, 70000)]
        self._sells = [_row("0000002", "A005930", 4, 72000, "143000")]
        autolink.sync_trades("20260630")
        note = self._only_note()
        self.assertEqual(note.status, NoteStatus.partial)
        sell = next(e for e in note.events if e.order_no == "0000002")
        self.assertEqual(sell.event_type.value, "partial_sell")

    def test_sell_without_active_note_creates_closed(self):
        # 시스템 도입 전 HTS 매수분 매도 — active 노트 없음
        self._sells = [_row("0000009", "A000660", 3, 120000, "100000")]
        res = autolink.sync_trades("20260630")
        self.assertEqual(res["notes"], 1)
        note = self._only_note()
        self.assertEqual(note.symbol, "000660")
        self.assertEqual(note.status, NoteStatus.closed)
        self.assertEqual(note.events[0].event_type.value, "sell")

    def test_unfilled_row_skipped(self):
        self._buys = [_row("0000001", "A005930", 0, 0)]  # cntr_qty=0
        res = autolink.sync_trades("20260630")
        self.assertEqual(res, {"created": 0, "updated": 0, "notes": 0})
        self.assertEqual(store.list_notes(), [])


if __name__ == "__main__":
    unittest.main()
