"""스윙 코드 판정(judge_code) 테스트.

DB·HTTP는 주입으로 대체한다. 임시 duckdb·sqlite에 샘플을 적재하고,
broker 호출은 URL로 분기하는 가짜 get으로 받는다.

실행 (etl 폴더): PYTHONPATH=. uv run python -m unittest tests.test_swing_pick_judge_code
"""
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from scripts.swing_pick.judge_code import (
    judge_code,
    load_accounts,
    load_ohlcv,
    per_value,
    prefilter,
    quarterly_series,
    score_chart,
    score_earnings,
    score_supply,
    supply_window,
    ttm_net,
)


def _prev_row(close=100, volume=1000, trading_value=6_000_000_000, market_cap=100):
    return {"close": close, "volume": volume, "trading_value": trading_value,
            "market_cap": market_cap}


class TestPrefilter(unittest.TestCase):
    def test_reasons_and_pass(self):
        cands = [
            {"ticker": "000001", "name": "보통주", "extra": 1},
            {"ticker": "000002", "name": "신규상장"},       # D-1 행 없음
            {"ticker": "000003", "name": "미래스팩3호"},    # 스팩 + 거래대금 부족
            {"ticker": "000004", "name": "정지산업"},       # 거래량 0
            {"ticker": "000005", "name": "잡주건설"},       # 거래대금 부족
        ]
        prev = {
            "000001": _prev_row(),
            "000003": _prev_row(trading_value=1_000),
            "000004": _prev_row(volume=0),
            "000005": _prev_row(trading_value=1_000_000_000),
        }
        passed, cut = prefilter(cands, prev)
        self.assertEqual(passed, [{"ticker": "000001", "name": "보통주", "extra": 1}])
        self.assertEqual(
            {c["ticker"]: c["cut_reason"] for c in cut},
            {"000002": "no_prev_data", "000003": "spac",
             "000004": "halted", "000005": "low_value"},
        )

    def test_priority(self):
        # 스팩이면서 거래대금 부족 → spac. D-1 행이 없으면 스팩이어도 no_prev_data.
        prev = {"A": _prev_row(trading_value=1_000)}
        _, cut = prefilter(
            [{"ticker": "A", "name": "스팩"}, {"ticker": "B", "name": "스팩"}], prev)
        self.assertEqual(
            {c["ticker"]: c["cut_reason"] for c in cut},
            {"A": "spac", "B": "no_prev_data"},
        )
        # 거래정지 + 거래대금 부족 → halted가 먼저.
        _, cut = prefilter(
            [{"ticker": "A", "name": "x"}],
            {"A": _prev_row(volume=0, trading_value=1_000)},
        )
        self.assertEqual(cut[0]["cut_reason"], "halted")

    def test_value_boundary(self):
        cands = [{"ticker": "A", "name": "a"}, {"ticker": "B", "name": "b"}]
        prev = {"A": _prev_row(trading_value=4_999_000_000),  # 49.99억 제외
                "B": _prev_row(trading_value=5_000_000_000)}  # 50억 통과
        passed, cut = prefilter(cands, prev)
        self.assertEqual([c["ticker"] for c in passed], ["B"])
        self.assertEqual(cut[0]["cut_reason"], "low_value")


def _acct_row(bsns_year, reprt_code, fs_div, account_nm, amount):
    # 실측 스키마: bsns_year·reprt_code는 TEXT.
    return {"bsns_year": str(bsns_year), "reprt_code": str(reprt_code),
            "fs_div": fs_div, "account_nm": account_nm, "amount": amount}


