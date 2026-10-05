"""Stage 2 & 3: broker REST API 경유 함수 테스트.

fetch_price_via_broker  — GET /quotes/{symbol}
place_order_via_broker  — POST /orders

BrokerClient (scripts/broker_client.py) 공용 클라이언트 테스트 — requests를
mock.patch 하며 실제 네트워크를 쓰지 않는다.
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import requests

from scripts.broker_client import BrokerClient, fills_by_order, quote_fresh
from scripts.run_close_bet import (
    fetch_price_via_broker,
    place_order_via_broker,
)

_BROKER = "http://localhost:8001"


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data
    m.raise_for_status.side_effect = None if status_code < 400 else Exception(f"HTTP {status_code}")
    return m


# ── Stage 2: fetch_price_via_broker ──────────────────────────────────────────

class TestFetchPriceViaBroker(unittest.TestCase):
    def test_returns_price_on_success(self):
        with patch("scripts.trading_batch_common.requests.get",
                   return_value=_mock_response({"symbol": "005930", "price": 75000, "raw": {}})):
            result = fetch_price_via_broker(_BROKER, "005930")
        self.assertEqual(result, 75000)

    def test_returns_none_when_price_is_null(self):
        with patch("scripts.trading_batch_common.requests.get",
                   return_value=_mock_response({"symbol": "005930", "price": None, "raw": {}})):
            result = fetch_price_via_broker(_BROKER, "005930")
        self.assertIsNone(result)

    def test_returns_none_on_http_error(self):
        mock = _mock_response({}, status_code=500)
        mock.raise_for_status.side_effect = Exception("HTTP 500")
        with patch("scripts.trading_batch_common.requests.get", return_value=mock):
            result = fetch_price_via_broker(_BROKER, "005930")
        self.assertIsNone(result)

    def test_returns_none_on_connection_error(self):
        with patch("scripts.trading_batch_common.requests.get", side_effect=ConnectionError("refused")):
            result = fetch_price_via_broker(_BROKER, "005930")
        self.assertIsNone(result)

    def test_calls_correct_url(self):
        with patch("scripts.trading_batch_common.requests.get",
                   return_value=_mock_response({"symbol": "000660", "price": 12000, "raw": {}})) as mock_get:
            fetch_price_via_broker(_BROKER, "000660")
        mock_get.assert_called_once_with(
            f"{_BROKER}/quotes/000660",
            timeout=unittest.mock.ANY,
        )


# ── Stage 3: place_order_via_broker ──────────────────────────────────────────

class TestPlaceOrderViaBroker(unittest.TestCase):
    def test_dry_run_returns_without_http_call(self):
        with patch("scripts.trading_batch_common.requests.post") as mock_post:
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=True)
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "dry_run")
        self.assertIn("DRY_", result["order_no"])
        self.assertIn("005930", result["order_no"])

    def test_success_returns_order_no(self):
        with patch("scripts.trading_batch_common.requests.post",
                   return_value=_mock_response(
                       {"accepted": True, "order_no": "0000123", "message": "", "raw": {}}
                   )):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["order_no"], "0000123")

    def test_accepted_false_returns_failed(self):
        with patch("scripts.trading_batch_common.requests.post",
                   return_value=_mock_response(
                       {"accepted": False, "order_no": None, "message": "잔고부족", "raw": {}}
                   )):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "failed")
        self.assertIn("잔고부족", result["message"])

    def test_422_returns_rejected_with_detail(self):
        """422 = broker 가드/키움 거부. 통신 실패(failed)와 구분해 detail 을 살린다."""
        mock = _mock_response({"detail": "시장가 예상금액 상한 초과"}, status_code=422)
        mock.raise_for_status.side_effect = Exception("HTTP 422")
        with patch("scripts.trading_batch_common.requests.post", return_value=mock):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("상한 초과", result["message"])

    def test_http_error_returns_failed(self):
        mock = _mock_response({}, status_code=500)
        mock.raise_for_status.side_effect = Exception("HTTP 500")
        with patch("scripts.trading_batch_common.requests.post", return_value=mock):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "failed")

    def test_connection_error_returns_failed(self):
        with patch("scripts.trading_batch_common.requests.post", side_effect=ConnectionError("refused")):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "failed")

    def test_request_body_format(self):
        with patch("scripts.trading_batch_common.requests.post",
                   return_value=_mock_response(
                       {"accepted": True, "order_no": "0000001", "message": "", "raw": {}}
                   )) as mock_post:
            place_order_via_broker(_BROKER, "005930", qty=3, dry_run=False)
        _, kwargs = mock_post.call_args
        body = kwargs.get("json") or mock_post.call_args[0][1]
        self.assertEqual(body["symbol"], "005930")
        self.assertEqual(body["side"], "buy")
        self.assertEqual(body["qty"], 3)
        self.assertEqual(body["order_type"], "market")


# ── BrokerClient (scripts/broker_client.py) ──────────────────────────────────

_KST = ZoneInfo("Asia/Seoul")
_NOON = datetime(2026, 6, 1, 12, 0, 0, tzinfo=_KST)


def _make_client(**kw):
    opts = {"sources": {"buy": "SRC_BUY", "sell": "SRC_SELL"}}
    opts.update(kw)
    return BrokerClient(_BROKER, **opts)


def _fixed_noon():
    return patch("scripts.broker_client._now_seoul", return_value=_NOON)


# ── quote_fresh (순수 함수) ──────────────────────────────────────────────────

class TestQuoteFresh(unittest.TestCase):
    def test_fresh_within_window(self):
        self.assertTrue(quote_fresh("115901", _NOON, 300))

    def test_fresh_at_exact_boundary(self):
        self.assertTrue(quote_fresh("115500", _NOON, 300))

    def test_stale_past_window(self):
        self.assertFalse(quote_fresh("115459", _NOON, 300))

    def test_malformed_is_fresh(self):
        for bad in ("abc", "", "12345", "1234567", "12:00:00", None):
            with self.subTest(base_tm=bad):
                self.assertTrue(quote_fresh(bad, _NOON, 300))

    def test_out_of_range_time_is_fresh(self):
        self.assertTrue(quote_fresh("250000", _NOON, 300))

    def test_future_base_tm_is_fresh(self):
        self.assertTrue(quote_fresh("120100", _NOON, 300))


# ── best_quote ───────────────────────────────────────────────────────────────

class TestBestQuote(unittest.TestCase):
    def test_buy_returns_ask_top_level(self):
        payload = {"sel_fpr_bid": "+70,000", "buy_fpr_bid": "69900"}
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(payload)):
            self.assertEqual(_make_client().best_quote("005930", "buy"), 70000)

    def test_sell_returns_bid_top_level(self):
        payload = {"sel_fpr_bid": "70000", "buy_fpr_bid": "-69,900"}
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(payload)):
            self.assertEqual(_make_client().best_quote("005930", "sell"), 69900)

    def test_falls_back_to_raw(self):
        payload = {"raw": {"sel_fpr_bid": "70000", "buy_fpr_bid": "69900"}}
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(payload)):
            client = _make_client()
            self.assertEqual(client.best_quote("005930", "buy"), 70000)
            self.assertEqual(client.best_quote("005930", "sell"), 69900)

    def test_zero_returns_none(self):
        payload = {"sel_fpr_bid": "0", "buy_fpr_bid": "69900"}
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(payload)):
            self.assertIsNone(_make_client().best_quote("005930", "buy"))

    def test_missing_field_returns_none(self):
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response({})):
            self.assertIsNone(_make_client().best_quote("005930", "buy"))

    def test_stale_base_tm_returns_none(self):
        payload = {"sel_fpr_bid": "70000", "bid_req_base_tm": "115459"}
        with _fixed_noon(), patch("scripts.broker_client.requests.get",
                                  return_value=_mock_response(payload)):
            self.assertIsNone(_make_client().best_quote("005930", "buy"))

    def test_fresh_base_tm_returns_value(self):
        payload = {"sel_fpr_bid": "70000", "bid_req_base_tm": "115500"}
        with _fixed_noon(), patch("scripts.broker_client.requests.get",
                                  return_value=_mock_response(payload)):
            self.assertEqual(_make_client().best_quote("005930", "buy"), 70000)

    def test_malformed_base_tm_treated_as_fresh(self):
        payload = {"sel_fpr_bid": "70000", "bid_req_base_tm": "abc"}
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(payload)):
            self.assertEqual(_make_client().best_quote("005930", "buy"), 70000)

    def test_max_quote_age_s_respected(self):
        payload = {"sel_fpr_bid": "70000", "bid_req_base_tm": "110000"}
        with _fixed_noon(), patch("scripts.broker_client.requests.get",
                                  return_value=_mock_response(payload)):
            self.assertIsNone(_make_client().best_quote("005930", "buy"))
            self.assertEqual(
                _make_client(max_quote_age_s=7200).best_quote("005930", "buy"),
                70000)

    def test_exception_returns_none(self):
        with patch("scripts.broker_client.requests.get",
                   side_effect=ConnectionError("refused")):
            self.assertIsNone(_make_client().best_quote("005930", "buy"))

    def test_invalid_side_returns_none_without_http(self):
        with patch("scripts.broker_client.requests.get") as mock_get:
            self.assertIsNone(_make_client().best_quote("005930", "hold"))
        mock_get.assert_not_called()

    def test_calls_orderbook_url(self):
        payload = {"sel_fpr_bid": "70000", "buy_fpr_bid": "69900"}
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(payload)) as mock_get:
            _make_client().best_quote("005930", "buy")
        mock_get.assert_called_once_with(
            f"{_BROKER}/quotes/005930/orderbook", timeout=15)


# ── limit_price ──────────────────────────────────────────────────────────────

class TestLimitPrice(unittest.TestCase):
    def _price(self, payload, side):
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(payload)):
            return _make_client().limit_price("005930", side)

    def test_buy_subtracts_tick_below_at_5000(self):
        self.assertEqual(self._price({"sel_fpr_bid": "5000"}, "buy"), 4995)

    def test_buy_at_2000_boundary(self):
        self.assertEqual(self._price({"sel_fpr_bid": "2000"}, "buy"), 1999)

    def test_sell_adds_tick_size_at_5000(self):
        self.assertEqual(self._price({"buy_fpr_bid": "5000"}, "sell"), 5010)

    def test_sell_at_1000(self):
        self.assertEqual(self._price({"buy_fpr_bid": "1000"}, "sell"), 1001)

    def test_none_when_no_quote(self):
        self.assertIsNone(self._price({"sel_fpr_bid": "0"}, "buy"))

    def test_none_when_result_not_positive(self):
        self.assertIsNone(self._price({"sel_fpr_bid": "1"}, "buy"))


# ── place ────────────────────────────────────────────────────────────────────

class TestPlaceOrders(unittest.TestCase):
    def test_success_submitted_with_source_and_exchange(self):
        body = {"accepted": True, "order_no": "0000123", "message": "ok"}
        with patch("scripts.broker_client.requests.post",
                   return_value=_mock_response(body)) as mock_post:
            result = _make_client().place("005930", "buy", 3, 70000, "limit")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["order_no"], "0000123")
        self.assertEqual(result["message"], "ok")
        mock_post.assert_called_once_with(
            f"{_BROKER}/orders/strategy",
            json={"symbol": "005930", "side": "buy", "qty": 3, "price": 70000,
                  "order_type": "limit", "source": "SRC_BUY",
                  "exchange": "KRX"},
            timeout=15)

    def test_accepted_false_is_rejected(self):
        body = {"accepted": False, "order_no": None, "message": "잔고부족"}
        with patch("scripts.broker_client.requests.post",
                   return_value=_mock_response(body)):
            result = _make_client().place("005930", "buy", 1, 70000, "limit")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["order_no"], "")
        self.assertIn("잔고부족", result["message"])

    def test_missing_order_no_is_failed(self):
        body = {"accepted": True, "message": ""}
        with patch("scripts.broker_client.requests.post",
                   return_value=_mock_response(body)):
            result = _make_client().place("005930", "buy", 1, 70000, "limit")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["order_no"], "")

    def test_empty_order_no_is_failed(self):
        body = {"accepted": True, "order_no": "", "message": ""}
        with patch("scripts.broker_client.requests.post",
                   return_value=_mock_response(body)):
            result = _make_client().place("005930", "buy", 1, 70000, "limit")
        self.assertEqual(result["status"], "failed")

    def test_exception_is_unknown(self):
        with patch("scripts.broker_client.requests.post",
                   side_effect=TimeoutError("timed out")):
            result = _make_client().place("005930", "buy", 1, 70000, "limit")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["order_no"], "")

    def test_unregistered_side_is_failed_without_http(self):
        with patch("scripts.broker_client.requests.post") as mock_post:
            result = _make_client().place("005930", "hold", 1, 70000, "limit")
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["order_no"], "")

    def test_unregistered_side_fails_even_in_dry_run(self):
        with patch("scripts.broker_client.requests.post") as mock_post:
            result = _make_client(dry_run=True).place(
                "005930", "hold", 1, 70000, "limit")
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "failed")

    def test_dry_run_no_http(self):
        with patch("scripts.broker_client.requests.post") as mock_post:
            result = _make_client(dry_run=True).place(
                "005930", "buy", 1, 70000, "limit")
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "dry_run")
        self.assertRegex(result["order_no"], r"^DRY_005930_\d{6}$")

    def test_market_forces_price_zero(self):
        body = {"accepted": True, "order_no": "1", "message": ""}
        with patch("scripts.broker_client.requests.post",
                   return_value=_mock_response(body)) as mock_post:
            _make_client().place("005930", "buy", 1, 70000, "market")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["price"], 0)
        self.assertEqual(kwargs["json"]["order_type"], "market")

    def test_place_limit_helper(self):
        body = {"accepted": True, "order_no": "1", "message": ""}
        with patch("scripts.broker_client.requests.post",
                   return_value=_mock_response(body)) as mock_post:
            result = _make_client().place_limit("005930", "sell", 2, 69900)
        self.assertEqual(result["status"], "submitted")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["order_type"], "limit")
        self.assertEqual(kwargs["json"]["price"], 69900)
        self.assertEqual(kwargs["json"]["source"], "SRC_SELL")

    def test_place_market_helper(self):
        body = {"accepted": True, "order_no": "1", "message": ""}
        with patch("scripts.broker_client.requests.post",
                   return_value=_mock_response(body)) as mock_post:
            result = _make_client().place_market("005930", "buy", 2)
        self.assertEqual(result["status"], "submitted")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["order_type"], "market")
        self.assertEqual(kwargs["json"]["price"], 0)


# ── modify ───────────────────────────────────────────────────────────────────

class TestModifyOrder(unittest.TestCase):
    def test_new_number_submitted(self):
        body = {"accepted": True, "order_no": "0000456", "message": ""}
        with patch("scripts.broker_client.requests.patch",
                   return_value=_mock_response(body)) as mock_patch:
            result = _make_client().modify("0000123", "005930", 70100, 3)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["order_no"], "0000456")
        mock_patch.assert_called_once_with(
            f"{_BROKER}/orders/0000123",
            json={"symbol": "005930", "price": 70100, "qty": 3,
                  "exchange": "KRX"},
            timeout=15)

    def test_same_number_is_failed(self):
        body = {"accepted": True, "order_no": "123", "message": ""}
        with patch("scripts.broker_client.requests.patch",
                   return_value=_mock_response(body)):
            result = _make_client().modify("0000123", "005930", 70100, 3)
        self.assertEqual(result["status"], "failed")
        self.assertIn("정정 후 새 주문번호 없음", result["message"])

    def test_missing_order_no_is_failed(self):
        body = {"accepted": True, "message": ""}
        with patch("scripts.broker_client.requests.patch",
                   return_value=_mock_response(body)):
            result = _make_client().modify("0000123", "005930", 70100, 3)
        self.assertEqual(result["status"], "failed")
        self.assertIn("정정 후 새 주문번호 없음", result["message"])

    def test_accepted_false_is_failed(self):
        body = {"accepted": False, "order_no": None, "message": "정정 불가"}
        with patch("scripts.broker_client.requests.patch",
                   return_value=_mock_response(body)):
            result = _make_client().modify("0000123", "005930", 70100, 3)
        self.assertEqual(result["status"], "failed")
        self.assertIn("정정 불가", result["message"])

    def test_exception_is_unknown(self):
        with patch("scripts.broker_client.requests.patch",
                   side_effect=TimeoutError("timed out")):
            result = _make_client().modify("0000123", "005930", 70100, 3)
        self.assertEqual(result["status"], "unknown")

    def test_dry_run_no_http(self):
        with patch("scripts.broker_client.requests.patch") as mock_patch:
            result = _make_client(dry_run=True).modify(
                "0000123", "005930", 70100, 3)
        mock_patch.assert_not_called()
        self.assertEqual(result["status"], "dry_run")


# ── cancel ───────────────────────────────────────────────────────────────────

class TestCancelOrder(unittest.TestCase):
    def test_success_is_cancelled(self):
        with patch("scripts.broker_client.requests.delete",
                   return_value=_mock_response({"accepted": True})) as mock_del:
            result = _make_client().cancel("0000123", "005930")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["order_no"], "0000123")
        mock_del.assert_called_once_with(
            f"{_BROKER}/orders/0000123",
            params={"symbol": "005930", "qty": 0, "exchange": "KRX"},
            timeout=15)

    def test_empty_body_is_cancelled(self):
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status.return_value = None
        mock.json.side_effect = ValueError("No JSON object could be decoded")
        with patch("scripts.broker_client.requests.delete",
                   return_value=mock):
            result = _make_client().cancel("0000123", "005930")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["order_no"], "0000123")

    def test_accepted_false_is_failed(self):
        body = {"accepted": False, "message": "취소 불가"}
        with patch("scripts.broker_client.requests.delete",
                   return_value=_mock_response(body)):
            result = _make_client().cancel("0000123", "005930")
        self.assertEqual(result["status"], "failed")
        self.assertIn("취소 불가", result["message"])

    def test_http_error_is_failed(self):
        mock = _mock_response({}, status_code=500)
        mock.raise_for_status.side_effect = requests.HTTPError("HTTP 500")
        with patch("scripts.broker_client.requests.delete", return_value=mock):
            result = _make_client().cancel("0000123", "005930")
        self.assertEqual(result["status"], "failed")

    def test_exception_is_unknown(self):
        with patch("scripts.broker_client.requests.delete",
                   side_effect=ConnectionError("refused")):
            result = _make_client().cancel("0000123", "005930")
        self.assertEqual(result["status"], "unknown")

    def test_dry_run_no_http(self):
        with patch("scripts.broker_client.requests.delete") as mock_del:
            result = _make_client(dry_run=True).cancel("0000123", "005930")
        mock_del.assert_not_called()
        self.assertEqual(result["status"], "dry_run")


# ── unfilled / history ───────────────────────────────────────────────────────

class TestUnfilledHistory(unittest.TestCase):
    def test_unfilled_returns_list(self):
        rows = [{"order_no": "1", "ticker": "005930"}]
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(rows)) as mock_get:
            self.assertEqual(_make_client().unfilled("sell"), rows)
        mock_get.assert_called_once_with(
            f"{_BROKER}/orders/unfilled", params={"side": "sell"}, timeout=15)

    def test_unfilled_dict_returns_none(self):
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response({"error": "x"})):
            self.assertIsNone(_make_client().unfilled("sell"))

    def test_unfilled_exception_returns_none(self):
        with patch("scripts.broker_client.requests.get",
                   side_effect=ConnectionError("refused")):
            self.assertIsNone(_make_client().unfilled("sell"))

    def test_history_returns_list(self):
        rows = [{"order_no": "1", "cntr_qty": 2}]
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response(rows)) as mock_get:
            self.assertEqual(_make_client().history("20260601", "buy"), rows)
        mock_get.assert_called_once_with(
            f"{_BROKER}/orders/history",
            params={"date": "20260601", "side": "buy"}, timeout=15)

    def test_history_dict_returns_none(self):
        with patch("scripts.broker_client.requests.get",
                   return_value=_mock_response({"error": "x"})):
            self.assertIsNone(_make_client().history("20260601", "buy"))

    def test_history_exception_returns_none(self):
        with patch("scripts.broker_client.requests.get",
                   side_effect=ConnectionError("refused")):
            self.assertIsNone(_make_client().history("20260601", "buy"))


# ── fills_by_order ───────────────────────────────────────────────────────────

class TestFillsByOrder(unittest.TestCase):
    def test_sums_same_normalized_no(self):
        rows = [{"order_no": "0000123", "cntr_qty": 2},
                {"order_no": "123", "cntr_qty": 3},
                {"order_no": "999", "cntr_qty": 1}]
        self.assertEqual(fills_by_order(rows),
                         {"123": {"cntr_qty": 5}, "999": {"cntr_qty": 1}})

    def test_ignores_empty_no(self):
        rows = [{"order_no": "", "cntr_qty": 2},
                {"order_no": None, "cntr_qty": 3},
                {"order_no": "0000000", "cntr_qty": 4}]
        self.assertEqual(fills_by_order(rows), {})

    def test_none_passthrough(self):
        self.assertIsNone(fills_by_order(None))

    def test_empty_list(self):
        self.assertEqual(fills_by_order([]), {})


if __name__ == "__main__":
    unittest.main()
