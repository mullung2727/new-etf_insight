import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import duckdb

from scripts import backfill_minute_bars as bb
from scripts.backfill_minute_bars import month_progress, nxt_universe, parse_args, process_month, recent_months, run


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


def _make_krx_db(path: Path, rows: list[tuple]) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE ohlcv(ticker VARCHAR, date VARCHAR)")
    con.executemany("INSERT INTO ohlcv VALUES (?, ?)", rows)
    con.close()


def _covering_fetch(seen: list):
    def fetch(symbol, scope, base_dt, **_kwargs):
        seen.append(symbol)
        return {"bars": [raw("20260601", "090000"), raw("20260602", "090000")],
                "cont_yn": "N", "next_key": ""}
    return fetch


class NxtBackfillTest(unittest.TestCase):
    def test_nxt_universe_returns_only_enabled_from_both_markets(self):
        calls = []

        def fake_fetch(mrkt_tp):
            calls.append(mrkt_tp)
            if mrkt_tp == "0":
                return [
                    {"code": "005930", "name": "삼성전자", "nxtEnable": "Y"},
                    {"code": "000001", "name": "비NXT", "nxtEnable": "N"},
                ]
            return [
                {"code": "123456", "name": "코스닥NXT", "nxtEnable": "Y"},
                {"code": "654321", "name": "코스닥제외", "nxtEnable": ""},
            ]

        self.assertEqual(nxt_universe(fetch=fake_fetch), {"005930", "123456"})
        self.assertEqual(sorted(calls), ["0", "10"])

    def test_nxt_market_plan_uses_suffixed_keys_and_drops_non_nxt(self):
        with tempfile.TemporaryDirectory() as tmp:
            krx_db = Path(tmp) / "krx.duckdb"
            minute_db = Path(tmp) / "minute.duckdb"
            _make_krx_db(krx_db, [
                ("005930", "20260601"), ("005930", "20260602"),
                ("000001", "20260601"), ("000001", "20260602"),
            ])
            seen: list = []
            args = parse_args(["--minute-db", str(minute_db), "--krx-db", str(krx_db),
                               "--month", "202606", "--max-runtime-min", "10",
                               "--market", "nxt"])
            original = bb.nxt_universe
            bb.nxt_universe = lambda: {"005930"}  # noqa: E731
            try:
                payload = run(args, fetch_page=_covering_fetch(seen))
            finally:
                bb.nxt_universe = original
            self.assertEqual(seen, ["005930_NX"])
            self.assertEqual(payload["market"], "nxt")
            self.assertEqual(payload["results"][0]["expected_tickers"], 1)
            with duckdb.connect(str(minute_db), read_only=True) as con:
                tickers = {row[0] for row in con.execute(
                    "SELECT DISTINCT ticker FROM minute_fetched").fetchall()}
            self.assertEqual(tickers, {"005930_NX"})

    def test_krx_market_plan_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            krx_db = Path(tmp) / "krx.duckdb"
            minute_db = Path(tmp) / "minute.duckdb"
            _make_krx_db(krx_db, [
                ("005930", "20260601"), ("005930", "20260602"),
                ("000001", "20260601"), ("000001", "20260602"),
            ])
            seen: list = []
            args = parse_args(["--minute-db", str(minute_db), "--krx-db", str(krx_db),
                               "--month", "202606", "--max-runtime-min", "10"])
            payload = run(args, fetch_page=_covering_fetch(seen))
            self.assertEqual(sorted(seen), ["000001", "005930"])
            self.assertEqual(payload["market"], "krx")
            with duckdb.connect(str(minute_db), read_only=True) as con:
                tickers = {row[0] for row in con.execute(
                    "SELECT DISTINCT ticker FROM minute_fetched").fetchall()}
            self.assertEqual(tickers, {"000001", "005930"})


if __name__ == "__main__":
    unittest.main()
