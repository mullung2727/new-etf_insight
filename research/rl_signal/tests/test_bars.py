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
    g = grid(DAY)
    return pd.DataFrame([_book_row(int(g[0]) - 1, **kw), _book_row(int(g[-1]), **kw)],
                        columns=_BOOK_COLS)


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
        res = build(DAY, trade, _cover_book(code="OTHER"), _empty_gaps())
        self.assertNotIn(CODE, res)
        self.assertIn((CODE, "no_book"), res["_excluded"])

    def test_short_day_excludes_all(self):
        g = grid(DAY)
        book = pd.DataFrame([_book_row(int(g[0]) - 1)], columns=_BOOK_COLS)
        trade = pd.DataFrame([_trade_row(int(g[50]), 5),
                              _trade_row(int(g[50]), 5, code="OTHER")],
                             columns=_TRADE_COLS)
        res = build(DAY, trade, book, _empty_gaps())
        self.assertEqual(sorted(res["_excluded"]), [(CODE, "short_day"), ("OTHER", "short_day")])
        self.assertNotIn(CODE, res)
        self.assertNotIn("OTHER", res)

    def test_short_day_empty_book(self):
        g = grid(DAY)
        trade = pd.DataFrame([_trade_row(int(g[50]), 5)], columns=_TRADE_COLS)
        book = pd.DataFrame([], columns=_BOOK_COLS)
        res = build(DAY, trade, book, _empty_gaps())
        self.assertEqual(res["_excluded"], [(CODE, "short_day")])

    def test_one_sided_zero_qty(self):
        res = build(DAY, _empty_trade(), _cover_book(aq1=0), _empty_gaps())
        self.assertIn(CODE, res)
        d = res[CODE]
        self.assertTrue((d["ask1"] == 0).all())
        self.assertTrue((d["bid1"] == 99).all())
        self.assertTrue((d["feat"][:, FEATURES.index("spread_bp")] == 0).all())
        self.assertEqual(res["_reasons"], {})

    def test_imb_sign(self):
        d = build(DAY, _empty_trade(), _cover_book(aq1=10, bq1=30), _empty_gaps())[CODE]
        imb = d["feat"][:, FEATURES.index("imb_1")]
        self.assertTrue((imb > 0).all())
        self.assertAlmostEqual(float(imb[0]), 0.5, places=6)

    def test_vol_rel_prior_mean(self):
        g = grid(DAY)
        trade = pd.DataFrame([_trade_row(int(g[0]) - 1, 10), _trade_row(int(g[1]) - 1, 30)],
                             columns=_TRADE_COLS)
        d = build(DAY, trade, _cover_book(), _empty_gaps())[CODE]
        vr = d["feat"][:, FEATURES.index("vol_rel")]
        self.assertAlmostEqual(float(vr[0]), 1.0, places=6)
        self.assertAlmostEqual(float(vr[1]), 3.0, places=6)

    def test_planes(self):
        d = build(DAY, _empty_trade(), _cover_book(), _empty_gaps(), planes=True)[CODE]
        pl = d["planes"]
        self.assertEqual(pl.shape, (759, 4, 20, 20))
        for tau in range(20):
            self.assertAlmostEqual(float(pl[-1, 0, :, tau].sum(dtype=np.float64)), 1.0, places=6)
        self.assertEqual(float(pl[0, 0, :, 0].sum()), 0.0)
        d_off = build(DAY, _empty_trade(), _cover_book(), _empty_gaps())[CODE]
        self.assertNotIn("planes", d_off)
        g = grid(DAY)
        book = pd.concat([_cover_book(),
                          pd.DataFrame([_book_row(int(g[-2]), aq1=20)], columns=_BOOK_COLS)],
                         ignore_index=True)
        trade = pd.DataFrame([_trade_row(int(g[-1]) - 1, 5), _trade_row(int(g[-1]) - 2, -3)],
                             columns=_TRADE_COLS)
        dd = build(DAY, trade, book, _empty_gaps(), planes=True)[CODE]
        tot_last = float(dd["aq"][-1].sum() + dd["bq"][-1].sum())
        self.assertAlmostEqual(float(dd["planes"][-1, 1, 9, 19]), (20 - 10) / tot_last, places=6)
        self.assertAlmostEqual(float(dd["planes"][-1, 2, 9, 19]), 5 / tot_last, places=6)
        self.assertAlmostEqual(float(dd["planes"][-1, 3, 10, 19]), 3 / tot_last, places=6)
        b = int(g[100])
        small = build(DAY, pd.DataFrame([_trade_row(b - 1, 1), _trade_row(b - 2, -1)],
                                        columns=_TRADE_COLS),
                      _cover_book(), _empty_gaps(), planes=True)[CODE]
        large = build(DAY, pd.DataFrame([_trade_row(b - 1, 100000), _trade_row(b - 2, -100000)],
                                        columns=_TRADE_COLS),
                      _cover_book(), _empty_gaps(), planes=True)[CODE]
        self.assertNotEqual(float(small["planes"][100, 2, 9, 19]),
                            float(large["planes"][100, 2, 9, 19]))


