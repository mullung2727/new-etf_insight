"""oracle 테스트 (합성 가격열만)."""

from __future__ import annotations

import math
import unittest

import numpy as np

from research.rl_signal.costs import FEE, UNIT_KRW, sell_cost, tick
from research.rl_signal.oracle import BUY, HOLD, K_MAX, SELL, solve, step_rewards

SAW = [100.0 if t % 2 == 0 else 102.0 for t in range(20)]


def _solve(bids, code="069500", spread=0.01, qty=20000, **kw):
    bid = np.array(bids, dtype=np.float64)
    ask = bid + spread
    aq = np.full(len(bid), qty, dtype=np.int64)
    bq = np.full(len(bid), qty, dtype=np.int64)
    return solve(ask, bid, aq, bq, code, **kw), bid


def _rewards(bids, code="069500", spread=0.01, qty=20000, **kw):
    bid = np.array(bids, dtype=np.float64)
    ask = bid + spread
    aq = np.full(len(bid), qty, dtype=np.int64)
    bq = np.full(len(bid), qty, dtype=np.int64)
    return step_rewards(ask, bid, aq, bq, code, **kw)


class TestOracle(unittest.TestCase):
    def test_rising(self):
        res, _ = _solve([100, 101, 102, 103, 104])
        self.assertEqual(int(res["action"][0]), BUY)
        self.assertEqual(int(res["action"][-1]), SELL)
        self.assertEqual(res["trades"], 1)
        expected = math.log(104 / 100.01) - math.log(1 + FEE) + math.log(1 - FEE)
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
        expected = math.log(102 / 100.01) + math.log(1 - FEE) - math.log(1 + FEE)
        res, _ = _solve(SAW)
        self.assertEqual(res["trades"], 5)
        self.assertAlmostEqual(res["total"], 5 * expected, places=9)
        res1, _ = _solve(SAW, k_max=1)
        self.assertEqual(res1["trades"], 1)
        self.assertAlmostEqual(res1["total"], expected, places=9)
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
        aq = np.full(len(bid), 20000, dtype=np.int64)
        bq = np.full(len(bid), 20000, dtype=np.int64)
        hold_r, buy_r, sell_r, _, _ = step_rewards(bid + 0.01, bid, aq, bq, "069500")
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


