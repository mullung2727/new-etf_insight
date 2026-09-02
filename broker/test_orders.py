"""Stage 1: place_order 성공 시 kiwoom_trade_history 기록 검증.

broker는 pytest가 없으므로 stdlib unittest + FastAPI TestClient로 돌린다.
    .venv/Scripts/python.exe -m unittest test_orders

kiwoom.orders.place_order(실제 키움 호출)를 mock해서 HTTP 없이 라우터 경로만 검증한다.
notes.db는 매 테스트마다 임시 파일로 교체한다.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from kiwoom.models import OrderResult
from notes import db as notes_db


def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    return Path(path)


def _trade_rows(db: Path) -> list[dict]:
    import sqlite3

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


class _OrdersTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _fresh_db()
        # notes.db 모듈 전역을 임시 파일로 교체하고 연결 초기화
        self._orig_path = notes_db.NOTES_DB_PATH
        self._orig_conn = notes_db._conn
        notes_db.NOTES_DB_PATH = self.db
        notes_db._conn = None
        notes_db.init()

        # 라우터를 포함한 최소 앱 (main.py 전체 lifespan/WS 기동 회피)
        from fastapi import FastAPI
        from routers import orders as orders_router

        app = FastAPI()
        app.include_router(orders_router.router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        if notes_db._conn is not None:
            notes_db._conn.close()
        notes_db.NOTES_DB_PATH = self._orig_path
        notes_db._conn = self._orig_conn
        self.db.unlink(missing_ok=True)


class TestPlaceOrderRecords(_OrdersTestBase):
    def test_records_trade_on_success(self):
        fake = OrderResult(accepted=True, order_no="0000050", message="", raw={"ord_no": "0000050"})
        with patch("routers.orders.orders.place_order", return_value=fake):
            resp = self.client.post(
                "/orders",
                json={"symbol": "069500", "side": "buy", "qty": 1,
                      "order_type": "market", "source": "close_bet"},
            )
        self.assertEqual(resp.status_code, 200)
        rows = _trade_rows(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["order_no"], "0000050")
        self.assertEqual(rows[0]["ticker"], "069500")
        self.assertEqual(rows[0]["side"], "buy")
        self.assertEqual(rows[0]["status"], "submitted")
        self.assertEqual(rows[0]["source"], "close_bet")

    def test_source_defaults_to_manual(self):
        fake = OrderResult(accepted=True, order_no="0000051", message="", raw={})
        with patch("routers.orders.orders.place_order", return_value=fake):
            resp = self.client.post(
                "/orders",
                json={"symbol": "005930", "side": "buy", "qty": 1, "order_type": "market"},
            )
        self.assertEqual(resp.status_code, 200)
        rows = _trade_rows(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "manual")

    def test_market_order_price_zero(self):
        fake = OrderResult(accepted=True, order_no="0000052", message="", raw={})
        with patch("routers.orders.orders.place_order", return_value=fake):
            self.client.post(
                "/orders",
                json={"symbol": "005930", "side": "buy", "qty": 1, "order_type": "market"},
            )
        rows = _trade_rows(self.db)
        self.assertEqual(rows[0]["price"], 0)

    def test_duplicate_order_no_ignored(self):
        fake = OrderResult(accepted=True, order_no="0000050", message="", raw={})
        with patch("routers.orders.orders.place_order", return_value=fake):
            for _ in range(2):
                self.client.post(
                    "/orders",
                    json={"symbol": "069500", "side": "buy", "qty": 1, "order_type": "market"},
                )
        rows = _trade_rows(self.db)
        self.assertEqual(len(rows), 1)

    def test_rejected_order_not_recorded(self):
        from kiwoom.guards import OrderRejected

        with patch("routers.orders.orders.place_order", side_effect=OrderRejected("한도 초과")):
            resp = self.client.post(
                "/orders",
                json={"symbol": "005930", "side": "buy", "qty": 99999, "order_type": "market"},
            )
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(_trade_rows(self.db), [])


class TestOrderRoutePolicy(_OrdersTestBase):
    """금액 상한 정책은 request source가 아니라 서버 라우트가 결정한다."""

    def test_manual_route_forces_cap_exemption_even_with_strategy_source(self):
        fake = OrderResult(accepted=True, order_no="0000901", message="", raw={})
        with patch("routers.orders.orders.place_order", return_value=fake) as submit:
            resp = self.client.post(
                "/orders",
                json={
                    "symbol": "161890", "side": "buy", "qty": 1,
                    "order_type": "market", "source": "close_bet",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(submit.call_args.kwargs["enforce_amount_cap"])

    def test_strategy_route_forces_cap_even_with_manual_source(self):
        fake = OrderResult(accepted=True, order_no="0000902", message="", raw={})
        with patch("routers.orders.orders.place_order", return_value=fake) as submit:
            resp = self.client.post(
                "/orders/strategy",
                json={
                    "symbol": "005930", "side": "buy", "qty": 1,
                    "order_type": "market", "source": "manual",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(submit.call_args.kwargs["enforce_amount_cap"])


class TestFriendlyOrderError(unittest.TestCase):
    """_friendly_order_error — 키움 원문에서 사람 메시지를 뽑고, '모의투자 장종료'류만
    장중 안내로 바꾼다. '모의투자' 글자만으로 다른 에러(매도가능수량 부족 등)를
    장중 안내로 덮어쓰던 버그 회귀 방지."""

    def _friendly(self, raw: str, env: str = "paper") -> str:
        from unittest.mock import patch

        from kiwoom.client import KiwoomError
        from routers import orders as orders_router

        with patch("routers.orders.get_current_env", return_value=env):
            return orders_router._friendly_order_error(KiwoomError(raw))

    def test_paper_market_closed_maps_to_hours_message(self):
        out = self._friendly("kt10001 return_code=20: [2000](RC4058:모의투자 장종료)")
        self.assertIn("장 중", out)

    def test_paper_insufficient_qty_surfaces_real_reason(self):
        # 이게 버그였다 — 장중인데 '장 중에만 가능'으로 가려졌음.
        out = self._friendly(
            "kt10001 return_code=20: [2000](800033:모의투자 매도가능수량이 부족합니다.)"
        )
        self.assertIn("매도가능수량", out)
        self.assertNotIn("장 중(09:00", out)

    def test_real_env_ignores_paper_branch(self):
        out = self._friendly(
            "kt10001 return_code=20: [2000](800033:모의투자 매도가능수량이 부족합니다.)",
            env="real",
        )
        self.assertIn("매도가능수량", out)

    def test_known_subcode_appends_hint(self):
        # 관측된 서브코드(800033)엔 원문 + 행동지침을 덧붙인다.
        out = self._friendly(
            "kt10001 return_code=20: [2000](800033:모의투자 매도가능수량이 부족합니다.)"
        )
        self.assertIn("매도가능수량", out)   # 원문 보존
        self.assertIn("T+2", out)            # 힌트 부착

    def test_unknown_subcode_no_hint(self):
        # 매핑 없는 코드는 원문만(추정 금지).
        out = self._friendly("kt10001 return_code=20: [2000](999999:알 수 없는 사유)")
        self.assertIn("알 수 없는 사유", out)
        self.assertNotIn("(", out.replace("알 수 없는 사유", ""))


class TestOrderHistoryWrapper(unittest.TestCase):
    """kiwoom.orders.get_order_history — kt00007 body + 연속조회 병합."""

    def test_body_fields_and_paging(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        calls: list[dict] = []

        def fake_request(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            calls.append({"api_id": api_id, "endpoint": endpoint, "body": dict(body),
                          "cont_yn": cont_yn, "next_key": next_key})
            if cont_yn == "N":
                return TrResult(data={"acnt_ord_cntr_prps_dtl": [{"ord_no": "0000050"}]},
                                cont_yn="Y", next_key="K2")
            return TrResult(data={"acnt_ord_cntr_prps_dtl": [{"ord_no": "0000051"}]},
                            cont_yn="N", next_key="")

        with patch("kiwoom.orders.request", side_effect=fake_request):
            rows = korders.get_order_history("20260615")

        self.assertEqual([r["ord_no"] for r in rows], ["0000050", "0000051"])
        body = calls[0]["body"]
        self.assertEqual(calls[0]["api_id"], "kt00007")
        self.assertEqual(body["ord_dt"], "20260615")
        self.assertEqual(body["qry_tp"], "4")
        self.assertEqual(body["stk_bond_tp"], "1")
        self.assertEqual(body["sell_tp"], "2")
        self.assertEqual(body["dmst_stex_tp"], "%")
        self.assertEqual(calls[1]["cont_yn"], "Y")
        self.assertEqual(calls[1]["next_key"], "K2")

    def test_empty_list_when_no_fills(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        with patch("kiwoom.orders.request",
                   return_value=TrResult(data={}, cont_yn="N", next_key="")):
            rows = korders.get_order_history("20260615")
        self.assertEqual(rows, [])

    def test_sell_tp_default_and_override(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        calls: list[dict] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            calls.append(dict(body))
            return TrResult(data={}, cont_yn="N", next_key="")

        with patch("kiwoom.orders.request", side_effect=fake):
            korders.get_order_history("20260615")              # 기본 매수
            korders.get_order_history("20260615", sell_tp="1")  # 매도
        self.assertEqual(calls[0]["sell_tp"], "2")
        self.assertEqual(calls[1]["sell_tp"], "1")


class TestUnfilledWrapper(unittest.TestCase):
    """kiwoom.orders.get_unfilled — ka10075 body + 연속조회 병합."""

    def test_body_fields_and_paging(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        calls: list[dict] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            calls.append({"api_id": api_id, "body": dict(body), "next_key": next_key})
            if cont_yn == "N":
                return TrResult(data={"oso": [{"ord_no": "0000070"}]}, cont_yn="Y", next_key="K2")
            return TrResult(data={"oso": [{"ord_no": "0000071"}]}, cont_yn="N", next_key="")

        with patch("kiwoom.orders.request", side_effect=fake):
            rows = korders.get_unfilled("sell")

        self.assertEqual([r["ord_no"] for r in rows], ["0000070", "0000071"])
        self.assertEqual(calls[0]["api_id"], "ka10075")
        self.assertEqual(calls[0]["body"]["trde_tp"], "1")   # 매도
        self.assertEqual(calls[0]["body"]["all_stk_tp"], "0")
        self.assertEqual(calls[1]["next_key"], "K2")

    def test_buy_side_trde_tp(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        seen: list[str] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            seen.append(body["trde_tp"])
            return TrResult(data={}, cont_yn="N", next_key="")

        with patch("kiwoom.orders.request", side_effect=fake):
            korders.get_unfilled("buy")
        self.assertEqual(seen, ["2"])

    def test_empty_oso(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        with patch("kiwoom.orders.request",
                   return_value=TrResult(data={}, cont_yn="N", next_key="")):
            self.assertEqual(korders.get_unfilled("sell"), [])


class TestOrderHistoryRoute(_OrdersTestBase):
    """GET /orders/history — 정규화(접두어 제거 + int 파싱)."""

    def test_normalizes_response(self):
        raw = [{
            "ord_no": "0000050", "stk_cd": "A069500",
            "cntr_qty": "0000000001", "cntr_uv": "0000012345",
            "ord_remnq": "0000000000",
        }]
        with patch("routers.orders.orders.get_order_history", return_value=raw):
            resp = self.client.get("/orders/history", params={"date": "20260615"})
        self.assertEqual(resp.status_code, 200)
        item = resp.json()[0]
        self.assertEqual(item["order_no"], "0000050")
        self.assertEqual(item["ticker"], "069500")
        self.assertEqual(item["cntr_qty"], 1)
        self.assertEqual(item["cntr_uv"], 12345)
        self.assertEqual(item["ord_remnq"], 0)

    def test_passes_date_through(self):
        seen: list[str] = []

        def fake(date, sell_tp="2"):
            seen.append((date, sell_tp))
            return []

        with patch("routers.orders.orders.get_order_history", side_effect=fake):
            self.client.get("/orders/history", params={"date": "20260601"})
            self.client.get("/orders/history", params={"date": "20260601", "side": "sell"})
        self.assertEqual(seen, [("20260601", "2"), ("20260601", "1")])


class TestUnfilledRoute(_OrdersTestBase):
    """GET /orders/unfilled — 정규화(접두 제거 + int 파싱)."""

    def test_normalizes_response(self):
        raw = [{"ord_no": "0000070", "stk_cd": "A005930", "stk_nm": "삼성전자",
                "ord_qty": "0000000010", "ord_pric": "0000070000",
                "oso_qty": "0000000001", "ord_stt": "접수",
                "io_tp_nm": "+매수", "tm": "093015"}]
        with patch("routers.orders.orders.get_unfilled", return_value=raw):
            resp = self.client.get("/orders/unfilled", params={"side": "sell"})
        self.assertEqual(resp.status_code, 200)
        item = resp.json()[0]
        self.assertEqual(item["order_no"], "0000070")
        self.assertEqual(item["ticker"], "005930")
        self.assertEqual(item["stk_nm"], "삼성전자")
        self.assertEqual(item["ord_qty"], 10)
        self.assertEqual(item["ord_price"], 70000)
        self.assertEqual(item["oso_qty"], 1)
        self.assertEqual(item["ord_stt"], "접수")
        self.assertEqual(item["io_tp_nm"], "+매수")
        self.assertEqual(item["tm"], "093015")

    def test_passes_side(self):
        seen: list[str] = []
        with patch("routers.orders.orders.get_unfilled",
                   side_effect=lambda side: seen.append(side) or []):
            self.client.get("/orders/unfilled", params={"side": "sell"})
        self.assertEqual(seen, ["sell"])


class TestModifyWrapper(unittest.TestCase):
    """kiwoom.orders.modify_order — kt10002 body."""

    def test_body_fields(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        calls: list[dict] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            calls.append({"api_id": api_id, "body": dict(body)})
            return TrResult(data={"ord_no": "0000099"}, cont_yn="N", next_key="")

        with patch("kiwoom.orders.request", side_effect=fake):
            res = korders.modify_order("0000070", "005930", 71000, qty=5)

        self.assertEqual(res.order_no, "0000099")
        self.assertEqual(calls[0]["api_id"], "kt10002")
        b = calls[0]["body"]
        self.assertEqual(b["orig_ord_no"], "0000070")
        self.assertEqual(b["mdfy_uv"], "71000")
        self.assertEqual(b["mdfy_qty"], "5")

    def test_qty_zero_full(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        seen: list[str] = []
        with patch("kiwoom.orders.request",
                   side_effect=lambda *a, **k: seen.append(a[2]["mdfy_qty"]) or
                   TrResult(data={"ord_no": "1"}, cont_yn="N", next_key="")):
            korders.modify_order("0000070", "005930", 71000)
        self.assertEqual(seen, ["0"])


class TestModifyRoute(_OrdersTestBase):
    """PATCH /orders/{order_no} — modify_order 위임 + 친화 에러."""

    def test_delegates(self):
        fake = OrderResult(accepted=True, order_no="0000099", message="", raw={})
        seen: list[tuple] = []
        with patch("routers.orders.orders.modify_order",
                   side_effect=lambda *a: seen.append(a) or fake):
            resp = self.client.patch("/orders/0000070",
                                     json={"symbol": "005930", "price": 71000, "qty": 5})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["order_no"], "0000099")
        self.assertEqual(seen[0], ("0000070", "005930", 71000, 5))

    def test_kiwoom_error_422(self):
        from kiwoom.client import KiwoomError
        with patch("routers.orders.orders.modify_order",
                   side_effect=KiwoomError("kt10002 return_code=1: 장 종료")):
            resp = self.client.patch("/orders/0000070",
                                     json={"symbol": "005930", "price": 71000})
        self.assertEqual(resp.status_code, 422)


class TestRealizedWrapper(unittest.TestCase):
    """kiwoom.orders.get_today_realized — ka10077 body + 연속조회 병합."""

    def test_body_and_paging(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        calls: list[dict] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            calls.append({"api_id": api_id, "body": dict(body), "next_key": next_key})
            if cont_yn == "N":
                return TrResult(data={"tdy_rlzt_pl_dtl": [{"stk_cd": "A005930"}]},
                                cont_yn="Y", next_key="K2")
            return TrResult(data={"tdy_rlzt_pl_dtl": [{"stk_cd": "A005930"}]},
                            cont_yn="N", next_key="")

        with patch("kiwoom.orders.request", side_effect=fake):
            rows = korders.get_today_realized("005930")

        self.assertEqual(len(rows), 2)
        self.assertEqual(calls[0]["api_id"], "ka10077")
        self.assertEqual(calls[0]["body"]["stk_cd"], "005930")
        self.assertEqual(calls[1]["next_key"], "K2")


class TestRealizedByDateWrapper(unittest.TestCase):
    """kiwoom.orders.get_realized_by_date — ka10072 body(strt_dt) + 병합."""

    def test_body_and_paging(self):
        from kiwoom import orders as korders
        from kiwoom.client import TrResult

        calls: list[dict] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            calls.append({"api_id": api_id, "body": dict(body), "next_key": next_key})
            if cont_yn == "N":
                return TrResult(data={"dt_stk_div_rlzt_pl": [{"stk_cd": "A005930"}]},
                                cont_yn="Y", next_key="K2")
            return TrResult(data={"dt_stk_div_rlzt_pl": [{"stk_cd": "A005930"}]},
                            cont_yn="N", next_key="")

        with patch("kiwoom.orders.request", side_effect=fake):
            rows = korders.get_realized_by_date("005930", "20260623")

        self.assertEqual(len(rows), 2)
        self.assertEqual(calls[0]["api_id"], "ka10072")
        self.assertEqual(calls[0]["body"]["stk_cd"], "005930")
        self.assertEqual(calls[0]["body"]["strt_dt"], "20260623")
        self.assertEqual(calls[1]["next_key"], "K2")


class TestRealizedRoute(_OrdersTestBase):
    """GET /orders/realized/{ticker} — net 손익율·수수료·세금·손익금 정규화."""

    def test_date_param_routes_to_ka10072(self):
        raw = [{
            "stk_cd": "A005930", "cntr_qty": "1", "buy_uv": "1000",
            "tdy_sel_pl": "-569", "pl_rt": "-5.67",
            "tdy_trde_cmsn": "60", "tdy_trde_tax": "19",
        }]
        with patch("routers.orders.orders.get_realized_by_date", return_value=raw) as by_date, \
             patch("routers.orders.orders.get_today_realized") as today:
            body = self.client.get("/orders/realized/005930?date=20260623").json()
        by_date.assert_called_once_with("005930", "20260623")
        today.assert_not_called()
        self.assertEqual(body["pnl_pct"], -5.67)
        self.assertEqual(body["cmsn"], 60)
        self.assertEqual(body["tax"], 19)

    def test_single_row_uses_pl_rt(self):
        raw = [{
            "stk_cd": "A005930", "cntr_qty": "0000000010", "buy_uv": "0000001000",
            "tdy_sel_pl": "0000048500", "pl_rt": "+4.85",
            "tdy_trde_cmsn": "0000000150", "tdy_trde_tax": "0000001350",
        }]
        with patch("routers.orders.orders.get_today_realized", return_value=raw):
            resp = self.client.get("/orders/realized/005930")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["ticker"], "005930")
        self.assertTrue(body["found"])
        self.assertEqual(body["pnl_pct"], 4.85)
        self.assertEqual(body["sel_pl_won"], 48500)
        self.assertEqual(body["cmsn"], 150)
        self.assertEqual(body["tax"], 1350)
        self.assertEqual(body["qty"], 10)

    def test_negative_pl(self):
        raw = [{
            "stk_cd": "A005930", "cntr_qty": "0000000010", "buy_uv": "0000001000",
            "tdy_sel_pl": "-0000031500", "pl_rt": "-3.15",
            "tdy_trde_cmsn": "0000000150", "tdy_trde_tax": "0000001350",
        }]
        with patch("routers.orders.orders.get_today_realized", return_value=raw):
            body = self.client.get("/orders/realized/005930").json()
        self.assertEqual(body["pnl_pct"], -3.15)
        self.assertEqual(body["sel_pl_won"], -31500)

    def test_multi_row_recomputes_pct(self):
        # 같은 종목 2건: 손익금 합산, 손익율은 원가기준 재계산
        raw = [
            {"stk_cd": "A005930", "cntr_qty": "5", "buy_uv": "1000",
             "tdy_sel_pl": "2500", "pl_rt": "+5.00",
             "tdy_trde_cmsn": "10", "tdy_trde_tax": "90"},
            {"stk_cd": "A005930", "cntr_qty": "5", "buy_uv": "1000",
             "tdy_sel_pl": "-1500", "pl_rt": "-3.00",
             "tdy_trde_cmsn": "10", "tdy_trde_tax": "90"},
        ]
        with patch("routers.orders.orders.get_today_realized", return_value=raw):
            body = self.client.get("/orders/realized/005930").json()
        self.assertEqual(body["sel_pl_won"], 1000)
        self.assertEqual(body["cmsn"], 20)
        self.assertEqual(body["tax"], 180)
        # cost = 5*1000 + 5*1000 = 10000, pl 1000 → 10.0%
        self.assertEqual(body["pnl_pct"], 10.0)

    def test_empty_not_found(self):
        with patch("routers.orders.orders.get_today_realized", return_value=[]):
            body = self.client.get("/orders/realized/005930").json()
        self.assertFalse(body["found"])
        self.assertEqual(body["pnl_pct"], 0.0)
        self.assertEqual(body["sel_pl_won"], 0)


class TestMarketOrderGuard(unittest.TestCase):
    """시장가 주문 금액 상한 — 매수만 현재가로 예상금액을 검사하고, 매도는 면제한다.

    매도까지 상한을 걸면 급등 종목의 손절/강제청산이 거부돼 포지션이 갇힌다.
    매도 수량 초과는 키움이 800033으로 거부하므로 가드가 중복으로 막을 필요가 없다.
    """

    MAX = 150_000

    def _cfg(self):
        from kiwoom.config import Config

        return Config(
            appkey="k", secretkey="s", env="paper",
            rest_host="https://mockapi.kiwoom.com", ws_host="wss://x",
            account_no="1", max_order_amount=self.MAX,
            token_cache_path=Path("/tmp/none.json"),
        )

    def _check(self, **kwargs):
        from kiwoom.guards import check_order

        base = {
            "qty": 1,
            "price": 0,
            "market": True,
            "side": "buy",
            "est_price": None,
            "enforce_amount_cap": True,
        }
        base.update(kwargs)
        check_order(self._cfg(), **base)

    def test_market_buy_over_cap_rejected(self):
        from kiwoom.guards import OrderRejected

        # 10,000원 × 20주 = 200,000 > 150,000
        with self.assertRaises(OrderRejected):
            self._check(qty=20, est_price=10_000)

    def test_market_buy_within_cap_passes(self):
        # 10,000원 × 10주 = 100,000 ≤ 150,000
        self._check(qty=10, est_price=10_000)

    def test_market_buy_without_price_rejected(self):
        from kiwoom.guards import OrderRejected

        with self.assertRaises(OrderRejected) as ctx:
            self._check(qty=1, est_price=None)
        self.assertIn("현재가", str(ctx.exception))

    def test_market_sell_exempt_from_cap(self):
        # 매도는 상한 초과 금액이어도 통과해야 한다(청산 차단 방지).
        self._check(qty=999, side="sell", est_price=10_000)

    def test_limit_sell_still_requires_positive_price(self):
        from kiwoom.guards import OrderRejected

        with self.assertRaises(OrderRejected):
            self._check(side="sell", market=False, price=0)

    def test_sell_zero_qty_rejected(self):
        from kiwoom.guards import OrderRejected

        with self.assertRaises(OrderRejected):
            self._check(qty=0, side="sell")


    def test_limit_cap_unchanged(self):
        from kiwoom.guards import OrderRejected

        self._check(qty=10, price=10_000, market=False)          # 100,000 ≤ 상한
        with self.assertRaises(OrderRejected):
            self._check(qty=20, price=10_000, market=False)      # 200,000 > 상한

    def test_manual_market_buy_skips_amount_cap_and_estimated_price(self):
        self._check(qty=1, est_price=None, enforce_amount_cap=False)

    def test_manual_limit_buy_skips_amount_cap_but_validates_price(self):
        from kiwoom.guards import OrderRejected

        self._check(qty=20, price=10_000, market=False, enforce_amount_cap=False)
        with self.assertRaises(OrderRejected):
            self._check(price=0, market=False, enforce_amount_cap=False)

    def test_manual_market_order_skips_quote_lookup(self):
        """수동 시장가는 상한 계산용 quote 장애와 무관하게 주문선까지 도달한다."""
        from kiwoom import orders as kiwoom_orders
        from kiwoom.client import TrResult
        from kiwoom.models import OrderRequest

        req = OrderRequest(
            symbol="161890", side="buy", qty=1,
            order_type="market", source="manual",
        )
        wire_result = TrResult(
            data={"ord_no": "0000001", "return_msg": "ok"},
            cont_yn="N", next_key="",
        )
        with patch("kiwoom.orders._config", return_value=self._cfg()), \
             patch("kiwoom.orders.quotes.get_quote") as quote, \
             patch("kiwoom.orders.request", return_value=wire_result) as wire:
            result = kiwoom_orders.place_order(req, enforce_amount_cap=False)

        quote.assert_not_called()
        wire.assert_called_once()
        self.assertTrue(result.accepted)

    def test_enforced_route_cannot_be_bypassed_by_manual_source(self):
        """자동매매 라우트는 body source가 manual이어도 상한을 유지한다."""
        from kiwoom import orders as kiwoom_orders
        from kiwoom.guards import OrderRejected
        from kiwoom.models import OrderRequest

        req = OrderRequest(
            symbol="005930", side="buy", qty=20,
            order_type="market", source="manual",
        )
        with patch("kiwoom.orders._config", return_value=self._cfg()), \
             patch("kiwoom.orders.quotes.get_quote") as quote, \
             patch("kiwoom.orders.request") as wire:
            quote.return_value.price = 10_000
            with self.assertRaises(OrderRejected):
                kiwoom_orders.place_order(req, enforce_amount_cap=True)

        quote.assert_called_once_with("005930")
        wire.assert_not_called()

    def test_quote_failure_rejects_before_wire(self):
        """자동 시장가의 현재가 조회가 실패하면 주문을 보내지 않고 거부한다."""
        from kiwoom import orders as kiwoom_orders
        from kiwoom.guards import OrderRejected
        from kiwoom.models import OrderRequest

        req = OrderRequest(
            symbol="005930", side="buy", qty=1,
            order_type="market", source="close_bet",
        )
        with patch("kiwoom.orders._config", return_value=self._cfg()), \
             patch("kiwoom.orders.quotes.get_quote", side_effect=RuntimeError("boom")), \
             patch("kiwoom.orders.request") as wire:
            with self.assertRaises(OrderRejected):
                kiwoom_orders.place_order(req, enforce_amount_cap=True)
        wire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
