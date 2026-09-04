"""pullback TP/SL·거래일 만기 청산 TDD 테스트."""
from __future__ import annotations

import argparse
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from unittest.mock import patch

from scripts.run_pullback_exit import (
    advance_holding_day, decide_exit, expire_stale_orders, is_exit_window_started, load_positions,
    mark_missing_positions, mark_sell_ordered, run_loop_step, sell_quantity, settle_sell_orders,
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
        realized = {"found": True, "pnl_pct": 2.5, "cmsn": 30, "tax": 5, "sel_pl_won": 65}
        with patch("scripts.run_pullback_exit.fetch_order_history", return_value=history) as fetch, \
                patch("scripts.run_pullback_exit.fetch_realized", return_value=realized):
            self.assertEqual(settle_sell_orders(self.db, "http://broker", "20260716"), 1)
        # kt00007은 매수/매도 분리 조회 — side="sell" 빠지면 매도 체결을 영원히 못 찾는다
        self.assertEqual(fetch.call_args.kwargs.get("side"), "sell")
        with connect_ro(self.db) as con:
            row = con.execute("SELECT status,sell_status,sell_price,sell_qty,exit_reason,"
                              "pnl_pct,sell_cmsn,sell_tax,sell_pl_won FROM pullback_orders").fetchone()
        # net 저장: 키움 %(-4.84식)를 /100 분수로, 수수료·세금·net손익금 원값
        self.assertEqual(row, ("closed", "filled", 1030, 3, "tp", 0.025, 30, 5, 65))

    def test_settle_falls_back_to_gross_pnl_when_realized_missing(self):
        position = load_positions(self.db)[0]
        mark_sell_ordered(self.db, position, "000077", "tp")
        history = [{"order_no": "77", "cntr_uv": 1030, "cntr_qty": 3}]
        with patch("scripts.run_pullback_exit.fetch_order_history", return_value=history), \
                patch("scripts.run_pullback_exit.fetch_realized", return_value=None):
            settle_sell_orders(self.db, "http://broker", "20260716")
        with connect_ro(self.db) as con:
            row = con.execute("SELECT pnl_pct,sell_cmsn,sell_tax,sell_pl_won FROM pullback_orders").fetchone()
        # realized 없음 → gross(1030/1000-1=0.03), 수수료·세금 NULL
        self.assertAlmostEqual(row[0], 0.03)
        self.assertEqual(row[1:], (None, None, None))


if __name__ == "__main__":
    unittest.main()


class MarkMissingPositionsTest(unittest.TestCase):
    """잔고에서 사라진 눌림목 포지션을 종료로 확정한다.

    기존 run_cycle 은 매도가능수량 0이면 continue 로 넘어가기만 해서 sell_status 가
    영원히 빈 값으로 남았다. 종가베팅 쪽 reconcile_balance 와 같은 결함이 두 벌로
    존재했고, 그래서 판정을 trading_batch_common 으로 합쳤다.
    """

    def setUp(self):
        fd, name = tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(name)
        self.db = Path(name)
        with connect_rw(self.db) as con:
            create_pullback_orders_table(con)
            con.execute(
                "INSERT INTO pullback_orders (watchlist_date,signal_date,ticker,strategy,"
                "prior_low,day_open,signal_price,qty,status,buy_price,buy_qty,bought_at,"
                "remaining_hold_days,created_at) VALUES "
                "('20260819','20260819','025320','lower_low_bullish_reversal',100,99,101,"
                "75,'confirmed',3970,75,'2026-08-19 16:00:04+09:00',3,'2026-08-19')"
            )

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _sell_status(self):
        with connect_ro(self.db) as con:
            return con.execute(
                "SELECT sell_status FROM pullback_orders WHERE ticker='025320'"
            ).fetchone()[0]

    def test_position_absent_from_balance_is_closed_as_missing(self):
        mark_missing_positions(self.db, load_positions(self.db), {"acnt_evlt_remn_indv_tot": []})
        self.assertEqual(self._sell_status(), "missing")
        self.assertEqual(load_positions(self.db), [])

    def test_held_position_is_untouched(self):
        balance = {"acnt_evlt_remn_indv_tot": [{"stk_cd": "A025320", "trde_able_qty": "75"}]}
        mark_missing_positions(self.db, load_positions(self.db), balance)
        self.assertIsNone(self._sell_status())

    def test_balance_lookup_failure_marks_nothing(self):
        mark_missing_positions(self.db, load_positions(self.db), {})
        self.assertIsNone(self._sell_status())


class ExpireStaleOrdersTest(unittest.TestCase):
    """전일 미체결 주문이 종료 처리돼 중복매수 가드를 막지 않는지."""

    def setUp(self):
        fd, name = tempfile.mkstemp(suffix=".sqlite3"); os.close(fd); os.unlink(name)
        self.db = Path(name)
        with connect_rw(self.db) as con:
            create_pullback_orders_table(con)
            for watchlist_date, signal_date, ticker, status in (
                ("20260818", "20260820", "950260", "unconfirmed"),   # 전일 미체결
                ("20260818", "20260820", "111111", "submitted"),     # 전일 전송만
                ("20260818", "20260821", "222222", "unconfirmed"),   # 당일 — 아직 대조 전
                ("20260818", "20260820", "333333", "confirmed"),     # 전일 체결
            ):
                con.execute(
                    "INSERT INTO pullback_orders (watchlist_date,signal_date,ticker,strategy,"
                    "prior_low,day_open,signal_price,qty,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (watchlist_date, signal_date, ticker, "lower_low_bullish_reversal",
                     100, 99, 101, 5, status, "2026-08-20"),
                )

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _status(self, ticker):
        with connect_ro(self.db) as con:
            return con.execute(
                "SELECT status FROM pullback_orders WHERE ticker=?", (ticker,)
            ).fetchone()[0]

    def test_previous_day_unfilled_orders_expire(self):
        self.assertEqual(expire_stale_orders(self.db, "20260821"), 2)
        self.assertEqual(self._status("950260"), "expired")
        self.assertEqual(self._status("111111"), "expired")

    def test_same_day_and_confirmed_untouched(self):
        expire_stale_orders(self.db, "20260821")
        self.assertEqual(self._status("222222"), "unconfirmed")
        self.assertEqual(self._status("333333"), "confirmed")

    def test_idempotent(self):
        expire_stale_orders(self.db, "20260821")
        self.assertEqual(expire_stale_orders(self.db, "20260821"), 0)