class TestExecSnapshot(unittest.TestCase):
    def test_exec_after_boundary(self):
        j, b = 100, int(grid(DAY)[100])
        book = pd.concat([_cover_book(),
                          pd.DataFrame([_book_row(b - 1, ap1=100, bp1=99),
                                        _book_row(b, ap1=200, bp1=199)],
                                       columns=_BOOK_COLS)], ignore_index=True)
        d = build(DAY, _empty_trade(), book, _empty_gaps())[CODE]
        self.assertEqual(float(d["ask1"][j]), 100.0)
        self.assertEqual(float(d["bid1"][j]), 99.0)
        self.assertEqual(float(d["ex_ask1"][j]), 200.0)
        self.assertEqual(float(d["ex_bid1"][j]), 199.0)
        self.assertEqual(d["ex_ask1"].dtype, np.float64)
        self.assertEqual(d["ex_aq1"].dtype, np.int64)

    def test_exec_missing(self):
        d = build(DAY, _empty_trade(), _cover_book(), _empty_gaps())[CODE]
        self.assertEqual(float(d["ex_ask1"][100]), 0.0)
        self.assertEqual(float(d["ex_bid1"][100]), 0.0)
        self.assertEqual(int(d["ex_aq1"][100]), 0)
        self.assertEqual(int(d["ex_bq1"][100]), 0)


class TestOneSidedStale(unittest.TestCase):
    def test_limit_up_uses_bid(self):
        res = build(DAY, _empty_trade(), _cover_book(ap1=0, aq1=0), _empty_gaps())
        self.assertIn(CODE, res)
        d = res[CODE]
        self.assertTrue((d["ask1"] == 0).all())
        self.assertTrue((d["bid1"] == 99).all())
        self.assertTrue((d["feat"][:, FEATURES.index("spread_bp")] == 0).all())
        keep = [j for j, nm in enumerate(FEATURES)
                if nm not in ("ret_prev", "ma20_gap", "ret5", "vol20")]
        self.assertTrue(np.isfinite(d["feat"][:, keep]).all())

    def test_limit_down_uses_ask(self):
        res = build(DAY, _empty_trade(), _cover_book(bp1=0, bq1=0), _empty_gaps())
        self.assertIn(CODE, res)
        d = res[CODE]
        self.assertTrue((d["bid1"] == 0).all())
        self.assertTrue((d["ask1"] == 100).all())
        self.assertTrue((d["feat"][:, FEATURES.index("spread_bp")] == 0).all())

    def test_stale_copies_previous(self):
        j, b = 100, int(grid(DAY)[100])
        book = pd.concat([_cover_book(),
                          pd.DataFrame([_book_row(b - 1, ap1=0, bp1=0, aq1=0, bq1=0),
                                        _book_row(b, ap1=100, bp1=99)],
                                       columns=_BOOK_COLS)], ignore_index=True)
        d = build(DAY, _empty_trade(), book, _empty_gaps())[CODE]
        self.assertEqual(float(d["ask1"][j]), float(d["ask1"][j - 1]))
        self.assertEqual(float(d["bid1"][j]), float(d["bid1"][j - 1]))
        self.assertGreaterEqual(int(d["stale_cells"]), 1)

    def test_stale_cross(self):
        j, b = 100, int(grid(DAY)[100])
        book = pd.concat([_cover_book(),
                          pd.DataFrame([_book_row(b - 1, ap1=99, bp1=100),
                                        _book_row(b, ap1=100, bp1=99)],
                                       columns=_BOOK_COLS)], ignore_index=True)
        d = build(DAY, _empty_trade(), book, _empty_gaps())[CODE]
        self.assertEqual(float(d["ask1"][j]), float(d["ask1"][j - 1]))
        self.assertGreaterEqual(int(d["stale_cells"]), 1)

    def test_stale_first_cell_no_book(self):
        res = build(DAY, _empty_trade(), _cover_book(ap1=0, bp1=0), _empty_gaps())
        self.assertNotIn(CODE, res)
        self.assertIn((CODE, "no_book"), res["_excluded"])
        self.assertEqual(res["_reasons"], {"no_book": 1})

    def test_reasons(self):
        b = int(grid(DAY)[10])
        gaps = pd.DataFrame([{"conn": "x", "start_rt": b - 15_000_000_000,
                              "end_rt": b, "reason": "t"}], columns=_GAP_COLS)
        res = build(DAY, _empty_trade(), _cover_book(), gaps)
        self.assertEqual(res["_reasons"], {"gap": 1})
        ok = build(DAY, _empty_trade(), _cover_book(), _empty_gaps())
        self.assertEqual(ok["_reasons"], {})


