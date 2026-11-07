"""dataset 테스트 (합성 cells만)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from research.rl_signal import bars
from research.rl_signal import costs as costs_mod
from research.rl_signal import dataset as ds_mod
from research.rl_signal.dataset import cache_key, cache_tag, day_cells, day_reasons, samples
from research.rl_signal.oracle import BUY, HOLD, SELL, step_rewards

F = len(bars.FEATURES)


def _cells(qfn):
    out = {}
    for c in ("AAA", "BBB"):
        out[c] = {"feat": np.random.default_rng(0).normal(size=(5, F)).astype(np.float32),
                  "q": qfn()}
    return out


def _flat_q():
    q = np.full((5, 2, 6, 3), -np.inf)
    q[..., HOLD] = 0.0
    return q


class TestSamples(unittest.TestCase):
    def test_counts_and_meta(self):
        X, y, meta, adv = samples(_cells(_flat_q), np.random.default_rng(0))
        self.assertEqual(X.shape, (2 * 4 * 2, F + 2))
        self.assertEqual(y.shape, (16,))
        self.assertEqual(adv.shape, (16,))
        self.assertTrue((np.array([t for _, t in meta]) < 4).all())
        self.assertEqual(sorted({c for c, _ in meta}), ["AAA", "BBB"])
        self.assertTrue((y == HOLD).all())

    def test_extra_cols(self):
        X, _, _, _ = samples(_cells(_flat_q), np.random.default_rng(0))
        pos, k5 = X[:, -2], X[:, -1]
        self.assertTrue(set(np.unique(pos)) <= {0.0, 1.0})
        k5r = np.round(k5 * 5).astype(int)
        self.assertTrue(set(np.unique(k5r[pos == 0])) <= {1, 2, 3, 4, 5})
        self.assertTrue(set(np.unique(k5r[pos == 1])) <= {0, 1, 2, 3, 4})

    def test_feat_prefix(self):
        cells = _cells(_flat_q)
        X, _, meta, _ = samples(cells, np.random.default_rng(0))
        c, t = meta[0]
        np.testing.assert_allclose(X[0, :-2], cells[c]["feat"][t], rtol=1e-6)

    def test_tie_break(self):
        def tie():
            return np.full((5, 2, 6, 3), 0.5)
        _, y, _, _ = samples(_cells(tie), np.random.default_rng(0))
        self.assertTrue((y == HOLD).all())

        def sell_buy():
            q = np.zeros((5, 2, 6, 3))
            q[..., SELL] = 0.3
            q[..., BUY] = 0.3
            return q
        _, y, _, _ = samples(_cells(sell_buy), np.random.default_rng(0))
        self.assertTrue((y == SELL).all())

        def no_hold():
            q = np.full((5, 2, 6, 3), -np.inf)
            q[..., SELL] = 0.2
            q[..., BUY] = 0.9
            return q
        _, y, _, _ = samples(_cells(no_hold), np.random.default_rng(0))
        self.assertTrue((y == BUY).all())

    def test_uses_q_not_q_slip1(self):
        cells = _cells(_flat_q)
        for c in cells:
            qs = np.full((5, 2, 6, 3), -np.inf)
            qs[..., BUY] = 1.0
            cells[c]["q_slip1"] = qs
            cells[c]["ex_ask1"] = np.ones(5)
            cells[c]["ex_bid1"] = np.ones(5)
            cells[c]["ex_aq1"] = np.ones(5, dtype=np.int64)
            cells[c]["ex_bq1"] = np.ones(5, dtype=np.int64)
        _, y, _, _ = samples(cells, np.random.default_rng(0))
        self.assertTrue((y == HOLD).all())


class TestAdv(unittest.TestCase):
    def test_values(self):
        def qfn():
            return np.random.default_rng(3).normal(size=(5, 2, 6, 3))
        cells = _cells(qfn)
        X, _, meta, adv = samples(cells, np.random.default_rng(0))
        for i, (c, t) in enumerate(meta):
            q = cells[c]["q"]
            pos = int(round(float(X[i, -2])))
            k = int(round(float(X[i, -1]) * 5))
            a = BUY if pos == 0 else SELL
            self.assertAlmostEqual(float(adv[i]), float(q[t, pos, k, a] - q[t, pos, k, HOLD]))

    def test_impossible_nan(self):
        _, _, _, adv = samples(_cells(_flat_q), np.random.default_rng(0))
        self.assertTrue(np.isnan(adv).all())

    def test_empty(self):
        X, y, meta, adv = samples({}, np.random.default_rng(0))
        self.assertEqual(X.shape, (0, F + 2))
        self.assertEqual(y.shape, (0,))
        self.assertEqual(meta, [])
        self.assertEqual(adv.shape, (0,))


class TestShuffle(unittest.TestCase):
    def test_x_same_y_differs(self):
        def ramp():
            q = np.full((5, 2, 6, 3), -np.inf)
            for t in range(5):
                q[t, :, :, HOLD] = 0.0
                q[t, :, :, BUY] = float(t)
                q[t, :, :, SELL] = float(t)
            return q
        cells = _cells(ramp)
        X0, y0, _, _ = samples(cells, np.random.default_rng(0))
        X1, y1, _, _ = samples(cells, np.random.default_rng(0), np.random.default_rng(7))
        np.testing.assert_array_equal(X1, X0)
        self.assertFalse((y1 == y0).all())


class TestCacheTag(unittest.TestCase):
    def test_len_hex(self):
        t = cache_tag()
        self.assertEqual(len(t), 8)
        int(t, 16)

    def test_fee_changes_tag(self):
        t0 = cache_tag()
        old = costs_mod.FEE
        try:
            costs_mod.FEE = old + 0.001
            t1 = cache_tag()
        finally:
            costs_mod.FEE = old
        self.assertNotEqual(t0, t1)


class TestDayReasonsCache(unittest.TestCase):
    def test_missing_day(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(ds_mod, "CACHE", Path(td)):
                self.assertEqual(day_reasons("19000101"), {})

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(ds_mod, "CACHE", Path(td)):
                tag = cache_tag()
                p = Path(td) / f"cells_20261109_{tag}.npz"
                np.savez_compressed(str(p), codes=np.array([]),
                                    reasons_keys=np.array(["gap", "no_exec"]),
                                    reasons_vals=np.array([2, 1], dtype=np.int64))
                self.assertEqual(day_reasons("20261109"), {"gap": 2, "no_exec": 1})

    def test_day_cells_loads_new_arrays(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(ds_mod, "CACHE", Path(td)):
                key = cache_key("20261109", None)
                p = Path(td) / f"cells_20261109_{key}.npz"
                n, nf = 5, len(bars.FEATURES)
                feat = np.zeros((1, n, nf), dtype=np.float32)
                ask1 = np.ones((1, n)) * 100.0
                bid1 = np.ones((1, n)) * 99.0
                ex_ask = np.ones((1, n)) * 101.0
                ex_bid = np.ones((1, n)) * 100.0
                ex_aq = np.ones((1, n), dtype=np.int64) * 20000
                ex_bq = np.ones((1, n), dtype=np.int64) * 20000
                q = np.zeros((1, n, 2, 6, 3))
                q1 = np.ones((1, n, 2, 6, 3))
                m0 = np.array([99.5])
                np.savez_compressed(str(p), codes=np.array(["AAA"]), feat=feat,
                                    ask1=ask1, bid1=bid1, ex_ask1=ex_ask, ex_bid1=ex_bid,
                                    ex_aq1=ex_aq, ex_bq1=ex_bq, q=q, q_slip1=q1, mark0=m0,
                                    reasons_keys=np.array([]), reasons_vals=np.array([], dtype=np.int64))
                cells = day_cells("20261109", None)
                self.assertIn("AAA", cells)
                e = cells["AAA"]
                for k in ("ex_ask1", "ex_bid1", "ex_aq1", "ex_bq1", "q", "q_slip1", "mark0"):
                    self.assertIn(k, e)
                np.testing.assert_array_equal(e["q_slip1"], q1[0])
                self.assertAlmostEqual(float(e["mark0"]), 99.5)


def _tbl(prev_close=100.0):
    return pd.DataFrame({"date": ["20261109"], "ticker": ["AAA"],
                         "prev_close": [prev_close], "ma20_gap": [0.0],
                         "ret5": [0.0], "vol20": [0.01]})


def _root_with(td, size):
    sub = Path(td) / "2026" / "11"
    sub.mkdir(parents=True)
    for nm in ("trade", "book", "gaps"):
        (sub / f"{nm}_20261109.parquet").write_bytes(b"x" * size)
    return td


def _built(n=3):
    def entry(ex_on):
        ex = np.array(ex_on, dtype=np.float64)
        return {"feat": np.zeros((n, F), dtype=np.float32),
                "ask1": np.full(n, 100.0), "bid1": np.full(n, 99.0),
                "ex_ask1": np.where(ex, 101.0, 0.0),
                "ex_bid1": np.where(ex, 100.0, 0.0),
                "ex_aq1": np.where(ex, 20000, 0).astype(np.int64),
                "ex_bq1": np.where(ex, 20000, 0).astype(np.int64)}
    return {"_reasons": {},
            "AAA": entry([False, True, True]),
            "BBB": entry([False, False, False])}


class TestCacheKey(unittest.TestCase):
    def test_size_changes_key(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ka = cache_key("20261109", None, root=_root_with(a, 10))
            kb = cache_key("20261109", None, root=_root_with(b, 11))
            self.assertNotEqual(ka, kb)

    def test_daily_changes_key(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root_with(td, 10)
            ka = cache_key("20261109", _tbl(100.0), root=root)
            kb = cache_key("20261109", _tbl(101.0), root=root)
            self.assertNotEqual(ka, kb)


class TestDayCellsExec(unittest.TestCase):
    def test_first_cell_missing_kept_all_missing_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(ds_mod, "CACHE", Path(td)):
                with mock.patch.object(ds_mod.bars, "load_day", return_value=(None, None, None)), \
                        mock.patch.object(ds_mod.bars, "build", return_value=_built()):
                    cells = day_cells("20261109", _tbl(), root=td)
                self.assertIn("AAA", cells)
                self.assertNotIn("BBB", cells)
                self.assertEqual(day_reasons("20261109"), {"no_exec": 1})
                e = cells["AAA"]
                self.assertAlmostEqual(float(e["mark0"]), 99.5)
                _, _, _, can_buy, can_sell = step_rewards(
                    e["ex_ask1"], e["ex_bid1"], e["ex_aq1"], e["ex_bq1"], "AAA", mark0=99.5)
                self.assertTrue(can_buy[1:].all())
                self.assertTrue(can_sell[1:].all())

    def test_corrupt_cache_rebuilt(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(ds_mod, "CACHE", Path(td)):
                key = cache_key("20261109", _tbl(), root=td)
                p = Path(td) / f"cells_20261109_{key}.npz"
                p.write_bytes(b"not a npz")
                with mock.patch.object(ds_mod.bars, "load_day", return_value=(None, None, None)), \
                        mock.patch.object(ds_mod.bars, "build", return_value=_built()):
                    cells = day_cells("20261109", _tbl(), root=td)
                self.assertIn("AAA", cells)
                self.assertIn("mark0", cells["AAA"])
                with np.load(str(p)) as z:
                    self.assertIn("AAA", [str(c) for c in z["codes"].tolist()])
                    self.assertIn("mark0", z)
                self.assertEqual(list(Path(td).glob("*.tmp.npz")), [])


if __name__ == "__main__":
    unittest.main()
