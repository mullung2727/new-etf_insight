"""ka10061 투자자 순매수 합계 + ka90001 종목 소속 테마.

    .venv/Scripts/python.exe -m unittest test_investor_theme test_stock_status test_quotes_batch

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

_INVESTOR_ROW = {
    "ind_invsr": "--28837", "frgnr_invsr": "--40142", "orgn": "+64891",
    "fnnc_invt": "+72584", "insrnc": "--9071", "invtrt": "--7790",
    "etc_fnnc": "+35307", "bank": "+526", "penfnd_etc": "--22783",
    "samo_fund": "--3881", "natn": "0", "etc_corp": "+1974", "natfor": "+2114",
}
_INVESTOR_DATA = {"stk_invsr_orgn_tot": [_INVESTOR_ROW], "return_code": 0}

_THEME_ROW = {
    "thema_grp_cd": "319", "thema_nm": "건강식품", "stk_num": "5", "flu_sig": "2",
    "flu_rt": "+0.02", "rising_stk_num": "1", "fall_stk_num": "0",
    "dt_prft_rt": "+157.80", "main_stk": "삼성전자",
}
_THEME_DATA = {"thema_grp": [_THEME_ROW], "return_code": 0}

_STATUS_DATA = {
    "code": "A005930", "name": "삼성전자", "auditInfo": "정상", "regDay": "19750611",
    "lastPrice": "+1200", "state": "정상", "marketName": "코스피", "orderWarning": "",
    "return_code": 0,
}

_INVESTOR_FIELDS = (
    "ind_invsr", "frgnr_invsr", "orgn", "fnnc_invt", "insrnc", "invtrt",
    "etc_fnnc", "bank", "penfnd_etc", "samo_fund", "natn", "etc_corp", "natfor",
)


def _fake(api_id, endpoint, body, *, cont_yn="N", next_key=""):
    _fake.calls.append((api_id, endpoint, dict(body)))
    data = {
        "ka10061": _INVESTOR_DATA,
        "ka90001": _THEME_DATA,
        "ka10100": _STATUS_DATA,
    }[api_id]
    return TrResult(data=data, cont_yn="N", next_key="")


class TestSignedInt(unittest.TestCase):
    def test_cases(self):
        cases = [
            ("--28837", -28837), ("-5", -5), ("+64891", 64891), ("0", 0),
            ("1,234", 1234), ("", None), (None, None), ("abc", None),
        ]
        for val, want in cases:
            with self.subTest(val=val):
                self.assertEqual(quotes._signed_int(val), want)


class TestGetInvestorSum(unittest.TestCase):
    def setUp(self):
        _fake.calls = []

    def test_call_args(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            quotes.get_investor_sum("005930", "20260901", "20260905")
        self.assertEqual(_fake.calls, [(
            "ka10061", "/api/dostk/stkinfo",
            {"stk_cd": "005930", "strt_dt": "20260901", "end_dt": "20260905",
             "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"},
        )])

    def test_parses_spec_example(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            r = quotes.get_investor_sum("005930", "20260901", "20260905")
        self.assertEqual(r["frgnr_invsr"], -40142)
        self.assertEqual(r["orgn"], 64891)
        self.assertEqual(r["natn"], 0)
        self.assertEqual(r["symbol"], "005930")

    def test_empty_array_gives_nones(self):
        empty = TrResult(data={"stk_invsr_orgn_tot": [], "return_code": 0},
                         cont_yn="N", next_key="")
        with patch("kiwoom.quotes.request", return_value=empty):
            r = quotes.get_investor_sum("005930", "20260901", "20260905")
        for k in _INVESTOR_FIELDS:
            self.assertIsNone(r[k])


class TestGetStockThemes(unittest.TestCase):
    def setUp(self):
        _fake.calls = []

    def test_call_args(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            quotes.get_stock_themes("005930")
        self.assertEqual(_fake.calls, [(
            "ka90001", "/api/dostk/thme",
            {"qry_tp": "2", "stk_cd": "005930", "date_tp": "5",
             "thema_nm": "", "flu_pl_amt_tp": "1", "stex_tp": "1"},
        )])

    def test_parses_spec_example(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            r = quotes.get_stock_themes("005930")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["name"], "건강식품")
        self.assertEqual(r[0]["period_return"], 157.8)
        self.assertEqual(r[0]["rising"], 1)
        self.assertEqual(r[0]["falling"], 0)
        self.assertEqual(r[0]["stk_num"], 5)

    def test_empty_gives_empty_list(self):
        empty = TrResult(data={"thema_grp": [], "return_code": 0},
                         cont_yn="N", next_key="")
        with patch("kiwoom.quotes.request", return_value=empty):
            self.assertEqual(quotes.get_stock_themes("005930"), [])

    def test_days_out_of_range(self):
        for bad in (0, 100):
            with self.subTest(days=bad), self.assertRaises(ValueError):
                quotes.get_stock_themes("005930", days=bad)


class TestInvestorThemeRouter(unittest.TestCase):
    def _client(self):
        app = FastAPI()
        app.include_router(quotes_router.router)
        return TestClient(app)

    def test_investor_sum_route(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            res = self._client().get(
                "/quotes/005930/investor-sum?start=20260901&end=20260905")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["orgn"], 64891)

    def test_themes_route(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            res = self._client().get("/quotes/005930/themes?days=5")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()[0]["name"], "건강식품")

    def test_themes_days_out_of_range_422(self):
        res = self._client().get("/quotes/005930/themes?days=0")
        self.assertEqual(res.status_code, 422)

    def test_status_route_still_200(self):
        with patch("kiwoom.quotes.request", side_effect=_fake):
            res = self._client().get("/quotes/005930/status")
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
