"""진입가 도달 감시 판정 + Discord 알림 테스트.

    .venv/Scripts/python.exe -m unittest test_notes_idea_alert

키움 시세(get_watchlist_quotes)와 웹훅 전송(send_discord_alert)을 monkeypatch해
HTTP 없이 판정 로직만 검증한다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kiwoom import quotes
from notes import alert, db, store
from notes.models import NoteCreate


class _Base(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self.db = Path(path)
        db.NOTES_DB_PATH = self.db
        db._conn = None
        db.init()
        self._orig_resolve = store.resolve_name
        store.resolve_name = lambda symbol: None
        self._orig_batch = quotes.get_watchlist_quotes
        self._orig_send = alert.send_discord_alert
        self.sent: list[str] = []
        alert.send_discord_alert = lambda msg: self.sent.append(msg) or True

    def tearDown(self):
        store.resolve_name = self._orig_resolve
        quotes.get_watchlist_quotes = self._orig_batch
        alert.send_discord_alert = self._orig_send
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        self.db.unlink(missing_ok=True)

    def _quotes(self, mapping: dict[str, int]):
        # {code: cur_prc} 로 get_watchlist_quotes 스텁.
        quotes.get_watchlist_quotes = lambda codes: [
            {"stk_cd": c, "cur_prc": mapping.get(c, 0)} for c in codes
        ]


class HitDetection(_Base):
    def test_alerts_when_price_at_or_below_entry(self):
        note = store.create_note(NoteCreate(symbol="005930", entry_price=68000))
        self._quotes({"005930": 68000})  # 정확히 도달
        fired = alert.run_idea_alert_check("20260722")
        self.assertEqual(fired, [note.uid])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(store.get_note(note.uid).alerted_on, "20260722")

    def test_no_alert_above_entry(self):
        store.create_note(NoteCreate(symbol="005930", entry_price=68000))
        self._quotes({"005930": 70000})
        self.assertEqual(alert.run_idea_alert_check("20260722"), [])
        self.assertEqual(self.sent, [])

    def test_zero_price_is_not_a_hit(self):
        # 거래정지·조회실패로 cur_prc=0 이면 도달로 오판하지 않는다.
        store.create_note(NoteCreate(symbol="005930", entry_price=68000))
        self._quotes({"005930": 0})
        self.assertEqual(alert.run_idea_alert_check("20260722"), [])

    def test_once_per_day(self):
        note = store.create_note(NoteCreate(symbol="005930", entry_price=68000))
        self._quotes({"005930": 67000})
        alert.run_idea_alert_check("20260722")
        # 같은 날 재실행 — 이미 alerted_on=오늘이라 후보에서 빠진다.
        fired2 = alert.run_idea_alert_check("20260722")
        self.assertEqual(fired2, [])
        self.assertEqual(len(self.sent), 1)

    def test_no_candidates_skips_quote_call(self):
        # 후보 0건이면 시세 콜을 하지 않는다(빈 codes로 불필요 호출 방지).
        called = []
        quotes.get_watchlist_quotes = lambda codes: called.append(codes) or []
        self.assertEqual(alert.run_idea_alert_check("20260722"), [])
        self.assertEqual(called, [])


class MarketHours(unittest.TestCase):
    """장중 게이트 — 평일 09:00~15:30 KST만 감시."""

    def _at(self, y, m, d, hh, mm):
        from datetime import datetime, timedelta, timezone

        import main
        return main._in_market_hours(
            datetime(y, m, d, hh, mm, tzinfo=timezone(timedelta(hours=9)))
        )

    def test_open(self):
        self.assertTrue(self._at(2026, 7, 22, 9, 0))    # 수 개장
        self.assertTrue(self._at(2026, 7, 22, 15, 30))  # 마감 경계

    def test_closed_off_hours(self):
        self.assertFalse(self._at(2026, 7, 22, 8, 59))
        self.assertFalse(self._at(2026, 7, 22, 15, 31))

    def test_closed_weekend(self):
        self.assertFalse(self._at(2026, 7, 25, 10, 0))  # 토


class Webhook(unittest.TestCase):
    def test_send_returns_false_when_unset(self):
        # DISCORD_WEBHOOK_URL 미설정이면 예외 없이 False.
        orig = os.environ.pop("DISCORD_WEBHOOK_URL", None)
        try:
            self.assertFalse(alert.send_discord_alert("test"))
        finally:
            if orig is not None:
                os.environ["DISCORD_WEBHOOK_URL"] = orig


if __name__ == "__main__":
    unittest.main()
