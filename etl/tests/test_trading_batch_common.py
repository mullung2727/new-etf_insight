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
    read_reservations,
    require_profile,
    write_reservation,
)


class TradingBatchCommonTest(unittest.TestCase):
    def test_order_window_includes_start_and_excludes_deadline(self):
        self.assertTrue(in_order_window(datetime(2026, 7, 15, 15, 19), "15:19:00", "15:20:00"))
        self.assertFalse(in_order_window(datetime(2026, 7, 15, 15, 20), "15:19:00", "15:20:00"))

    @patch("scripts.trading_batch_common.requests.get")
    def test_quote_and_cash_are_normalized(self, get: Mock):
        responses = [Mock(json=lambda: {"price": "001230"}), Mock(json=lambda: {"100stk_ord_alow_amt": "0300000"})]
        for response in responses:
            response.raise_for_status = Mock()
        get.side_effect = responses
        self.assertEqual(current_price("http://broker", "005930"), 1230)
        self.assertEqual(available_cash("http://broker"), 300000)

    @patch("scripts.trading_batch_common.requests.get")
    def test_cash_uses_100stk_not_ord_alow_amt(self, get: Mock):
        both = Mock(json=lambda: {"ord_alow_amt": "000000000791240", "100stk_ord_alow_amt": "000000017466360"})
        both.raise_for_status = Mock()
        get.return_value = both
        self.assertEqual(available_cash("http://broker", exclude_reserve=False), 17466360)
        only_old = Mock(json=lambda: {"ord_alow_amt": "000000000791240"})
        only_old.raise_for_status = Mock()
        get.return_value = only_old
        self.assertIsNone(available_cash("http://broker", exclude_reserve=False))

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
        response = Mock(json=lambda: {"100stk_ord_alow_amt": value})
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
        entry = data[_broker_key(self.URL)]["rights_dip"]
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
        leftovers = [p.name for p in self.path.parent.iterdir()
                     if p.name not in (self.path.name, self.path.name + ".lock")]
        self.assertEqual(leftovers, [])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["127.0.0.1:8002"]["reserve"], 100)


    def _new_entry(self, reserve, capital, strategy):
        return {"reserve": reserve, "capital_total": capital,
                "strategy": strategy, "updated": "2026-10-02T00:00:00+09:00"}

    def test_legacy_flat_file_read_as_single_strategy(self):
        entry = self._entry()
        self._write({_broker_key(self.URL): entry})
        self.assertEqual(read_reservation(self.URL), entry)
        self.assertEqual(read_reservations(self.URL), {"rights_dip": entry})

    @patch("scripts.trading_batch_common.requests.get")
    def test_only_other_strategies_subtracted(self, get: Mock):
        get.return_value = self._deposit("4000000")
        self._write({_broker_key(self.URL): {
            "rights_dip": self._new_entry(500_000, 8_000_000, "rights_dip"),
            "rebound": self._new_entry(2_970_000, 6_000_000, "rebound"),
        }})
        self.assertEqual(available_cash(self.URL), 530_000)
        self.assertEqual(available_cash(self.URL, strategy="envelope"), 530_000)
        self.assertEqual(available_cash(self.URL, strategy="rebound"), 3_500_000)
        self.assertEqual(available_cash(self.URL, strategy="rights_dip"), 1_030_000)
        self.assertEqual(available_cash(self.URL, exclude_reserve=False), 4_000_000)

    def test_write_to_legacy_file_preserves_existing_strategy(self):
        self._write({_broker_key(self.URL): self._entry()})
        write_reservation(self.URL, 100, 6_000_000, "rebound")
        entries = read_reservations(self.URL)
        self.assertEqual(set(entries), {"rights_dip", "rebound"})
        self.assertEqual(entries["rights_dip"], self._entry())
        self.assertEqual(entries["rebound"]["reserve"], 100)
        self.assertEqual(entries["rebound"]["capital_total"], 6_000_000)

    def test_write_same_strategy_twice_keeps_last(self):
        write_reservation(self.URL, 500_000, 8_000_000, "rights_dip")
        write_reservation(self.URL, 2_970_000, 6_000_000, "rebound")
        write_reservation(self.URL, 100, 6_000_000, "rights_dip")
        entries = read_reservations(self.URL)
        self.assertEqual(entries["rights_dip"]["reserve"], 100)
        self.assertEqual(entries["rebound"]["reserve"], 2_970_000)

    @patch("scripts.trading_batch_common.requests.get")
    def test_invalid_other_strategy_fails_closed(self, get: Mock):
        for bad in (-1, "abc"):
            with self.subTest(bad=bad):
                get.return_value = self._deposit("4000000")
                self._write({_broker_key(self.URL): {
                    "rights_dip": self._new_entry(bad, 8_000_000, "rights_dip"),
                    "rebound": self._new_entry(2_970_000, 6_000_000, "rebound"),
                }})
                with patch("builtins.print") as warned:
                    self.assertEqual(available_cash(self.URL, strategy="rebound"), 0)
                self.assertTrue(warned.called)

    @patch("scripts.trading_batch_common.requests.get")
    def test_invalid_self_strategy_not_subtracted(self, get: Mock):
        get.return_value = self._deposit("4000000")
        self._write({_broker_key(self.URL): {
            "rights_dip": self._new_entry("bad", 8_000_000, "rights_dip"),
            "rebound": self._new_entry(2_970_000, 6_000_000, "rebound"),
        }})
        self.assertEqual(available_cash(self.URL, strategy="rights_dip"), 1_030_000)


    @patch("scripts.trading_batch_common.requests.get")
    def test_malformed_strategy_entry_fails_closed(self, get: Mock):
        """새 형식에서 값이 dict 아님 → 차감 0, 쓰기 거부, 파일 불변."""
        get.return_value = self._deposit("10000000")
        self._write({_broker_key(self.URL): {
            "rights_dip": self._new_entry(500_000, 8_000_000, "rights_dip"),
            "rebound": "x",
        }})
        before = self.path.read_text(encoding="utf-8")
        with patch("builtins.print") as warned:
            self.assertEqual(available_cash(self.URL, strategy="rights_dip"), 0)
        self.assertTrue(warned.called)
        with self.assertRaises(RuntimeError) as ctx:
            write_reservation(self.URL, 1, 1, "envelope")
        self.assertIn("형식 오류", str(ctx.exception))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    @patch("scripts.trading_batch_common.requests.get")
    def test_list_account_value_fails_closed(self, get: Mock):
        """계좌 값이 리스트 → 형식 오류로 보고 0."""
        get.return_value = self._deposit("10000000")
        self._write({_broker_key(self.URL): [1, 2]})
        with patch("builtins.print") as warned:
            self.assertEqual(available_cash(self.URL), 0)
        self.assertTrue(warned.called)

    @patch("scripts.trading_batch_common.requests.get")
    def test_missing_reserve_fails_closed(self, get: Mock):
        """다른 전략 항목에 reserve 키 없음 → 0."""
        get.return_value = self._deposit("4000000")
        self._write({_broker_key(self.URL): {
            "rights_dip": {"capital_total": 100},
            "rebound": self._new_entry(2_970_000, 6_000_000, "rebound"),
        }})
        with patch("builtins.print") as warned:
            self.assertEqual(available_cash(self.URL, strategy="rebound"), 0)
        self.assertTrue(warned.called)

    @patch("scripts.trading_batch_common.requests.get")
    def test_missing_capital_total_fails_closed(self, get: Mock):
        """다른 전략 항목에 capital_total 키 없음 → 0."""
        get.return_value = self._deposit("4000000")
        self._write({_broker_key(self.URL): {
            "rights_dip": {"reserve": 100},
            "rebound": self._new_entry(2_970_000, 6_000_000, "rebound"),
        }})
        with patch("builtins.print") as warned:
            self.assertEqual(available_cash(self.URL, strategy="rebound"), 0)
        self.assertTrue(warned.called)

    def test_write_creates_lock_file(self):
        """쓰기 후 잠금 파일이 남는다."""
        write_reservation(self.URL, 100, 8_000_000, "rights_dip")
        self.assertTrue(self.path.with_name(self.path.name + ".lock").exists())

    def test_concurrent_writes_keep_both_strategies(self):
        """스레드 2개가 서로 다른 전략으로 각 20회 쓰기 → 두 항목 모두 존재."""
        import threading
        errors: list = []

        def worker(strategy: str):
            try:
                for _ in range(20):
                    write_reservation(self.URL, 100, 8_000_000, strategy)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(name,))
                   for name in ("rights_dip", "rebound")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(set(read_reservations(self.URL)), {"rights_dip", "rebound"})


