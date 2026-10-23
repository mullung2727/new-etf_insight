"""P2: 미국 주문·잔고·예수금·체결·현재가 + /us 라우터 검증.

broker는 pytest가 없으므로 stdlib unittest + FastAPI TestClient로 돌린다.
    .venv/Scripts/python.exe -m unittest test_us_orders

키움 호출은 kiwoom.us.request를 mock해 HTTP 없이 거래소 선택/정규화/가드만 검증한다.
notes.db는 매 테스트마다 임시 파일로 교체한다(test_orders.py와 같은 패턴).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiwoom import us
from kiwoom.client import KiwoomError, TrResult
from kiwoom.guards import check_us_strategy_order
from notes import db as notes_db
from routers import us as us_router


def _tr(data: dict, cont_yn: str = "N") -> TrResult:
    return TrResult(data=data, cont_yn=cont_yn, next_key="")


def _1903(api_id: str, ex: str, code: str = "QQQ") -> KiwoomError:
    return KiwoomError(
        f"{api_id} return_code=7: [1903:종목 정보가 없습니다 거래소구분={ex}, 종목코드={code}]")


def _quote_data(price: str = "+150.2500") -> dict:
    return {"stex_tp": "ND", "stk_cd": "QQQ", "stk_nm": "QQQ",
            "cur_prc": price, "buy_1bid": "+150.2000", "sel_1bid": "+150.3000",
            "trde_qty": "1234567"}


def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return Path(path)


def _trade_rows(db: Path) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT order_no, ticker, side, order_type, qty, price, status, source "
            "FROM kiwoom_trade_history"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


class _UsOrdersTestBase(unittest.TestCase):
    def setUp(self) -> None:
        us._EXCHANGE.clear()
        self.db = _fresh_db()
        # notes.db 모듈 전역을 임시 파일로 교체하고 연결 초기화
        self._orig_path = notes_db.NOTES_DB_PATH
        self._orig_conn = notes_db._conn
        notes_db.NOTES_DB_PATH = self.db
        notes_db._conn = None
        notes_db.init()

        # 라우터를 포함한 최소 앱 (main.py 전체 lifespan/WS 기동 회피)
        app = FastAPI()
        app.include_router(us_router.router)
        self.client = TestClient(app)

        env = patch.dict(os.environ, {"ALLOWED_ORDER_SOURCES": "",
                                      "MAX_US_ORDER_USD": "5000"})
        env.start()
        self.addCleanup(env.stop)
        profile = patch("routers.us.load_config",
                        return_value=SimpleNamespace(profile="US"))
        profile.start()
        self.addCleanup(profile.stop)

    def tearDown(self) -> None:
        if notes_db._conn is not None:
            notes_db._conn.close()
        notes_db.NOTES_DB_PATH = self._orig_path
        notes_db._conn = self._orig_conn
        self.db.unlink(missing_ok=True)


class TestCheckUsStrategyOrder(unittest.TestCase):
    """kiwoom.guards.check_us_strategy_order — 매수 상한·매도 면제·형식."""

    def test_buy_within_cap_passes(self):
        check_us_strategy_order("buy", 10, 1502.5, 5000.0)  # raise 없으면 통과

    def test_buy_over_cap_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            check_us_strategy_order("buy", 100, 15025.0, 5000.0)
        self.assertIn("MAX_US_ORDER_USD", str(ctx.exception))

    def test_sell_any_size_passes(self):
        check_us_strategy_order("sell", 100000, 15_025_000.0, 5000.0)

    def test_bad_qty_rejected(self):
        for qty in (0, -1):
            with self.assertRaises(ValueError, msg=f"qty={qty}"):
                check_us_strategy_order("buy", qty, 100.0, 5000.0)

    def test_bad_side_rejected(self):
        with self.assertRaises(ValueError):
            check_us_strategy_order("hold", 1, 100.0, 5000.0)


class TestPlaceUsOrder(unittest.TestCase):
    """kiwoom.us.place_us_order — 매수/매도 TR·body·1903 폴백."""

    def setUp(self) -> None:
        us._EXCHANGE.clear()

    def test_buy_market_body_and_tr(self):
        with patch("kiwoom.us.request",
                   return_value=_tr({"ord_no": "000000282"})) as m:
            out = us.place_us_order("QQQ", "buy", 10, "03", None)
        self.assertEqual(out["order_no"], "000000282")
        api_id, endpoint, body = m.call_args.args
        self.assertEqual(api_id, "ust20000")
        self.assertEqual(endpoint, "/api/us/ordr")
        self.assertEqual(body, {"stex_tp": "ND", "stk_cd": "QQQ", "ord_qty": "10",
                                "ord_uv": "", "trde_tp": "03"})

    def test_sell_limit_body_and_tr(self):
        with patch("kiwoom.us.request",
                   return_value=_tr({"ord_no": "000000283"})) as m:
            us.place_us_order("QQQ", "sell", 5, "00", 150.25)
        api_id, _endpoint, body = m.call_args.args
        self.assertEqual(api_id, "ust20001")
        self.assertEqual(body["ord_uv"], "150.25")
        self.assertEqual(body["trde_tp"], "00")

    def test_1903_falls_through_and_caches(self):
        seen: list[str] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            seen.append(body["stex_tp"])
            if body["stex_tp"] == "ND":
                raise _1903(api_id, "ND")
            return _tr({"ord_no": "000000283"})

        with patch("kiwoom.us.request", side_effect=fake):
            out = us.place_us_order("QQQ", "buy", 1, "03", None)
        self.assertEqual(seen, ["ND", "NY"])
        self.assertEqual(out["order_no"], "000000283")
        self.assertEqual(us._EXCHANGE["QQQ"], "NY")

    def test_all_1903_reraises_last(self):
        with patch("kiwoom.us.request",
                   side_effect=_1903("ust20000", "NA")) as m:
            with self.assertRaises(KiwoomError) as ctx:
                us.place_us_order("QQQ", "buy", 1, "03", None)
        self.assertEqual(m.call_count, 3)
        self.assertIn("[1903:", str(ctx.exception))

    def test_non_1903_propagates_at_once(self):
        with patch("kiwoom.us.request",
                   side_effect=KiwoomError("ust20000 return_code=1: 시스템 오류")) as m:
            with self.assertRaises(KiwoomError):
                us.place_us_order("QQQ", "buy", 1, "03", None)
        m.assert_called_once()


class TestUsReads(unittest.TestCase):
    """kiwoom.us 잔고·예수금·체결·현재가 — 정규화."""

    def setUp(self) -> None:
        us._EXCHANGE.clear()

    def test_balance_single_call_empty_body_skips_zero(self):
        # 실측 2026-10-23: {"stex_tp": "", "stk_cd": ""} 1회로 전 종목. 행 exchange는
        # _EXCHANGE 캐시에 있으면 그 값, 없으면 None. 수량 0 행은 제외.
        us._EXCHANGE["QQQ"] = "ND"
        rows = [
            {"stk_cd": "QQQ", "poss_qty": "000000000010",
             "sell_alowq": "000000000008", "frgn_stk_book_uv": "100.5000",
             "now_pric": "+150.2500", "evlt_amt": "1502.5000"},
            {"stk_cd": "SPY", "poss_qty": "000000000005",
             "sell_alowq": "000000000005", "frgn_stk_book_uv": "400.0000",
             "now_pric": "500.0000", "evlt_amt": "2500.0000"},
            {"stk_cd": "DUST", "poss_qty": "000000000000",
             "sell_alowq": "000000000000", "frgn_stk_book_uv": "1.0000",
             "now_pric": "1.0000", "evlt_amt": "0.0000"},
        ]
        with patch("kiwoom.us.request", return_value=_tr({"result_list": rows})) as m:
            out = us.get_us_balance()
        m.assert_called_once_with("ust21070", "/api/us/acnt", {"stex_tp": "", "stk_cd": ""})
        self.assertEqual([(b["code"], b["exchange"], b["qty"]) for b in out],
                         [("QQQ", "ND", 10), ("SPY", None, 5)])
        self.assertEqual(out[0]["sellable_qty"], 8)
        self.assertEqual(out[0]["avg_price"], 100.5)
        self.assertEqual(out[0]["last_price"], 150.25)
        self.assertEqual(out[0]["eval_amt"], 1502.5)

    def test_deposit_picks_usd_row(self):
        data = {"result_list": [
            {"crnc_code": "JPY", "fc_entra": "0.00",
             "fc_ord_alowa": "2902953435.06", "fc_pymn_alowa": "0.00"},
            {"crnc_code": "USD", "fc_entra": "18039493.57",
             "fc_ord_alowa": "18613792.11", "fc_pymn_alowa": "18039493.57"},
        ]}
        with patch("kiwoom.us.request", return_value=_tr(data)) as m:
            out = us.get_us_deposit()
        m.assert_called_once_with("ust21110", "/api/us/acnt", {})
        self.assertEqual(out, {"usd_cash": 18039493.57, "usd_orderable": 18613792.11,
                               "usd_withdrawable": 18039493.57})

    def test_deposit_without_usd_returns_zeros(self):
        with patch("kiwoom.us.request",
                   return_value=_tr({"result_list": [{"crnc_code": "JPY"}]})):
            out = us.get_us_deposit()
        self.assertEqual(out, {"usd_cash": 0, "usd_orderable": 0, "usd_withdrawable": 0})

    def test_fills_mapping(self):
        rows = [
            {"ord_no": "000000281", "stk_cd": "NVDA", "slby_tp": "2",
             "ord_qty": "000000000005", "cntr_qty": "000000000003",
             "cntr_uv": "213.0400", "ord_stat": "접수"},
            {"ord_no": "000000280", "stk_cd": "AAPL", "slby_tp": "1",
             "ord_qty": "000000000010", "cntr_qty": "000000000010",
             "cntr_uv": "275.2400", "ord_stat": "체결완료"},
            {"ord_no": "000000279", "stk_cd": "TSLA", "slby_tp": "9",
             "ord_qty": "000000000001", "cntr_qty": "000000000000",
             "cntr_uv": "0.0000", "ord_stat": ""},
        ]
        with patch("kiwoom.us.request", return_value=_tr({"result_list": rows})) as m:
            out = us.get_us_fills()
        m.assert_called_once_with("ust21510", "/api/us/acnt",
                                  {"slby_tp": "0", "stex_tp": "", "stk_cd": ""})
        self.assertEqual(out[0], {"order_no": "000000281", "code": "NVDA", "side": "buy",
                                  "qty": 5, "filled_qty": 3, "avg_fill_price": 213.04,
                                  "status": "접수"})
        self.assertEqual(out[1]["side"], "sell")
        self.assertIsNone(out[2]["side"])  # 매핑 불가 → None
        self.assertIsNone(out[2]["status"])

    def test_quote_mapping_and_cache(self):
        with patch("kiwoom.us.request", return_value=_tr(_quote_data())) as m:
            out = us.get_us_quote("QQQ")
        m.assert_called_once_with("usa20101", "/api/us/mrkcond",
                                  {"stex_tp": "ND", "stk_cd": "QQQ"})
        self.assertEqual(out, {"code": "QQQ", "exchange": "ND", "price": 150.25,
                               "bid1": 150.2, "ask1": 150.3, "volume": 1234567})
        self.assertEqual(us._EXCHANGE["QQQ"], "ND")

    def test_quote_negative_sign_to_positive(self):
        # 실측 2026-10-23: usa20101 cur_prc·호가는 전일 대비 부호("-705.14") → 절대값.
        data = _quote_data(price="-705.1400")
        data["buy_1bid"] = "-705.1000"
        data["sel_1bid"] = "-705.2000"
        with patch("kiwoom.us.request", return_value=_tr(data)):
            out = us.get_us_quote("QQQ")
        self.assertEqual(out["price"], 705.14)
        self.assertEqual(out["bid1"], 705.1)
        self.assertEqual(out["ask1"], 705.2)


class TestUsStrategyRoute(_UsOrdersTestBase):
    """POST /us/orders/strategy — 가드·dry_run·제출·거부."""

    def test_limit_buy_over_cap_422_no_order(self):
        # 지정가 매수도 현재가와 max로 상한 계산 → 현재가 1콜 후 거부, 주문 미전송.
        with patch("kiwoom.us.request",
                   return_value=_tr(_quote_data())) as m:
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "buy", "qty": 100, "trde_tp": "00",
                "price": 150.25, "source": "close_bet"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("MAX_US_ORDER_USD", r.json()["detail"])
        m.assert_called_once()
        self.assertEqual(m.call_args.args[0], "usa20101")

    def test_market_buy_over_cap_uses_quote(self):
        with patch("kiwoom.us.request",
                   return_value=_tr(_quote_data())) as m:
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "buy", "qty": 100, "source": "close_bet"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("MAX_US_ORDER_USD", r.json()["detail"])
        m.assert_called_once()  # 현재가 1콜 후 가드 거부 — 주문 미전송

    def test_sell_any_size_passes_and_records(self):
        with patch("kiwoom.us.request",
                   return_value=_tr({"ord_no": "000000282",
                                     "return_msg": "미국 매도주문 입력이 완료되었습니다."})) as m:
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "sell", "qty": 100000, "trde_tp": "00",
                "price": 150.25, "source": "close_bet"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "submitted")
        self.assertEqual(body["order_no"], "000000282")
        m.assert_called_once()
        self.assertEqual(m.call_args.args[0], "ust20001")
        rows = _trade_rows(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "QQQ")
        self.assertEqual(rows[0]["side"], "sell")
        self.assertEqual(rows[0]["order_type"], "US:00")
        self.assertEqual(rows[0]["price"], 150)
        self.assertEqual(rows[0]["status"], "submitted")

    def test_limit_buy_under_cap_orders_with_quote(self):
        # 지정가 매수도 현재가 조회 후 max로 상한 계산 → 2콜(현재가+주문).
        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            if api_id == "usa20101":
                return _tr(_quote_data())
            return _tr({"ord_no": "000000284", "return_msg": "ok"})

        with patch("kiwoom.us.request", side_effect=fake) as m:
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "buy", "qty": 1, "trde_tp": "00",
                "price": 150.25, "source": "close_bet"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(m.call_count, 2)
        self.assertEqual(m.call_args.args[0], "ust20000")

    def test_disallowed_source_422_before_wire(self):
        with patch.dict(os.environ, {"ALLOWED_ORDER_SOURCES": "close_bet"}):
            with patch("kiwoom.us.request") as m:
                r = self.client.post("/us/orders/strategy", json={
                    "code": "QQQ", "side": "buy", "qty": 1,
                    "price": 1.0, "source": "evil"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("허용되지 않음", r.json()["detail"])
        m.assert_not_called()

    def test_limit_without_price_422(self):
        with patch("kiwoom.us.request") as m:
            for trde_tp in ("00", "30"):
                r = self.client.post("/us/orders/strategy", json={
                    "code": "QQQ", "side": "buy", "qty": 1,
                    "trde_tp": trde_tp, "source": "close_bet"})
                self.assertEqual(r.status_code, 422, trde_tp)
        m.assert_not_called()

    def test_bad_trde_tp_422(self):
        with patch("kiwoom.us.request") as m:
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "buy", "qty": 1,
                "trde_tp": "99", "source": "close_bet"})
        self.assertEqual(r.status_code, 422)
        m.assert_not_called()

    def test_dry_run_makes_no_wire_calls(self):
        with patch("kiwoom.us.request") as m:
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "buy", "qty": 100, "source": "close_bet",
                "dry_run": True})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "dry_run")
        self.assertEqual(body["order_no"], "")
        m.assert_not_called()
        self.assertEqual(_trade_rows(self.db), [])

    def test_market_order_sends_empty_price(self):
        seen: list[dict] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            seen.append({"api_id": api_id, "body": body})
            if api_id == "usa20101":
                return _tr(_quote_data())
            return _tr({"ord_no": "000000283", "return_msg": "미국 매수주문입력 완료되었습니다"})

        with patch("kiwoom.us.request", side_effect=fake):
            r = self.client.post("/us/orders/strategy", json={
                "code": "qqq", "side": "buy", "qty": 1, "source": "close_bet"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "submitted")
        order = [c for c in seen if c["api_id"] == "ust20000"][0]["body"]
        self.assertEqual(order["ord_uv"], "")
        self.assertEqual(order["trde_tp"], "03")
        self.assertEqual(order["ord_qty"], "1")
        self.assertEqual(order["stk_cd"], "QQQ")  # 소문자 → 대문자

    def test_kiwoom_rejection_returns_rejected_200(self):
        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            if api_id == "usa20101":
                return _tr(_quote_data())
            raise KiwoomError("ust20000 return_code=1: 주문거부 수량부족")

        with patch("kiwoom.us.request", side_effect=fake):
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "buy", "qty": 1, "source": "close_bet"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(body["order_no"], "")
        self.assertIn("주문거부", body["message"])
        self.assertEqual(_trade_rows(self.db), [])

    def test_non_us_profile_422(self):
        # US 프로파일이 아니면 일반·dry_run 모두 422, wire 없음.
        for profile in ("", "HIGH52"):
            with patch("routers.us.load_config",
                       return_value=SimpleNamespace(profile=profile)):
                with patch("kiwoom.us.request") as m:
                    r = self.client.post("/us/orders/strategy", json={
                        "code": "QQQ", "side": "buy", "qty": 1,
                        "source": "close_bet"})
                    self.assertEqual(r.status_code, 422, profile)
                    self.assertIn("KIWOOM_PROFILE=US", r.json()["detail"])
                    r = self.client.post("/us/orders/strategy", json={
                        "code": "QQQ", "side": "buy", "qty": 1,
                        "source": "close_bet", "dry_run": True})
                    self.assertEqual(r.status_code, 422, f"dry_run {profile}")
                    self.assertIn("KIWOOM_PROFILE=US", r.json()["detail"])
                m.assert_not_called()

    def test_dry_run_still_validates_format(self):
        # dry_run이어도 형식 오류면 422, wire 없음.
        bad_bodies = [
            {"code": "QQQ", "side": "hold", "qty": 1},
            {"code": "QQQ", "side": "buy", "qty": 0},
            {"code": "QQQ", "side": "buy", "qty": -5},
            {"code": "QQQ!", "side": "buy", "qty": 1},
            {"code": "QQQ", "side": "buy", "qty": 1, "trde_tp": "99"},
            {"code": "QQQ", "side": "buy", "qty": 1, "trde_tp": "00"},
        ]
        with patch("kiwoom.us.request") as m:
            for body in bad_bodies:
                body.update({"source": "close_bet", "dry_run": True})
                r = self.client.post("/us/orders/strategy", json=body)
                self.assertEqual(r.status_code, 422, body)
        m.assert_not_called()

    def test_market_buy_ignores_request_price(self):
        # 시장가 매수는 요청 price=0.01을 무시하고 현재가(150.25)로 상한 계산.
        # 0.01*100=1달러면 통과할 주문이 150.25*100=15025달러로 422.
        with patch("kiwoom.us.request",
                   return_value=_tr(_quote_data())) as m:
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "buy", "qty": 100, "trde_tp": "03",
                "price": 0.01, "source": "close_bet"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("MAX_US_ORDER_USD", r.json()["detail"])
        m.assert_called_once()
        self.assertEqual(m.call_args.args[0], "usa20101")

    def test_limit_buy_low_price_uses_current(self):
        # 지정가 price(0.01)가 현재가(150.25)보다 낮으면 현재가로 상한 계산.
        # 0.01*100=1달러면 통과할 주문이 max→150.25*100=15025달러로 422.
        with patch("kiwoom.us.request",
                   return_value=_tr(_quote_data())) as m:
            r = self.client.post("/us/orders/strategy", json={
                "code": "QQQ", "side": "buy", "qty": 100, "trde_tp": "00",
                "price": 0.01, "source": "close_bet"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("MAX_US_ORDER_USD", r.json()["detail"])
        m.assert_called_once()
        self.assertEqual(m.call_args.args[0], "usa20101")

    def test_empty_max_env_defaults_to_5000(self):
        # MAX_US_ORDER_USD 빈 값이면 5000. 3005달러 통과·6010달러 거부로 확인.
        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            if api_id == "usa20101":
                return _tr(_quote_data())
            return _tr({"ord_no": "000000285", "return_msg": "ok"})

        with patch.dict(os.environ, {"MAX_US_ORDER_USD": ""}):
            with patch("kiwoom.us.request", side_effect=fake) as m:
                r = self.client.post("/us/orders/strategy", json={
                    "code": "QQQ", "side": "buy", "qty": 20,
                    "source": "close_bet"})  # 20*150.25=3005 < 5000
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "submitted")
            self.assertEqual(m.call_count, 2)  # 현재가+주문
            with patch("kiwoom.us.request",
                       return_value=_tr(_quote_data())) as m2:
                r = self.client.post("/us/orders/strategy", json={
                    "code": "QQQ", "side": "buy", "qty": 40,
                    "source": "close_bet"})  # 40*150.25=6010 > 5000
            self.assertEqual(r.status_code, 422)
            self.assertIn("MAX_US_ORDER_USD", r.json()["detail"])
            m2.assert_called_once()

    def test_bad_max_env_buy_422_sell_passes(self):
        # 상한값 오류면 매수는 422(설정 오류), 매도는 상한 파싱 없이 통과.
        for bad in ("abc", "inf", "-inf", "nan", "0", "-100", "0.0"):
            with patch.dict(os.environ, {"MAX_US_ORDER_USD": bad}):
                with patch("kiwoom.us.request") as m:
                    r = self.client.post("/us/orders/strategy", json={
                        "code": "QQQ", "side": "buy", "qty": 1,
                        "source": "close_bet"})
                self.assertEqual(r.status_code, 422, bad)
                self.assertIn("설정 오류", r.json()["detail"])
                m.assert_not_called()  # 상한 파싱 실패 → 현재가 조회도 없음
        with patch.dict(os.environ, {"MAX_US_ORDER_USD": "abc"}):
            with patch("kiwoom.us.request",
                       return_value=_tr({"ord_no": "000000286",
                                         "return_msg": "ok"})) as m:
                r = self.client.post("/us/orders/strategy", json={
                    "code": "QQQ", "side": "sell", "qty": 100000,
                    "trde_tp": "00", "price": 150.25, "source": "close_bet"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "submitted")
        m.assert_called_once()
        self.assertEqual(m.call_args.args[0], "ust20001")


class TestUsReadRoutes(_UsOrdersTestBase):
    """GET /us/quote·/balance·/deposit·/fills — 200 + 422/502 매핑."""

    def test_quote_ok(self):
        with patch("kiwoom.us.request", return_value=_tr(_quote_data())):
            r = self.client.get("/us/quote", params={"code": "qqq"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["code"], "QQQ")
        self.assertEqual(body["price"], 150.25)
        self.assertEqual(body["bid1"], 150.2)
        self.assertEqual(body["ask1"], 150.3)

    def test_quote_bad_code_422(self):
        with patch("kiwoom.us.request") as m:
            r = self.client.get("/us/quote", params={"code": "QQQ!"})
        self.assertEqual(r.status_code, 422)
        m.assert_not_called()

    def test_read_routes_map_kiwoom_error_to_502(self):
        with patch("kiwoom.us.request",
                   side_effect=KiwoomError("ust21070 return_code=1: 시스템 오류")):
            for path in ("/us/balance", "/us/deposit", "/us/fills"):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 502, path)

    def test_balance_route_single_call(self):
        rows = [{"stk_cd": "QQQ", "poss_qty": "000000000010",
                 "sell_alowq": "000000000010", "frgn_stk_book_uv": "100.5000",
                 "now_pric": "150.2500", "evlt_amt": "1502.5000"}]
        with patch("kiwoom.us.request", return_value=_tr({"result_list": rows})) as m:
            r = self.client.get("/us/balance")
        self.assertEqual(r.status_code, 200)
        m.assert_called_once_with("ust21070", "/api/us/acnt", {"stex_tp": "", "stk_cd": ""})
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["code"], "QQQ")

    def test_deposit_route(self):
        data = {"result_list": [{"crnc_code": "USD", "fc_entra": "100.00",
                                 "fc_ord_alowa": "90.00", "fc_pymn_alowa": "80.00"}]}
        with patch("kiwoom.us.request", return_value=_tr(data)):
            r = self.client.get("/us/deposit")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"usd_cash": 100.0, "usd_orderable": 90.0,
                                    "usd_withdrawable": 80.0})

    def test_fills_route(self):
        rows = [{"ord_no": "000000281", "stk_cd": "NVDA", "slby_tp": "2",
                 "ord_qty": "000000000005", "cntr_qty": "000000000000",
                 "cntr_uv": "0.0000", "ord_stat": "접수"}]
        with patch("kiwoom.us.request", return_value=_tr({"result_list": rows})):
            r = self.client.get("/us/fills")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["side"], "buy")


if __name__ == "__main__":
    unittest.main()