class TestExecFeasibility(unittest.TestCase):
    def test_no_exec_cell(self):
        ask = np.array([100.01, 0.0, 102.01])
        bid = np.array([100.0, 0.0, 102.0])
        aq = np.array([20000, 0, 20000], dtype=np.int64)
        bq = np.array([20000, 0, 20000], dtype=np.int64)
        _, _, _, can_buy, can_sell = step_rewards(ask, bid, aq, bq, "069500")
        self.assertTrue(can_buy[0] and can_sell[0])
        self.assertFalse(can_buy[1])
        self.assertFalse(can_sell[1])
        self.assertTrue(can_buy[2] and can_sell[2])

    def test_limit_up_no_buy(self):
        bid = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        ask = bid + 0.01
        ask[0] = 0.0
        aq = np.full(5, 20000, dtype=np.int64)
        bq = np.full(5, 20000, dtype=np.int64)
        _, _, _, can_buy, _ = step_rewards(ask, bid, aq, bq, "069500")
        self.assertFalse(can_buy[0])
        res = solve(ask, bid, aq, bq, "069500")
        self.assertNotEqual(int(res["action"][0]), BUY)

    def test_limit_down_hold(self):
        bid = np.array([100.0, 101.0, 0.0, 103.0, 104.0])
        ask = np.array([100.01, 101.01, 102.01, 103.01, 104.01])
        aq = np.full(5, 20000, dtype=np.int64)
        bq = np.array([20000, 20000, 0, 20000, 20000], dtype=np.int64)
        _, _, _, _, can_sell = step_rewards(ask, bid, aq, bq, "069500")
        self.assertFalse(can_sell[2])
        res = solve(ask, bid, aq, bq, "069500")
        self.assertEqual(int(res["action"][0]), BUY)
        self.assertEqual(int(res["action"][2]), HOLD)

    def test_locked_close(self):
        ask = np.array([100.01, 101.0])
        bid = np.array([100.0, 0.0])
        aq = np.array([20000, 20000], dtype=np.int64)
        bq = np.array([20000, 0], dtype=np.int64)
        res = solve(ask, bid, aq, bq, "069500")
        self.assertEqual(int(res["action"][-1]), SELL)
        expected = (math.log(100.0 / 100.01) - math.log(1 + FEE)
                    + math.log(101.0 / 100.0) + math.log(1 - FEE))
        self.assertAlmostEqual(res["total"], expected, places=9)

    def test_qty_insufficient(self):
        ask = np.array([100.0])
        bid = np.array([99.99])
        need = math.ceil(UNIT_KRW / 100.0)
        aq = np.array([need - 1], dtype=np.int64)
        bq = np.array([20000], dtype=np.int64)
        _, _, _, can_buy, can_sell = step_rewards(ask, bid, aq, bq, "069500")
        self.assertFalse(can_buy[0])
        self.assertTrue(can_sell[0])

    def test_slip(self):
        bids = [100, 101, 102, 103, 104]
        res0, _ = _solve(bids, slip=0)
        res1, _ = _solve(bids, slip=1)
        self.assertLess(res1["total"], res0["total"])
        bid = np.array(bids, dtype=np.float64)
        ask = bid + 0.01
        aq = np.full(5, 20000, dtype=np.int64)
        bq = np.full(5, 20000, dtype=np.int64)
        h0, b0, s0, _, _ = step_rewards(ask, bid, aq, bq, "069500", slip=0)
        h1, b1, s1, _, _ = step_rewards(ask, bid, aq, bq, "069500", slip=1)
        t = tick("069500", ask[0])
        exp_buy = math.log(bid[0] / (ask[0] + t)) - math.log(1 + FEE)
        exp_sell = math.log((bid[0] - tick("069500", bid[0])) / bid[0]) + math.log(1 - FEE)
        self.assertAlmostEqual(b1[0], exp_buy, places=9)
        self.assertAlmostEqual(s1[0], exp_sell, places=9)
        self.assertLess(b1[0], b0[0])
        self.assertLess(s1[0], s0[0])


class TestSellTickBelow(unittest.TestCase):
    def test_stock_boundary(self):
        log_sc = math.log(1 - sell_cost("005930"))
        for bid_px, want in ((50000.0, 49950.0), (50100.0, 50000.0)):
            ask = np.array([bid_px + 100.0])
            bid = np.array([bid_px])
            aq = np.array([20000], dtype=np.int64)
            bq = np.array([20000], dtype=np.int64)
            _, _, sell_r, _, can_sell = step_rewards(ask, bid, aq, bq, "005930", slip=1)
            self.assertTrue(can_sell[0])
            self.assertAlmostEqual(bid_px * math.exp(sell_r[0] - log_sc), want, places=6)


class TestMark0(unittest.TestCase):
    def test_first_missing_uses_mark0(self):
        ask = np.array([0.0, 102.0])
        bid = np.array([0.0, 101.0])
        aq = np.zeros(2, dtype=np.int64)
        bq = np.zeros(2, dtype=np.int64)
        hold_r, _, _, can_buy, can_sell = step_rewards(ask, bid, aq, bq, "069500", mark0=100.0)
        self.assertAlmostEqual(hold_r[0], math.log(101.0 / 100.0), places=9)
        self.assertFalse(can_buy[0])
        self.assertFalse(can_sell[0])
        res = solve(ask, bid, aq, bq, "069500", mark0=100.0)
        self.assertTrue(math.isfinite(res["total"]))

    def test_default_zero(self):
        ask = np.array([0.0, 102.0])
        bid = np.array([0.0, 101.0])
        aq = np.zeros(2, dtype=np.int64)
        bq = np.zeros(2, dtype=np.int64)
        hold_r, _, _, _, _ = step_rewards(ask, bid, aq, bq, "069500")
        self.assertEqual(hold_r[0], 0.0)


if __name__ == "__main__":
    unittest.main()
