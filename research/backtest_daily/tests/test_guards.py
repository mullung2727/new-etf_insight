"""guards 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from research.backtest_daily.guards import (
    fin_flags,
    halt_ok,
    jump_ok,
    limit_up_close,
    limit_up_open,
    liquidity,
)


def _df(ms, close=None, open_=None, tv=2e9, cap=1e11):
    ms = list(ms)
    n = len(ms)
    if close is None:
        close = [100.0] * n
    if open_ is None:
        open_ = list(close)
    tvs = list(tv) if isinstance(tv, (list, tuple)) else [tv] * n
    return pd.DataFrame({"ms": ms, "open": list(open_), "close": list(close),
                         "trading_value": tvs, "market_cap": [cap] * n})


class TestLimitClose(unittest.TestCase):
    def test_boundary(self):
        base = [100.0] * 11
        hi = _df(range(12), close=base + [129.6])
        lo = _df(range(12), close=base + [129.4])
        self.assertEqual(limit_up_close(hi, 11), "limit_up")
        self.assertIsNone(limit_up_close(lo, 11))

    def test_undecidable_on_gap(self):
        df = _df(list(range(11)) + [12])
        self.assertEqual(limit_up_close(df, 11), "limit_undecidable")

    def test_undecidable_on_bad_close(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                df = _df(range(12), close=[100.0] * 11 + [bad])
                self.assertEqual(limit_up_close(df, 11), "limit_undecidable")


class TestLimitOpen(unittest.TestCase):
    def test_boundary(self):
        hi = _df([0, 1], open_=[100.0, 129.6], close=[100.0, 100.0])
        lo = _df([0, 1], open_=[100.0, 129.4], close=[100.0, 100.0])
        self.assertEqual(limit_up_open(hi, 0), "limit_up")
        self.assertIsNone(limit_up_open(lo, 0))

    def test_undecidable_on_bad_price(self):
        # 종가 0 (0 나눗셈 방지)
        df = _df([0, 1], open_=[100.0, 130.0], close=[0.0, 100.0])
        self.assertEqual(limit_up_open(df, 0), "limit_undecidable")
        # NaN 시가
        df = _df([0, 1], open_=[100.0, float("nan")], close=[100.0, 100.0])
        self.assertEqual(limit_up_open(df, 0), "limit_undecidable")
        # NaN 당일 종가
        df = _df([0, 1], open_=[100.0, 130.0], close=[float("nan"), 100.0])
        self.assertEqual(limit_up_open(df, 0), "limit_undecidable")


class TestLiquidity(unittest.TestCase):
    def test_boundary(self):
        self.assertEqual(liquidity(_df(range(20), tv=9.9e8), 19), "liq_low")
        self.assertIsNone(liquidity(_df(range(20), tv=1e9), 19))

    def test_short(self):
        self.assertEqual(liquidity(_df(range(9), tv=2e9), 8), "liq_short")


class TestJumpOk(unittest.TestCase):
    def test_minus_32pct(self):
        px = pd.DataFrame({"ticker": ["A", "A"], "close": [100.0, 68.0]})
        got = jump_ok(px)
        self.assertTrue(got[0])
        self.assertFalse(got[1])


class TestHaltOk(unittest.TestCase):
    def test_one_missing_day(self):
        md = [f"202001{i:02d}" for i in range(1, 26)]
        miss = "20200110"
        a = pd.DataFrame({"ticker": "A", "date": [d for d in md if d != miss],
                          "volume": 100})
        got = halt_ok(a, md)
        # 20200121행: 직전 20일에 20200110 결측 → False
        i = a.index[a["date"] == "20200121"][0]
        self.assertFalse(got[i])
        # 결측 없는 종목 같은 날 → True
        b = pd.DataFrame({"ticker": "B", "date": md, "volume": 100})
        got_b = halt_ok(b, md)
        j = b.index[b["date"] == "20200121"][0]
        self.assertTrue(got_b[j])


class TestFinFlags(unittest.TestCase):
    def test_unknown_reprt_code_ignored(self):
        accts = ["매출액", "법인세차감전 순이익", "자본총계", "자본금"]
        vals = [1e11, 1e10, 5e10, 1e10]
        rows = [("A", 2022, rc, "OFS", n, v)
                for rc in ("11011", "99999")
                for n, v in zip(accts, vals)]
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "fin.sqlite3")
            sq = sqlite3.connect(db)
            sq.execute("CREATE TABLE accounts (stock_code TEXT, bsns_year INT,"
                       " reprt_code TEXT, fs_div TEXT, account_nm TEXT, amount REAL)")
            sq.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?)", rows)
            sq.commit()
            sq.close()
            T = pd.DataFrame({"ticker": ["A"], "ent_date": ["20230601"],
                              "market": ["KOSPI"], "market_cap": [1e12]})
            got = fin_flags(T, 1, fin_db=db)
        self.assertEqual(len(got), 1)
        self.assertFalse(bool(got["fin_nodata"].iloc[0]))
        self.assertFalse(bool(got["fin_bad"].iloc[0]))


if __name__ == "__main__":
    unittest.main()
