import contextlib
import io
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import duckdb
import pandas as pd

from scripts.build_us_early import (
    check_completion,
    fetch_daily,
    fetch_yahoo_index,
    load_extra_tickers,
    main,
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


class TestFetchYahooIndex(unittest.TestCase):
    def _download(self, captured):
        def fake(tickers, start, end):
            captured["tickers"] = tickers
            captured["start"] = start
            captured["end"] = end
            idx = pd.to_datetime(["2026-10-21", "2026-10-22"])
            cols = pd.MultiIndex.from_product(
                [tickers, ["Open", "High", "Low", "Close"]]
            )
            frame = pd.DataFrame(float("nan"), index=idx, columns=cols)
            frame.loc[idx[1]] = [16.0, 17.0, 15.5, 16.5] * len(tickers)
            return frame

        return fake

    def test_nan_rows_dropped(self):
        captured: dict = {}
        out = fetch_yahoo_index(
            ["^VIX", "^VIX3M"], 10, download=self._download(captured)
        )
        today = datetime.now().date()
        self.assertEqual(
            captured["start"], (today - timedelta(days=20)).strftime("%Y%m%d")
        )
        self.assertEqual(captured["end"], (today + timedelta(days=1)).strftime("%Y%m%d"))
        self.assertEqual(
            out["^VIX"],
            [{"date": "20261022", "open": 16.0, "high": 17.0, "low": 15.5, "close": 16.5}],
        )
        self.assertEqual(
            out["^VIX3M"],
            [{"date": "20261022", "open": 16.0, "high": 17.0, "low": 15.5, "close": 16.5}],
        )

    def test_missing_ticker_returns_empty(self):
        out = fetch_yahoo_index(["^VIX"], 10, download=lambda t, s, e: pd.DataFrame())
        self.assertEqual(out, {"^VIX": []})


class TestYahooFallback(unittest.TestCase):
    VIX_CSV = (
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        "10/20/2026,20.0,21.0,19.0,20.5\n"
        "10/21/2026,21.0,22.0,20.0,21.5\n"
    )
    VIX3M_CSV = VIX_CSV + "10/22/2026,22.0,23.0,21.0,22.5\n"
    # 10-21 bogus 값: DB 에 있는 날짜는 야후 값으로 덮지 않아야 한다.
    YAHOO = {
        "^VIX": [
            {"date": "20261021", "open": 999.0, "high": 999.0, "low": 999.0, "close": 999.0},
            {"date": "20261022", "open": 16.0, "high": 17.0, "low": 15.5, "close": 16.5},
        ],
        "^VIX3M": [
            {"date": "20261022", "open": 888.0, "high": 888.0, "low": 888.0, "close": 888.0},
        ],
    }

    def _run(self, yahoo=None, yahoo_exc=None, cboe_exc=None):
        with tempfile.TemporaryDirectory() as tmp:
            ohlcv_path = Path(tmp) / "us_ohlcv.duckdb"
            macro_path = Path(tmp) / "us_macro.duckdb"

            def fake_fetch_daily(broker_url, code, from_date, today_ny=None):
                self.assertEqual(code, "QQQ")
                return [_bar("20261021", 700.0), _bar("20261022", 710.0)]

            def fake_fetch_cboe(symbol):
                if cboe_exc is not None:
                    raise cboe_exc
                return {"VIX": self.VIX_CSV, "VIX3M": self.VIX3M_CSV}[symbol]

            if yahoo_exc is not None:
                yahoo_patch = mock.patch(
                    "scripts.build_us_early.fetch_yahoo_index", side_effect=yahoo_exc
                )
            else:
                yahoo_patch = mock.patch(
                    "scripts.build_us_early.fetch_yahoo_index", return_value=yahoo
                )
            with mock.patch("scripts.build_us_early.ETF_TICKERS", ("QQQ",)), \
                    mock.patch("scripts.build_us_early.OHLCV_DB_PATH", ohlcv_path), \
                    mock.patch("scripts.build_us_early.MACRO_DB_PATH", macro_path), \
                    mock.patch("scripts.build_us_early.fetch_daily", side_effect=fake_fetch_daily), \
                    mock.patch("scripts.build_us_early.fetch_cboe", side_effect=fake_fetch_cboe), \
                    yahoo_patch:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = main([])
            con = duckdb.connect(str(macro_path))
            try:
                vix = con.execute(
                    "SELECT date, open, high, low, close FROM index_ohlcv"
                    " WHERE ticker='^VIX' ORDER BY date"
                ).fetchall()
                vix3m = con.execute(
                    "SELECT date, open, high, low, close FROM index_ohlcv"
                    " WHERE ticker='^VIX3M' ORDER BY date"
                ).fetchall()
            finally:
                con.close()
            return rc, buf.getvalue(), vix, vix3m

    def test_yahoo_fills_only_missing_dates(self):
        rc, out, vix, vix3m = self._run(yahoo=self.YAHOO)
        self.assertEqual(rc, 0)
        self.assertEqual(
            vix,
            [
                ("20261020", 20.0, 21.0, 19.0, 20.5),
                ("20261021", 21.0, 22.0, 20.0, 21.5),
                ("20261022", 16.0, 17.0, 15.5, 16.5),
            ],
        )
        self.assertEqual(
            vix3m,
            [
                ("20261020", 20.0, 21.0, 19.0, 20.5),
                ("20261021", 21.0, 22.0, 20.0, 21.5),
                ("20261022", 22.0, 23.0, 21.0, 22.5),
            ],
        )
        self.assertIn("[us-early] ^VIX 야후로 채움 20261022", out)
        self.assertNotIn("^VIX3M 야후로 채움", out)
        self.assertIn("vix=ok", out)

    def test_yahoo_failure_missing_without_exception(self):
        rc, out, vix, _vix3m = self._run(yahoo_exc=ConnectionError("down"))
        self.assertEqual(rc, 1)
        self.assertEqual([row[0] for row in vix], ["20261020", "20261021"])
        self.assertIn("MISSING", out)
        self.assertIn("^VIX", out)
        self.assertIn("vix=missing", out)

    def test_cboe_failure_yahoo_fills(self):
        yahoo = {
            "^VIX": [
                {"date": "20261022", "open": 16.0, "high": 17.0, "low": 15.5, "close": 16.5},
            ],
            "^VIX3M": [
                {"date": "20261022", "open": 17.0, "high": 18.0, "low": 16.5, "close": 17.5},
            ],
        }
        rc, out, vix, vix3m = self._run(yahoo=yahoo, cboe_exc=ConnectionError("blocked"))
        self.assertEqual(rc, 0)
        self.assertEqual(vix, [("20261022", 16.0, 17.0, 15.5, 16.5)])
        self.assertEqual(vix3m, [("20261022", 17.0, 18.0, 16.5, 17.5)])
        self.assertIn("[us-early] ^VIX 야후로 채움 20261022", out)
        self.assertIn("[us-early] ^VIX3M 야후로 채움 20261022", out)
        self.assertIn("vix=ok", out)


if __name__ == "__main__":
    unittest.main()
