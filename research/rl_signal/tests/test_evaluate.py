"""evaluate 테스트 (합성 per_code만, 실파일 읽지 않음)."""

from __future__ import annotations

import unittest

import numpy as np

from research.rl_signal.evaluate import eval_codes, strata, summarize
from research.rl_signal.oracle import BUY


def _per():
    return {
        "A": {"model": 0.02, "model_slip1": 0.015, "trades": 2,
              "log_total": 0.0198, "log_total_slip1": 0.0149,
              "bh": 0.01, "oracle": 0.05,
              "pr": np.array([0.0, 0.01, 0.03]), "pt": np.array([-0.01, 0.0, 0.02]),
              "ps": np.array([0.005, 0.015, 0.025])},
        "B": {"model": 0.04, "model_slip1": 0.03, "trades": 1,
              "log_total": 0.0392, "log_total_slip1": 0.0296,
              "bh": -0.01, "oracle": 0.07,
              "pr": np.array([0.02, 0.02, 0.02]), "pt": np.array([0.05, 0.05, 0.05]),
              "ps": np.array([0.03, 0.03, 0.03])},
    }


class TestSummarize(unittest.TestCase):
    def test_two_codes(self):
        s = summarize(_per())
        self.assertEqual(s["n"], 2)
        self.assertAlmostEqual(s["model"], 0.03)
        self.assertAlmostEqual(s["model_slip1"], 0.0225)
        self.assertAlmostEqual(s["bh"], 0.0)
        self.assertAlmostEqual(s["oracle"], 0.06)
        self.assertEqual(s["trades"], 3)
        self.assertAlmostEqual(s["log_sum"], 0.059)
        self.assertAlmostEqual(s["log_sum_slip1"], 0.0445)
        np.testing.assert_allclose(s["ps_day"], [0.0175, 0.0225, 0.0275], rtol=1e-9)
        # pr_day = [0.01, 0.015, 0.025] → med 0.015, 전부 model(0.03)보다 작음
        self.assertAlmostEqual(s["pr_med"], 0.015)
        self.assertAlmostEqual(s["diag_pct_random"], 1.0)
        # pt_day = [0.02, 0.025, 0.035] → 2/3 만 model(0.03)보다 작음
        self.assertAlmostEqual(s["pt_med"], 0.025)
        self.assertAlmostEqual(s["pct_timing"], 2 / 3)
        # capture = (0.03-0.015)/(0.06-0.015) = 1/3
        self.assertAlmostEqual(s["diag_capture"], 1 / 3)

    def test_tie_half(self):
        per = {"A": {"model": 0.5, "model_slip1": 0.5, "trades": 1,
                     "log_total": 0.5, "log_total_slip1": 0.5, "bh": 0.0, "oracle": 1.0,
                     "pr": np.array([0.5]), "pt": np.array([0.5]), "ps": np.array([0.5])}}
        s = summarize(per)
        self.assertAlmostEqual(s["diag_pct_random"], 0.5)
        self.assertAlmostEqual(s["pct_timing"], 0.5)

    def test_subset(self):
        s = summarize(_per(), ["A"])
        self.assertEqual(s["n"], 1)
        self.assertAlmostEqual(s["model"], 0.02)
        self.assertAlmostEqual(s["pr_med"], 0.01)
        self.assertAlmostEqual(s["diag_capture"], (0.02 - 0.01) / (0.05 - 0.01))

    def test_capture_zero_denom(self):
        base = {"model": 0.01, "model_slip1": 0.008, "trades": 0,
                "log_total": 0.01, "log_total_slip1": 0.008, "bh": 0.0,
                "pr": np.array([0.02]), "pt": np.array([0.0]), "ps": np.array([0.01])}
        self.assertTrue(np.isnan(summarize({"A": dict(base, oracle=0.02)})["diag_capture"]))
        self.assertTrue(np.isnan(summarize({"A": dict(base, oracle=0.01)})["diag_capture"]))

    def test_empty(self):
        s = summarize(_per(), [])
        self.assertEqual(s["n"], 0)
        for k in ("model", "model_slip1", "bh", "oracle", "trades", "log_sum", "log_sum_slip1",
                  "diag_pct_random", "pct_timing", "pr_med", "pt_med", "diag_capture"):
            self.assertTrue(np.isnan(s[k]), k)
        self.assertEqual(len(s["ps_day"]), 0)


class TestStrata(unittest.TestCase):
    def test_split(self):
        self.assertEqual(strata(["069500", "000001", "122630"]),
                         {"stock": ["000001"], "etf": ["069500", "122630"]})

    def test_empty(self):
        self.assertEqual(strata([]), {"stock": [], "etf": []})


class TestEvalCodes(unittest.TestCase):
    def _cells(self):
        n = 5
        ask = np.array([100.01, 101.01, 102.01, 103.01, 104.01])
        bid = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        aq = np.full(n, 20000, dtype=np.int64)
        bq = np.full(n, 20000, dtype=np.int64)
        feat = np.zeros((n, 2), dtype=np.float32)
        q = np.zeros((n, 2, 6, 3))
        return {"AAA": {"ex_ask1": ask, "ex_bid1": bid, "ex_aq1": aq, "ex_bq1": bq,
                        "feat": feat, "q": q}}

    def test_model_slip1(self):
        cells = self._cells()

        class M:
            def decide_fn(self, scaler, feat, names):
                return lambda t, pos, k: BUY

        class S:
            def transform(self, X, names):
                return X

        per = eval_codes(M(), S(), ["f0", "f1"], cells, ["AAA"], 2, np.random.default_rng(0))
        self.assertIn("model_slip1", per["AAA"])
        self.assertIn("ps", per["AAA"])
        self.assertIn("log_total", per["AAA"])
        self.assertIn("log_total_slip1", per["AAA"])
        self.assertEqual(len(per["AAA"]["ps"]), 2)
        self.assertEqual(per["AAA"]["trades"], 1)
        self.assertLess(per["AAA"]["model_slip1"], per["AAA"]["model"])


if __name__ == "__main__":
    unittest.main()
