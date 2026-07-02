"""투자노트 손익계산(notes/pnl.py) 단위 테스트.

    .venv/Scripts/python.exe -m unittest test_notes_pnl
"""
from __future__ import annotations

import os
import unittest

from notes import pnl
from notes.models import EventType, NoteEvent


def _ev(id_, event_type, price, qty, executed_at="2026-07-01T00:00:00+00:00"):
    return NoteEvent(
        id=id_,
        note_uid="u",
        event_type=event_type,
        price=price,
        qty=qty,
        executed_at=executed_at,
        created_at=executed_at,
    )


class _NoFee(unittest.TestCase):
    """수수료율 env 미설정(기본 0) 상태에서의 gross 계산."""

    def setUp(self):
        for k in ("NOTE_BUY_FEE_RATE", "NOTE_SELL_FEE_RATE", "NOTE_SELL_TAX_RATE"):
            os.environ.pop(k, None)

    def test_open_position_unrealized(self):
        events = [_ev(1, EventType.buy, 10000, 10)]
        result = pnl.compute(events, current_price=11000)
        self.assertEqual(result.remaining_qty, 10)
        self.assertEqual(result.invested_amt, 100000)
        self.assertEqual(result.recovered_amt, 110000)
        self.assertEqual(result.net_pnl, 10000)
        self.assertEqual(result.net_pnl_pct, 10.0)
        self.assertFalse(result.fee_applied)
        self.assertFalse(result.needs_price)

    def test_open_position_missing_price_flags_needs_price(self):
        events = [_ev(1, EventType.buy, 10000, 10)]
        result = pnl.compute(events, current_price=None)
        self.assertTrue(result.needs_price)
        # 평가금액 못 구해도 계산은 죽지 않는다(0 취급).
        self.assertEqual(result.recovered_amt, 0)

    def test_fully_closed_realized_only(self):
        events = [
            _ev(1, EventType.buy, 10000, 10, "2026-07-01T00:00:00+00:00"),
            _ev(2, EventType.sell, 12000, 10, "2026-07-02T00:00:00+00:00"),
        ]
        result = pnl.compute(events, current_price=99999)  # 다 팔았으니 무시돼야
        self.assertEqual(result.remaining_qty, 0)
        self.assertIsNone(result.avg_cost)
        self.assertEqual(result.invested_amt, 100000)
        self.assertEqual(result.recovered_amt, 120000)
        self.assertEqual(result.net_pnl, 20000)
        self.assertFalse(result.needs_price)

    def test_partial_sell_combines_realized_and_unrealized(self):
        events = [
            _ev(1, EventType.buy, 10000, 10, "2026-07-01T00:00:00+00:00"),
            _ev(2, EventType.partial_sell, 12000, 4, "2026-07-02T00:00:00+00:00"),
        ]
        result = pnl.compute(events, current_price=10000)
        self.assertEqual(result.remaining_qty, 6)
        self.assertEqual(result.avg_cost, 10000.0)
        # 매수원가 100000, 매도금액(4주*12000=48000) + 평가금액(6주*10000=60000) = 108000
        self.assertEqual(result.invested_amt, 100000)
        self.assertEqual(result.recovered_amt, 108000)
        self.assertEqual(result.net_pnl, 8000)

    def test_weighted_average_cost_across_add_buys(self):
        events = [
            _ev(1, EventType.buy, 10000, 10, "2026-07-01T00:00:00+00:00"),
            _ev(2, EventType.add_buy, 12000, 10, "2026-07-02T00:00:00+00:00"),
        ]
        result = pnl.compute(events, current_price=11000)
        self.assertEqual(result.remaining_qty, 20)
        self.assertEqual(result.avg_cost, 11000.0)  # (100000+120000)/20
        self.assertEqual(result.net_pnl, 0)  # 현재가==평단가


class _WithFee(unittest.TestCase):
    def setUp(self):
        os.environ["NOTE_BUY_FEE_RATE"] = "0.001"   # 0.1%
        os.environ["NOTE_SELL_FEE_RATE"] = "0.001"  # 0.1%
        os.environ["NOTE_SELL_TAX_RATE"] = "0.002"  # 0.2%

    def tearDown(self):
        for k in ("NOTE_BUY_FEE_RATE", "NOTE_SELL_FEE_RATE", "NOTE_SELL_TAX_RATE"):
            os.environ.pop(k, None)

    def test_fees_reduce_net_pnl(self):
        events = [
            _ev(1, EventType.buy, 10000, 10, "2026-07-01T00:00:00+00:00"),
            _ev(2, EventType.sell, 12000, 10, "2026-07-02T00:00:00+00:00"),
        ]
        result = pnl.compute(events, current_price=None)
        # invested = 100000 + 100*0.001*1000(buy_fee=100) = 100100
        self.assertEqual(result.invested_amt, 100100)
        # sold=120000, fee+tax = 120000*0.003=360 -> recovered=119640
        self.assertEqual(result.recovered_amt, 119640)
        self.assertEqual(result.net_pnl, 19540)
        self.assertTrue(result.fee_applied)


if __name__ == "__main__":
    unittest.main()
