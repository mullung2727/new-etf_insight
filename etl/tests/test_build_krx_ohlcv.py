"""build_krx_ohlcv 단위 테스트.

핵심 검증:
  - 휴장일 자기학습: 빈 응답 날짜는 "나중 날짜에 데이터 존재"할 때만 holidays 기록
  - holidays 기록된 날짜는 이후 실행에서 재호출하지 않음 (force 포함)
  - 네트워크 오류는 해당 날짜만 스킵하고 배치 계속
  - OutBlock_1 키 없는 200 응답은 에러로 처리 (빈 날 위장 방지)
"""
import unittest
from unittest.mock import MagicMock, patch

import duckdb
import requests

from scripts.build_krx_ohlcv import (
    _parse_float,
    _parse_int,
    ensure_etf_ohlcv,
    ensure_ohlcv,
    fetch_day,
    fetch_etf_day,
    get_trading_calendar,
    holiday_dates,
    run_etf_step,
)


def _row(date: str, ticker: str, market: str = "KOSPI") -> tuple:
    return (date, ticker, market, 100, 110, 90, 105, 1000, 105000, 999, 10, 5)


def _etf_row(date: str, ticker: str) -> tuple:
    return (date, ticker, "NAME", "코스피 200", 100, 110, 90, 105,
            1000, 105000, 999, 10, 100.5, 777)


class TestParseInt(unittest.TestCase):
    def test_comma_number(self):
        self.assertEqual(_parse_int("53,400"), 53400)

    def test_dash_and_empty_are_none(self):
        self.assertIsNone(_parse_int("-"))
        self.assertIsNone(_parse_int(""))
        self.assertIsNone(_parse_int(None))

    def test_float_string(self):
        self.assertEqual(_parse_int("123.0"), 123)


class TestTradingCalendar(unittest.TestCase):
    def test_weekdays_only_ascending(self):
        # 2025-01-01(수)~2025-01-07(화): 주말 1/4(토),1/5(일) 제외
        cal = get_trading_calendar("20250101", "20250107")
        self.assertEqual(
            cal, ["20250101", "20250102", "20250103", "20250106", "20250107"]
        )

    def test_empty_when_from_after_to(self):
        self.assertEqual(get_trading_calendar("20250107", "20250101"), [])


class TestFetchDay(unittest.TestCase):
    def _resp(self, json_body):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = json_body
        resp.text = str(json_body)
        return resp

    def test_retries_timeout_then_succeeds(self):
        ok = self._resp({"OutBlock_1": []})
        session = MagicMock()
        session.get.side_effect = [requests.ReadTimeout("slow"), ok, ok]
        with patch("scripts.build_krx_ohlcv.time.sleep") as slept:
            self.assertEqual(fetch_day("20250102", "k", session=session), [])
        self.assertEqual(session.get.call_count, 3)   # KOSPI 2번(1번 실패) + KOSDAQ 1번
        self.assertIn(30, [c.args[0] for c in slept.call_args_list])

    def test_gives_up_after_three_timeouts(self):
        session = MagicMock()
        session.get.side_effect = requests.ReadTimeout("down")
        with patch("scripts.build_krx_ohlcv.time.sleep"):
            with self.assertRaises(requests.ReadTimeout):
                fetch_day("20250102", "k", session=session)
        self.assertEqual(session.get.call_count, 3)

    def test_4xx_not_retried(self):
        bad = self._resp({})
        bad.status_code = 401
        bad.raise_for_status.side_effect = requests.HTTPError("401")
        session = MagicMock()
        session.get.return_value = bad
        with patch("scripts.build_krx_ohlcv.time.sleep"):
            with self.assertRaises(requests.HTTPError):
                fetch_day("20250102", "k", session=session)
        self.assertEqual(session.get.call_count, 1)

    def test_missing_outblock_raises(self):
        session = MagicMock()
        session.get.return_value = self._resp({"respMsg": "oops"})
        with self.assertRaises(RuntimeError):
            fetch_day("20250102", "k", session=session)

    def test_rows_from_both_markets(self):
        session = MagicMock()
        session.get.return_value = self._resp(
            {"OutBlock_1": [{"BAS_DD": "20250102", "ISU_CD": "005930",
                             "TDD_OPNPRC": "100", "TDD_HGPRC": "110",
                             "TDD_LWPRC": "90", "TDD_CLSPRC": "105",
                             "ACC_TRDVOL": "1,000", "ACC_TRDVAL": "105,000",
                             "MKTCAP": "999", "LIST_SHRS": "10"}]}
        )
        with patch("scripts.build_krx_ohlcv.time.sleep"):
            rows = fetch_day("20250102", "k", session=session)
        self.assertEqual(len(rows), 2)  # KOSPI + KOSDAQ 콜 각 1행
        self.assertEqual(rows[0][:3], ("20250102", "005930", "KOSPI"))
        self.assertEqual(rows[1][2], "KOSDAQ")

    def test_names_out_collects_code_name(self):
        session = MagicMock()
        session.get.return_value = self._resp(
            {"OutBlock_1": [{"BAS_DD": "20250102", "ISU_CD": "005930", "ISU_NM": "삼성전자",
                             "TDD_OPNPRC": "1", "TDD_HGPRC": "1", "TDD_LWPRC": "1",
                             "TDD_CLSPRC": "1", "ACC_TRDVOL": "1", "ACC_TRDVAL": "1",
                             "MKTCAP": "1", "LIST_SHRS": "1"}]}
        )
        names: list = []
        with patch("scripts.build_krx_ohlcv.time.sleep"):
            fetch_day("20250102", "k", session=session, names_out=names)
        # KOSPI+KOSDAQ 각 1행 → 2건
        self.assertEqual(names, [("005930", "삼성전자"), ("005930", "삼성전자")])

    def test_cmp_prev_parsed_from_response(self):
        session = MagicMock()
        session.get.return_value = self._resp(
            {"OutBlock_1": [{"BAS_DD": "20250102", "ISU_CD": "005930",
                             "TDD_OPNPRC": "100", "TDD_HGPRC": "110",
                             "TDD_LWPRC": "90", "TDD_CLSPRC": "105",
                             "ACC_TRDVOL": "1,000", "ACC_TRDVAL": "105,000",
                             "MKTCAP": "999", "LIST_SHRS": "10",
                             "CMPPREVDD_PRC": "-12"}]}
        )
        with patch("scripts.build_krx_ohlcv.time.sleep"):
            rows = fetch_day("20250102", "k", session=session)
        self.assertEqual(len(rows[0]), 12)
        self.assertEqual(rows[0][-1], -12)


