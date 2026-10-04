"""portfolio 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import pandas as pd

from research.backtest_daily.portfolio import run

D2 = ["20240102", "20240103"]
D3 = ["20240102", "20240103", "20240104"]


class TestRun(unittest.TestCase):
    def test_p1_single_asset_full(self):
        returns = pd.DataFrame({"A": [0.10, -0.05]}, index=D2)
        weights = pd.DataFrame({"A": [1.0, 1.0]}, index=D2)
        out = run(weights, returns, 0.0)
        self.assertAlmostEqual(out["ret"].iloc[0], 0.0)
        self.assertAlmostEqual(out["ret"].iloc[1], -0.05)

    def test_p2_cost(self):
        returns = pd.DataFrame({"A": [0.10, -0.05]}, index=D2)
        weights = pd.DataFrame({"A": [1.0, 1.0]}, index=D2)
        out = run(weights, returns, 0.001)
        self.assertAlmostEqual(out["turnover"].iloc[0], 1.0)
        self.assertAlmostEqual(out["ret"].iloc[0], -0.001)

    def test_p3_drift_no_trade(self):
        returns = pd.DataFrame({"A": [0.0, 0.10], "B": [0.0, 0.0]}, index=D2)
        weights = pd.DataFrame({"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=D2)
        out = run(weights, returns, 0.0)
        self.assertAlmostEqual(out["turnover"].iloc[1], 0.0)
        self.assertAlmostEqual(out["w_A"].iloc[1], 0.55 / 1.05)

    def test_p4_trade_only_on_target_change(self):
        returns = pd.DataFrame({"A": [0.0, 0.10, 0.0], "B": [0.0, 0.0, 0.0]}, index=D3)
        weights = pd.DataFrame({"A": [0.5, 0.5, 1.0], "B": [0.5, 0.5, 0.0]}, index=D3)
        out = run(weights, returns, 0.0)
        self.assertAlmostEqual(out["turnover"].iloc[1], 0.0)
        w_a, w_b = out["w_A"].iloc[1], out["w_B"].iloc[1]
        rg = w_a * 0.0 + w_b * 0.0
        wd_a = w_a * 1.0 / (1 + rg)
        wd_b = w_b * 1.0 / (1 + rg)
        self.assertAlmostEqual(out["turnover"].iloc[2], abs(1 - wd_a) + abs(0 - wd_b))

    def test_p5_no_lookahead(self):
        returns = pd.DataFrame({"A": [0.0, 0.10, -0.05], "B": [0.0, 0.0, 0.02]}, index=D3)
        w1 = pd.DataFrame({"A": [0.5, 0.5, 0.5], "B": [0.5, 0.5, 0.5]}, index=D3)
        w2 = pd.DataFrame({"A": [0.5, 0.5, 1.0], "B": [0.5, 0.5, 0.0]}, index=D3)
        cost = 0.001
        o1 = run(w1, returns, cost)
        o2 = run(w2, returns, cost)
        for t in range(2):
            self.assertAlmostEqual(o1["ret"].iloc[t], o2["ret"].iloc[t])
        g1 = (1 + o1["ret"].iloc[2]) / (1 - o1["turnover"].iloc[2] * cost) - 1
        g2 = (1 + o2["ret"].iloc[2]) / (1 - o2["turnover"].iloc[2] * cost) - 1
        self.assertAlmostEqual(g1, g2)

    def test_p6_cash(self):
        returns = pd.DataFrame({"A": [0.0, 0.10]}, index=D2)
        weights = pd.DataFrame({"A": [0.6, 0.6]}, index=D2)
        out = run(weights, returns, 0.0)
        self.assertAlmostEqual(out["ret"].iloc[1], 0.06)

    def test_p7_validation_errors(self):
        returns = pd.DataFrame({"A": [0.0, 0.10]}, index=D2)
        good = pd.DataFrame({"A": [1.0, 1.0]}, index=D2)
        bad_idx = pd.DataFrame({"A": [1.0, 1.0]}, index=["20240102", "20240104"])
        with self.assertRaises(ValueError):
            run(bad_idx, returns, 0.0)
        with self.assertRaises(ValueError):
            run(pd.DataFrame({"A": [1.2, 1.0]}, index=D2), returns, 0.0)
        with self.assertRaises(ValueError):
            run(pd.DataFrame({"A": [-0.1, 1.0]}, index=D2), returns, 0.0)
        with self.assertRaises(ValueError):
            run(pd.DataFrame({"A": [float("nan"), 1.0]}, index=D2), returns, 0.0)
        held_nan = pd.DataFrame({"A": [0.0, float("nan")]}, index=D2)
        with self.assertRaises(ValueError):
            run(good, held_nan, 0.0)

    def test_p7_unheld_nan_ok(self):
        returns = pd.DataFrame({"A": [0.0, 0.10], "B": [float("nan")] * 2}, index=D2)
        weights = pd.DataFrame({"A": [1.0, 1.0], "B": [0.0, 0.0]}, index=D2)
        out = run(weights, returns, 0.0)
        self.assertAlmostEqual(out["ret"].iloc[1], 0.10)


    def test_p8_rebalance_every_periodic(self):
        returns = pd.DataFrame({"A": [0.10, 0.10, 0.10], "B": [0.0, 0.0, 0.0]}, index=D3)
        weights = pd.DataFrame({"A": [0.5, 0.5, 0.5], "B": [0.5, 0.5, 0.5]}, index=D3)
        out = run(weights, returns, 0.0, rebalance_every=2)
        self.assertAlmostEqual(out["turnover"].iloc[0], 1.0)
        self.assertAlmostEqual(out["turnover"].iloc[1], 0.0)
        self.assertGreater(out["turnover"].iloc[2], 0.0)
        self.assertAlmostEqual(out["w_A"].iloc[2], 0.5)
        out_none = run(weights, returns, 0.0)
        self.assertAlmostEqual(out_none["turnover"].iloc[1], 0.0)
        self.assertAlmostEqual(out_none["turnover"].iloc[2], 0.0)

    def test_p9_rebalance_every_validation(self):
        returns = pd.DataFrame({"A": [0.10, -0.05]}, index=D2)
        weights = pd.DataFrame({"A": [1.0, 1.0]}, index=D2)
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                run(weights, returns, 0.0, rebalance_every=bad)

    def test_p10_default_equals_none(self):
        returns = pd.DataFrame({"A": [0.0, 0.10], "B": [0.0, 0.0]}, index=D2)
        weights = pd.DataFrame({"A": [0.5, 0.5], "B": [0.5, 0.5]}, index=D2)
        pd.testing.assert_frame_equal(run(weights, returns, 0.001),
                                      run(weights, returns, 0.001, None))


if __name__ == "__main__":
    unittest.main()
