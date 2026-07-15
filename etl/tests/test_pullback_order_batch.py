"""15:19 pullback 매수 배치 통합 TDD 테스트."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts import run_pullback_order as target
from scripts.wl_sqlite import connect_ro


CONFIG = {"budget_per_stock": 300000, "max_new_positions": 3, "tp": 0.03,
          "sl": 0.03, "max_wait_days": 5, "max_hold_days": 3}
CANDIDATE = {"watchlist_date": "20260710", "signal_date": "20260715", "ticker": "005930",
             "prior_low": 100, "day_open": 99, "day_low": 98, "signal_price": 101, "wait_days": 3}


class PullbackOrderBatchTest(unittest.TestCase):
    def setUp(self):
        fd, name = tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(name)
        self.db = Path(name)

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def run_main(self, dry_run="true", now=datetime(2026, 7, 15, 15, 19, 10), order_result=None):
        result = order_result or {"order_no": "DRY", "status": "dry_run", "message": ""}
        with patch.object(target, "DEFAULT_WATCHLIST_DB", self.db), \
             patch.object(target, "load", return_value=CONFIG), \
             patch.object(target, "load_today_signal_candidates", return_value=[CANDIDATE]), \
             patch.object(target, "now_seoul", return_value=now), \
             patch.object(target, "available_cash", return_value=300000), \
             patch.object(target, "current_price", return_value=10000), \
             patch.object(target, "market_order", return_value=result) as order, \
             patch.object(target, "send_discord"), \
             patch("sys.argv", ["run_pullback_order.py", "--dry-run", dry_run]):
            code = target.main()
        return code, order

    def test_dry_run_records_state_without_real_order_contract(self):
        code, order = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(order.call_args.args[5])
        with connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT status, qty FROM pullback_orders").fetchone(), ("dry_run", 30))

    def test_outside_order_window_aborts_without_row(self):
        code, order = self.run_main(now=datetime(2026, 7, 15, 15, 20))
        self.assertEqual(code, 1)
        order.assert_not_called()
        self.assertFalse(self.db.exists())

    def test_broker_rejection_is_persisted(self):
        code, _ = self.run_main("false", order_result={"order_no": "", "status": "rejected", "message": "guard"})
        self.assertEqual(code, 0)
        with connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT status, message FROM pullback_orders").fetchone(),
                             ("rejected", "guard"))


if __name__ == "__main__":
    unittest.main()
