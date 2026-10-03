"""두 자동매매 배치가 공유하는 broker 주문 인프라 테스트.

실행 (etl/ 에서):
    $env:PYTHONPATH="."; uv run python -m unittest tests.test_trading_batch_common
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import trading_batch_common as tbc
from scripts.trading_batch_common import (
    CLOSED_SELL_STATUSES,
    _broker_key,
    available_cash,
    current_price,
    fetch_realized,
    held_quantities,
    in_order_window,
    market_order,
    quote_snapshot,
    quantity_for_budget,
    read_reservation,
    require_profile,
    write_reservation,
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
        self.assertEqual(post.call_args.args[0], "http://broker/orders/strategy")
        self.assertEqual(post.call_args.kwargs["json"]["source"], "pullback_order")
        self.assertEqual(result["status"], "submitted")

    @patch("scripts.trading_batch_common.requests.post")
    def test_market_order_distinguishes_broker_rejection(self, post: Mock):
        response = Mock(status_code=422, json=lambda: {"detail": "금액 상한 초과"})
        post.return_value = response
        result = market_order("http://broker", "005930", 2, "buy", "pullback_order", False)
        self.assertEqual(result["status"], "rejected")

    @patch("scripts.trading_batch_common.requests.get")
    def test_fetch_realized_found(self, get: Mock):
        response = Mock(json=lambda: {"found": True, "pnl_pct": 4.8, "cmsn": 1,
                                      "tax": 2, "sel_pl_won": 100})
        response.raise_for_status = Mock()
        get.return_value = response
        self.assertEqual(fetch_realized("http://broker", "005930")["pnl_pct"], 4.8)

    @patch("scripts.trading_batch_common.requests.get")
    def test_fetch_realized_not_found_returns_none(self, get: Mock):
        response = Mock(json=lambda: {"found": False, "pnl_pct": 0.0})
        response.raise_for_status = Mock()
        get.return_value = response
        self.assertIsNone(fetch_realized("http://broker", "005930"))

    def test_quantity_for_budget(self):
        self.assertEqual(quantity_for_budget(300000, 10000), 30)
        self.assertEqual(quantity_for_budget(300000, 400000), 0)


if __name__ == "__main__":
    unittest.main()


class HeldQuantitiesTest(unittest.TestCase):
    """잔고 → 보유수량 맵. 두 청산 배치가 각자 복사해 갖고 있던 파싱을 공용화한 것."""

    def test_strips_prefix_and_zero_pad(self):
        balance = {"acnt_evlt_remn_indv_tot": [
            {"stk_cd": "A005930", "trde_able_qty": "000000000000014"},
            {"stk_cd": "025320", "trde_able_qty": "75"},
        ]}
        self.assertEqual(held_quantities(balance), {"005930": 14, "025320": 75})

    def test_strips_prefix_from_alphanumeric_ticker(self):
        balance = {"acnt_evlt_remn_indv_tot": [
            {"stk_cd": "A0220W0", "trde_able_qty": "000000000000009"},
        ]}
        self.assertEqual(held_quantities(balance), {"0220W0": 9})

    def test_empty_account_is_not_a_failure(self):
        """보유 0건은 정상 응답 — 유령 마감 판정이 진행돼야 한다."""
        self.assertEqual(held_quantities({"acnt_evlt_remn_indv_tot": []}), {})

    def test_lookup_failure_returns_none(self):
        """조회 실패를 '전 종목 미보유'로 오독하면 멀쩡한 포지션이 몰살된다."""
        self.assertIsNone(held_quantities({}))
        self.assertIsNone(held_quantities({"return_code": 3, "return_msg": "오류"}))
        self.assertIsNone(held_quantities({"acnt_evlt_remn_indv_tot": None}))


class RequireProfileTest(unittest.TestCase):
    """러너가 다른 계좌 broker 에 주문하지 않도록 /health profile 을 확인한다."""

    @staticmethod
    def _health(body):
        response = Mock(json=lambda: body)
        response.raise_for_status = Mock()
        return response

    @patch("scripts.trading_batch_common.requests.get")
    def test_matching_profile(self, get: Mock):
        get.return_value = self._health({"status": "ok", "profile": "HIGH52"})
        self.assertTrue(require_profile("http://broker", "HIGH52"))
        self.assertEqual(get.call_args.args[0], "http://broker/health")

    @patch("scripts.trading_batch_common.requests.get")
    def test_main_account_broker_is_refused(self, get: Mock):
        get.return_value = self._health({"status": "ok", "profile": ""})
        self.assertFalse(require_profile("http://broker", "HIGH52"))

    @patch("scripts.trading_batch_common.requests.get")
    def test_old_broker_without_profile_field_is_refused(self, get: Mock):
        get.return_value = self._health({"status": "ok"})
        self.assertFalse(require_profile("http://broker", "HIGH52"))

    @patch("scripts.trading_batch_common.requests.get", side_effect=RuntimeError("down"))
    def test_lookup_failure_is_refused(self, _get: Mock):
        """어느 계좌인지 모르면 주문하지 않는다."""
        self.assertFalse(require_profile("http://broker", "HIGH52"))


class ClosedSellStatusesTest(unittest.TestCase):
    def test_missing_counts_as_closed(self):
        """'missing' 이 종료로 안 잡히면 중복매수 가드가 그 종목을 영구 차단한다."""
        self.assertIn("filled", CLOSED_SELL_STATUSES)
        self.assertIn("missing", CLOSED_SELL_STATUSES)


class BrokerKeyTest(unittest.TestCase):
    """예약 키 — host:port, localhost ≡ 127.0.0.1."""

    def test_localhost_equals_loopback(self):
        self.assertEqual(_broker_key("http://localhost:8001"),
                         _broker_key("http://127.0.0.1:8001/"))

    def test_port_distinguishes_accounts(self):
        self.assertNotEqual(_broker_key("http://127.0.0.1:8001"),
                            _broker_key("http://127.0.0.1:8002"))


class CashReservationTest(unittest.TestCase):
    """유증 자금 분리 예약 (R2b P3a) — 파일 없으면 기존 동작 그대로."""

    URL = "http://127.0.0.1:8001"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name) / "cash_reservations.json"
        self._patch = patch.object(tbc, "RESERVATION_PATH", self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self.td.cleanup()

    def _deposit(self, value):
        response = Mock(json=lambda: {"ord_alow_amt": value})
        response.raise_for_status = Mock()
        return response

    def _write(self, obj):
        self.path.write_text(json.dumps(obj), encoding="utf-8")

    def _entry(self, reserve=8_000_000, capital=8_000_000):
        return {"reserve": reserve, "capital_total": capital,
                "strategy": "rights_dip", "updated": "2026-10-02T00:00:00+09:00"}

    @patch("scripts.trading_batch_common.requests.get")
    def test_no_file_returns_broker_value(self, get: Mock):
        get.return_value = self._deposit("10000000")
        self.assertEqual(available_cash(self.URL), 10_000_000)
        self.assertIsNone(read_reservation(self.URL))

    @patch("scripts.trading_batch_common.requests.get")
    def test_reserve_subtracted(self, get: Mock):
        get.return_value = self._deposit("10000000")
        self._write({_broker_key(self.URL): self._entry()})
        self.assertEqual(available_cash(self.URL), 2_000_000)
        self.assertEqual(available_cash(self.URL, exclude_reserve=False), 10_000_000)

    @patch("scripts.trading_batch_common.requests.get")
    def test_reserve_over_cash_floors_zero(self, get: Mock):
        get.return_value = self._deposit("1000000")
        self._write({_broker_key(self.URL): self._entry()})
        self.assertEqual(available_cash(self.URL), 0)

    @patch("scripts.trading_batch_common.requests.get")
    def test_corrupt_file_fails_closed(self, get: Mock):
        get.return_value = self._deposit("10000000")
        self.path.write_text("{깨짐", encoding="utf-8")
        with patch("builtins.print") as warned:
            self.assertEqual(available_cash(self.URL), 0)   # A-10 — 깨지면 0
            self.assertIsNone(read_reservation(self.URL))
        self.assertTrue(warned.called)

    def test_missing_key_returns_none(self):
        self._write({"127.0.0.1:8002": self._entry()})
        self.assertIsNone(read_reservation(self.URL))

    def test_write_roundtrip_preserves_other_keys(self):
        self._write({"127.0.0.1:8002": self._entry(reserve=100)})
        write_reservation(self.URL, 8_000_000, 8_000_000, "rights_dip")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["127.0.0.1:8002"]["reserve"], 100)
        entry = data[_broker_key(self.URL)]
        self.assertEqual((entry["reserve"], entry["capital_total"], entry["strategy"]),
                         (8_000_000, 8_000_000, "rights_dip"))
        self.assertIn("updated", entry)

    def test_localhost_key_shared_with_loopback(self):
        write_reservation("http://localhost:8001", 100, 8_000_000, "rights_dip")
        self.assertIsNotNone(read_reservation("http://127.0.0.1:8001/"))

    @patch("scripts.trading_batch_common.requests.get", side_effect=RuntimeError("down"))
    def test_query_failure_stays_none(self, _get: Mock):
        self._write({_broker_key(self.URL): self._entry()})
        self.assertIsNone(available_cash(self.URL))

    @patch("scripts.trading_batch_common.requests.get")
    def test_read_error_fails_closed(self, get: Mock):
        """권한 오류 등 읽기 실패 → 깨진 파일과 같이 다른 전략 0."""
        get.return_value = self._deposit("10000000")
        self._write({_broker_key(self.URL): self._entry()})
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with patch("builtins.print") as warned:
                self.assertEqual(available_cash(self.URL), 0)
                self.assertIsNone(read_reservation(self.URL))
            self.assertTrue(warned.called)

    @patch("scripts.trading_batch_common.requests.get")
    def test_invalid_reserve_fails_closed(self, get: Mock):
        get.return_value = self._deposit("10000000")
        for bad in ("abc", -1, True):
            with self.subTest(bad=bad):
                self._write({_broker_key(self.URL): self._entry(reserve=bad)})
                with patch("builtins.print") as warned:
                    self.assertEqual(available_cash(self.URL), 0)
                self.assertTrue(warned.called)

    @patch("scripts.trading_batch_common.requests.get")
    def test_invalid_capital_total_fails_closed(self, get: Mock):
        get.return_value = self._deposit("10000000")
        for bad in ("abc", -1, True):
            with self.subTest(bad=bad):
                self._write({_broker_key(self.URL): self._entry(capital=bad)})
                with patch("builtins.print") as warned:
                    self.assertEqual(available_cash(self.URL), 0)
                self.assertTrue(warned.called)

    def test_write_refuses_corrupt_file(self):
        before = "{깨짐"
        self.path.write_text(before, encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            write_reservation(self.URL, 100, 8_000_000, "rights_dip")
        self.assertIn(str(self.path), str(ctx.exception))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_write_refuses_non_dict_file(self):
        before = json.dumps([1, 2])
        self.path.write_text(before, encoding="utf-8")
        with self.assertRaises(RuntimeError):
            write_reservation(self.URL, 100, 8_000_000, "rights_dip")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_write_read_error_raises(self):
        self._write({_broker_key(self.URL): self._entry()})
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            with self.assertRaises(RuntimeError):
                write_reservation(self.URL, 100, 8_000_000, "rights_dip")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {_broker_key(self.URL): self._entry()})

    def test_write_rejects_bad_amounts(self):
        for bad in ("abc", -1, True, 8.0, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    write_reservation(self.URL, bad, 8_000_000, "rights_dip")
                with self.assertRaises(ValueError):
                    write_reservation(self.URL, 100, bad, "rights_dip")
        self.assertFalse(self.path.exists())

    def test_write_leaves_no_temp_files(self):
        self._write({"127.0.0.1:8002": self._entry(reserve=100)})
        write_reservation(self.URL, 8_000_000, 8_000_000, "rights_dip")
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name != self.path.name]
        self.assertEqual(leftovers, [])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["127.0.0.1:8002"]["reserve"], 100)
