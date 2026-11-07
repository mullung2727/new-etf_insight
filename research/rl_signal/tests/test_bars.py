"""bars 테스트 (합성 DataFrame만, 실파일 읽지 않음)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from research.rl_signal.bars import FEATURES, build, grid

try:
    from etl.scripts import market_session
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "etl" / "scripts"))
    import market_session

DAY = "20261109"
CODE = "069500"

_BOOK_COLS = (["rt", "code", "t"]
              + [f"{p}{i}" for p in ("ap", "bp", "aq", "bq") for i in range(1, 11)]
              + ["ask_tot", "bid_tot"])
_TRADE_COLS = ["rt", "code", "t", "price", "qty", "cum_vol", "ask1", "bid1"]
_GAP_COLS = ["conn", "start_rt", "end_rt", "reason"]


def _book_row(rt, code=CODE, ap1=100, bp1=99, aq1=10, bq1=30):
    row = {"rt": rt, "code": code, "t": rt}
    for i in range(1, 11):
        row[f"ap{i}"] = ap1 + i - 1
        row[f"bp{i}"] = bp1 - i + 1
        row[f"aq{i}"] = aq1
        row[f"bq{i}"] = bq1
    row["ask_tot"] = aq1 * 10
    row["bid_tot"] = bq1 * 10
    return row


def _trade_row(rt, qty, code=CODE, price=100):
    return {"rt": rt, "code": code, "t": rt, "price": price, "qty": qty,
            "cum_vol": 0, "ask1": 100, "bid1": 99}


def _cover_book(**kw):
    return pd.DataFrame([_book_row(int(grid(DAY)[0]) - 1, **kw)], columns=_BOOK_COLS)


def _empty_trade():
    return pd.DataFrame([], columns=_TRADE_COLS)


def _empty_gaps():
    return pd.DataFrame([], columns=_GAP_COLS)


class TestGrid(unittest.TestCase):
    def test_grid(self):
        g = grid(DAY)
        self.assertEqual(len(g), 759)
        self.assertTrue((np.diff(g) == 30_000_000_000).all())
        for hms, v in (("09:00:30", g[0]), ("15:19:30", g[-1])):
            at = market_session.at(DAY, hms)
            self.assertEqual(int(v), int(at.timestamp()) * 1_000_000_000 + at.microsecond * 1000)


class TestBuild(unittest.TestCase):
    def test_aggregation(self):
        j, b = 100, int(grid(DAY)[100])
        trade = pd.DataFrame([_trade_row(b - 1, 3), _trade_row(b - 2, -2), _trade_row(b - 3, 1)],
                             columns=_TRADE_COLS)
        d = build(DAY, trade, _cover_book(), _empty_gaps())[CODE]
        self.assertEqual(int(d["buy_vol"][j]), 4)
        self.assertEqual(int(d["sell_vol"][j]), 2)
        self.assertAlmostEqual(float(d["feat"][j, FEATURES.index("buy_ratio")]), 4 / 6, places=6)
        rest = np.concatenate([d["buy_vol"][:j], d["buy_vol"][j + 1:]])
        self.assertEqual(int(rest.sum()), 0)

    def test_no_leak_middle(self):
        b = int(grid(DAY)[100])
        base = build(DAY, _empty_trade(), _cover_book(), _empty_gaps())[CODE]
        mod_book = pd.concat([_cover_book(), pd.DataFrame([_book_row(b, ap1=200, bp1=199)],
                                                          columns=_BOOK_COLS)], ignore_index=True)
        mod_trade = pd.DataFrame([_trade_row(b, 1000)], columns=_TRADE_COLS)
        mod = build(DAY, mod_trade, mod_book, _empty_gaps())[CODE]
        for key in ("ask1", "bid1", "buy_vol", "sell_vol"):
            self.assertEqual(mod[key][100], base[key][100], key)
        np.testing.assert_array_equal(mod["feat"][100], base["feat"][100])

    def test_no_leak_last(self):
        b = int(grid(DAY)[-1])
        base = build(DAY, _empty_trade(), _cover_book(), _empty_gaps())[CODE]
        bad = pd.DataFrame([_book_row(b, ap1=0, bp1=0)], columns=_BOOK_COLS)
        mod_book = pd.concat([_cover_book(), bad], ignore_index=True)
        mod_trade = pd.DataFrame([_trade_row(b, 10 ** 9), _trade_row(b, -(10 ** 9))],
                                 columns=_TRADE_COLS)
        mod = build(DAY, mod_trade, mod_book, _empty_gaps())[CODE]
        for key in ("ask1", "bid1", "ap", "bp", "aq", "bq", "buy_vol", "sell_vol", "feat"):
            np.testing.assert_array_equal(mod[key], base[key])

    def test_gap_excludes(self):
        b = int(grid(DAY)[10])
        gaps = pd.DataFrame([{"conn": "x", "start_rt": b - 15_000_000_000,
                              "end_rt": b, "reason": "t"}], columns=_GAP_COLS)
        res = build(DAY, _empty_trade(), _cover_book(), gaps)
        self.assertNotIn(CODE, res)
        self.assertIn((CODE, "gap"), res["_excluded"])

    def test_no_book_excludes(self):
        trade = pd.DataFrame([_trade_row(int(grid(DAY)[50]), 5)], columns=_TRADE_COLS)
        res = build(DAY, trade, pd.DataFrame([], columns=_BOOK_COLS), _empty_gaps())
        self.assertNotIn(CODE, res)
        self.assertIn((CODE, "no_book"), res["_excluded"])

    def test_imb_sign(self):
        d = build(DAY, _empty_trade(), _cover_book(aq1=10, bq1=30), _empty_gaps())[CODE]
        imb = d["feat"][:, FEATURES.index("imb_1")]
        self.assertTrue((imb > 0).all())
        self.assertAlmostEqual(float(imb[0]), 0.5, places=6)

    def test_planes(self):
        d = build(DAY, _empty_trade(), _cover_book(), _empty_gaps(), planes=True)[CODE]
        pl = d["planes"]
        self.assertEqual(pl.shape, (759, 4, 20, 20))
        for tau in range(20):
            self.assertAlmostEqual(float(pl[-1, 0, :, tau].sum(dtype=np.float64)), 1.0, places=6)
        self.assertEqual(float(pl[0, 0, :, 0].sum()), 0.0)
        d_off = build(DAY, _empty_trade(), _cover_book(), _empty_gaps())[CODE]
        self.assertNotIn("planes", d_off)


if __name__ == "__main__":
    unittest.main()