class RunLiveBatchTest(unittest.TestCase):
    """공용 시작 절차 run_live_batch — 거래일 → 계좌 → body → 알림/종료코드."""

    TAG = "LIVEBATCH"
    TODAY = "20260105"

    def _call(self, body, alert, *, trading=True, profile_ok=True, **kwargs):
        """load_dotenv·거래일·계좌를 patch하고 run_live_batch 실행 → (code, trading, require)."""
        kwargs.setdefault("broker_url", "http://127.0.0.1:8001")
        kwargs.setdefault("profile", "HIGH52")
        kwargs.setdefault("today", self.TODAY)
        with patch("dotenv.load_dotenv"), \
                patch("scripts.check_krx_trading_day.is_krx_trading_day") as trading_day, \
                patch("scripts.trading_batch_common.require_profile") as require:
            trading_day.return_value = trading
            require.return_value = profile_ok
            code = tbc.run_live_batch(self.TAG, body, notify_fn=alert, **kwargs)
        return code, trading_day, require

    def test_holiday_returns_zero_without_side_effects(self):
        body, alert = Mock(), Mock(return_value=True)
        with patch("builtins.print"):
            code, _trading, require = self._call(body, alert, trading=False)
        self.assertEqual(code, 0)
        body.assert_not_called()
        alert.assert_not_called()
        require.assert_not_called()

    def test_profile_mismatch_returns_one_without_body(self):
        body, alert = Mock(), Mock(return_value=True)
        code, _trading, _require = self._call(body, alert, profile_ok=False)
        self.assertEqual(code, 1)
        body.assert_not_called()
        self.assertEqual(alert.call_count, 1)
        self.assertIn(self.TAG, alert.call_args.args[0])

    def test_success_with_lines_notifies_once(self):
        alert = Mock(return_value=True)
        body = Mock(return_value={"lines": ["매수 005930 10주", "매도 000660 5주"], "code": 0})
        code, _trading, _require = self._call(body, alert)
        self.assertEqual(code, 0)
        self.assertEqual(alert.call_count, 1)
        message = alert.call_args.args[0]
        self.assertIn(self.TAG, message)
        self.assertIn("매수 005930 10주", message)
        self.assertIn("매도 000660 5주", message)

    def test_success_without_lines_stays_silent(self):
        alert = Mock(return_value=True)
        code, _trading, _require = self._call(
            Mock(return_value={"lines": [], "code": 0}), alert)
        self.assertEqual(code, 0)
        alert.assert_not_called()

    def test_body_none_returns_zero_silently(self):
        alert = Mock(return_value=True)
        code, _trading, _require = self._call(Mock(return_value=None), alert)
        self.assertEqual(code, 0)
        alert.assert_not_called()

    def test_nonzero_code_passes_through(self):
        alert = Mock(return_value=True)
        code, _trading, _require = self._call(Mock(return_value={"code": 1}), alert)
        self.assertEqual(code, 1)
        alert.assert_not_called()

    def test_body_error_with_alert_returns_two(self):
        alert = Mock(return_value=True)
        with patch("traceback.print_exc"):
            code, _trading, _require = self._call(
                Mock(side_effect=ValueError("폭락")), alert)
        self.assertEqual(code, 2)
        self.assertEqual(alert.call_count, 1)
        message = alert.call_args.args[0]
        self.assertIn("실패", message)
        self.assertIn("ValueError", message)

    def test_body_error_without_alert_returns_one(self):
        alert = Mock(return_value=False)
        with patch("traceback.print_exc"):
            code, _trading, _require = self._call(
                Mock(side_effect=ValueError("폭락")), alert)
        self.assertEqual(code, 1)
        self.assertEqual(alert.call_count, 1)

    def test_body_error_with_alert_failure_returns_one(self):
        alert = Mock(side_effect=RuntimeError("끊김"))
        with patch("traceback.print_exc"), patch("builtins.print"):
            code, _trading, _require = self._call(
                Mock(side_effect=ValueError("폭락")), alert)
        self.assertEqual(code, 1)

    def test_no_broker_url_skips_profile_check(self):
        body = Mock(return_value=None)
        alert = Mock(return_value=True)
        code, _trading, require = self._call(body, alert, broker_url=None)
        self.assertEqual(code, 0)
        require.assert_not_called()
        body.assert_called_once_with()

    def test_trading_day_false_skips_calendar(self):
        body = Mock(return_value=None)
        alert = Mock(return_value=True)
        code, trading, _require = self._call(body, alert, trading_day=False)
        self.assertEqual(code, 0)
        trading.assert_not_called()
        body.assert_called_once_with()


