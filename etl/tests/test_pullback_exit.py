"""pullback TP/SL·거래일 만기 청산 TDD 테스트."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from scripts.run_pullback_exit import (
    advance_holding_day, decide_exit, load_positions, mark_sell_ordered,
    sell_quantity, settle_sell_orders,
)
from scripts.run_pullback_order import create_pullback_orders_table
from scripts.wl_sqlite import connect_ro, connect_rw


class PullbackExitTest(unittest.TestCase):
    def setUp(self):
        fd, name = tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(name)
        self.db = Path(name)
        with connect_rw(self.db) as con:
            create_pullback_orders_table(con)
            con.execute(
                "INSERT INTO pullback_orders (watchlist_date,signal_date,ticker,strategy,prior_low,"
                "day_open,signal_price,qty,status,buy_price,buy_qty,bought_at,remaining_hold_days,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("20260710", "20260715", "005930", "lower_low_bullish_reversal", 100, 99, 101,
                 3, "confirmed", 1000, 3, "2026-07-15 15:30:00", 3, "2026-07-15"),
            )

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_tp_sl_use_best_bid(self):
        self.assertEqual(decide_exit(1030, 1000, 0.03, 0.03), "tp")
        self.assertEqual(decide_exit(970, 1000, 0.03, 0.03), "sl")
        self.assertIsNone(decide_exit(1000, 1000, 0.03, 0.03))

    def test_holding_day_decrements_once_per_confirmed_market_day(self):
        self.assertEqual(advance_holding_day(self.db, "20260716", True), 0)
        self.assertEqual(advance_holding_day(self.db, "20260716", True), 0)
        with connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT remaining_hold_days FROM pullback_orders").fetchone()[0], 2)

    def test_holiday_does_not_decrement(self):
        advance_holding_day(self.db, "20260716", False)
        with connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT remaining_hold_days FROM pullback_orders").fetchone()[0], 3)

    def test_third_market_day_sets_expiry_and_returns_due_count(self):
        advance_holding_day(self.db, "20260716", True)
        advance_holding_day(self.db, "20260717", True)
        self.assertEqual(advance_holding_day(self.db, "20260720", True), 1)
        with connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT remaining_hold_days,expiry_date FROM pullback_orders").fetchone(),
                             (0, "20260720"))

    def test_sell_quantity_never_exceeds_strategy_or_account_quantity(self):
        self.assertEqual(sell_quantity(3, 5), 3)
        self.assertEqual(sell_quantity(3, 2), 2)
        self.assertEqual(sell_quantity(3, 0), 0)

    def test_sell_order_state_blocks_duplicate_and_fill_closes_position(self):
        position = load_positions(self.db)[0]
        mark_sell_ordered(self.db, position, "000077", "tp")
        self.assertEqual(load_positions(self.db), [])
        history = [{"order_no": "77", "cntr_uv": 1030, "cntr_qty": 3}]
        with patch("scripts.run_pullback_exit.fetch_order_history", return_value=history):
            self.assertEqual(settle_sell_orders(self.db, "http://broker", "20260716"), 1)
        with connect_ro(self.db) as con:
            row = con.execute("SELECT status,sell_status,sell_price,sell_qty,exit_reason FROM pullback_orders").fetchone()
        self.assertEqual(row, ("closed", "filled", 1030, 3, "tp"))


if __name__ == "__main__":
    unittest.main()
