import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import duckdb
import requests

from scripts.build_us_macro import (
    ensure_schema,
    fetch_fred,
    load_series,
    main,
    run,
    upsert_fred,
)

T1 = datetime(2026, 10, 1)
T2 = datetime(2026, 10, 2)


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

        result = run(self.con, "KEY", series=("A", "B"), fetch=fake_fetch)
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


if __name__ == "__main__":
    unittest.main()
