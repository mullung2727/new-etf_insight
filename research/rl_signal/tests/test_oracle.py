"""oracle 테스트 (합성 가격열만)."""

from __future__ import annotations

import math
import unittest

import numpy as np

from research.rl_signal.costs import FEE
from research.rl_signal.oracle import BUY, HOLD, K_MAX, SELL, solve, step_rewards

SAW = [100.0 if t % 2 == 0 else 102.0 for t in range(20)]


def _solve(bids, code="069500", spread=0.01, **kw):
    bid = np.array(bids, dtype=np.float64)
    return solve(bid + spread, bid, code, **kw), bid


class TestOracle(unittest.TestCase):
    def test_rising(self):
        res, _ = _solve([100, 101, 102, 103, 104])
        self.assertEqual(int(res["action"][0]), BUY)
        self.assertEqual(int(res["action"][-1]), SELL)
        self.assertEqual(res["trades"], 1)
        expected = math.log(104 / 100.01) + math.log(1 - FEE) + math.log(1 - FEE)
        self.assertAlmostEqual(res["total"], expected, places=9)

    def test_falling(self):
        res, _ = _solve([104, 103, 102, 101, 100])
        self.assertTrue((res["action"] == HOLD).all())
        self.assertEqual(res["total"], 0.0)
        self.assertEqual(res["trades"], 0)

    def test_v_shape(self):
        res, _ = _solve([100, 98, 96, 98, 100])
        self.assertEqual(int(res["action"][2]), BUY)
        self.assertEqual(int(res["action"][4]), SELL)
        self.assertEqual(res["trades"], 1)

    def test_cost_dominates(self):
        res, _ = _solve([100, 100.1, 100, 100.1, 100], code="005930")
        self.assertTrue((res["action"] == HOLD).all())
        self.assertEqual(res["trades"], 0)

    def test_trade_cap(self):
        res, _ = _solve(SAW)
        self.assertLessEqual(res["trades"], 5)
        res1, _ = _solve(SAW, k_max=1)
        self.assertLessEqual(res1["trades"], 1)
        self.assertEqual(res["q"].shape, (20, 2, K_MAX + 1, 3))

    def test_last_cell_flat(self):
        for bids in ([100, 101, 102, 103, 104], [104, 103, 102, 101, 100],
                     [100, 98, 96, 98, 100], SAW):
            res, _ = _solve(bids)
            pos = 0
            for a in res["action"]:
                if a == BUY:
                    pos = 1
                elif a == SELL:
                    pos = 0
            self.assertEqual(pos, 0, bids)
            self.assertNotEqual(int(res["action"][-1]), BUY, bids)

    def test_impossible_actions(self):
        res, _ = _solve([100, 101, 102, 103, 104])
        q = res["q"]
        self.assertTrue(np.isneginf(q[:, 0, :, SELL]).all())
        self.assertTrue(np.isneginf(q[:, 1, :, BUY]).all())
        self.assertTrue(np.isneginf(q[-1, 0, :, BUY]).all())
        self.assertTrue(np.isneginf(q[-1, 1, :, HOLD]).all())

    def test_q_consistency(self):
        res, bid = _solve([100, 98, 96, 98, 100])
        self.assertEqual(res["total"], float(res["q"][0, 0, K_MAX].max()))
        hold_r, buy_r, sell_r = step_rewards(bid + 0.01, bid, "069500")
        s, pos = 0.0, 0
        for t, a in enumerate(res["action"]):
            if a == BUY:
                s += buy_r[t]
                pos = 1
            elif a == SELL:
                s += sell_r[t]
                pos = 0
            if t < len(bid) - 1 and pos == 1:
                s += hold_r[t]
        self.assertAlmostEqual(s, res["total"], places=9)


if __name__ == "__main__":
    unittest.main()
