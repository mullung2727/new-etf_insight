"""run_close_bet_force_exit.py (백스톱) 단위테스트.

검증:
  1. select_residual: 잔고0 무동작 / 미체결 제외 / 순수 잔존만
  2. 멱등: 잔존 없으면 execute_sell 미호출
"""
import unittest
from unittest.mock import patch

from scripts import run_close_bet_force_exit as bs
from scripts.run_close_bet_force_exit import select_residual


def _bal(*pairs):
    return {"acnt_evlt_remn_indv_tot": [
        {"stk_cd": f"A{t}", "trde_able_qty": str(q).zfill(15)} for t, q in pairs]}


class TestSelectResidual(unittest.TestCase):
    def test_empty_balance_no_residual(self):
        pos = [{"ticker": "005930", "cntr_price": 1000, "qty": 5}]
        self.assertEqual(select_residual(pos, {}, set()), [])

    def test_excludes_unfilled(self):
        pos = [{"ticker": "005930", "cntr_price": 1000, "qty": 5}]
        out = select_residual(pos, _bal(("005930", 5)), {"005930"})  # 미체결 큐에 있음
        self.assertEqual(out, [])

    def test_pure_residual_kept(self):
        pos = [
            {"ticker": "005930", "cntr_price": 1000, "qty": 5},  # 잔존
            {"ticker": "000660", "cntr_price": 2000, "qty": 3},  # 미체결 → 제외
            {"ticker": "035720", "cntr_price": 3000, "qty": 2},  # 잔고 미보유 → 제외
        ]
        balance = _bal(("005930", 5), ("000660", 3))
        out = select_residual(pos, balance, {"000660"})
        self.assertEqual([w["ticker"] for w in out], ["005930"])
        self.assertEqual(out[0]["qty_eff"], 5)


class TestBackstopMainIdempotent(unittest.TestCase):
    def _run(self, residual):
        with patch.object(bs, "load_unsold_positions", return_value=[{"ticker": "X"}]), \
             patch.object(bs, "fetch_balance", return_value={}), \
             patch.object(bs, "fetch_unfilled_tickers", return_value=set()), \
             patch.object(bs, "connect_rw"), \
             patch.object(bs, "send_discord"), \
             patch.object(bs, "select_residual", return_value=residual), \
             patch.object(bs, "execute_sell",
                          return_value={"order_no": "0000070", "status": "submitted", "qty": 1}) as sell, \
             patch("sys.argv", ["x", "--dry-run", "false"]):
            bs.main()
        return sell

    def test_no_residual_no_sell(self):
        sell = self._run([])
        sell.assert_not_called()

    def test_residual_triggers_sell(self):
        sell = self._run([{"ticker": "005930", "qty": 5, "qty_eff": 5,
                           "cntr_price": 1000, "date": "20260617"}])
        sell.assert_called_once()


if __name__ == "__main__":
    unittest.main()
