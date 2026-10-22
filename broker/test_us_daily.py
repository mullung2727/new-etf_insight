"""P1: 미국 일봉 (usa06012) + /us/daily 라우터 검증.

broker는 pytest가 없으므로 stdlib unittest + FastAPI TestClient로 돌린다.
    .venv/Scripts/python.exe -m unittest test_us_daily

키움 호출은 kiwoom.us.request를 mock해 HTTP 없이 거래소 선택/페이징/정규화만 검증한다.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiwoom import us
from kiwoom.client import KiwoomError, TrResult
from routers import us as us_router


def _bar(dt: str, close: str = "100.5", qty: str = "1234567") -> dict:
    return {
        "dt": dt, "open_pric": "+99.10", "high_pric": "101.20",
        "low_pric": "98.70", "cur_prc": close, "acc_trde_qty": qty,
    }


class TestGetUsDaily(unittest.TestCase):
    """kiwoom.us.get_us_daily — 거래소 선택·캐시·필터·정규화."""

    def setUp(self) -> None:
        us._EXCHANGE.clear()

    def test_empty_nd_falls_through_to_ny_and_caches(self):
        seen: list[str] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            seen.append(body["stex_tp"])
            if body["stex_tp"] == "ND":
                return TrResult(data={"result_list": []}, cont_yn="N", next_key="")
            return TrResult(data={"result_list": [_bar("20261021")]}, cont_yn="N", next_key="")

        with patch("kiwoom.us.request", side_effect=fake):
            rows = us.get_us_daily("QQQ", "20261013", "20261022")
            self.assertEqual(seen, ["ND", "NY"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(us._EXCHANGE["QQQ"], "NY")
            # 두 번째 호출부터 NY 먼저 — 1콜로 끝
            seen.clear()
            us.get_us_daily("QQQ", "20261013", "20261022")
            self.assertEqual(seen, ["NY"])

    def test_1903_nd_falls_through_to_ny_and_caches(self):
        seen: list[str] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            seen.append(body["stex_tp"])
            if body["stex_tp"] == "ND":
                raise KiwoomError(
                    "usa06012 return_code=7: [1903:종목 정보가 없습니다 "
                    "거래소구분=ND, 종목코드=SPY]")
            return TrResult(data={"result_list": [_bar("20261021")]}, cont_yn="N", next_key="")

        with patch("kiwoom.us.request", side_effect=fake):
            rows = us.get_us_daily("SPY", "20261013", "20261022")
            self.assertEqual(seen, ["ND", "NY"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(us._EXCHANGE["SPY"], "NY")
            # 두 번째 호출부터 NY 먼저 — 1콜로 끝
            seen.clear()
            us.get_us_daily("SPY", "20261013", "20261022")
            self.assertEqual(seen, ["NY"])

    def test_non_1903_kiwoom_error_propagates(self):
        with patch("kiwoom.us.request",
                   side_effect=KiwoomError("usa06012 return_code=1: 시스템 오류")):
            with self.assertRaises(KiwoomError):
                us.get_us_daily("SPY", "20261013", "20261022")
        self.assertNotIn("SPY", us._EXCHANGE)

    def test_filters_today_and_before_from_ascending_numbers(self):
        bars = [  # 응답은 최신일 내림차순
            _bar("20261022", "200.00"),   # 뉴욕 당일 진행 봉 → 제외
            _bar("20261021", "+1.3212"),
            _bar("20261020", "201.3612", ""),
            _bar("20261012", "148.00"),   # from 이전 → 제외
            {"dt": "20261019", "open_pric": "", "high_pric": "",
             "low_pric": "", "cur_prc": "", "acc_trde_qty": ""},  # 종가 없음 → 제외
        ]
        with patch("kiwoom.us.request",
                   return_value=TrResult(data={"result_list": bars}, cont_yn="N", next_key="")):
            rows = us.get_us_daily("QQQ", "20261013", "20261022")
        self.assertEqual([r["date"] for r in rows], ["20261020", "20261021"])  # 오름차순
        self.assertEqual(rows[1]["close"], 1.3212)  # "+1.3212" 부호 문자열
        self.assertEqual(rows[1]["open"], 99.10)
        self.assertEqual(rows[1]["volume"], 1234567)
        self.assertEqual(rows[0]["close"], 201.3612)
        self.assertIsNone(rows[0]["volume"])  # 빈 거래량 → None

    def test_all_exchanges_empty_returns_empty(self):
        with patch("kiwoom.us.request",
                   return_value=TrResult(data={"result_list": []}, cont_yn="N", next_key="")) as m:
            rows = us.get_us_daily("QQQ", "20261013", "20261022")
        self.assertEqual(rows, [])
        self.assertEqual(m.call_count, 3)  # ND·NY·NA 전부 조회
        self.assertNotIn("QQQ", us._EXCHANGE)


class TestUsDailyPaging(unittest.TestCase):
    """cont_yn 연속조회 — from에 닿으면 멈춤."""

    def setUp(self) -> None:
        us._EXCHANGE.clear()

    def test_follows_next_key_until_from(self):
        calls: list[dict] = []

        def fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
            calls.append({"cont_yn": cont_yn, "next_key": next_key})
            if not next_key:
                return TrResult(data={"result_list": [_bar("20261021"), _bar("20261020")]},
                                cont_yn="Y", next_key="page2")
            return TrResult(data={"result_list": [_bar("20261019"), _bar("20261012")]},
                            cont_yn="N", next_key="")

        with patch("kiwoom.us.request", side_effect=fake):
            rows = us.get_us_daily("QQQ", "20261013", "20261022")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], {"cont_yn": "Y", "next_key": "page2"})
        self.assertEqual([r["date"] for r in rows], ["20261019", "20261020", "20261021"])

    def test_stops_when_oldest_reaches_from(self):
        with patch("kiwoom.us.request",
                   return_value=TrResult(data={"result_list": [_bar("20261021"), _bar("20261013")]},
                                         cont_yn="Y", next_key="page2")) as m:
            rows = us.get_us_daily("QQQ", "20261013", "20261022")
        m.assert_called_once()  # 가장 오래된 날 == from → 추가 페이지 없음
        self.assertEqual([r["date"] for r in rows], ["20261013", "20261021"])


class TestUsDailyRoute(unittest.TestCase):
    """GET /us/daily — 200 + 리스트, 형식 오류 422."""

    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(us_router.router)
        self.client = TestClient(app)

    def test_daily_ok(self):
        expected = [{"date": "20261021", "close": 150.25}]
        with patch("routers.us.us.get_us_daily", return_value=expected) as m:
            r = self.client.get("/us/daily", params={"code": "QQQ", "from": "20261013",
                                                     "today_ny": "20261022"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), expected)
        m.assert_called_once_with("QQQ", "20261013", "20261022")

    def test_lowercase_code_uppercased(self):
        with patch("routers.us.us.get_us_daily", return_value=[]) as m:
            r = self.client.get("/us/daily", params={"code": "qqq", "from": "20261013",
                                                     "today_ny": "20261022"})
        self.assertEqual(r.status_code, 200)
        m.assert_called_once_with("QQQ", "20261013", "20261022")

    def test_bad_from_422(self):
        with patch("routers.us.us.get_us_daily") as m:
            r = self.client.get("/us/daily", params={"code": "QQQ", "from": "2026",
                                                     "today_ny": "20261022"})
        self.assertEqual(r.status_code, 422)
        m.assert_not_called()

    def test_bad_code_422(self):
        with patch("routers.us.us.get_us_daily") as m:
            r = self.client.get("/us/daily", params={"code": "QQQ!", "from": "20261013",
                                                     "today_ny": "20261022"})
        self.assertEqual(r.status_code, 422)
        m.assert_not_called()


class TestCutoff(unittest.TestCase):
    """라우터 _cutoff — 뉴욕 16:30 이후면 내일 날짜."""

    def test_after_session_done_returns_tomorrow(self):
        now = datetime(2026, 10, 22, 17, 30, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(us_router._cutoff(now), "20261023")

    def test_before_session_done_returns_today(self):
        now = datetime(2026, 10, 22, 15, 59, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(us_router._cutoff(now), "20261022")


if __name__ == "__main__":
    unittest.main()
