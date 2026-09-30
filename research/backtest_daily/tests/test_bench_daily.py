"""bench_daily 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import importlib
import inspect
import tempfile
import unittest
from pathlib import Path

import duckdb
import pandas as pd

from research.backtest_daily import bench_daily as BD


def _write_db(path, rows, names):
    con = duckdb.connect(str(path))
    try:
        con.register("odf", pd.DataFrame(rows))
        con.execute("CREATE TABLE ohlcv AS SELECT * FROM odf")
        con.unregister("odf")
        ndf = pd.DataFrame([{"code": k, "name": v} for k, v in names.items()])
        con.register("ndf", ndf)
        con.execute("CREATE TABLE stock_names AS SELECT * FROM ndf")
        con.unregister("ndf")
    finally:
        con.close()


def _row(date, ticker, open_, close, tv, cap):
    return {"date": date, "ticker": ticker, "market": "KOSPI", "open": open_,
            "high": max(open_, close), "low": min(open_, close), "close": close,
            "volume": 100000, "trading_value": tv, "market_cap": cap}


def _syn_table():
    return pd.DataFrame([
        {"date": "20240102", "ms": 0, "bench_id": "CAP1_ALL", "n": 10,
         "r_cc": 0.01, "r_on": 0.002, "r_in": 0.008},
        {"date": "20240103", "ms": 1, "bench_id": "CAP1_ALL", "n": 10,
         "r_cc": 0.02, "r_on": 0.003, "r_in": 0.017},
        {"date": "20240104", "ms": 2, "bench_id": "CAP1_ALL", "n": 10,
         "r_cc": 0.03, "r_on": 0.004, "r_in": 0.026},
    ])


class TestPieces(unittest.TestCase):
    def test_on_in_multiply_to_cc(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            dates = ["20240102", "20240103", "20240104", "20240105"]
            closes = [10000.0, 10100.0, 10200.0, 10300.0]
            opens = [10000.0, 10030.0, 10130.0, 10230.0]
            rows = []
            for t, sh in (("A", 5e6), ("B", 2e7)):
                for d, o, c in zip(dates, opens, closes):
                    rows.append(_row(d, t, o, c, 2e9, c * sh))
            _write_db(db, rows, {"A": "일반기업", "B": "보통회사"})
            BD.build(db=db, out=out)
            got = BD.load(out=out)
            self.assertGreater(len(got), 0)
            for _, r in got.iterrows():
                self.assertAlmostEqual((1 + r["r_on"]) * (1 + r["r_in"]),
                                       1 + r["r_cc"], places=9)


class TestCombos(unittest.TestCase):
    def test_four_combos_hardcoded(self):
        t = _syn_table()
        self.assertAlmostEqual(
            BD.bench_return("CAP1_ALL", "20240102", "close", "20240103", "open",
                             table=t), 0.003, places=9)
        self.assertAlmostEqual(
            BD.bench_return("CAP1_ALL", "20240102", "open", "20240103", "close",
                             table=t), 1.008 * 1.02 - 1, places=9)
        self.assertAlmostEqual(
            BD.bench_return("CAP1_ALL", "20240102", "close", "20240104", "close",
                             table=t), 1.02 * 1.03 - 1, places=9)
        self.assertAlmostEqual(
            BD.bench_return("CAP1_ALL", "20240103", "open", "20240103", "close",
                             table=t), 0.017, places=9)


class TestBuildRules(unittest.TestCase):
    def test_prev_cap_bucket(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            rows = [_row("20240102", "A", 10000.0, 10000.0, 2e9, 990e8),
                    _row("20240103", "A", 10050.0, 10100.0, 2e9, 1010e8)]
            _write_db(db, rows, {"A": "일반기업"})
            BD.build(db=db, out=out)
            got = BD.load(out=out)
            d2 = set(got[got["date"] == "20240103"]["bench_id"])
            self.assertIn("CAP1_ALL", d2)
            self.assertNotIn("CAP2_ALL", d2)

    def test_liq10_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            rows = [_row("20240102", "A", 10000.0, 10000.0, 0.99e9, 500e8),
                    _row("20240102", "B", 10000.0, 10000.0, 1e9, 500e8),
                    _row("20240103", "A", 10050.0, 10100.0, 2e9, 505e8),
                    _row("20240103", "B", 10050.0, 10100.0, 2e9, 505e8)]
            _write_db(db, rows, {"A": "일반기업", "B": "보통회사"})
            BD.build(db=db, out=out)
            got = BD.load(out=out)
            d2 = got[got["date"] == "20240103"]
            n_all = int(d2[d2["bench_id"] == "CAP1_ALL"]["n"].iloc[0])
            n_liq = int(d2[d2["bench_id"] == "CAP1_LIQ10"]["n"].iloc[0])
            self.assertEqual(n_all, 2)
            self.assertEqual(n_liq, 1)

    def test_spac_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            rows = [_row("20240102", "A", 10000.0, 10000.0, 2e9, 500e8),
                    _row("20240102", "B", 10000.0, 10000.0, 2e9, 500e8),
                    _row("20240103", "A", 10050.0, 10100.0, 2e9, 505e8),
                    _row("20240103", "B", 10050.0, 10100.0, 2e9, 505e8)]
            _write_db(db, rows, {"A": "일반기업", "B": "OO스팩제1호"})
            BD.build(db=db, out=out)
            got = BD.load(out=out)
            d2 = got[got["date"] == "20240103"]
            n_all = int(d2[d2["bench_id"] == "CAP1_ALL"]["n"].iloc[0])
            self.assertEqual(n_all, 1)


class TestErrors(unittest.TestCase):
    def test_value_errors(self):
        t = _syn_table()
        with self.assertRaises(ValueError):
            BD.bench_return("CAP1_ALL", "20240103", "close", "20240103", "open",
                             table=t)
        with self.assertRaises(ValueError):
            BD.bench_return("CAP1_ALL", "20240104", "close", "20240103", "close",
                             table=t)
        with self.assertRaises(ValueError):
            BD.bench_return("CAP1_ALL", "20240102", "mid", "20240103", "close",
                             table=t)
        with self.assertRaises(ValueError):
            BD.bench_return("CAP1_ALL", "20240102", "open", "20240103", "CLOSE",
                             table=t)
        with self.assertRaises(ValueError):
            BD.bench_return("CAP1_ALL", "19990101", "close", "20240103", "close",
                             table=t)
        with self.assertRaises(ValueError):
            BD.bench_return("CAP1_ALL", "20240102", "close", "19990105", "close",
                             table=t)
        with self.assertRaises(ValueError):
            BD.bench_id(500.0, "LIQ")
        with self.assertRaises(ValueError):
            BD.bench_id(float("nan"), "ALL")
        with self.assertRaises(ValueError):
            BD.bench_id(0.0, "ALL")


class TestSignatures(unittest.TestCase):
    def test_no_defaults(self):
        ps = list(inspect.signature(BD.bench_id).parameters.values())
        self.assertEqual(len(ps), 2)
        for p in ps:
            self.assertIs(p.default, inspect.Parameter.empty)
        pr = list(inspect.signature(BD.bench_return).parameters.values())
        self.assertEqual(len(pr), 6)
        for p in pr[:5]:
            self.assertIs(p.default, inspect.Parameter.empty)
        self.assertIsNot(pr[5].default, inspect.Parameter.empty)


class TestEnsure(unittest.TestCase):
    def test_built_fresh_stale(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            rows = [_row("20240102", "A", 10000.0, 10000.0, 2e9, 500e8),
                    _row("20240103", "A", 10050.0, 10100.0, 2e9, 505e8)]
            _write_db(db, rows, {"A": "일반기업"})
            BD.build(db=db, out=out)
            self.assertEqual(BD.ensure(db=db, out=out), "fresh")
            con = duckdb.connect(str(out))
            con.execute("UPDATE bench_build SET source_max_date='20000101'")
            con.close()
            self.assertEqual(BD.ensure(db=db, out=out), "built")
            self.assertEqual(BD.ensure(db=db, out=out), "fresh")
            con = duckdb.connect(str(out))
            con.execute("UPDATE bench_build SET code_version='999'")
            con.close()
            self.assertEqual(BD.ensure(db=db, out=out), "built")


class TestVector(unittest.TestCase):
    def test_scalar_vs_vector(self):
        base = _syn_table()
        extra = base.copy()
        extra["bench_id"] = "CAP2_ALL"
        extra["r_cc"] = [0.015, 0.025, 0.035]
        extra["r_on"] = [0.005, 0.006, 0.007]
        extra["r_in"] = [0.010, 0.019, 0.028]
        t = pd.concat([base, extra], ignore_index=True)
        ids = ["CAP1_ALL", "CAP1_ALL", "CAP2_ALL", "CAP2_ALL"]
        ed = ["20240102", "20240103", "20240102", "20240103"]
        xd = ["20240104", "20240104", "20240103", "20240103"]
        got = BD.bench_returns(ids, ed, "open", xd, "close", table=t)
        for i in range(4):
            want = BD.bench_return(ids[i], ed[i], "open", xd[i], "close", table=t)
            self.assertAlmostEqual(float(got[i]), want, places=12)


class TestPackageSingle(unittest.TestCase):
    def test_import_and_guards(self):
        m = importlib.import_module("research.backtest_daily.bench_daily")
        self.assertTrue(hasattr(m, "bench_return"))
        forbidden = ".".join(["research", "private"])
        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertNotIn(forbidden, src)
        first = (m.__doc__ or "").splitlines()[0] if m.__doc__ else ""
        self.assertIn("일봉 전용", first)


if __name__ == "__main__":
    unittest.main()
