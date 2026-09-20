"""ka10100 종목정보 조회 — kiwoom.quotes.get_stock_status + GET /quotes/{symbol}/status.

    .venv/Scripts/python.exe -m unittest test_stock_status

키움 호출은 kiwoom.quotes.request 를 mock 해서 HTTP 없이 필드 정리만 검증한다.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiwoom import quotes
from kiwoom.client import TrResult
from routers import quotes as quotes_router

_DATA = {
    "code": "A123450", "name": "테스트", "listCount": "0000000026034239", "auditInfo": "관리종목",
    "regDay": "20090803", "lastPrice": "-00001360", "state": "관리종목|증거금100%", "marketCode": "10",
    "marketName": "코스닥", "orderWarning": "2", "return_code": 0,
}


def _fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
    _fake.calls.append((api_id, endpoint, dict(body)))
    return TrResult(data=_DATA, cont_yn="N", next_key="")


class TestGetStockStatus(unittest.TestCase):
    def setUp(self):
        _fake.calls = []

    def test_calls_ka10100_on_stkinfo(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            quotes.get_stock_status("123450")
        self.assertEqual(_fake.calls, [("ka10100", "/api/dostk/stkinfo", {"stk_cd": "123450"})])

    def test_normalizes_fields(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            r = quotes.get_stock_status("123450")
        self.assertEqual(r["code"], "123450")            # 'A' 접두 제거
        self.assertEqual(r["audit_info"], "관리종목")
        self.assertEqual(r["order_warning"], "2")        # 정리매매
        self.assertEqual(r["state"], "관리종목|증거금100%")
        self.assertEqual(r["last_price"], 1360)          # 부호·0 채움 제거
        self.assertEqual(r["reg_day"], "20090803")

    def test_router(self):
        app = FastAPI()
        app.include_router(quotes_router.router)
        with patch("kiwoom.quotes.request", side_effect=_fake):
            res = TestClient(app).get("/quotes/123450/status")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["audit_info"], "관리종목")


if __name__ == "__main__":
    unittest.main()
