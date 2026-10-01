"""data 단위 테스트 — 합성 데이터만, 임시 duckdb (unittest)."""
import inspect
import tempfile
import unittest
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import research.backtest_minute.data as dm
from research.backtest_minute.data import (
    FULL_SINCE,
    coverage,
    day_bars,
    snapshot,
    universe,
)

OHLCV_COLS = ["date", "ticker", "market", "open", "high", "low", "close",
              "volume", "trading_value", "market_cap"]


def _make_krx(path):
    rows = [
        ("20251201", "000010", "KOSPI", 10000, 10200, 9900, 10100, 1000000, 1e10, 1e12),
        ("20251201", "000020", "KOSDAQ", 10000, 10100, 9900, 10050, 100, 1e6, 5e11),
        ("20251201", "000030", "KOSDAQ", 10000, 10100, 9900, 10080, 5000, 5e7, 8e11),
        ("20251202", "000010", "KOSPI", 10100, 10300, 10000, 10200, 900000, 9e9, 1e12),
        ("20251202", "000020", "KOSDAQ", 10050, 10150, 9950, 10100, 120, 1.2e6, 5e11),
        ("20251203", "000010", "KOSPI", 10200, 10400, 10100, 10300, 800000, 8e9, 1e12),
        ("20251203", "000020", "KOSDAQ", 10100, 10200, 10000, 10150, 110, 1.1e6, 5e11),
    ]
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR, market VARCHAR,"
                    " open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT,"
                    " trading_value DOUBLE, market_cap DOUBLE)")
        con.executemany(f"INSERT INTO ohlcv VALUES ({','.join('?' * 10)})", rows)
        con.execute("CREATE TABLE stock_names(code VARCHAR, name VARCHAR)")
        con.executemany("INSERT INTO stock_names VALUES (?, ?)",
                        [("000010", "가가"), ("000020", "나나"), ("000030", "다다")])
    finally:
        con.close()


def _make_mb(path):
    rows = [
        ("20251202", "000010", "090000", 10100, 10150, 10090, 10120, 100),
        ("20251202", "000010", "090100", 10120, 10180, 10100, 10150, 120),
        ("20251202", "000010", "160000", 10150, 10160, 10140, 10155, 10),
    ]
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE minute_bars(date VARCHAR, ticker VARCHAR, time VARCHAR,"
                    " open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT)")
        con.executemany("INSERT INTO minute_bars VALUES (?,?,?,?,?,?,?,?)", rows)
    finally:
        con.close()


class TestUniverse(unittest.TestCase):
    def test_missing_d_and_low_volume_kept(self):
        with tempfile.TemporaryDirectory() as td:
            krx = str(Path(td) / "krx.duckdb")
            _make_krx(krx)
            u = universe(krx_db=krx, since="20251202")
        for c in ["date", "ticker", "market", "ms", "f_date", "pc", "ptv",
                  "pcap", "pvol", "d_open"]:
            self.assertIn(c, u.columns)
        self.assertNotIn("d_close", u.columns)
        self.assertFalse(any(c in {"rk", "rank", "vol_rank"} or c.lower().startswith("rank")
                             for c in u.columns))
        # D 거래 없는 종목도 행 존재, d_open NaN
        c = u[(u["date"] == "20251202") & (u["ticker"] == "000030")]
        self.assertEqual(len(c), 1)
        self.assertTrue(np.isnan(float(c["d_open"].iloc[0])))
        # 거래량 하위 종목도 포함
        b = u[(u["date"] == "20251202") & (u["ticker"] == "000020")]
        self.assertEqual(len(b), 1)
        self.assertEqual(float(b["pvol"].iloc[0]), 100)

    def test_default_since(self):
        self.assertEqual(FULL_SINCE, "20251201")
        sig = inspect.signature(universe)
        self.assertEqual(sig.parameters["since"].default, "20251201")


class TestDayBars(unittest.TestCase):
    def test_arrays_and_none(self):
        with tempfile.TemporaryDirectory() as td:
            mb = str(Path(td) / "mb.duckdb")
            _make_mb(mb)
            m = day_bars([("20251202", "000010"), ("20251202", "000020")], mb_db=mb)
            cov = coverage([("20251202", "000010"),
                            ("20251202", "000020")], mb_db=mb)
        b = m[("20251202", "000010")]
        self.assertEqual(b["time"].tolist(), [90000, 90100])
        self.assertAlmostEqual(float(b["open"][0]), 10100.0)
        self.assertIsNone(m[("20251202", "000020")])
        self.assertEqual(cov, 0.5)

    def test_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            mb = str(Path(td) / "mb.duckdb")
            _make_mb(mb)
            m = day_bars([("20251202", "000010")], mb_db=mb)
        b = m[("20251202", "000010")]
        self.assertAlmostEqual(snapshot(b, 90000), 10100.0)
        self.assertAlmostEqual(snapshot(b, 90050), 10120.0)
        self.assertAlmostEqual(snapshot(b, 90100), 10150.0)
        self.assertTrue(np.isnan(snapshot(b, 85900)))
        self.assertTrue(np.isnan(snapshot(None, 90100)))


class TestNoSideEffect(unittest.TestCase):
    def test_no_fetch_import(self):
        src = Path(dm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("minute_bar_store", src)
        self.assertNotIn("kiwoom", src.lower())


if __name__ == "__main__":
    unittest.main()