class TestQuarterlySeries(unittest.TestCase):
    def test_samsung_2024_q4(self):
        # 삼성전자 2024 영업이익 실측치. 4Q = 연간 − 1~3Q.
        rows = [
            _acct_row(2024, 11013, "CFS", "영업이익", 6606009),
            _acct_row(2024, 11012, "CFS", "영업이익", 10443878),
            _acct_row(2024, 11014, "CFS", "영업이익", 9183371),
            _acct_row(2024, 11011, "CFS", "영업이익", 32725961),
            _acct_row(2024, 11013, "CFS", "당기순이익(손실)", 6000000),
            _acct_row(2024, 11012, "CFS", "당기순이익(손실)", 9000000),
            _acct_row(2024, 11014, "CFS", "당기순이익(손실)", 8000000),
            _acct_row(2024, 11011, "CFS", "당기순이익(손실)", 34000000),
        ]
        s = quarterly_series(rows)
        self.assertEqual(
            s[(2024, 4)]["op"], 32725961 - (6606009 + 10443878 + 9183371))
        self.assertEqual(
            s[(2024, 4)]["net"], 34000000 - (6000000 + 9000000 + 8000000))
        self.assertEqual(s[(2024, 1)]["op"], 6606009)

    def test_cfs_preferred_over_ofs(self):
        rows = [_acct_row(2024, 11013, "CFS", "영업이익", 100),
                _acct_row(2024, 11013, "OFS", "영업이익", 999),
                _acct_row(2024, 11013, "OFS", "당기순이익(손실)", 999)]
        s = quarterly_series(rows)
        self.assertEqual(s[(2024, 1)], {"op": 100, "net": None})

    def test_ofs_when_no_cfs(self):
        s = quarterly_series([_acct_row(2024, 11013, "OFS", "영업이익", 50)])
        self.assertEqual(s[(2024, 1)]["op"], 50)

    def test_no_q4_when_quarter_missing(self):
        rows = [_acct_row(2024, 11013, "CFS", "영업이익", 100),
                _acct_row(2024, 11012, "CFS", "영업이익", 200),
                _acct_row(2024, 11011, "CFS", "영업이익", 1000)]
        self.assertNotIn((2024, 4), quarterly_series(rows))

    def test_alt_op_name(self):
        rows = [_acct_row(2024, 11013, "CFS", "영업이익(손실)", 77)]
        self.assertEqual(quarterly_series(rows)[(2024, 1)]["op"], 77)


class TestScoreEarnings(unittest.TestCase):
    def test_loss_zero(self):
        s, raw = score_earnings({(2025, 2): {"op": -5.0, "net": 1.0},
                                 (2024, 2): {"op": -10.0, "net": 1.0}})
        self.assertEqual(s, 0)
        self.assertEqual(raw["quarter"], "2025Q2")

    def test_growth_two(self):
        s, raw = score_earnings({(2025, 2): {"op": 100.0, "net": None},
                                 (2024, 2): {"op": 80.0, "net": None}})
        self.assertEqual(s, 2)
        self.assertEqual(raw["op_yoy"], 80.0)

    def test_decline_one(self):
        s, _ = score_earnings({(2025, 2): {"op": 60.0, "net": None},
                               (2024, 2): {"op": 80.0, "net": None}})
        self.assertEqual(s, 1)

    def test_no_yoy_one(self):
        s, raw = score_earnings({(2025, 2): {"op": 100.0, "net": None}})
        self.assertEqual(s, 1)
        self.assertIsNone(raw["op_yoy"])

    def test_no_data_none(self):
        s, raw = score_earnings({})
        self.assertIsNone(s)
        self.assertIsNone(raw["quarter"])
        s, _ = score_earnings({(2025, 2): {"op": None, "net": 5.0}})
        self.assertIsNone(s)


class TestTtmNet(unittest.TestCase):
    def test_continuous_sum(self):
        s = {(2025, 2): {"op": 1.0, "net": 10.0},
             (2025, 1): {"op": 1.0, "net": 20.0},
             (2024, 4): {"op": 1.0, "net": 30.0},
             (2024, 3): {"op": 1.0, "net": 40.0}}
        self.assertEqual(ttm_net(s), 100.0)

    def test_gap_returns_none(self):
        s = {(2025, 2): {"net": 10.0}, (2025, 1): {"net": 20.0},
             (2024, 3): {"net": 40.0}}  # (2024, 4) 결측
        self.assertIsNone(ttm_net(s))

    def test_none_net_returns_none(self):
        s = {(2025, 2): {"net": 10.0}, (2025, 1): {"net": None},
             (2024, 4): {"net": 30.0}, (2024, 3): {"net": 40.0}}
        self.assertIsNone(ttm_net(s))


