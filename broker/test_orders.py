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
        raw = [{"ord_no": "0000070", "stk_cd": "A005930",
                "oso_qty": "0000000001", "ord_stt": "접수"}]
        with patch("routers.orders.orders.get_unfilled", return_value=raw):
            resp = self.client.get("/orders/unfilled", params={"side": "sell"})
        self.assertEqual(resp.status_code, 200)
        item = resp.json()[0]
        self.assertEqual(item["order_no"], "0000070")
        self.assertEqual(item["ticker"], "005930")
        self.assertEqual(item["oso_qty"], 1)
        self.assertEqual(item["ord_stt"], "접수")

    def test_passes_side(self):
        seen: list[str] = []
        with patch("routers.orders.orders.get_unfilled",
                   side_effect=lambda side: seen.append(side) or []):
            self.client.get("/orders/unfilled", params={"side": "sell"})
        self.assertEqual(seen, ["sell"])


if __name__ == "__main__":
    unittest.main()
