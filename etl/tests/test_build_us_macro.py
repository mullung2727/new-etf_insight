import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import duckdb
import pandas as pd
import requests

from scripts.build_us_macro import (
    NEW_YORK,
    ensure_index,
    ensure_schema,
    fetch_first_release,
    fetch_fred,
    index_cutoff_day,
    load_series,
    main,
    run,
    upsert_first_release,
    upsert_fred,
)

T1 = datetime(2026, 10, 1)
T2 = datetime(2026, 10, 2)


def _frame(ticker_days):
    """ticker_days: ticker -> [(YYYY-MM-DD, close)] → yfinance group_by="ticker" 형태"""
    pieces = {}
    for ticker, values in ticker_days.items():
        index = pd.to_datetime([value[0] for value in values])
        close = [value[1] for value in values]
        pieces[ticker] = pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close},
            index=index,
        )
    return pd.concat(pieces, axis=1)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"observations": []}

    def json(self):
        return self._payload


class TestFredObs(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_t1_history_preserved(self):
        upsert_fred(self.con, "S", [("20260101", 3.0)], T1)
        upsert_fred(self.con, "S", [("20260101", 3.1)], T2)
        self.assertEqual(self.con.execute("SELECT count(*) FROM fred_obs").fetchone()[0], 2)
        self.assertEqual(load_series(self.con, "S"), [("20260101", 3.1)])

    def test_t2_no_change_no_new_rows(self):
        rows = [("20260101", 3.0)]
        upsert_fred(self.con, "S", rows, T1)
        second = upsert_fred(self.con, "S", rows, T2)
        self.assertEqual(second, 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM fred_obs").fetchone()[0], 1)

    def test_t3_as_of_reproduces_point_in_time(self):
        upsert_fred(self.con, "S", [("20260101", 3.0)], T1)
        upsert_fred(self.con, "S", [("20260101", 3.1)], T2)
        self.assertEqual(load_series(self.con, "S", as_of=T1), [("20260101", 3.0)])

    def test_t4_missing_values_ignored(self):
        payload = {"observations": [
            {"date": "2026-01-01", "value": "."},
            {"date": "2026-01-02", "value": "3.5"},
        ]}
        rows = fetch_fred("S", "KEY", get=lambda url, params, timeout: _FakeResponse(payload=payload))
        self.assertEqual(rows, [("20260102", 3.5)])

    def test_t5_missing_key_exits_before_connect(self):
        with (
            patch.dict(os.environ, {"FRED_API_KEY": ""}),
            patch("scripts.build_us_macro.load_dotenv"),
            patch.object(sys, "argv", ["build_us_macro.py"]),
            patch("scripts.build_us_macro.duckdb") as mock_duck,
        ):
            with self.assertRaises(SystemExit):
                main()
        mock_duck.connect.assert_not_called()

    def test_t6_partial_failure(self):
        def fake_fetch(series_id, api_key):
            if series_id == "A":
                raise RuntimeError("boom")
            return [("20260101", 1.0)]

        result = run(self.con, "KEY", series=("A", "B"), fetch=fake_fetch, indices=(), first_release=())
        self.assertIn("A", result["failed"])
        self.assertGreater(result["inserted"]["B"], 0)
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM fred_obs WHERE series_id='B'").fetchone()[0], 1
        )

    def test_t7_api_key_never_in_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            fetch_fred("S", "SECRETKEY123", get=lambda url, params, timeout: _FakeResponse(500))
        self.assertNotIn("SECRETKEY123", str(ctx.exception))

        def bad_get(url, params, timeout):
            raise requests.ConnectionError("...api_key=SECRETKEY123...")

        with self.assertRaises(RuntimeError) as ctx:
            fetch_fred("S", "SECRETKEY123", get=bad_get)
        self.assertNotIn("SECRETKEY123", str(ctx.exception))

    def test_t8_vanished_obs_date_preserved(self):
        upsert_fred(self.con, "S", [("20260101", 1.0), ("20260102", 2.0)], T1)
        upsert_fred(self.con, "S", [("20260102", 2.0)], T2)
        self.assertEqual(
            self.con.execute(
                "SELECT count(*) FROM fred_obs WHERE series_id='S' AND date='20260101'"
            ).fetchone()[0],
            1,
        )


class TestIndexOhlcv(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_v1_index_full_replace_and_truncated_protected(self):
        def download_for(days):
            def _download(tickers, start, end):
                return _frame({ticker: list(days) for ticker in tickers})
            return _download

        now_ny = datetime(2026, 10, 3, 12, 0, tzinfo=NEW_YORK)
        days = [("2026-09-30", 20), ("2026-10-01", 20), ("2026-10-02", 20)]
        ensure_index(self.con, now_ny=now_ny, download=download_for(days), tickers=("^VIX",))
        days2 = [(day, 21) for day, _ in days]
        ensure_index(self.con, now_ny=now_ny, download=download_for(days2), tickers=("^VIX",))
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM index_ohlcv WHERE ticker='^VIX'").fetchone()[0],
            3,
        )
        self.assertEqual(
            self.con.execute("SELECT DISTINCT close FROM index_ohlcv WHERE ticker='^VIX'").fetchall(),
            [(21.0,)],
        )
        truncated = ensure_index(
            self.con, now_ny=now_ny, download=download_for(days2[1:]), tickers=("^VIX",),
        )
        self.assertIn("^VIX", truncated["failed"])
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM index_ohlcv WHERE ticker='^VIX'").fetchone()[0],
            3,
        )
        self.assertEqual(
            self.con.execute("SELECT DISTINCT close FROM index_ohlcv WHERE ticker='^VIX'").fetchall(),
            [(21.0,)],
        )

    def test_index_cutoff_day(self):
        self.assertEqual(
            index_cutoff_day(datetime(2026, 10, 2, 19, 59, tzinfo=NEW_YORK)), "20261002"
        )
        self.assertEqual(
            index_cutoff_day(datetime(2026, 10, 2, 20, 1, tzinfo=NEW_YORK)), "20261003"
        )

    def test_v2_index_today_bar(self):
        days = [("2026-10-01", 20), ("2026-10-02", 21)]
        download = lambda tickers, start, end: _frame({ticker: list(days) for ticker in tickers})
        ensure_index(
            self.con,
            now_ny=datetime(2026, 10, 2, 19, 59, tzinfo=NEW_YORK),
            download=download,
            tickers=("^VIX",),
        )
        self.assertEqual(
            self.con.execute(
                "SELECT count(*) FROM index_ohlcv WHERE ticker='^VIX' AND date='20261002'"
            ).fetchone()[0],
            0,
        )
        ensure_index(
            self.con,
            now_ny=datetime(2026, 10, 2, 20, 1, tzinfo=NEW_YORK),
            download=download,
            tickers=("^VIX",),
        )
        self.assertEqual(
            self.con.execute(
                "SELECT count(*) FROM index_ohlcv WHERE ticker='^VIX' AND date='20261002'"
            ).fetchone()[0],
            1,
        )

    def test_v3_index_failure_isolated_from_fred(self):
        def bad_download(tickers, start, end):
            raise RuntimeError("network")

        result = run(
            self.con, "KEY",
            series=("B",),
            fetch=lambda series_id, api_key: [("20260101", 1.0)],
            indices=("^VIX",),
            index_download=bad_download,
            now_ny=datetime(2026, 10, 3, 12, 0, tzinfo=NEW_YORK),
            first_release=(),
        )
        self.assertGreater(result["inserted"]["B"], 0)
        self.assertIn("^VIX", result["failed"])
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM fred_obs WHERE series_id='B'").fetchone()[0], 1
        )


