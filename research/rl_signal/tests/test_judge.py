"""judge 테스트 (합성 일별 값만, 실파일 읽지 않음)."""

from __future__ import annotations

import unittest

import numpy as np

from research.rl_signal.judge import block_boot_lower, pct_half, required_days, verdict


class TestPctHalf(unittest.TestCase):
    def test_half_tie(self):
        self.assertAlmostEqual(pct_half(0.5, [0.0, 0.5, 1.0]), 0.5)
        self.assertAlmostEqual(pct_half(1.0, [1.0, 1.0]), 0.5)
        self.assertAlmostEqual(pct_half(2.0, [0.0, 1.0]), 1.0)
        self.assertAlmostEqual(pct_half(-1.0, [0.0, 1.0]), 0.0)


class TestBlockBoot(unittest.TestCase):
    def test_constant_positive(self):
        lo = block_boot_lower(np.full(60, 0.002), 5, 500, np.random.default_rng(0))
        self.assertGreater(lo, 0)
        self.assertAlmostEqual(lo, 0.002)

    def test_zeros(self):
        lo = block_boot_lower(np.zeros(20), 5, 200, np.random.default_rng(0))
        self.assertAlmostEqual(lo, 0.0)


class TestRequiredDays(unittest.TestCase):
    def test_56(self):
        self.assertEqual(required_days(0.003), 56)


class TestVerdict(unittest.TestCase):
    def test_hold(self):
        self.assertEqual(verdict(59, True, True, True, True, 60), "보류")

    def test_pass(self):
        self.assertEqual(verdict(60, True, True, True, True, 60), "통과")

    def test_fail_names_criteria(self):
        v = verdict(60, True, False, True, False, 60)
        self.assertTrue(v.startswith("불통과"))
        self.assertIn("2", v)
        self.assertIn("4", v)
        self.assertNotIn("1", v)


if __name__ == "__main__":
    unittest.main()
