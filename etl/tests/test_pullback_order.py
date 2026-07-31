"""lower_low_bullish_reversal 1단계: 신호와 거래일 계산 테스트."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import duckdb

from scripts import run_pullback_order as target
from scripts.run_pullback_order import (
    candidate_trading_days,
    create_pullback_orders_table,
    exclude_held_tickers,
    find_first_signal,
    is_terminal_watchlist_order,
    load_open_close_bet_tickers,
    load_open_pullback_tickers,
    order_budget,
    order_qty,
    rank_candidates,
    record_signal_note,
)


def day(date: str, *, open_: int, low: int, close: int) -> dict:
    return {"date": date, "open": open_, "low": low, "close": close}


class CandidateTradingDaysTest(unittest.TestCase):
    def test_returns_only_next_five_available_trading_days(self):
        dates = [
            "20260703",  # watchlist 금요일
            "20260706", "20260707", "20260708", "20260709", "20260710",
            "20260713",
        ]
        self.assertEqual(
            candidate_trading_days("20260703", dates),
            ["20260706", "20260707", "20260708", "20260709", "20260710"],
        )

    def test_ignores_dates_on_or_before_watchlist_date(self):
        self.assertEqual(
            candidate_trading_days("20260703", ["20260702", "20260703", "20260706"]),
            ["20260706"],
        )


class FindFirstSignalTest(unittest.TestCase):
    def setUp(self):
        self.watchlist_day = day("20260703", open_=105, low=100, close=104)

    def test_enters_when_lower_low_and_bullish_are_same_day(self):
        signal = find_first_signal(
            self.watchlist_day,
            [day("20260706", open_=99, low=98, close=101)],
        )
        self.assertEqual(signal["signal_date"], "20260706")
        self.assertEqual(signal["prior_low"], 100)
        self.assertEqual(signal["signal_price"], 101)
        self.assertEqual(signal["wait_days"], 1)

    def test_bearish_lower_low_updates_prior_low_and_keeps_scanning(self):
        signal = find_first_signal(
            self.watchlist_day,
            [
                day("20260706", open_=101, low=98, close=99),
                day("20260707", open_=98, low=97, close=100),
            ],
        )
        self.assertEqual(signal["signal_date"], "20260707")
        self.assertEqual(signal["prior_low"], 98)
        self.assertEqual(signal["wait_days"], 2)

    def test_does_not_combine_lower_low_and_bullish_from_different_days(self):
        signal = find_first_signal(
            self.watchlist_day,
            [
                day("20260706", open_=101, low=98, close=99),
                day("20260707", open_=98, low=98, close=100),
            ],
        )
        self.assertIsNone(signal)

    def test_accepts_signal_on_d5(self):
        future = [
            day("20260706", open_=100, low=100, close=100),
            day("20260707", open_=100, low=100, close=100),
            day("20260708", open_=100, low=100, close=100),
            day("20260709", open_=100, low=100, close=100),
            day("20260710", open_=99, low=98, close=101),
        ]
        self.assertEqual(find_first_signal(self.watchlist_day, future)["wait_days"], 5)

    def test_ignores_signal_after_d5(self):
        future = [day(f"202607{value:02d}", open_=100, low=100, close=100) for value in range(6, 11)]
        future.append(day("20260713", open_=99, low=98, close=101))
        self.assertIsNone(find_first_signal(self.watchlist_day, future))

    def test_uses_1519_snapshot_as_current_day_close(self):
        signal = find_first_signal(
            self.watchlist_day,
            [{"date": "20260706", "open": 99, "low": 98, "current_price": 101}],
        )
        self.assertEqual(signal["signal_price"], 101)


class BatchCandidateSnapshotTest(unittest.TestCase):
    def setUp(self):
        watch_fd, watch_name = tempfile.mkstemp(suffix=".sqlite3")
        krx_fd, krx_name = tempfile.mkstemp(suffix=".duckdb")
        os.close(watch_fd)
        os.close(krx_fd)
        Path(watch_name).unlink()
        Path(krx_name).unlink()
        self.watch_db = Path(watch_name)
        self.krx_db = Path(krx_name)
        with target.connect_rw(self.watch_db) as con:
            con.execute("CREATE TABLE watchlist (date TEXT, stock_code TEXT)")
            con.execute("INSERT INTO watchlist VALUES ('20260714','005930')")
        with duckdb.connect(str(self.krx_db)) as con:
            con.execute("CREATE TABLE ohlcv (ticker TEXT,date TEXT,open INTEGER,low INTEGER,close INTEGER)")
            con.execute("INSERT INTO ohlcv VALUES ('005930','20260714',105,100,104)")

    def tearDown(self):
        self.watch_db.unlink(missing_ok=True)
        self.krx_db.unlink(missing_ok=True)

    @patch("scripts.run_pullback_order.batch_quote_snapshots")
    def test_candidate_loader_uses_one_batch_snapshot(self, batch):
        batch.return_value = {
            "005930": {"current_price": 101, "open": 99, "low": 98,
                       "upper_limit": 130}
        }
        result = target.load_today_signal_candidates(
            self.watch_db, self.krx_db, "20260715", "http://broker", {"max_wait_days": 5}
        )
        batch.assert_called_once_with("http://broker", ["005930"])
        self.assertEqual(result[0]["ticker"], "005930")
        self.assertEqual(result[0]["upper_limit"], 130)


class PullbackOrderStateTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")

    def tearDown(self):
        self.con.close()

    def test_open_pullback_tickers_is_empty_before_migration(self):
        self.assertEqual(load_open_pullback_tickers(self.con), set())

    def test_schema_creation_is_idempotent_and_has_position_fields(self):
        create_pullback_orders_table(self.con)
        create_pullback_orders_table(self.con)
        columns = {row[1] for row in self.con.execute("PRAGMA table_info(pullback_orders)")}
        self.assertTrue({"bought_at", "expiry_date", "sell_status", "exit_reason", "note_uid"} <= columns)

    def test_same_watchlist_item_is_terminal_after_order_attempt(self):
        create_pullback_orders_table(self.con)
        self.con.execute(
            "INSERT INTO pullback_orders "
            "(watchlist_date, signal_date, ticker, strategy, prior_low, day_open, "
            "signal_price, qty, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("20260701", "20260703", "005930", "lower_low_bullish_reversal",
             100, 99, 101, 2, "submitted", "2026-07-03T15:19:00"),
        )
        self.assertTrue(is_terminal_watchlist_order(self.con, "20260701", "005930"))
        self.assertFalse(is_terminal_watchlist_order(self.con, "20260702", "005930"))

    def test_loads_open_tickers_from_both_strategy_tables(self):
        create_pullback_orders_table(self.con)
        self.con.execute("CREATE TABLE close_bet_orders (ticker TEXT, status TEXT, sell_status TEXT)")
        self.con.executemany(
            "INSERT INTO close_bet_orders VALUES (?,?,?)",
            [("005930", "confirmed", None), ("035420", "confirmed", "filled")],
        )
        self.con.execute(
            "INSERT INTO pullback_orders (watchlist_date, signal_date, ticker, strategy, prior_low, "
            "day_open, signal_price, qty, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("20260701", "20260703", "000660", "lower_low_bullish_reversal",
             100, 99, 101, 2, "confirmed", "2026-07-03"),
        )
        self.assertEqual(load_open_close_bet_tickers(self.con), {"005930"})
        self.assertEqual(load_open_pullback_tickers(self.con), {"000660"})

    def test_close_bet_guard_supports_pre_exit_schema(self):
        self.con.execute("CREATE TABLE close_bet_orders (ticker TEXT, status TEXT)")
        self.con.execute("INSERT INTO close_bet_orders VALUES ('005930', 'submitted')")
        self.assertEqual(load_open_close_bet_tickers(self.con), {"005930"})


class PullbackCandidateGuardTest(unittest.TestCase):
    def test_limit_price_is_half_percent_above_and_rounded_up_to_tick(self):
        self.assertEqual(target.pullback_limit_price(16_360, 21_250), 16_450)

    def test_limit_price_never_exceeds_upper_limit(self):
        self.assertEqual(target.pullback_limit_price(2_375, 2_375), 2_375)

    def test_limit_price_uses_tick_band_of_final_price(self):
        cases = [
            (1_990, 2_000),
            (4_976, 5_010),
            (19_901, 20_050),
            (49_800, 50_100),
            (199_005, 200_500),
            (497_512, 500_000),
        ]
        for current, expected in cases:
            with self.subTest(current=current):
                self.assertEqual(
                    target.pullback_limit_price(current, 9_999_000), expected
                )

    @patch("scripts.run_pullback_order.requests.get")
    def test_batch_quote_snapshots_uses_one_request(self, get):
        get.return_value = Mock(
            json=Mock(return_value=[{
                "stk_cd": "005930", "cur_prc": 16_360, "upl_pric": 21_250,
                "raw": {"stk_nm": "삼성전자", "open_pric": "+16000", "low_pric": "-15800"},
            }])
        )
        snapshots = target.batch_quote_snapshots("http://broker", ["005930", "005930"])
        get.assert_called_once_with(
            "http://broker/quotes", params={"codes": "005930"}, timeout=target.REQUEST_TIMEOUT
        )
        self.assertEqual(
            snapshots["005930"],
            {"name": "삼성전자", "current_price": 16_360, "open": 16_000,
             "low": 15_800, "upper_limit": 21_250},
        )

    @patch("scripts.run_pullback_order.requests.post")
    def test_process_local_order_sends_limit_payload(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"accepted": True, "order_no": "123", "message": "ok"}),
        )
        result = target.pullback_limit_order(
            "http://broker", "005930", 2, 70_000, "pullback_order", False
        )
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result.get("requested_price"), 70_000)
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"symbol": "005930", "side": "buy", "qty": 2, "price": 70_000,
             "order_type": "limit", "source": "pullback_order"},
        )

    @patch("scripts.run_pullback_order.requests.post")
    def test_accepted_response_without_order_number_is_failed(self, post):
        post.return_value = Mock(
            status_code=200,
            json=Mock(return_value={"accepted": True, "order_no": None, "message": "ok"}),
        )
        result = target.pullback_limit_order(
            "http://broker", "005930", 2, 70_000, "pullback_order", False
        )
        self.assertEqual(result["status"], "failed")

    def test_excludes_tickers_held_by_either_strategy(self):
        candidates = [{"ticker": "005930"}, {"ticker": "000660"}, {"ticker": "035420"}]
        self.assertEqual(exclude_held_tickers(candidates, {"005930"}, {"000660"}), [{"ticker": "035420"}])

    def test_ranks_by_watchlist_date_and_ticker_with_configured_cap(self):
        candidates = [
            {"watchlist_date": "20260702", "ticker": "C"},
            {"watchlist_date": "20260701", "ticker": "B"},
            {"watchlist_date": "20260701", "ticker": "A"},
        ]
        self.assertEqual([item["ticker"] for item in rank_candidates(candidates, 2)], ["A", "B"])

    def test_budget_and_qty_use_config_values(self):
        self.assertEqual(order_budget(600_000, 3, 250_000), 200_000)
        self.assertEqual(order_qty(200_000, 10_000), 20)
        self.assertEqual(order_qty(200_000, 300_000), 0)


class RecordSignalNoteTest(unittest.TestCase):
    CANDIDATE = {"watchlist_date": "20260714", "signal_date": "20260721", "ticker": "017180",
                 "prior_low": 1211, "day_open": 1200, "signal_price": 1251}
    CONFIG = {"max_hold_days": 3}

    @patch("scripts.run_pullback_order.requests.post")
    @patch("scripts.run_pullback_order.requests.get")
    def test_creates_note_with_reason_and_no_target_price(self, get, post):
        get.return_value = Mock(json=Mock(return_value=[]))
        post.return_value = Mock(json=Mock(return_value={"uid": "note-1"}))
        uid = record_signal_note("http://b", self.CANDIDATE, {"order_no": "0160291"}, self.CONFIG)
        self.assertEqual(uid, "note-1")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["symbol"], "017180")
        self.assertIn("lower_low_bullish_reversal", payload["buy_reason"])
        self.assertIn("order_no=0160291", payload["memo"])
        self.assertNotIn("target_price", payload)

    @patch("scripts.run_pullback_order.requests.patch")
    @patch("scripts.run_pullback_order.requests.get")
    def test_updates_existing_note_instead_of_creating(self, get, patch_req):
        get.return_value = Mock(json=Mock(return_value=[{"uid": "old", "created_at": "2026-07-21"}]))
        uid = record_signal_note("http://b", self.CANDIDATE, {"order_no": "1"}, self.CONFIG)
        self.assertEqual(uid, "old")
        patch_req.assert_called_once()


if __name__ == "__main__":
    unittest.main()
