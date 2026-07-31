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
BATCH_CANDIDATE = {**CANDIDATE, "signal_price": 9_950, "upper_limit": 13_000}


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
             patch.object(target, "load_today_signal_candidates", return_value=[BATCH_CANDIDATE]), \
             patch.object(target, "now_seoul", return_value=now), \
             patch.object(target, "available_cash", return_value=300000), \
             patch.object(target, "pullback_limit_order", return_value=result) as order, \
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

    def test_all_orders_are_sent_before_any_note_work(self):
        events = []
        candidates = [
            {**CANDIDATE, "ticker": "005930", "signal_price": 10_000,
             "upper_limit": 13_000},
            {**CANDIDATE, "ticker": "000660", "signal_price": 20_000,
             "upper_limit": 26_000},
        ]

        def order(_url, ticker, _qty, price, _source, _dry_run, **_kwargs):
            events.append(("order", ticker, price))
            return {"order_no": ticker, "status": "submitted", "message": ""}

        def note(_url, candidate, _result, _config):
            events.append(("note", candidate["ticker"]))
            return f"note-{candidate['ticker']}"

        now = datetime(2026, 7, 15, 15, 19, 10)
        with target.connect_rw(self.db) as con:
            target.create_pullback_orders_table(con)
        with patch.object(target, "DEFAULT_WATCHLIST_DB", self.db), \
             patch.object(target, "load", return_value=CONFIG), \
             patch.object(target, "load_today_signal_candidates", return_value=candidates), \
             patch.object(target, "now_seoul", side_effect=[now, now]) as clock, \
             patch.object(target, "available_cash", return_value=300000), \
             patch.object(target, "pullback_limit_order", side_effect=order), \
             patch.object(target, "persist_order_result"), \
             patch.object(target, "record_signal_note", side_effect=note), \
             patch.object(target, "send_discord") as notify, \
             patch("sys.argv", ["run_pullback_order.py", "--dry-run", "false"]):
            self.assertEqual(target.main(), 0)

        self.assertEqual([event[0] for event in events], ["order", "order", "note", "note"])
        self.assertEqual(events[0], ("order", "000660", 20_100))
        self.assertEqual(events[1], ("order", "005930", 10_050))
        self.assertEqual(clock.call_count, 2)
        message = notify.call_args.args[0]
        self.assertIn("[눌림목 15:19 주문 결과] 2026-07-15", message)
        self.assertIn("후보 2 · 접수 2", message)

    def test_candidate_scan_finishing_after_deadline_notifies_failure(self):
        start = datetime(2026, 7, 15, 15, 19, 10)
        late = datetime(2026, 7, 15, 15, 20, 0)
        with patch.object(target, "load", return_value=CONFIG), \
             patch.object(target, "load_today_signal_candidates", return_value=[BATCH_CANDIDATE]), \
             patch.object(target, "now_seoul", side_effect=[start, late]), \
             patch.object(target, "pullback_limit_order") as order, \
             patch.object(target, "send_discord") as notify, \
             patch("sys.argv", ["run_pullback_order.py"]):
            self.assertEqual(target.main(), 1)
        order.assert_not_called()
        self.assertTrue(notify.called)
        self.assertEqual(
            notify.call_args.args[0],
            "[눌림목 15:19 실행 실패] 2026-07-15\n후보 1 · 주문 0\n사유: 후보 계산이 15:20:00 이후 완료됨",
        )

    def test_order_summary_is_human_readable_and_distinguishes_status(self):
        processed = [
            ({"ticker": "047920", "name": "HLB제약", "signal_price": 8_440},
             35, 8_490,
             {"status": "submitted", "order_no": "0157831", "message": ""}),
            ({"ticker": "008290", "name": "원풍물산", "signal_price": 422},
             710, 425,
             {"status": "rejected", "order_no": "",
              "message": "모의투자 매매제한 종목입니다."}),
        ]
        message = target.format_order_summary("20260715", processed, note_failures=0)
        self.assertEqual(
            message,
            "[눌림목 15:19 주문 결과] 2026-07-15\n"
            "후보 2 · 접수 1 · 거절 1 · 실패 0 · 건너뜀 0\n"
            "※ 주문접수 기준이며 체결은 아직 미확정\n\n"
            "✅ HLB제약(047920)\n"
            "8,440원 → 지정가 8,490원 · 35주\n"
            "주문번호 0157831\n\n"
            "❌ 원풍물산(008290)\n"
            "422원 → 지정가 425원 · 710주\n"
            "사유: 모의투자 매매제한 종목입니다."
        )

    def test_no_candidate_message_is_readable(self):
        now = datetime(2026, 7, 15, 15, 19, 10)
        with patch.object(target, "load", return_value=CONFIG), \
             patch.object(target, "load_today_signal_candidates", return_value=[]), \
             patch.object(target, "now_seoul", return_value=now), \
             patch.object(target, "send_discord") as notify, \
             patch("sys.argv", ["run_pullback_order.py"]):
            self.assertEqual(target.main(), 0)
        self.assertEqual(
            notify.call_args.args[0],
            "[눌림목 15:19] 2026-07-15\n주문 후보 없음",
        )

    def test_broker_rejection_is_persisted(self):
        code, _ = self.run_main("false", order_result={"order_no": "", "status": "rejected", "message": "guard"})
        self.assertEqual(code, 0)
        with connect_ro(self.db) as con:
            self.assertEqual(con.execute("SELECT status, message FROM pullback_orders").fetchone(),
                             ("rejected", "guard"))


if __name__ == "__main__":
    unittest.main()
