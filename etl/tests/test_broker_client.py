"""Stage 2 & 3: broker REST API 경유 함수 테스트.

fetch_price_via_broker  — GET /quotes/{symbol}
place_order_via_broker  — POST /orders
"""
import unittest
from unittest.mock import MagicMock, patch

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
        with patch("scripts.run_close_bet.requests.get",
                   return_value=_mock_response({"symbol": "005930", "price": 75000, "raw": {}})):
            result = fetch_price_via_broker(_BROKER, "005930")
        self.assertEqual(result, 75000)

    def test_returns_none_when_price_is_null(self):
        with patch("scripts.run_close_bet.requests.get",
                   return_value=_mock_response({"symbol": "005930", "price": None, "raw": {}})):
            result = fetch_price_via_broker(_BROKER, "005930")
        self.assertIsNone(result)

    def test_returns_none_on_http_error(self):
        mock = _mock_response({}, status_code=500)
        mock.raise_for_status.side_effect = Exception("HTTP 500")
        with patch("scripts.run_close_bet.requests.get", return_value=mock):
            result = fetch_price_via_broker(_BROKER, "005930")
        self.assertIsNone(result)

    def test_returns_none_on_connection_error(self):
        with patch("scripts.run_close_bet.requests.get", side_effect=ConnectionError("refused")):
            result = fetch_price_via_broker(_BROKER, "005930")
        self.assertIsNone(result)

    def test_calls_correct_url(self):
        with patch("scripts.run_close_bet.requests.get",
                   return_value=_mock_response({"symbol": "000660", "price": 12000, "raw": {}})) as mock_get:
            fetch_price_via_broker(_BROKER, "000660")
        mock_get.assert_called_once_with(
            f"{_BROKER}/quotes/000660",
            timeout=unittest.mock.ANY,
        )


# ── Stage 3: place_order_via_broker ──────────────────────────────────────────

class TestPlaceOrderViaBroker(unittest.TestCase):
    def test_dry_run_returns_without_http_call(self):
        with patch("scripts.run_close_bet.requests.post") as mock_post:
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=True)
        mock_post.assert_not_called()
        self.assertEqual(result["status"], "dry_run")
        self.assertIn("DRY_", result["order_no"])
        self.assertIn("005930", result["order_no"])

    def test_success_returns_order_no(self):
        with patch("scripts.run_close_bet.requests.post",
                   return_value=_mock_response(
                       {"accepted": True, "order_no": "0000123", "message": "", "raw": {}}
                   )):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["order_no"], "0000123")

    def test_accepted_false_returns_failed(self):
        with patch("scripts.run_close_bet.requests.post",
                   return_value=_mock_response(
                       {"accepted": False, "order_no": None, "message": "잔고부족", "raw": {}}
                   )):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "failed")
        self.assertIn("잔고부족", result["message"])

    def test_http_error_returns_failed(self):
        mock = _mock_response({}, status_code=422)
        mock.raise_for_status.side_effect = Exception("HTTP 422")
        with patch("scripts.run_close_bet.requests.post", return_value=mock):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "failed")

    def test_connection_error_returns_failed(self):
        with patch("scripts.run_close_bet.requests.post", side_effect=ConnectionError("refused")):
            result = place_order_via_broker(_BROKER, "005930", qty=1, dry_run=False)
        self.assertEqual(result["status"], "failed")

    def test_request_body_format(self):
        with patch("scripts.run_close_bet.requests.post",
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


if __name__ == "__main__":
    unittest.main()