class UnknownRecheckTest(unittest.TestCase):
    """불명 주문 재판정 classify_unknown + 재주문 수량 retry_qty."""

    def test_lookup_none_is_lookup_failed(self):
        self.assertEqual(tbc.classify_unknown("0024530", None, []), ("lookup_failed", 0))
        self.assertEqual(tbc.classify_unknown("0024530", [], None), ("lookup_failed", 0))

    def test_zero_order_no_is_distinct_from_lookup_failed(self):
        self.assertEqual(tbc.classify_unknown("0000000", [], []), ("no_order_no", 0))

    def test_open_absorbs_zero_padding(self):
        self.assertEqual(tbc.classify_unknown("0024530", [{"order_no": "24530"}], []),
                         ("open", 0))

    def test_open_reports_filled_qty(self):
        fills = [{"order_no": "0024530", "cntr_qty": 30}]
        self.assertEqual(tbc.classify_unknown("0024530", [{"order_no": "24530"}], fills),
                         ("open", 30))

    def test_filled_sums_across_padded_rows(self):
        fills = [{"order_no": "24530", "cntr_qty": 10},
                 {"order_no": "0024530", "cntr_qty": 20}]
        self.assertEqual(tbc.classify_unknown("0024530", [], fills), ("filled", 30))

    def test_not_found(self):
        self.assertEqual(tbc.classify_unknown("0024530", [], []), ("not_found", 0))

    def test_retry_qty(self):
        self.assertEqual(tbc.retry_qty(10, 7), 7)
        self.assertEqual(tbc.retry_qty(10, None), 0)
        self.assertEqual(tbc.retry_qty(10, 0), 0)
        self.assertEqual(tbc.retry_qty(5, 9), 5)
