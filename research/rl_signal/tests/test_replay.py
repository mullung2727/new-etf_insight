"""replay 테스트 (합성 호가만)."""

from __future__ import annotations

import math
import unittest

import numpy as np

from research.rl_signal import oracle

from research.rl_signal.oracle import BUY, HOLD, SELL
from research.rl_signal.replay import placebo_random, placebo_shift, placebo_timing, policy_run, run

ASK = np.array([100.0, 101.0, 102.0, 101.0, 100.0])
BID = ASK - 1.0
AQ = np.full(5, 20000, dtype=np.int64)
BQ = np.full(5, 20000, dtype=np.int64)
CODE = "000000"


class TestReplay(unittest.TestCase):
    def test_oracle_match(self):
        sol = oracle.solve(ASK, BID, AQ, BQ, CODE)
        tot, buys, act = run(sol["action"], ASK, BID, AQ, BQ, CODE)
        self.assertAlmostEqual(tot, sol["total"], places=9)
        self.assertEqual(buys, sol["trades"])
        np.testing.assert_array_equal(act, sol["action"])

    def test_oracle_match_mark0_first_cell_missing(self):
        ask = ASK.copy()
        bid = BID.copy()
        ask[0], bid[0] = 0.0, 0.0
        aq = AQ.copy()
        bq = BQ.copy()
        aq[0], bq[0] = 0, 0
        m = 99.5
        sol = oracle.solve(ask, bid, aq, bq, CODE, mark0=m)
        tot, _, _ = run(sol["action"], ask, bid, aq, bq, CODE, mark0=m)
        self.assertAlmostEqual(tot, sol["total"], places=9)
        self.assertTrue(math.isfinite(tot))
        self.assertTrue(math.isfinite(sol["total"]))

    def test_infeasible_hold(self):
        tot, buys, act = run(np.full(5, BUY), ASK, BID, AQ, BQ, CODE)
        np.testing.assert_array_equal(act, [BUY, HOLD, HOLD, HOLD, SELL])
        self.assertEqual(buys, 1)
        tot0, buys0, act0 = run(np.full(5, SELL), ASK, BID, AQ, BQ, CODE)
        np.testing.assert_array_equal(act0, np.zeros(5, dtype=int))
        self.assertEqual(tot0, 0.0)
        self.assertEqual(buys0, 0)

    def test_last_bin_flat(self):
        _, _, act = run([BUY, HOLD, HOLD, HOLD, HOLD], ASK, BID, AQ, BQ, CODE)
        self.assertEqual(act[-1], SELL)
        pos = 0
        for a in act:
            if a == BUY:
                pos = 1
            elif a == SELL:
                pos = 0
        self.assertEqual(pos, 0)

    def test_kmax(self):
        _, buys, _ = run([BUY, SELL, BUY, SELL, HOLD], ASK, BID, AQ, BQ, CODE, k_max=1)
        self.assertEqual(buys, 1)

    def test_policy_run(self):
        tot, buys, act = policy_run(lambda t, pos, k: BUY, ASK, BID, AQ, BQ, CODE)
        np.testing.assert_array_equal(act, [BUY, HOLD, HOLD, HOLD, SELL])
        self.assertEqual(buys, 1)

    def test_placebo_timing_matches_run(self):
        rng = np.random.default_rng(0)
        out = placebo_timing(2, ASK, BID, AQ, BQ, CODE, 1, rng)
        picks = np.sort(np.random.default_rng(0).choice(4, size=4, replace=False))
        actions = np.zeros(5, dtype=int)
        actions[picks[0]], actions[picks[1]] = BUY, SELL
        actions[picks[2]], actions[picks[3]] = BUY, SELL
        tot, _, _ = run(actions, ASK, BID, AQ, BQ, CODE)
        self.assertAlmostEqual(out[0], tot, places=9)
        self.assertTrue((placebo_timing(0, ASK, BID, AQ, BQ, CODE, 3, rng) == 0).all())

    def test_placebo_random_len(self):
        out = placebo_random(ASK, BID, AQ, BQ, CODE, 7, np.random.default_rng(1))
        self.assertEqual(len(out), 7)
        self.assertTrue(np.isfinite(out).all())

    def test_exec_infeasible_hold(self):
        ask = ASK.copy()
        ask[1] = 0.0
        _, _, act = run([HOLD, BUY, HOLD, HOLD, HOLD], ask, BID, AQ, BQ, CODE)
        self.assertEqual(int(act[1]), HOLD)
        bid = BID.copy()
        bid[2] = 0.0
        _, _, act2 = run([BUY, HOLD, SELL, HOLD, HOLD], ASK, bid, AQ, BQ, CODE)
        self.assertEqual(int(act2[2]), HOLD)

    def test_locked_close(self):
        bid = BID.copy()
        bid[-1] = 0.0
        tot, _, act = run([BUY, HOLD, HOLD, HOLD, HOLD], ASK, bid, AQ, BQ, CODE)
        self.assertEqual(int(act[-1]), SELL)
        self.assertTrue(math.isfinite(tot))


class TestPlaceboShift(unittest.TestCase):
    def _geo(self, n=5, g=1.01):
        v = 100.0 * g ** np.arange(n)
        aq = np.full(n, 20000, dtype=np.int64)
        return v, v.copy(), aq, aq.copy()

    def test_preserves_intervals(self):
        ask, bid, aq, bq = self._geo()
        actions = np.array([BUY, SELL, BUY, SELL, HOLD])
        tot, _, _ = run(actions, ask, bid, aq, bq, CODE)
        out = placebo_shift(actions, ask, bid, aq, bq, CODE, 30, np.random.default_rng(0))
        self.assertEqual(out.shape, (30,))
        self.assertTrue(np.isfinite(out).all())
        for v in out:
            self.assertAlmostEqual(v, tot, places=6)

    def test_no_intervals_zero(self):
        out = placebo_shift(np.zeros(5, dtype=int), ASK, BID, AQ, BQ, CODE, 4,
                            np.random.default_rng(0))
        np.testing.assert_array_equal(out, np.zeros(4))


if __name__ == "__main__":
    unittest.main()
