import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckdb

from scripts.backfill_minute_bars import (
    PROBE_TICKER,
    load_month_plan,
    month_progress,
    parse_args,
    probe_extra_dates,
    process_month,
    recent_months,
    run,
)


def raw(date: str, time: str) -> dict:
    return {"cntr_tm": date + time, "open_pric": 100, "high_pric": 100,
            "low_pric": 100, "cur_prc": 100, "trde_qty": 1}


class MinuteBackfillTest(unittest.TestCase):
    def test_runner_waits_with_wait_process_before_reading_exit_code(self):
        runner = (Path(__file__).resolve().parents[2]
                  / "ops" / "scheduled-tasks" / "run-minute-bars-backfill.ps1")
        text = runner.read_text(encoding="utf-8-sig")
        self.assertIn("Wait-Process -InputObject $process", text)
        self.assertNotIn("$process.WaitForExit()", text)

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

    def test_probe_extra_dates_adopts_open_day(self):
        def fetch(symbol, scope, base_dt, **kwargs):
            self.assertEqual(symbol, PROBE_TICKER)
            self.assertEqual(kwargs.get("cont_yn"), "N")
            self.assertEqual(kwargs.get("next_key"), "")
            return {"bars": [raw("20261008", "090000")], "cont_yn": "N", "next_key": ""}

        self.assertEqual(
            probe_extra_dates("20261007", "20261009", "1", fetch), ["20261008"]
        )

    def test_probe_extra_dates_skips_weekend(self):
        calls = []

        def fetch(symbol, scope, base_dt, **kwargs):
            calls.append(base_dt)
            return {"bars": [raw(base_dt, "090000")], "cont_yn": "N", "next_key": ""}

        self.assertEqual(
            probe_extra_dates("20261009", "20261013", "1", fetch), ["20261012"]
        )
        self.assertEqual(calls, ["20261012"])

    def test_probe_extra_dates_empty_on_holiday(self):
        def fetch(symbol, scope, base_dt, **kwargs):
            return {"bars": [raw("20261007", "090000")], "cont_yn": "N", "next_key": ""}

        self.assertEqual(probe_extra_dates("20261007", "20261009", "1", fetch), [])

    def test_probe_extra_dates_excludes_today(self):
        calls = []

        def fetch(symbol, scope, base_dt, **kwargs):
            calls.append(base_dt)
            return {"bars": [raw(base_dt, "090000")], "cont_yn": "N", "next_key": ""}

        self.assertEqual(probe_extra_dates("20261008", "20261009", "1", fetch), [])
        self.assertEqual(calls, [])

    def test_load_month_plan_adds_extra_only_to_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            krx_db = Path(tmp) / "krx.duckdb"
            with duckdb.connect(str(krx_db)) as con:
                con.execute("CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR)")
                con.executemany("INSERT INTO ohlcv VALUES (?, ?)", [
                    ("20261007", "AAA"), ("20261007", "BBB"),
                ])
            plan = load_month_plan(
                krx_db, "202610",
                extra_dates=["20261008", "20261007", "20260930"],
                base_tickers=["AAA"],
            )
            self.assertEqual(plan["AAA"], ["20261007", "20261008"])
            self.assertEqual(plan["BBB"], ["20261007"])

    def test_run_backfills_probed_extra_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            krx_db = Path(tmp) / "krx.duckdb"
            minute_db = Path(tmp) / "minute.duckdb"
            with duckdb.connect(str(krx_db)) as con:
                con.execute("CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR)")
                con.executemany("INSERT INTO ohlcv VALUES (?, ?)", [
                    ("20261007", "000001"), ("20261007", "000002"),
                ])
            args = parse_args([
                "--krx-db", str(krx_db), "--minute-db", str(minute_db),
                "--today", "20261009", "--max-runtime-min", "1",
            ])

            def fetch(symbol, scope, base_dt, **kwargs):
                return {"bars": [raw("20261006", "090000"), raw("20261007", "090000"),
                                 raw("20261008", "090000")],
                        "cont_yn": "N", "next_key": ""}

            payload = run(args, fetch_page=fetch)
            self.assertEqual(payload["extra_dates"], ["20261008"])
            with duckdb.connect(str(minute_db), read_only=True) as con:
                rows = con.execute(
                    "SELECT ticker FROM minute_fetched WHERE date='20261008'"
                ).fetchall()
            self.assertEqual({row[0] for row in rows}, {"000001", "000002"})

    def test_run_continues_when_probe_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            krx_db = Path(tmp) / "krx.duckdb"
            minute_db = Path(tmp) / "minute.duckdb"
            with duckdb.connect(str(krx_db)) as con:
                con.execute("CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR)")
                con.execute("INSERT INTO ohlcv VALUES ('20261007', '000001')")
            args = parse_args([
                "--krx-db", str(krx_db), "--minute-db", str(minute_db),
                "--today", "20261009", "--max-runtime-min", "1",
            ])

            def fetch(symbol, scope, base_dt, **kwargs):
                if symbol == PROBE_TICKER and base_dt > "20261007":
                    raise RuntimeError("probe boom")
                return {"bars": [raw("20261006", "090000"), raw("20261007", "090000")],
                        "cont_yn": "N", "next_key": ""}

            payload = run(args, fetch_page=fetch)
            self.assertEqual(payload["extra_dates"], [])
            self.assertIn("extra_dates_error", payload)
            with duckdb.connect(str(minute_db), read_only=True) as con:
                rows = con.execute(
                    "SELECT date FROM minute_fetched WHERE ticker='000001'"
                ).fetchall()
            self.assertIn(("20261007",), rows)


    def test_probe_extra_dates_respects_deadline(self):
        calls = []

        def fetch(symbol, scope, base_dt, **kwargs):
            calls.append(base_dt)
            return {"bars": [raw(base_dt, "090000")], "cont_yn": "N", "next_key": ""}

        self.assertEqual(probe_extra_dates("20261007", "20261009", "1", fetch, 0), [])
        self.assertEqual(calls, [])

    def test_run_probe_rate_limit_stops_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            krx_db = Path(tmp) / "krx.duckdb"
            minute_db = Path(tmp) / "minute.duckdb"
            with duckdb.connect(str(krx_db)) as con:
                con.execute("CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR)")
                con.execute("INSERT INTO ohlcv VALUES ('20261007', '000001')")
            args = parse_args([
                "--krx-db", str(krx_db), "--minute-db", str(minute_db),
                "--today", "20261009", "--max-runtime-min", "1",
            ])
            calls = []

            def fetch(symbol, scope, base_dt, **kwargs):
                calls.append((symbol, base_dt))
                if symbol == PROBE_TICKER:
                    raise RuntimeError("HTTP 429")
                return {"bars": [raw("20261006", "090000"), raw("20261007", "090000")],
                        "cont_yn": "N", "next_key": ""}

            payload = run(args, fetch_page=fetch)
            self.assertEqual(payload["stop_reason"], "api_rate_limit")
            self.assertEqual(payload["selected_months"], [])
            self.assertEqual(payload["results"], [])
            self.assertTrue(calls)
            self.assertTrue(all(symbol == PROBE_TICKER for symbol, _ in calls))

    def test_parse_args_rejects_invalid_calendar_date(self):
        with self.assertRaises(SystemExit):
            parse_args(["--today", "20260230"])


if __name__ == "__main__":
    unittest.main()
