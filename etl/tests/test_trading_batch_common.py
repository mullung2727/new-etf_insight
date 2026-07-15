"""두 자동매매 배치가 공유하는 broker 주문 인프라 테스트."""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from scripts.trading_batch_common import (
    available_cash,
    current_price,
    in_order_window,
    market_order,
    quote_snapshot,
    quantity_for_budget,
)


class TradingBatchCommonTest(unittest.TestCase):
    def test_order_window_includes_start_and_excludes_deadline(self):
        self.assertTrue(in_order_window(datetime(2026, 7, 15, 15, 19), "15:19:00", "15:20:00"))
        self.assertFalse(in_order_window(datetime(2026, 7, 15, 15, 20), "15:19:00", "15:20:00"))

    @patch("scripts.trading_batch_common.requests.get")
    def test_quote_and_cash_are_normalized(self, get: Mock):
        responses = [Mock(json=lambda: {"price": "001230"}), Mock(json=lambda: {"ord_alow_amt": "0300000"})]
        for response in responses:
            response.raise_for_status = Mock()
        get.side_effect = responses
        self.assertEqual(current_price("http://broker", "005930"), 1230)
        self.assertEqual(available_cash("http://broker"), 300000)

    @patch("scripts.trading_batch_common.requests.get")
    def test_quote_snapshot_normalizes_intraday_ohlc(self, get: Mock):
        response = Mock(json=lambda: {"price": 101, "raw": {"open_pric": "-99", "low_pric": "-98"}})
        response.raise_for_status = Mock()
        get.return_value = response
        self.assertEqual(quote_snapshot("http://broker", "005930"),
                         {"current_price": 101, "open": 99, "low": 98})

    def test_dry_run_never_posts(self):
        with patch("scripts.trading_batch_common.requests.post") as post:
            result = market_order("http://broker", "005930", 2, "buy", "pullback_order", True,
                                  now=datetime(2026, 7, 15, 15, 19))
        post.assert_not_called()
        self.assertEqual(result["status"], "dry_run")

    @patch("scripts.trading_batch_common.requests.post")
    def test_market_order_passes_strategy_source(self, post: Mock):
        response = Mock(json=lambda: {"accepted": True, "order_no": "001", "message": "ok"})
        response.raise_for_status = Mock()
        post.return_value = response
        result = market_order("http://broker", "005930", 2, "buy", "pullback_order", False)
        self.assertEqual(post.call_args.kwargs["json"]["source"], "pullback_order")
        self.assertEqual(result["status"], "submitted")

    @patch("scripts.trading_batch_common.requests.post")
    def test_market_order_distinguishes_broker_rejection(self, post: Mock):
        response = Mock(status_code=422, json=lambda: {"detail": "금액 상한 초과"})
        post.return_value = response
        result = market_order("http://broker", "005930", 2, "buy", "pullback_order", False)
        self.assertEqual(result["status"], "rejected")

    def test_quantity_for_budget(self):
        self.assertEqual(quantity_for_budget(300000, 10000), 30)
        self.assertEqual(quantity_for_budget(300000, 400000), 0)


if __name__ == "__main__":
    unittest.main()
