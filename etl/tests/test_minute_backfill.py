import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckdb

from scripts.backfill_minute_bars import month_progress, process_month, recent_months


def raw(date: str, time: str) -> dict:
    return {"cntr_tm": date + time, "open_pric": 100, "high_pric": 100,
            "low_pric": 100, "cur_prc": 100, "trde_qty": 1}


class MinuteBackfillTest(unittest.TestCase):
    def test_recent_months_crosses_year(self):
        self.assertEqual(recent_months("202601", 3), ["202601", "202512", "202511"])

    def test_process_month_reuses_completed_ticker(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "minute.duckdb"
            plan = {"000001": ["20260601", "20260602"]}
            calls = []

            def fetch(symbol, scope, base_dt, **_kwargs):
                calls.append((symbol, base_dt))
                return {"bars": [raw("20260529", "090000"), raw("20260601", "090000"),
                                 raw("20260602", "090000")], "cont_yn": "N", "next_key": ""}

            first = process_month(db, "202606", plan, scope="1", deadline=10**20,
                                  max_attempts=3, max_failures=2, max_tickers=None,
                                  retry_blocked=False, fetch_page=fetch)
            second = process_month(db, "202606", plan, scope="1", deadline=10**20,
                                   max_attempts=3, max_failures=2, max_tickers=None,
                                   retry_blocked=False, fetch_page=fetch)
            self.assertEqual(first["inserted_bars"], 2)
            self.assertEqual(second["attempted_tickers"], 0)
            self.assertEqual(len(calls), 1)

    def test_repeated_failure_becomes_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "minute.duckdb"
            plan = {"000001": ["20260601"]}

            def fail(*_args, **_kwargs):
                raise RuntimeError("unavailable")

            for _ in range(3):
                result = process_month(db, "202606", plan, scope="1", deadline=10**20,
                                       max_attempts=3, max_failures=2, max_tickers=None,
                                       retry_blocked=False, fetch_page=fail)
            self.assertEqual(result["blocked_total"], 1)
            fourth = process_month(db, "202606", plan, scope="1", deadline=10**20,
                                   max_attempts=3, max_failures=2, max_tickers=None,
                                   retry_blocked=False, fetch_page=fail)
            self.assertEqual(fourth["attempted_tickers"], 0)
            self.assertEqual(month_progress(db, plan, "202606", "1")["actionable_missing_pairs"], 0)

    def test_rate_limit_stops_without_blocking_ticker(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "minute.duckdb"
            plan = {"000001": ["20260601"]}

            def limited(*_args, **_kwargs):
                raise RuntimeError("HTTP 429")

            result = process_month(db, "202606", plan, scope="1", deadline=10**20,
                                   max_attempts=3, max_failures=2, max_tickers=None,
                                   retry_blocked=False, fetch_page=limited)
            self.assertEqual(result["stop_reason"], "api_rate_limit")
            with duckdb.connect(str(db), read_only=True) as con:
                count = con.execute("SELECT count(*) FROM minute_backfill_failures").fetchone()[0]
            self.assertEqual(count, 0)

    def test_429_inside_ticker_or_date_is_ordinary_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "minute.duckdb"
            plan = {"042900": ["20260429"]}

            def fail(*_args, **_kwargs):
                raise RuntimeError("042900 20260429~20260429: 장 시작까지 도달하지 못함")

            result = process_month(db, "202604", plan, scope="1", deadline=10**20,
                                   max_attempts=3, max_failures=2, max_tickers=None,
                                   retry_blocked=False, fetch_page=fail)
            self.assertNotEqual(result["stop_reason"], "api_rate_limit")
            with duckdb.connect(str(db), read_only=True) as con:
                count = con.execute("SELECT count(*) FROM minute_backfill_failures").fetchone()[0]
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
