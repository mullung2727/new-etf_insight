import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import duckdb

from scripts.build_us_early import (
    check_completion,
    fetch_daily,
    load_extra_tickers,
    parse_cboe,
    upsert_bars,
    upsert_index_bars,
)
from scripts.build_us_macro import ensure_schema as ensure_macro_schema
from scripts.build_us_ohlcv import ensure_schema as ensure_ohlcv_schema


def _bar(day, close=100.0, volume=1000):
    return {
        "date": day, "open": close - 1, "high": close + 1, "low": close - 2,
        "close": close, "volume": volume,
    }


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestUpsertBars(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_ohlcv_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_insert_then_same_values_noop(self):
        bars = [_bar("20261020"), _bar("20261021")]
        self.assertEqual(upsert_bars(self.con, "etf_ohlcv", "QQQ", bars), (2, 0))
        self.assertEqual(upsert_bars(self.con, "etf_ohlcv", "QQQ", bars), (0, 0))
        self.assertEqual(self.con.execute("SELECT count(*) FROM etf_ohlcv").fetchone()[0], 2)

    def test_changed_values_update(self):
        upsert_bars(self.con, "etf_ohlcv", "QQQ", [_bar("20261021", 100.0)])
        inserted, updated = upsert_bars(self.con, "etf_ohlcv", "QQQ", [_bar("20261021", 725.2)])
        self.assertEqual((inserted, updated), (0, 1))
        self.assertEqual(
            self.con.execute(
                "SELECT close FROM etf_ohlcv WHERE ticker='QQQ' AND date='20261021'"
            ).fetchone()[0],
            725.2,
        )

    def test_ohlcv_insert_nulls_and_update_ohlcv_only(self):
        upsert_bars(self.con, "ohlcv", "AAA", [_bar("20261021", 50.0)])
        self.assertEqual(
            self.con.execute(
                "SELECT market, trading_value, market_cap, list_shrs FROM ohlcv"
            ).fetchone(),
            (None, None, None, None),
        )
        self.con.execute("UPDATE ohlcv SET market='NASDAQ', list_shrs=1000")
        inserted, updated = upsert_bars(self.con, "ohlcv", "AAA", [_bar("20261021", 51.0)])
        self.assertEqual((inserted, updated), (0, 1))
        self.assertEqual(
            self.con.execute("SELECT close, market, list_shrs FROM ohlcv").fetchone(),
            (51.0, "NASDAQ", 1000),
        )

    def test_unknown_table_rejected(self):
        with self.assertRaises(ValueError):
            upsert_bars(self.con, "splits", "AAA", [_bar("20261021")])


class TestParseCboe(unittest.TestCase):
    def test_header_dates_and_blank_lines(self):
        text = (
            "DATE,OPEN,HIGH,LOW,CLOSE\n"
            "\n"
            "10/20/2026,20.1,21.0,19.5,20.5\n"
            "10/21/2026,20.5,22.0,20.0,21.5\n"
            "\n"
        )
        self.assertEqual(
            parse_cboe(text),
            [
                {"date": "20261020", "open": 20.1, "high": 21.0, "low": 19.5, "close": 20.5},
                {"date": "20261021", "open": 20.5, "high": 22.0, "low": 20.0, "close": 21.5},
            ],
        )


class TestFetchDaily(unittest.TestCase):
    def test_code_from_today_ny_passed(self):
        captured: dict = {}

        def fake_get(url, params, timeout):
            captured["url"] = url
            captured["params"] = params
            captured["timeout"] = timeout
            return _FakeResponse([{"date": "20261021"}])

        bars = fetch_daily(
            "http://localhost:8001", "QQQ", "20261001", "20261022", get=fake_get
        )
        self.assertEqual(bars, [{"date": "20261021"}])
        self.assertEqual(captured["url"], "http://localhost:8001/us/daily")
        self.assertEqual(
            captured["params"],
            {"code": "QQQ", "from": "20261001", "today_ny": "20261022"},
        )
        self.assertEqual(captured["timeout"], 30)

    def test_today_ny_omitted_when_none(self):
        captured: dict = {}

        def fake_get(url, params, timeout):
            captured["params"] = params
            return _FakeResponse([])

        fetch_daily("http://localhost:8001/", "SPY", "20261001", None, get=fake_get)
        self.assertEqual(captured["params"], {"code": "SPY", "from": "20261001"})


class TestExtraTickers(unittest.TestCase):
    def test_comments_and_blanks_ignored(self):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8"
        ) as handle:
            handle.write("# conviction list\n\nAAA\n  bbb  \n#CCC\n")
            path = handle.name
        try:
            self.assertEqual(load_extra_tickers(path), ["AAA", "BBB"])
        finally:
            Path(path).unlink()


class TestCompletion(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_ohlcv_schema(self.con)
        self.mcon = duckdb.connect(":memory:")
        ensure_macro_schema(self.mcon)

    def tearDown(self):
        self.con.close()
        self.mcon.close()

    def test_missing_reported(self):
        upsert_bars(self.con, "etf_ohlcv", "QQQ", [_bar("20261021")])
        upsert_bars(self.con, "etf_ohlcv", "SPY", [_bar("20261020")])
        upsert_bars(self.con, "ohlcv", "AAA", [_bar("20261021")])
        upsert_index_bars(self.mcon, "^VIX", [_bar("20261021")], datetime(2026, 10, 22))
        missing = check_completion(self.con, self.mcon, ("QQQ", "SPY"), ["AAA"], "20261021")
        self.assertEqual(missing, ["SPY", "^VIX3M"])

    def test_all_current_ok(self):
        upsert_bars(self.con, "etf_ohlcv", "QQQ", [_bar("20261021")])
        upsert_bars(self.con, "ohlcv", "AAA", [_bar("20261022")])
        upsert_index_bars(self.mcon, "^VIX", [_bar("20261021")], datetime(2026, 10, 22))
        upsert_index_bars(self.mcon, "^VIX3M", [_bar("20261021")], datetime(2026, 10, 22))
        missing = check_completion(self.con, self.mcon, ("QQQ",), ["AAA"], "20261021")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
