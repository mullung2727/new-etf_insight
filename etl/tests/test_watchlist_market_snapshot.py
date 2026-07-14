from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts.collect_watchlist_market_snapshot import (
    is_capture_window,
    parse_snapshot,
    run,
    upsert_snapshots,
)


SEOUL = ZoneInfo("Asia/Seoul")


class WatchlistMarketSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "watchlist.sqlite3"
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("CREATE TABLE watchlist(date TEXT, stock_code TEXT, PRIMARY KEY(date,stock_code))")
            con.execute("CREATE TABLE llm_scores(date TEXT,ticker TEXT,score INTEGER,PRIMARY KEY(date,ticker))")
            con.execute("INSERT INTO watchlist VALUES ('20260714','005930')")
            con.execute("INSERT INTO llm_scores VALUES ('20260714','005930',58)")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parse_signed_kiwoom_fields(self) -> None:
        row = parse_snapshot("005930", {
            "cur_prc": "-254500", "open_pric": "+255000", "high_pric": "+270000",
            "trde_qty": "24,547,141", "flu_rt": "-0.20",
        }, datetime(2026, 7, 14, 15, 0, 5, tzinfo=SEOUL))
        self.assertEqual(row["current_price"], 254500)
        self.assertEqual(row["volume"], 24547141)
        self.assertEqual(row["change_rate"], -0.2)

    def test_new_table_does_not_change_existing_tables(self) -> None:
        row = parse_snapshot("005930", {
            "cur_prc": "100", "open_pric": "95", "high_pric": "110",
            "trde_qty": "1000", "flu_rt": "+2.00",
        }, datetime(2026, 7, 14, 15, 0, 5, tzinfo=SEOUL))
        self.assertEqual(upsert_snapshots(self.db, "20260714", [row]), 1)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT score FROM llm_scores").fetchone()[0], 58)
            self.assertEqual(con.execute("SELECT stock_code FROM watchlist").fetchone()[0], "005930")
            saved = con.execute("SELECT current_price,high_price,source FROM watchlist_market_snapshots").fetchone()
        self.assertEqual(saved, (100, 110, "ka10001"))

    def test_capture_window_is_limited_to_1500_through_1502(self) -> None:
        self.assertTrue(is_capture_window(datetime(2026, 7, 14, 15, 0, tzinfo=SEOUL)))
        self.assertTrue(is_capture_window(datetime(2026, 7, 14, 15, 2, 59, tzinfo=SEOUL)))
        self.assertFalse(is_capture_window(datetime(2026, 7, 14, 15, 3, tzinfo=SEOUL)))

    def test_run_reads_candidates_and_saves_all_snapshots(self) -> None:
        now = datetime(2026, 7, 14, 15, 0, 5, tzinfo=SEOUL)

        def fake_fetcher(_token, _host, ticker, clock):
            return parse_snapshot(ticker, {
                "cur_prc": "100", "open_pric": "95", "high_pric": "110",
                "trde_qty": "1000", "flu_rt": "+2.00",
            }, clock())

        with patch(
            "scripts.collect_watchlist_market_snapshot.get_token", return_value="token"
        ):
            count = run(
                self.db, "20260714", clock=lambda: now,
                fetcher=fake_fetcher, sleep_fn=lambda _seconds: None,
            )
        self.assertEqual(count, 1)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM watchlist_market_snapshots").fetchone()[0], 1
            )

    def test_one_ticker_failure_does_not_block_other_snapshots(self) -> None:
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO watchlist VALUES ('20260714','000660')")
        now = datetime(2026, 7, 14, 15, 0, 5, tzinfo=SEOUL)

        def fake_fetcher(_token, _host, ticker, clock):
            if ticker == "005930":
                raise ValueError("시세 누락")
            return parse_snapshot(ticker, {
                "cur_prc": "100", "open_pric": "95", "high_pric": "110",
                "trde_qty": "1000", "flu_rt": "+2.00",
            }, clock())

        with patch(
            "scripts.collect_watchlist_market_snapshot.get_token", return_value="token"
        ):
            count = run(
                self.db, "20260714", clock=lambda: now,
                fetcher=fake_fetcher, sleep_fn=lambda _seconds: None,
            )
        self.assertEqual(count, 1)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(
                con.execute("SELECT ticker FROM watchlist_market_snapshots").fetchone()[0], "000660"
            )

    def test_all_ticker_failures_return_zero_without_raising(self) -> None:
        now = datetime(2026, 7, 14, 15, 0, 5, tzinfo=SEOUL)
        with patch(
            "scripts.collect_watchlist_market_snapshot.get_token", return_value="token"
        ):
            count = run(
                self.db, "20260714", clock=lambda: now,
                fetcher=lambda *_args: (_ for _ in ()).throw(RuntimeError("API 실패")),
                sleep_fn=lambda _seconds: None,
            )
        self.assertEqual(count, 0)

    def test_1459_start_waits_until_1500(self) -> None:
        times = iter([
            datetime(2026, 7, 14, 14, 59, 0, tzinfo=SEOUL),
            datetime(2026, 7, 14, 15, 0, 0, tzinfo=SEOUL),
            datetime(2026, 7, 14, 15, 0, 1, tzinfo=SEOUL),
        ])
        waits = []

        def fake_fetcher(_token, _host, ticker, clock):
            return parse_snapshot(ticker, {
                "cur_prc": "100", "open_pric": "95", "high_pric": "110",
                "trde_qty": "1000", "flu_rt": "+2.00",
            }, clock())

        with patch(
            "scripts.collect_watchlist_market_snapshot.get_token", return_value="token"
        ):
            count = run(
                self.db, "20260714", clock=lambda: next(times),
                fetcher=fake_fetcher, sleep_fn=waits.append,
            )
        self.assertEqual(count, 1)
        self.assertEqual(waits, [60.0])

    def test_runner_marks_snapshot_best_effort_and_uses_exit_code_for_native_steps(self) -> None:
        runner = (
            Path(__file__).resolve().parents[2]
            / "ops" / "scheduled-tasks" / "run-watchlist-intraday.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('Invoke-BestEffortStep "capture 15:00 market snapshot"', runner)
        self.assertIn('$ErrorActionPreference = "Continue"', runner)
        self.assertIn('$code = $LASTEXITCODE', runner)
        self.assertIn('watchlist_probability_langgraph.py', runner)
        self.assertIn('"--write-db"', runner)
        self.assertNotIn('run_watchlist_research.py', runner)


if __name__ == "__main__":
    unittest.main()
