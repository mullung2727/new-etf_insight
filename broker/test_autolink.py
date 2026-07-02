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
        # create_note가 실제 키움 ka10001을 때리지 않도록 종목명 조회 스텁.
        self._orig_resolve = store.resolve_name
        store.resolve_name = lambda symbol: None

    def tearDown(self):
        orders.get_order_history = self._orig
        store.resolve_name = self._orig_resolve
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

    def test_resync_closed_position_no_orphan(self):
        # 회귀: 닫힌 노트를 (폴링이 같은 날 반복하듯) 재동기화해도 빈 고아 노트가
        # 생기지 않아야 한다. kt00007은 닫힌 주문 행을 계속 반환한다.
        self._buys = [_row("0000001", "A005930", 10, 70000)]
        self._sells = [_row("0000002", "A005930", 10, 72000, "143000")]
        autolink.sync_trades("20260630")
        note = self._only_note()
        self.assertEqual(note.status, NoteStatus.closed)
        # 같은 행으로 두 번 더 재동기화
        autolink.sync_trades("20260630")
        autolink.sync_trades("20260630")
        notes = store.list_notes()
        self.assertEqual(len(notes), 1)  # 고아 노트 없음
        note = store.get_note(notes[0].uid)
        self.assertEqual(len(note.events), 2)  # 중복 이벤트 없음
        self.assertEqual(note.status, NoteStatus.closed)

    def test_resync_multibuy_keeps_first_buy(self):
        # 회귀: 매수 2건 노트를 재동기화해도 최초 'buy'가 'add_buy'로 뒤집히면 안 됨.
        self._buys = [
            _row("0000001", "A005930", 10, 70000, "093000"),
            _row("0000002", "A005930", 5, 71000, "100000"),
        ]
        autolink.sync_trades("20260630")
        autolink.sync_trades("20260630")  # 재동기화
        note = self._only_note()
        types = [e.event_type.value for e in note.events]  # executed_at 정렬
        self.assertEqual(types.count("buy"), 1)
        self.assertEqual(types.count("add_buy"), 1)
        # 가장 이른 주문이 'buy'
        first = min(note.events, key=lambda e: (e.executed_at, e.order_no))
        self.assertEqual(first.event_type.value, "buy")
        self.assertEqual(note.status, NoteStatus.open)

    def test_partial_then_full_sell_typed_by_order(self):
        # buy 10 → 분할매도 4(이른 시각) → 전량매도 6(늦은 시각).
        self._buys = [_row("0000001", "A005930", 10, 70000, "093000")]
        self._sells = [
            _row("0000002", "A005930", 4, 72000, "100000"),
            _row("0000003", "A005930", 6, 73000, "143000"),
        ]
        autolink.sync_trades("20260630")
        autolink.sync_trades("20260630")  # 재동기화해도 동일
        note = self._only_note()
        by_no = {e.order_no: e.event_type.value for e in note.events}
        self.assertEqual(by_no["0000002"], "partial_sell")
        self.assertEqual(by_no["0000003"], "sell")
        self.assertEqual(note.status, NoteStatus.closed)


if __name__ == "__main__":
    unittest.main()
