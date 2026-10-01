"""bench_daily 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import datetime
import importlib
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class TestLimitFilter(unittest.TestCase):
    def test_exact_limit_up_kept(self):
        # 리뷰 #3 재현: +30%와 -29.9% 두 종목 → n=2, 평균 +0.05%
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            rows = [_row("20240102", "A", 100.0, 100.0, 2e9, 100.0 * 5e6),
                    _row("20240102", "B", 100.0, 100.0, 2e9, 100.0 * 5e6),
                    _row("20240103", "A", 100.0, 130.0, 2e9, 130.0 * 5e6),
                    _row("20240103", "B", 100.0, 70.1, 2e9, 70.1 * 5e6)]
            _write_db(db, rows, {"A": "일반기업", "B": "보통회사"})
            BD.build(db=db, out=out)
            got = BD.load(out=out)
            d2 = got[(got["date"] == "20240103")
                     & (got["bench_id"] == "MKT_ALL")]
            self.assertEqual(len(d2), 1)
            self.assertEqual(int(d2["n"].iloc[0]), 2)
            self.assertAlmostEqual(float(d2["r_cc"].iloc[0]), 0.0005, places=9)

    def test_intraday_80pct_kept(self):
        # 장중 +80%(하한가 시가→상승 종가)는 정상이라 포함
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            rows = [_row("20240102", "C", 100.0, 100.0, 2e9, 100.0 * 5e6),
                    _row("20240103", "C", 70.0, 126.0, 2e9, 126.0 * 5e6)]
            _write_db(db, rows, {"C": "일반기업"})
            BD.build(db=db, out=out)
            got = BD.load(out=out)
            d2 = got[(got["date"] == "20240103")
                     & (got["bench_id"] == "MKT_ALL")]
            self.assertEqual(len(d2), 1)
            self.assertEqual(int(d2["n"].iloc[0]), 1)
            self.assertAlmostEqual(float(d2["r_in"].iloc[0]), 0.80, places=9)

    def test_impossible_45pct_excluded(self):
        # 원 가격 +45%에 보정 없음 → 클립된 불가능 움직임이라 제외
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            rows = [_row("20240102", "D", 100.0, 100.0, 2e9, 100.0 * 5e6),
                    _row("20240103", "D", 100.0, 145.0, 2e9, 145.0 * 5e6)]
            _write_db(db, rows, {"D": "일반기업"})
            BD.build(db=db, out=out)
            got = BD.load(out=out)
            self.assertEqual(len(got), 0)


class TestVectorNaN(unittest.TestCase):
    def _nan_table(self, ccs):
        rows = []
        for i, cc in enumerate(ccs):
            rows.append({"date": f"2024010{i + 2}", "ms": i,
                         "bench_id": "CAP1_ALL", "n": 10,
                         "r_cc": cc, "r_on": 0.001, "r_in": 0.004})
        return pd.DataFrame(rows)

    def test_nan_before_range(self):
        # 리뷰 #4 재현: r_cc=[NaN,1%,2%,3%], 2번째 종가→4번째 종가 = 5.06%
        t = self._nan_table([float("nan"), 0.01, 0.02, 0.03])
        want = 1.02 * 1.03 - 1
        s = BD.bench_return("CAP1_ALL", "20240103", "close", "20240105",
                             "close", table=t)
        v = BD.bench_returns(["CAP1_ALL"], ["20240103"], "close",
                              ["20240105"], "close", table=t)
        self.assertAlmostEqual(s, want, places=12)
        self.assertAlmostEqual(float(v[0]), want, places=12)

    def test_nan_inside_range_raises(self):
        # 조회 구간 안 NaN 은 스칼라·벡터 모두 ValueError
        t = self._nan_table([0.01, float("nan"), 0.02, 0.03])
        with self.assertRaises(ValueError):
            BD.bench_return("CAP1_ALL", "20240102", "close", "20240105",
                             "close", table=t)
        with self.assertRaises(ValueError):
            BD.bench_returns(["CAP1_ALL"], ["20240102"], "close",
                              ["20240105"], "close", table=t)


class TestBuildTmp(unittest.TestCase):
    def _tiny_db(self, td):
        db = Path(td) / "d.duckdb"
        rows = [_row("20240102", "A", 10000.0, 10000.0, 2e9, 500e8),
                _row("20240103", "A", 10050.0, 10100.0, 2e9, 505e8)]
        _write_db(db, rows, {"A": "일반기업"})
        return db

    def test_tmp_name_unique_per_call(self):
        # 같은 out 에 두 번 빌드해도 임시 파일명이 매번 다름
        with tempfile.TemporaryDirectory() as td:
            db = self._tiny_db(td)
            out = Path(td) / "b.duckdb"
            with mock.patch("os.replace", wraps=os.replace) as m:
                BD.build(db=db, out=out)
                BD.build(db=db, out=out)
            srcs = [str(c.args[0]) for c in m.call_args_list
                    if str(c.args[1]) == str(out)]
            self.assertEqual(len(srcs), 2)
            self.assertNotEqual(srcs[0], srcs[1])
            for s in srcs:
                self.assertTrue(s.endswith(".tmp"))
                self.assertIn(str(os.getpid()), s)

    def test_no_tmp_left_on_replace_failure(self):
        # 교체 실패 시 만든 임시 파일이 남지 않음
        with tempfile.TemporaryDirectory() as td:
            db = self._tiny_db(td)
            out = Path(td) / "b.duckdb"
            real_replace = os.replace

            def _boom(src, dst):
                if Path(dst) == out:
                    raise RuntimeError("boom")
                return real_replace(src, dst)

            with mock.patch("os.replace", side_effect=_boom):
                with self.assertRaises(RuntimeError):
                    BD.build(db=db, out=out)
            self.assertEqual(list(Path(td).glob("*.tmp")), [])
            self.assertFalse(out.exists())

    def test_no_tmp_left_on_connect_failure(self):
        # 임시 DB 연결 실패해도 잔여 파일 없음
        real_connect = duckdb.connect

        def _boom_connect(path, *a, **k):
            if str(path).endswith(".tmp"):
                raise RuntimeError("boom")
            return real_connect(path, *a, **k)

        with tempfile.TemporaryDirectory() as td:
            db = self._tiny_db(td)
            out = Path(td) / "b.duckdb"
            with mock.patch("duckdb.connect", side_effect=_boom_connect):
                with self.assertRaises(RuntimeError):
                    BD.build(db=db, out=out)
            self.assertEqual(list(Path(td).glob("*.tmp")), [])


class TestSpacPriceRule(unittest.TestCase):
    def test_flat_2000_range_excluded(self):
        # 이름은 일반 회사인데 60행 평탄 2000원 구간은 제외, 합병 후 변동 구간은 포함
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "d.duckdb"
            out = Path(td) / "b.duckdb"
            base = datetime.date(2023, 1, 1)
            dates = [(base + datetime.timedelta(days=i)).strftime("%Y%m%d")
                     for i in range(70)]
            a_close = [1990.0 if i % 2 == 0 else 2010.0 for i in range(66)]
            a_close += [2600.0, 3400.0, 4400.0, 5700.0]
            rows = []
            for i, d in enumerate(dates):
                c = a_close[i]
                rows.append(_row(d, "A", c, c, 2e9, c * 1e6))
                b = 10000.0 * 1.01 ** i
                rows.append(_row(d, "B", b / 1.005, b, 2e9, b * 4e6))
            _write_db(db, rows, {"A": "일반기업", "B": "보통회사"})
            BD.build(db=db, out=out)
            got = BD.load(out=out)
            cap1 = got[got["bench_id"] == "CAP1_ALL"].set_index("date")
            for i in list(range(1, 59)) + list(range(66, 70)):
                self.assertEqual(int(cap1.loc[dates[i], "n"]), 2, f"idx {i}")
            for i in range(59, 66):
                self.assertEqual(int(cap1.loc[dates[i], "n"]), 1, f"idx {i}")
                self.assertAlmostEqual(float(cap1.loc[dates[i], "r_cc"]),
                                       0.01, places=9)


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
