"""dataset 테스트 (합성 cells만)."""

from __future__ import annotations

import unittest

import numpy as np

from research.rl_signal import bars
from research.rl_signal.dataset import samples
from research.rl_signal.oracle import BUY, HOLD, SELL

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
        X, y, meta = samples(_cells(_flat_q), np.random.default_rng(0))
        self.assertEqual(X.shape, (2 * 4 * 2, F + 2))
        self.assertEqual(y.shape, (16,))
        self.assertTrue((np.array([t for _, t in meta]) < 4).all())
        self.assertEqual(sorted({c for c, _ in meta}), ["AAA", "BBB"])
        self.assertTrue((y == HOLD).all())

    def test_extra_cols(self):
        X, _, _ = samples(_cells(_flat_q), np.random.default_rng(0))
        pos, k5 = X[:, -2], X[:, -1]
        self.assertTrue(set(np.unique(pos)) <= {0.0, 1.0})
        k5r = np.round(k5 * 5).astype(int)
        self.assertTrue(set(np.unique(k5r[pos == 0])) <= {1, 2, 3, 4, 5})
        self.assertTrue(set(np.unique(k5r[pos == 1])) <= {0, 1, 2, 3, 4})

    def test_feat_prefix(self):
        cells = _cells(_flat_q)
        X, _, meta = samples(cells, np.random.default_rng(0))
        c, t = meta[0]
        np.testing.assert_allclose(X[0, :-2], cells[c]["feat"][t], rtol=1e-6)

    def test_tie_break(self):
        def tie():
            return np.full((5, 2, 6, 3), 0.5)
        _, y, _ = samples(_cells(tie), np.random.default_rng(0))
        self.assertTrue((y == HOLD).all())

        def sell_buy():
            q = np.zeros((5, 2, 6, 3))
            q[..., SELL] = 0.3
            q[..., BUY] = 0.3
            return q
        _, y, _ = samples(_cells(sell_buy), np.random.default_rng(0))
        self.assertTrue((y == SELL).all())

        def no_hold():
            q = np.full((5, 2, 6, 3), -np.inf)
            q[..., SELL] = 0.2
            q[..., BUY] = 0.9
            return q
        _, y, _ = samples(_cells(no_hold), np.random.default_rng(0))
        self.assertTrue((y == BUY).all())


if __name__ == "__main__":
    unittest.main()