class TestPerValue(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(per_value(1000, 100), (10.0, None))
        self.assertEqual(per_value(1000, 0), (None, "적자"))
        self.assertEqual(per_value(1000, -5), (None, "적자"))
        self.assertEqual(per_value(1000, None), (None, "재무 부족"))
        self.assertEqual(per_value(None, 100), (None, "시총 없음"))


class TestSupplyWindow(unittest.TestCase):
    def test_window(self):
        days = [f"202601{day:02d}" for day in range(1, 9)]
        self.assertEqual(supply_window(days, "20260109"), ("20260105", "20260109"))

    def test_exactly_four(self):
        days = ["20260101", "20260102", "20260105", "20260106"]
        self.assertEqual(supply_window(days, "20260107"), ("20260101", "20260107"))

    def test_too_few_raises(self):
        with self.assertRaises(ValueError):
            supply_window(["20260101", "20260102", "20260105"], "20260106")


class TestScoreSupply(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(score_supply(1, 1), 2)
        self.assertEqual(score_supply(1, -1), 1)
        self.assertEqual(score_supply(0, -5), 0)  # 0은 순매수 아님
        self.assertEqual(score_supply(0, 0), 0)
        self.assertIsNone(score_supply(None, 1))
        self.assertIsNone(score_supply(1, None))


class TestScoreChart(unittest.TestCase):
    def test_below_ma20_zero(self):
        s, raw = score_chart([100] * 25, 90)
        self.assertEqual(s, 0)
        self.assertAlmostEqual(raw["ma20"], 99.5)

    def test_exactly_30pct_zero(self):
        s, raw = score_chart([100] * 25, 130)
        self.assertEqual(s, 0)
        self.assertAlmostEqual(raw["ret_5d"], 0.30)

    def test_just_below_30pct_one(self):
        s, raw = score_chart([10000] * 25, 12999)
        self.assertEqual(s, 1)
        self.assertLess(raw["ret_5d"], 0.30)

    def test_exactly_15pct_one(self):
        s, raw = score_chart([100] * 25, 115)
        self.assertEqual(s, 1)
        self.assertAlmostEqual(raw["ret_5d"], 0.15)

    def test_just_below_15pct_two(self):
        s, raw = score_chart([10000] * 25, 11499)
        self.assertEqual(s, 2)
        self.assertLess(raw["ret_5d"], 0.15)

    def test_short_history_none(self):
        s, raw = score_chart([100] * 24, 100)
        self.assertIsNone(s)
        self.assertIsNone(raw["ma20"])

    def test_no_today_none(self):
        self.assertIsNone(score_chart([100] * 25, None)[0])
        self.assertIsNone(score_chart([100] * 25, 0)[0])


_OHLCV_DDL = (
    "CREATE TABLE ohlcv (date VARCHAR, ticker VARCHAR, market VARCHAR, "
    "open INTEGER, high INTEGER, low INTEGER, close INTEGER, volume BIGINT, "
    "trading_value BIGINT, market_cap BIGINT, list_shrs BIGINT)"
)

_ACCOUNTS_DDL = (
    "CREATE TABLE accounts (corp_code TEXT, bsns_year TEXT, reprt_code TEXT, "
    "fs_div TEXT, account_nm TEXT, amount REAL, stock_code TEXT)"
)


def _seed_ohlcv(path, dates, tickers, close_fn):
    con = duckdb.connect(str(path))
    con.execute(_OHLCV_DDL)
    for i, day in enumerate(dates):
        for t in tickers:
            c = close_fn(t, i)
            con.execute(
                "INSERT INTO ohlcv VALUES (?, ?, 'KOSPI', ?, ?, ?, ?, ?, ?, ?, ?)",
                [day, t, c, c, c, c, 1000, 10_000_000_000, 1000, 100],
            )
    con.commit()
    con.close()


def _seed_accounts(path, tickers):
    con = sqlite3.connect(path)
    con.execute(_ACCOUNTS_DDL)
    rows = []
    for n, t in enumerate(tickers):
        corp = f"C{n}"
        for year, code, name, amount in [
            (2025, 11013, "영업이익", 100), (2025, 11012, "영업이익", 200),
            (2025, 11014, "영업이익", 300), (2025, 11011, "영업이익", 1000),
            (2024, 11013, "영업이익", 50), (2024, 11012, "영업이익", 60),
            (2024, 11014, "영업이익", 70), (2024, 11011, "영업이익", 430),
            (2025, 11013, "당기순이익(손실)", 10),
            (2025, 11012, "당기순이익(손실)", 20),
            (2025, 11014, "당기순이익(손실)", 30),
            (2025, 11011, "당기순이익(손실)", 100),
        ]:
            rows.append((corp, str(year), str(code), "CFS", name, float(amount), t))
    con.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


class TestLoaders(unittest.TestCase):
    def test_load_ohlcv(self):
        dates = ["20260101", "20260102", "20260105", "20260106", "20260107"]
        with TemporaryDirectory() as d:
            db = Path(d) / "krx.duckdb"
            _seed_ohlcv(db, dates, ["111111", "222222"],
                        lambda t, i: (100 if t == "111111" else 200) + i)
            prev, closes, days = load_ohlcv(
                db, ["111111", "222222", "999999"], "20260107", lookback=3)
        self.assertEqual(days, ["20260105", "20260106", "20260107"])
        self.assertEqual(prev["111111"]["close"], 104)
        self.assertEqual(prev["111111"]["trading_value"], 10_000_000_000)
        self.assertEqual(prev["222222"]["market_cap"], 1000)
        self.assertEqual(closes["111111"], [102, 103, 104])
        self.assertEqual(closes["222222"], [202, 203, 204])
        self.assertEqual(closes["999999"], [])
        self.assertNotIn("999999", prev)

    def test_load_accounts(self):
        with TemporaryDirectory() as d:
            db = Path(d) / "fin.sqlite3"
            _seed_accounts(db, ["111111", "222222"])
            out = load_accounts(db, ["111111", "222222"])
        self.assertEqual(len(out["111111"]), 12)
        self.assertEqual(len(out["222222"]), 12)
        self.assertEqual(out["111111"][0]["bsns_year"], "2025")
        self.assertNotIn("999999", out)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestJudgeCode(unittest.TestCase):
    AAA = "111111"  # 전부 정상
    BBB = "222222"  # investor-sum 실패
    CCC = "333333"  # 오늘 가격 누락

    def test_judge_code(self):
        base = datetime(2026, 1, 5)
        dates = [(base + timedelta(days=i)).strftime("%Y%m%d") for i in range(30)]
        tickers = [self.AAA, self.BBB, self.CCC]
        with TemporaryDirectory() as d:
            ohlcv_db = Path(d) / "krx.duckdb"
            fin_db = Path(d) / "fin.sqlite3"
            _seed_ohlcv(ohlcv_db, dates, tickers, lambda t, i: 100 + i)
            _seed_accounts(fin_db, tickers)
            calls = []

            def fake_get(url, params=None, timeout=None):
                calls.append((url, dict(params or {})))
                if url.endswith("/quotes"):
                    return _Resp([{"stk_cd": self.AAA, "cur_prc": 130},
                                  {"stk_cd": self.BBB, "cur_prc": 130}])
                if "investor-sum" in url:
                    if f"/{self.BBB}/" in url:
                        raise ConnectionError("ka10061 down")
                    return _Resp({"frgnr_invsr": 100, "orgn": 200})
                raise AssertionError(f"unexpected {url}")

            res = judge_code(
                tickers, today=(base + timedelta(days=30)).strftime("%Y%m%d"),
                prev_date=dates[-1], broker_url="http://broker:8001",
                ohlcv_db=ohlcv_db, fin_db=fin_db, get=fake_get,
            )

        quotes_calls = [c for c in calls if c[0].endswith("/quotes")]
        inv_calls = [c for c in calls if "investor-sum" in c[0]]
        # 오늘 가격 일괄 1회(codes에 전 종목), 투자자 합계는 종목당 1회. 그 외 없음.
        self.assertEqual(len(calls), 4)  # 일괄 1 + 종목당 1, 그 외 없음
        self.assertEqual(len(quotes_calls), 1)
        self.assertEqual(set(quotes_calls[0][1]["codes"].split(",")), set(tickers))
        self.assertEqual(len(inv_calls), 3)
        for t in tickers:
            one = [c for c in inv_calls if f"/{t}/" in c[0]]
            self.assertEqual(len(one), 1)
            self.assertEqual(one[0][1]["start"], dates[-4])
            self.assertEqual(one[0][1]["end"], (base + timedelta(days=30)).strftime("%Y%m%d"))

        aaa = res[self.AAA]
        self.assertEqual((aaa["s3"], aaa["s4"], aaa["s5"]), (2, 2, 2))
        self.assertEqual(aaa["quarter"], "2025Q4")
        self.assertEqual(aaa["op_profit"], 400.0)
        self.assertEqual(aaa["op_profit_yoy"], 250.0)
        self.assertEqual((aaa["frgn_net_5d"], aaa["orgn_net_5d"]), (100, 200))
        self.assertEqual(aaa["today_price"], 130)
        self.assertAlmostEqual(aaa["ma20"], 120.5)
        self.assertAlmostEqual(aaa["ret_5d"], 0.04)
        self.assertEqual(aaa["per"], 10.0)
        self.assertIsNone(aaa["per_note"])
        self.assertEqual(aaa["market_cap_prev"], 1000)
        self.assertEqual(aaa["errors"], {})

        bbb = res[self.BBB]
        self.assertIsNone(bbb["s4"])
        self.assertEqual(bbb["errors"], {"s4": "investor_sum_failed"})
        self.assertEqual((bbb["s3"], bbb["s5"]), (2, 2))

        ccc = res[self.CCC]
        self.assertIsNone(ccc["s5"])
        self.assertEqual(ccc["errors"], {"s5": "no_today_price"})
        self.assertEqual((ccc["s3"], ccc["s4"]), (2, 2))

        expect_keys = {"s3", "s4", "s5", "op_profit", "op_profit_yoy", "quarter",
                       "frgn_net_5d", "orgn_net_5d", "today_price", "ma20",
                       "ma20_gap", "ret_5d", "per", "per_note", "market_cap_prev",
                       "errors"}
        for t in tickers:
            self.assertEqual(set(res[t].keys()), expect_keys)


if __name__ == "__main__":
    unittest.main()
