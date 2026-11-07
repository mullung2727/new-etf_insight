"""models 테스트 (합성 표본만). AdvModel 이득 회귀, ClassModel 래퍼 동등, early_stopping 끔."""

from __future__ import annotations

import unittest

import numpy as np

from research.rl_signal.models import AdvModel, ClassModel, decide_fn, make
from research.rl_signal.oracle import BUY, HOLD, K_MAX, SELL


class _IdScaler:
    def transform(self, X, names):
        return np.asarray(X, dtype=np.float32)


def _adv_data(seed=0, n=400):
    rng = np.random.default_rng(seed)
    f = rng.normal(size=(n, 1))
    pos = rng.integers(0, 2, size=n).astype(np.float64)
    k = np.where(pos == 0, rng.integers(1, K_MAX + 1, size=n),
                 rng.integers(0, K_MAX, size=n))
    X = np.column_stack([f, pos, k / 5]).astype(np.float32)
    adv = np.where(f[:, 0] > 0, 1.0, -1.0)
    return X, adv


class TestAdvModel(unittest.TestCase):
    def test_buy_sell_hold(self):
        X, adv = _adv_data()
        m = AdvModel().fit(X, np.zeros(len(X), dtype=int), adv, 1)
        dec = m.decide_fn(_IdScaler(), np.array([[2.0], [-2.0]]), ["f0"])
        self.assertEqual(dec(0, 0, 3), BUY)
        self.assertEqual(dec(1, 0, 3), HOLD)
        self.assertEqual(dec(0, 0, 0), HOLD)
        self.assertEqual(dec(0, 1, 2), SELL)
        self.assertEqual(dec(1, 1, 2), HOLD)

    def test_fit_returns_self(self):
        X, adv = _adv_data()
        m = AdvModel()
        self.assertIs(m.fit(X, np.zeros(len(X), dtype=int), adv, 1), m)

    def test_sell_empty_no_trade(self):
        X, adv = _adv_data()
        adv = np.where(X[:, 1] == 1, np.nan, adv)
        m = AdvModel().fit(X, np.zeros(len(X), dtype=int), adv, 1)
        dec = m.decide_fn(_IdScaler(), np.array([[2.0], [-2.0]]), ["f0"])
        for t in (0, 1):
            for k in range(K_MAX + 1):
                self.assertEqual(dec(t, 1, k), HOLD)

    def test_both_empty_no_trade(self):
        X, _ = _adv_data()
        adv = np.full(len(X), np.nan)
        m = AdvModel().fit(X, np.zeros(len(X), dtype=int), adv, 1)
        dec = m.decide_fn(_IdScaler(), np.array([[2.0], [-2.0]]), ["f0"])
        for t in (0, 1):
            for k in range(K_MAX + 1):
                self.assertEqual(dec(t, 0, k), HOLD)
                self.assertEqual(dec(t, 1, k), HOLD)


class TestClassModel(unittest.TestCase):
    def test_same_as_decide_fn(self):
        rng = np.random.default_rng(1)
        X = rng.normal(size=(120, 3)).astype(np.float32)
        y = (X[:, 0] > 0).astype(int) + (X[:, 1] > 0.5).astype(int)
        m = make("hgb")
        m.fit(X, y, np.zeros(len(X)), 0)
        feat = rng.normal(size=(6, 1))
        d_wrap = m.decide_fn(_IdScaler(), feat, ["f0"])
        d_raw = decide_fn(m.est, _IdScaler(), feat, ["f0"])
        for t in range(6):
            for pos in (0, 1):
                for k in range(K_MAX + 1):
                    self.assertEqual(d_wrap(t, pos, k), d_raw(t, pos, k))

    def test_fit_ignores_adv(self):
        m = make("logit")
        X = np.random.default_rng(2).normal(size=(30, 3))
        y = (X[:, 0] > 0).astype(int)
        self.assertIs(m.fit(X, y, None, 0), m)

    def test_single_label(self):
        rng = np.random.default_rng(3)
        X = rng.normal(size=(40, 3)).astype(np.float32)
        m = make("hgb")
        m.fit(X, np.zeros(40, dtype=int), None, 0)
        pr = m.est.predict_proba(X)
        self.assertEqual(pr.shape, (40, 1))
        np.testing.assert_allclose(pr, 1.0)
        dec = m.decide_fn(_IdScaler(), rng.normal(size=(4, 1)), ["f0"])
        for t in range(3):
            for pos in (0, 1):
                for k in range(K_MAX + 1):
                    self.assertEqual(dec(t, pos, k), HOLD)


class TestMake(unittest.TestCase):
    def test_names(self):
        self.assertIsInstance(make("logit"), ClassModel)
        self.assertIsInstance(make("hgb"), ClassModel)
        self.assertIsInstance(make("hgb_adv"), AdvModel)
        with self.assertRaises(ValueError):
            make("nope")

    def test_hgb_no_early_stopping(self):
        self.assertIs(make("hgb").est.early_stopping, False)

    def test_adv_no_early_stopping(self):
        X, adv = _adv_data(n=200)
        m = make("hgb_adv").fit(X, np.zeros(len(X), dtype=int), adv, 1)
        self.assertIs(m._buy.early_stopping, False)
        self.assertIs(m._sell.early_stopping, False)


if __name__ == "__main__":
    unittest.main()