class TestFirstRelease(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_r1_fetch_first_release(self):
        payload = {"observations": [
            {"date": "2026-01-01", "value": "4.1", "realtime_start": "2026-02-01"},
            {"date": "2026-02-01", "value": ".", "realtime_start": "2026-03-01"},
            {"date": "2026-03-01", "value": "4.2", "realtime_start": "2026-04-05"},
        ]}
        captured: dict = {}

        def fake_get(url, params, timeout):
            captured.update(params)
            return _FakeResponse(payload=payload)

        rows = fetch_first_release("UNRATE", "KEY", get=fake_get)
        self.assertEqual(rows, [("20260101", 4.1, "20260201"), ("20260301", 4.2, "20260405")])
        self.assertEqual(captured.get("output_type"), 4)

    def test_r2_upsert_first_release_backfilled(self):
        rows = [
            ("20260101", 1.0, "20200101"),
            ("20260201", 2.0, "20200101"),
            ("20260301", 3.0, "20260401"),
        ]
        count = upsert_first_release(self.con, "U", rows, T1)
        self.assertEqual(count, 3)
        stored = self.con.execute(
            "SELECT date, backfilled FROM fred_first_release WHERE series_id='U' ORDER BY date"
        ).fetchall()
        self.assertEqual(stored, [("20260101", True), ("20260201", True), ("20260301", False)])

    def test_r3_run_partial_failure_first_release(self):
        def fake_fetch(series_id, api_key):
            return [("20260101", 1.0)]

        def fake_first(series_id, api_key):
            raise RuntimeError("boom")

        result = run(
            self.con, "KEY",
            series=("B",),
            fetch=fake_fetch,
            indices=(),
            first_release=("X",),
            fetch_first=fake_first,
        )
        self.assertIn("X:first", result["failed"])
        self.assertGreater(result["inserted"]["B"], 0)
        self.assertEqual(
            self.con.execute("SELECT count(*) FROM fred_obs WHERE series_id='B'").fetchone()[0], 1
        )


if __name__ == "__main__":
    unittest.main()
