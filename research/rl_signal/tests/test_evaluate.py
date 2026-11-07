"""evaluate 테스트 (합성 per_code만, 실파일 읽지 않음)."""

from __future__ import annotations

import unittest

import numpy as np

from research.rl_signal.evaluate import summarize


def _per():
    return {
        "A": {"model": 0.02, "trades": 2, "bh": 0.01, "oracle": 0.05,
              "pr": np.array([0.0, 0.01, 0.03]), "pt": np.array([-0.01, 0.0, 0.01])},
        "B": {"model": 0.04, "trades": 1, "bh": -0.01, "oracle": 0.07,
              "pr": np.array([0.02, 0.02, 0.02]), "pt": np.array([0.05, 0.05, 0.05])},
    }


class TestSummarize(unittest.TestCase):
    def test_two_codes(self):
        s = summarize(_per())
        self.assertEqual(s["n"], 2)
        self.assertAlmostEqual(s["model"], 0.03)
        self.assertAlmostEqual(s["bh"], 0.0)
        self.assertAlmostEqual(s["oracle"], 0.06)
        self.assertEqual(s["trades"], 3)
        # pr_day = [0.01, 0.015, 0.025] → med 0.015, 전부 model(0.03)보다 작음
        self.assertAlmostEqual(s["pr_med"], 0.015)
        self.assertAlmostEqual(s["pct_random"], 1.0)
        # pt_day = [0.02, 0.025, 0.03] → med 0.025, 2/3 만 model보다 작음
        self.assertAlmostEqual(s["pt_med"], 0.025)
        self.assertAlmostEqual(s["pct_timing"], 2 / 3)
        # capture = (0.03-0.015)/(0.06-0.015) = 1/3
        self.assertAlmostEqual(s["capture"], 1 / 3)

    def test_subset(self):
        s = summarize(_per(), ["A"])
        self.assertEqual(s["n"], 1)
        self.assertAlmostEqual(s["model"], 0.02)
        self.assertAlmostEqual(s["pr_med"], 0.01)
        self.assertAlmostEqual(s["capture"], (0.02 - 0.01) / (0.05 - 0.01))

    def test_capture_zero_denom(self):
        base = {"model": 0.01, "trades": 0, "bh": 0.0,
                "pr": np.array([0.02]), "pt": np.array([0.0])}
        self.assertTrue(np.isnan(summarize({"A": dict(base, oracle=0.02)})["capture"]))
        self.assertTrue(np.isnan(summarize({"A": dict(base, oracle=0.01)})["capture"]))

    def test_empty(self):
        s = summarize(_per(), [])
        self.assertEqual(s["n"], 0)
        for k in ("model", "bh", "oracle", "trades", "pct_random", "pct_timing",
                  "pr_med", "pt_med", "capture"):
            self.assertTrue(np.isnan(s[k]), k)


if __name__ == "__main__":
    unittest.main()