class TestNewFeatures(unittest.TestCase):
    def test_features_20(self):
        self.assertEqual(len(FEATURES), 20)
        self.assertEqual(FEATURES[10:], ["rv_10", "rv_30", "ret_open", "hl_pos", "ret_prev",
                                        "ma20_gap", "ret5", "vol20", "vwap_gap", "ma30_gap"])

    def test_new_features_no_leak(self):
        j, b = 100, int(grid(DAY)[100])
        day = {CODE: {"prev_close": 99.0, "ma20_gap": 0.01, "ret5": 0.02, "vol20": 0.03}}
        base = build(DAY, _empty_trade(), _cover_book(), _empty_gaps(), daily=day)[CODE]
        mod_book = pd.concat([_cover_book(), pd.DataFrame([_book_row(b, ap1=200, bp1=199)],
                                                          columns=_BOOK_COLS)], ignore_index=True)
        mod_trade = pd.DataFrame([_trade_row(b, 1000, price=500)], columns=_TRADE_COLS)
        mod = build(DAY, mod_trade, mod_book, _empty_gaps(), daily=day)[CODE]
        np.testing.assert_array_equal(mod["feat"][:j + 1], base["feat"][:j + 1])

    def test_vwap_value(self):
        j, b = 100, int(grid(DAY)[100])
        trade = pd.DataFrame([_trade_row(b - 1, 2, price=100),
                              _trade_row(b - 2, -1, price=110)], columns=_TRADE_COLS)
        d = build(DAY, trade, _cover_book(), _empty_gaps())[CODE]
        vg = d["feat"][:, FEATURES.index("vwap_gap")]
        self.assertAlmostEqual(float(vg[j - 1]), 0.0, places=6)
        self.assertAlmostEqual(float(vg[j]), np.log(99.5 / (310 / 3)), places=6)

    def test_hl_pos_range(self):
        g = grid(DAY)
        book = pd.concat([_cover_book(),
                          pd.DataFrame([_book_row(int(g[200]), ap1=110, bp1=109)],
                                       columns=_BOOK_COLS)], ignore_index=True)
        d = build(DAY, _empty_trade(), book, _empty_gaps())[CODE]
        hl = d["feat"][:, FEATURES.index("hl_pos")]
        self.assertTrue(((hl >= 0) & (hl <= 1)).all())
        self.assertAlmostEqual(float(hl[0]), 0.5, places=6)
        self.assertAlmostEqual(float(hl[201]), 1.0, places=6)

    def test_daily_none_nan(self):
        d = build(DAY, _empty_trade(), _cover_book(), _empty_gaps())[CODE]
        for name in ("ret_prev", "ma20_gap", "ret5", "vol20"):
            self.assertTrue(np.isnan(d["feat"][:, FEATURES.index(name)]).all(), name)

    def test_daily_const(self):
        day = {CODE: {"prev_close": 100.0, "ma20_gap": 0.01, "ret5": 0.02, "vol20": 0.03}}
        d = build(DAY, _empty_trade(), _cover_book(), _empty_gaps(), daily=day)[CODE]
        for name, v in (("ma20_gap", 0.01), ("ret5", 0.02), ("vol20", 0.03)):
            np.testing.assert_allclose(d["feat"][:, FEATURES.index(name)], v, rtol=1e-6)
        np.testing.assert_allclose(d["feat"][:, FEATURES.index("ret_prev")],
                                   np.log(99.5 / 100.0), rtol=1e-6)


if __name__ == "__main__":
    unittest.main()