class TestEnsureOhlcv(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")

    def tearDown(self):
        self.con.close()

    def _run(self, from_date, to_date, day_map, force=False):
        """day_map: date -> rows(list) | Exception. 미지정 날짜는 빈 응답."""
        def fake_fetch(date, key, session=None, names_out=None):
            v = day_map.get(date, [])
            if isinstance(v, Exception):
                raise v
            return v

        with patch("scripts.build_krx_ohlcv.fetch_day", side_effect=fake_fetch):
            return ensure_ohlcv(self.con, from_date, to_date, "k", force=force)

    def test_inserts_missing_days(self):
        stats = self._run("20250102", "20250103", {
            "20250102": [_row("20250102", "005930")],
            "20250103": [_row("20250103", "005930")],
        })
        self.assertEqual(stats["fetched_days"], 2)
        self.assertEqual(stats["inserted_rows"], 2)
        n = self.con.execute("SELECT count(*) FROM ohlcv").fetchone()[0]
        self.assertEqual(n, 2)

    def test_fetched_day_upserts_stock_names(self):
        # names_out 를 채우는 fake → ensure_ohlcv 가 stock_names 로 upsert 하는지
        def fake_fetch(date, key, session=None, names_out=None):
            if names_out is not None:
                names_out.append(("005930", "삼성전자"))
            return [_row(date, "005930")]

        with patch("scripts.build_krx_ohlcv.fetch_day", side_effect=fake_fetch):
            ensure_ohlcv(self.con, "20250102", "20250102", "k")
        name = self.con.execute(
            "SELECT name FROM stock_names WHERE code='005930'"
        ).fetchone()
        self.assertEqual(name[0], "삼성전자")

    def test_empty_day_with_later_data_recorded_as_holiday(self):
        # 1/1(수) 휴장 → 빈 응답, 1/2 데이터 있음 → 1/1 휴장일 확정
        stats = self._run("20250101", "20250102", {
            "20250102": [_row("20250102", "005930")],
        })
        self.assertEqual(stats["holiday_days"], 1)
        self.assertEqual(holiday_dates(self.con, "20250101", "20250102"), {"20250101"})

    def test_empty_latest_day_not_recorded_retried_next_run(self):
        # 마지막 날짜가 빈 응답(미공시 가능) → 휴장일 기록 안 함
        stats = self._run("20250102", "20250103", {
            "20250102": [_row("20250102", "005930")],
        })
        self.assertEqual(stats["holiday_days"], 0)
        self.assertEqual(holiday_dates(self.con, "20250101", "20250131"), set())
        # 다음 실행에서 재시도 대상에 포함
        stats2 = self._run("20250102", "20250103", {
            "20250103": [_row("20250103", "005930")],
        })
        self.assertEqual(stats2["fetched_days"], 1)

    def test_recorded_holiday_never_refetched(self):
        self._run("20250101", "20250102", {
            "20250102": [_row("20250102", "005930")],
        })
        calls = []

        def fake_fetch(date, key, session=None, names_out=None):
            calls.append(date)
            return [_row(date, "005930")]

        with patch("scripts.build_krx_ohlcv.fetch_day", side_effect=fake_fetch):
            ensure_ohlcv(self.con, "20250101", "20250102", "k")
            ensure_ohlcv(self.con, "20250101", "20250102", "k", force=True)
        self.assertNotIn("20250101", calls)  # force여도 휴장일은 재호출 안 함
        self.assertIn("20250102", calls)     # force는 적재일 재호출

    def test_network_error_skips_day_and_continues(self):
        stats = self._run("20250102", "20250103", {
            "20250102": requests.ConnectionError("boom"),
            "20250103": [_row("20250103", "005930")],
        })
        self.assertEqual(stats["failed_days"], 1)
        self.assertEqual(stats["fetched_days"], 1)

    def test_runtime_error_skips_day_and_continues(self):
        stats = self._run("20250102", "20250103", {
            "20250102": RuntimeError("KRX unexpected response"),
            "20250103": [_row("20250103", "005930")],
        })
        self.assertEqual(stats["failed_days"], 1)
        self.assertEqual(stats["fetched_days"], 1)
        # 실패일은 미적재 → 다음 실행에서 재시도
        stats2 = self._run("20250102", "20250103", {
            "20250102": [_row("20250102", "005930")],
        })
        self.assertEqual(stats2["fetched_days"], 1)

    def test_held_days_skipped_without_force(self):
        self._run("20250102", "20250102", {
            "20250102": [_row("20250102", "005930")],
        })
        with patch("scripts.build_krx_ohlcv.fetch_day") as mocked:
            stats = ensure_ohlcv(self.con, "20250102", "20250102", "k")
        mocked.assert_not_called()
        self.assertEqual(stats["missing_days"], 0)

    def test_migration_adds_cmp_prev_to_old_schema(self):
        self.con.execute("""
            CREATE TABLE ohlcv (
                date VARCHAR,
                ticker VARCHAR,
                market VARCHAR,
                open INTEGER,
                high INTEGER,
                low INTEGER,
                close INTEGER,
                volume BIGINT,
                trading_value BIGINT,
                market_cap BIGINT,
                list_shrs BIGINT,
                PRIMARY KEY (date, ticker)
            )
        """)
        stats = self._run("20250102", "20250102", {
            "20250102": [_row("20250102", "005930")],
        })
        cols = {r[1] for r in self.con.execute("PRAGMA table_info('ohlcv')").fetchall()}
        self.assertIn("cmp_prev", cols)
        self.assertEqual(stats["fetched_days"], 1)
        val = self.con.execute(
            "SELECT cmp_prev FROM ohlcv WHERE date='20250102' AND ticker='005930'"
        ).fetchone()[0]
        self.assertEqual(val, _row("20250102", "005930")[-1])


class TestFetchEtfDay(unittest.TestCase):
    def _resp(self, json_body):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = json_body
        resp.text = str(json_body)
        return resp

    def _etf_body(self):
        return {"OutBlock_1": [{
            "BAS_DD": "20250102", "ISU_CD": "069500",
            "ISU_NM": " KODEX 200 ", "IDX_IND_NM": " 코스피 200 ",
            "TDD_OPNPRC": "10,000", "TDD_HGPRC": "10,100",
            "TDD_LWPRC": "9,900", "TDD_CLSPRC": "10,050",
            "ACC_TRDVOL": "1,000", "ACC_TRDVAL": "10,000,000",
            "MKTCAP": "999", "LIST_SHRS": "10",
            "NAV": "10,055.12",
            "INVSTASST_NETASST_TOTAMT": "1,000,000",
        }]}

    def test_field_mapping(self):
        session = MagicMock()
        session.get.return_value = self._resp(self._etf_body())
        with patch("scripts.build_krx_ohlcv.time.sleep"):
            rows = fetch_etf_day("20250102", "k", session=session)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(len(row), 14)
        (date, ticker, name, idx_name, o, h, low, close,
         vol, val, cap, shrs, nav, net_asset) = row
        self.assertEqual(name, "KODEX 200")
        self.assertEqual(idx_name, "코스피 200")
        self.assertEqual(close, 10050)
        self.assertAlmostEqual(nav, 10055.12)
        self.assertEqual(net_asset, 1000000)

    def test_nav_dash_is_none(self):
        self.assertIsNone(_parse_float("-"))
        self.assertIsNone(_parse_float(""))
        self.assertIsNone(_parse_float(None))

    def test_missing_outblock_raises(self):
        session = MagicMock()
        session.get.return_value = self._resp({"respMsg": "oops"})
        with self.assertRaises(RuntimeError):
            fetch_etf_day("20250102", "k", session=session)


class TestEnsureEtfOhlcv(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")

    def tearDown(self):
        self.con.close()

    def _seed(self, stock_dates, etf_dates):
        self.con.execute(
            "CREATE TABLE ohlcv (date VARCHAR, ticker VARCHAR, "
            "market VARCHAR, open INTEGER)"
        )
        for d in stock_dates:
            self.con.execute("INSERT INTO ohlcv VALUES (?, '005930', 'KOSPI', 1)", [d])
        if etf_dates:
            self.con.execute(
                "CREATE TABLE etf_ohlcv (date VARCHAR, ticker VARCHAR, name VARCHAR, "
                "idx_name VARCHAR, open INTEGER, high INTEGER, low INTEGER, close INTEGER, "
                "volume BIGINT, trading_value BIGINT, market_cap BIGINT, list_shrs BIGINT, "
                "nav DOUBLE, net_asset BIGINT, PRIMARY KEY (date, ticker))"
            )
            for d in etf_dates:
                self.con.execute(
                    "INSERT INTO etf_ohlcv VALUES (?, '069500', 'n', '코스피 200', "
                    "1, 1, 1, 1, 1, 1, 1, 1, 1.0, 1)", [d])

    def test_only_missing_dates_fetched(self):
        self._seed(["20250102", "20250103", "20250106"], ["20250102"])
        calls = []

        def fake_fetch(date, key, session=None):
            calls.append(date)
            return [_etf_row(date, "069500")]

        with patch("scripts.build_krx_ohlcv.fetch_etf_day", side_effect=fake_fetch):
            stats = ensure_etf_ohlcv(self.con, "20250102", "20250106", "k")
        self.assertEqual(sorted(calls), ["20250103", "20250106"])
        self.assertEqual(stats["missing_days"], 2)
        self.assertEqual(stats["fetched_days"], 2)
        self.assertEqual(stats["inserted_rows"], 2)

    def test_fetch_error_counts_failed_and_continues(self):
        self._seed(["20250102", "20250103"], [])
        def fake_fetch(date, key, session=None):
            if date == "20250102":
                raise RuntimeError("boom")
            return [_etf_row(date, "069500")]

        with patch("scripts.build_krx_ohlcv.fetch_etf_day", side_effect=fake_fetch):
            stats = ensure_etf_ohlcv(self.con, "20250102", "20250103", "k")
        self.assertEqual(stats["failed_days"], 1)
        self.assertEqual(stats["fetched_days"], 1)
        n = self.con.execute("SELECT count(*) FROM etf_ohlcv").fetchone()[0]
        self.assertEqual(n, 1)

    def test_insert_error_counts_failed_and_continues(self):
        import scripts.build_krx_ohlcv as mod
        self._seed(["20250102", "20250103", "20250106"], [])
        real_insert = mod._insert_etf_ohlcv_rows
        calls: list = []

        def fake_insert(con, rows):
            calls.append(rows[0][0])
            if len(calls) == 2:
                raise RuntimeError("boom")
            return real_insert(con, rows)

        def fake_fetch(date, key, session=None):
            return [_etf_row(date, "069500")]

        with patch("scripts.build_krx_ohlcv.fetch_etf_day", side_effect=fake_fetch):
            with patch("scripts.build_krx_ohlcv._insert_etf_ohlcv_rows",
                       side_effect=fake_insert):
                stats = ensure_etf_ohlcv(self.con, "20250102", "20250106", "k")
        self.assertEqual(stats["failed_days"], 1)
        dates = sorted(
            r[0] for r in self.con.execute(
                "SELECT DISTINCT date FROM etf_ohlcv").fetchall())
        self.assertEqual(dates, ["20250102", "20250106"])


class TestRunEtfStep(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")

    def tearDown(self):
        self.con.close()

    def test_error_returns_string_without_raise(self):
        with patch("scripts.build_krx_ohlcv.ensure_etf_ohlcv",
                   side_effect=RuntimeError("boom")):
            s = run_etf_step(self.con, "20250102", "20250103", "k")
        self.assertTrue(s.startswith("etf_error="))

    def test_success_format(self):
        with patch("scripts.build_krx_ohlcv.ensure_etf_ohlcv",
                   return_value={"missing_days": 2, "fetched_days": 2,
                                 "inserted_rows": 5, "failed_days": 0}):
            s = run_etf_step(self.con, "20250102", "20250103", "k")
        self.assertEqual(s, "etf=fetched 2d/5rows failed=0")

    def test_missing_ohlcv_table_returns_etf_error(self):
        s = run_etf_step(self.con, "20250102", "20250103", "k")
        self.assertTrue(s.startswith("etf_error="))


if __name__ == "__main__":
    unittest.main()
