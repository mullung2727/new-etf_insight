"""scale 테스트 (합성 배열만)."""

from __future__ import annotations

import unittest

import numpy as np

from research.rl_signal.scale import Scaler

NAMES = ["ret_1", "spread_bp", "imb_1", "vol20", "pos"]


def _row(ret_1=0.0, spread_bp=1.0, imb_1=0.5, vol20=0.02, pos=1.0):
    return [ret_1, spread_bp, imb_1, vol20, pos]


class TestScaler(unittest.TestCase):
    def test_fit_stats_frozen(self):
        sc = Scaler().fit(np.array([_row(0.02), _row(0.04)]), NAMES)
        mu, sd = sc.mean_.copy(), sc.std_.copy()
        sc.transform(np.array([_row(999.0, vol20=999.0)]), NAMES)
        np.testing.assert_array_equal(sc.mean_, mu)
        np.testing.assert_array_equal(sc.std_, sd)

    def test_by_vol_numeric(self):
        sc = Scaler().fit(np.array([_row(0.02), _row(0.04)]), NAMES)
        out = sc.transform(np.array([_row(0.06)]), NAMES)
        self.assertAlmostEqual(float(out[0, 0]), 3.0, places=5)  # (3-1.5)/0.5
        fb = sc.transform(np.array([_row(0.06, vol20=np.nan)]), NAMES)
        self.assertAlmostEqual(float(fb[0, 0]), 3.0, places=5)  # fallback 0.02

    def test_clip(self):
        sc = Scaler().fit(np.array([_row(0.02), _row(0.04)]), NAMES)
        out = sc.transform(np.array([_row(100.0), _row(-100.0)]), NAMES)
        self.assertAlmostEqual(float(out[0, 0]), 5.0, places=5)
        self.assertAlmostEqual(float(out[1, 0]), -5.0, places=5)

    def test_nan_to_zero(self):
        sc = Scaler().fit(np.array([_row(0.02), _row(0.04)]), NAMES)
        out = sc.transform(np.array([_row(np.nan)]), NAMES)
        self.assertEqual(float(out[0, 0]), 0.0)

    def test_bounded_passthrough(self):
        sc = Scaler().fit(np.array([_row(imb_1=0.5), _row(imb_1=-0.2)]), NAMES)
        out = sc.transform(np.array([_row(imb_1=0.3), _row(imb_1=-0.7)]), NAMES)
        np.testing.assert_allclose(out[:, 2], [0.3, -0.7], rtol=1e-6)
        np.testing.assert_allclose(out[:, 4], [1.0, 1.0], rtol=1e-6)  # 미등록 pos 도 그대로


if __name__ == "__main__":
    unittest.main()
