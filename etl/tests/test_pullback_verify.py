"""pullback 체결검증·투자노트 연동 TDD 테스트."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.run_pullback_order import create_pullback_orders_table
from scripts.run_pullback_verify import aggregate_fills, record_investment_note, verify_orders
from scripts.wl_sqlite import connect_ro, connect_rw


class PullbackVerifyTest(unittest.TestCase):
    def setUp(self):
        fd, name = tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(name)
        self.db = Path(name)
        with connect_rw(self.db) as con:
            create_pullback_orders_table(con)
            con.execute(
                "INSERT INTO pullback_orders (watchlist_date, signal_date, ticker, strategy, "
                "prior_low, day_open, signal_price, qty, status, buy_order_no, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("20260710", "20260715", "005930", "lower_low_bullish_reversal",
                 100, 99, 101, 3, "submitted", "000050", "2026-07-15"),
            )

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_partial_fills_use_weighted_average_and_sum_quantity(self):
        fills = aggregate_fills([
            {"order_no": "50", "cntr_uv": 1000, "cntr_qty": 1},
            {"order_no": "000050", "cntr_uv": 1100, "cntr_qty": 2},
        ])
        self.assertEqual(fills["50"], {"price": 1067, "qty": 3})

    def test_confirmed_order_records_hold_counter_and_note_once(self):
        note = Mock(return_value="note-1")
        history = [{"order_no": "50", "cntr_uv": 1000, "cntr_qty": 3}]
        summary = verify_orders(self.db, "20260715", history, 3, note,
                                datetime(2026, 7, 15, 16, 0))
        self.assertEqual(summary["confirmed"], 1)
        with connect_ro(self.db) as con:
            row = con.execute(
                "SELECT status,buy_price,buy_qty,bought_at,remaining_hold_days,note_uid "
                "FROM pullback_orders"
            ).fetchone()
        self.assertEqual(row, ("confirmed", 1000, 3, "2026-07-15 16:00:00", 3, "note-1"))
        verify_orders(self.db, "20260715", history, 3, note, datetime(2026, 7, 15, 16, 1))
        note.assert_called_once()

    def test_note_failure_keeps_confirmed_trade_retryable(self):
        failing = Mock(side_effect=RuntimeError("notes down"))
        history = [{"order_no": "50", "cntr_uv": 1000, "cntr_qty": 3}]
        summary = verify_orders(self.db, "20260715", history, 3, failing,
                                datetime(2026, 7, 15, 16, 0))
        self.assertEqual(summary["note_failed"], 1)
        with connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT status,note_uid FROM pullback_orders").fetchone(),
                             ("confirmed", None))
        success = Mock(return_value="note-1")
        verify_orders(self.db, "20260715", [], 3, success, datetime(2026, 7, 15, 16, 1))
        success.assert_called_once()

    @patch("scripts.run_pullback_verify.requests.post")
    @patch("scripts.run_pullback_verify.requests.get")
    def test_note_payload_contains_strategy_and_syncs_trade(self, get: Mock, post: Mock):
        get_response = Mock(json=lambda: []); get_response.raise_for_status = Mock(); get.return_value = get_response
        create_response = Mock(json=lambda: {"uid": "note-1"}); create_response.raise_for_status = Mock()
        sync_response = Mock(); sync_response.raise_for_status = Mock(); post.side_effect = [create_response, sync_response]
        order = {"ticker": "005930", "buy_price": 1000, "watchlist_date": "20260710",
                 "signal_date": "20260715", "prior_low": 100, "day_open": 99,
                 "signal_price": 101, "buy_order_no": "50"}
        uid = record_investment_note("http://broker", order, {"tp": 0.03, "max_hold_days": 3})
        self.assertEqual(uid, "note-1")
        payload = post.call_args_list[0].kwargs["json"]
        self.assertIn("lower_low_bullish_reversal", payload["buy_reason"])
        self.assertEqual(post.call_args_list[1].kwargs["params"], {"date": "20260715"})


if __name__ == "__main__":
    unittest.main()