def _at(hms: str) -> datetime:
    return datetime.fromisoformat(f"2026-09-04T{hms}+09:00")


class ExitWindowTest(unittest.TestCase):
    """정규장 시작 경계. 장전 예상호가는 TP/SL 판정에 쓸 수 없다."""

    def test_preopen_is_blocked(self):
        self.assertFalse(is_exit_window_started(_at("08:50:00"), "09:00:00"))
        self.assertFalse(is_exit_window_started(_at("08:59:59"), "09:00:00"))

    def test_regular_session_start_is_allowed(self):
        self.assertTrue(is_exit_window_started(_at("09:00:00"), "09:00:00"))
        self.assertTrue(is_exit_window_started(_at("15:19:00"), "09:00:00"))


class ExitLoopStepTest(unittest.TestCase):
    """가드가 broker 호출보다 앞에 있는지. 경계값이 아니라 호출 여부를 본다."""

    def setUp(self):
        self.calls: list[tuple] = []
        self.args = argparse.Namespace(
            window_start="09:00:00", force_exit_time="15:19:00", stop_time="15:25:00",
        )

    def _step(self, hms: str, counted: bool = False):
        return run_loop_step(
            _at(hms), self.args, "http://broker", {}, True, counted,
            settle=lambda *a: self.calls.append(("settle",) + a[2:]),
            cycle=lambda *a: self.calls.append(("cycle",) + a[3:]),
        )

    def test_preopen_touches_no_broker_function(self):
        # 써니전자 매도가 접수된 시각. settle·cycle 어느 쪽도 불려선 안 된다.
        self.assertEqual(self._step("08:53:14"), ("wait", False))
        self.assertEqual(self.calls, [])

    def test_regular_session_runs_both(self):
        self.assertEqual(self._step("09:00:00"), ("ran", False))
        self.assertEqual([c[0] for c in self.calls], ["settle", "cycle"])

    def test_stop_time_wins_over_window(self):
        self.assertEqual(self._step("15:25:00"), ("stop", False))
        self.assertEqual(self.calls, [])

    def test_force_marks_counted_once(self):
        self.assertEqual(self._step("15:19:00"), ("ran", True))
        self.assertTrue(self.calls[1][2])          # cycle(force=True)
        self.calls.clear()
        self.assertEqual(self._step("15:19:00", counted=True), ("ran", True))
        self.assertFalse(self.calls[1][2])         # 같은 날 두 번 차감하지 않는다
