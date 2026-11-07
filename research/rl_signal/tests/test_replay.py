"""replay 테스트 (합성 호가만)."""

from __future__ import annotations

import unittest

import numpy as np

from research.rl_signal import oracle
from research.rl_signal.oracle import BUY, HOLD, SELL
from research.rl_signal.replay import placebo_random, placebo_timing, policy_run, run

ASK = np.array([100.0, 101.0, 102.0, 101.0, 100.0])
BID = ASK - 1.0
CODE = "000000"


class TestReplay(unittest.TestCase):
    def test_oracle_match(self):
        sol = oracle.solve(ASK, BID, CODE)
        tot, buys, act = run(sol["action"], ASK, BID, CODE)
        self.assertAlmostEqual(tot, sol["total"], places=9)
        self.assertEqual(buys, sol["trades"])
        np.testing.assert_array_equal(act, sol["action"])

    def test_infeasible_hold(self):
        tot, buys, act = run(np.full(5, BUY), ASK, BID, CODE)
        np.testing.assert_array_equal(act, [BUY, HOLD, HOLD, HOLD, SELL])
        self.assertEqual(buys, 1)
        tot0, buys0, act0 = run(np.full(5, SELL), ASK, BID, CODE)
        np.testing.assert_array_equal(act0, np.zeros(5, dtype=int))
        self.assertEqual(tot0, 0.0)
        self.assertEqual(buys0, 0)

    def test_last_bin_flat(self):
        _, _, act = run([BUY, HOLD, HOLD, HOLD, HOLD], ASK, BID, CODE)
        self.assertEqual(act[-1], SELL)
        pos = 0
        for a in act:
            if a == BUY:
                pos = 1
            elif a == SELL:
                pos = 0
        self.assertEqual(pos, 0)

    def test_kmax(self):
        _, buys, _ = run([BUY, SELL, BUY, SELL, HOLD], ASK, BID, CODE, k_max=1)
        self.assertEqual(buys, 1)

    def test_policy_run(self):
        tot, buys, act = policy_run(lambda t, pos, k: BUY, ASK, BID, CODE)
        np.testing.assert_array_equal(act, [BUY, HOLD, HOLD, HOLD, SELL])
        self.assertEqual(buys, 1)

    def test_placebo_timing_matches_run(self):
        rng = np.random.default_rng(0)
        out = placebo_timing(2, ASK, BID, CODE, 1, rng)
        picks = np.sort(np.random.default_rng(0).choice(4, size=4, replace=False))
        actions = np.zeros(5, dtype=int)
        actions[picks[0]], actions[picks[1]] = BUY, SELL
        actions[picks[2]], actions[picks[3]] = BUY, SELL
        tot, _, _ = run(actions, ASK, BID, CODE)
        self.assertAlmostEqual(out[0], tot, places=9)
        self.assertTrue((placebo_timing(0, ASK, BID, CODE, 3, rng) == 0).all())

    def test_placebo_random_len(self):
        out = placebo_random(ASK, BID, CODE, 7, np.random.default_rng(1))
        self.assertEqual(len(out), 7)
        self.assertTrue(np.isfinite(out).all())


if __name__ == "__main__":
    unittest.main()
