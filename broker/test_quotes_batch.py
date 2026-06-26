"""G1: ka10095 복수종목 일괄시세 + /quotes 라우터 TTL 캐시 검증.

broker는 pytest가 없으므로 stdlib unittest + FastAPI TestClient로 돌린다.
    .venv/Scripts/python.exe -m unittest test_quotes_batch

키움 호출은 kiwoom.quotes.request를 mock해 HTTP 없이 정규화/분할/캐시만 검증한다.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiwoom import quotes
from kiwoom.client import TrResult


def _stk(code: str, cur: str = "+1000", buy: str = "+999") -> dict:
    return {
        "stk_cd": code, "cur_prc": cur, "buy_bid": buy, "sel_bid": "+1001",
        "flu_rt": "+28.68", "upl_pric": "+1300", "lst_pric": "-700",
        "exp_cntr_pric": "+1000", "pred_pre_sig": "2",
    }


class TestGetWatchlistQuotes(unittest.TestCase):
    """kiwoom.quotes.get_watchlist_quotes — | 조인 / 분할콜 / 정규화."""

    def test_joins_codes_with_pipe_single_call(self):
        calls: list[dict] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            calls.append({"api_id": api_id, "endpoint": endpoint, "body": dict(body)})
            return TrResult(data={"atn_stk_infr": [_stk("005930")]}, cont_yn="N", next_key="")

        with patch("kiwoom.quotes.request", side_effect=fake):
            rows = quotes.get_watchlist_quotes(["005930", "000660", "069500"])

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["api_id"], "ka10095")
        self.assertEqual(calls[0]["body"]["stk_cd"], "005930|000660|069500")
        self.assertEqual(len(rows), 1)

    def test_normalizes_fields(self):
        with patch("kiwoom.quotes.request",
                   return_value=TrResult(data={"atn_stk_infr": [_stk("A005930", "+156600", "+156500")]},
                                         cont_yn="N", next_key="")):
            rows = quotes.get_watchlist_quotes(["005930"])
        r = rows[0]
        self.assertEqual(r["stk_cd"], "005930")     # 'A' 접두 제거
        self.assertEqual(r["cur_prc"], 156600)       # abs int
        self.assertEqual(r["buy_bid"], 156500)
        self.assertEqual(r["lst_pric"], 700)         # 부호 제거
        self.assertEqual(r["flu_rt"], 28.68)         # float
        self.assertEqual(r["pred_pre_sig"], "2")     # 원본 문자열

    def test_splits_into_chunks(self):
        codes = [f"{i:06d}" for i in range(120)]  # 120종목, CHUNK=50 → 3콜
        calls: list[list[str]] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            chunk_codes = body["stk_cd"].split("|")
            calls.append(chunk_codes)
            return TrResult(data={"atn_stk_infr": [_stk(c) for c in chunk_codes]},
                            cont_yn="N", next_key="")

        with patch("kiwoom.quotes.request", side_effect=fake):
            rows = quotes.get_watchlist_quotes(codes)

        self.assertEqual([len(c) for c in calls], [50, 50, 20])
        self.assertEqual(len(rows), 120)

    def test_empty_codes_no_call(self):
        with patch("kiwoom.quotes.request") as m:
            rows = quotes.get_watchlist_quotes(["", "  "])
        self.assertEqual(rows, [])
        m.assert_not_called()

    def test_missing_list_returns_empty(self):
        with patch("kiwoom.quotes.request",
                   return_value=TrResult(data={}, cont_yn="N", next_key="")):
            rows = quotes.get_watchlist_quotes(["005930"])
        self.assertEqual(rows, [])


class TestGetQuote(unittest.TestCase):
    """kiwoom.quotes.get_quote — 단일종목 현재가. 부호 제거(절대값) 검증."""

    def test_strips_negative_sign(self):
        # ka10001 cur_prc 부호는 등락방향 — 절대값이 실가격(-4535 → 4535)
        with patch("kiwoom.quotes.request",
                   return_value=TrResult(data={"cur_prc": "-4535"}, cont_yn="N", next_key="")):
            q = quotes.get_quote("014940")
        self.assertEqual(q.price, 4535)

    def test_positive_sign(self):
        with patch("kiwoom.quotes.request",
                   return_value=TrResult(data={"cur_prc": "+156600"}, cont_yn="N", next_key="")):
            q = quotes.get_quote("005930")
        self.assertEqual(q.price, 156600)


class TestQuotesRouteCache(unittest.TestCase):
    """GET /quotes?codes= — TTL 캐시 히트/만료."""

    def setUp(self) -> None:
        from routers import quotes as quotes_router

        self.qr = quotes_router
        quotes_router._clear_cache()
        app = FastAPI()
        app.include_router(quotes_router.router)
        self.client = TestClient(app)

    def test_cache_hit_within_ttl(self):
        with patch("routers.quotes.quotes.get_watchlist_quotes",
                   return_value=[{"stk_cd": "005930"}]) as m:
            r1 = self.client.get("/quotes", params={"codes": "005930,000660"})
            r2 = self.client.get("/quotes", params={"codes": "000660,005930"})  # 순서 무관
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json(), r2.json())
        m.assert_called_once()  # 2요청 → 상류 1콜

    def test_cache_expires_after_ttl(self):
        t = [1000.0]
        with patch("routers.quotes.time.monotonic", side_effect=lambda: t[0]), \
             patch("routers.quotes.quotes.get_watchlist_quotes",
                   return_value=[{"stk_cd": "005930"}]) as m:
            self.client.get("/quotes", params={"codes": "005930"})
            t[0] += 3.0  # TTL(2s) 초과
            self.client.get("/quotes", params={"codes": "005930"})
        self.assertEqual(m.call_count, 2)


if __name__ == "__main__":
    unittest.main()
