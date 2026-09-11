"""1분봉 DuckDB 저장소 회귀 테스트 — 네트워크 없이 가짜 fetch_page 로 검증.

검증 항목:
  - 미조회 날짜만 조회하고, 이미 받은 날짜는 재조회하지 않는다(중복 조회 제거가 이 모듈의 목적)
  - 봉이 0개인 날도 조회 완료로 표시된다(거래정지 ≠ 미조회)
  - 같은 봉을 두 번 넣어도 PK 로 중복 제거된다
  - 정규장 밖 봉과 요청 밖 날짜는 반환하지 않는다
  - JSON 캐시 마이그레이션이 earliest_requested_dt~base_dt 범위만 적재한다
"""
import json
import tempfile
import unittest
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from research.watchlist_expected_return.minute_bar_store import (
    MinuteFetchIncomplete,
    _insert_bars,
    connect,
    fetch_into_store,
    load_bars,
    migrate_json_cache,
    missing_dates,
)

TICKER = "005930"
DATES = ["20260601", "20260602", "20260603"]


def _raw(date, time, price=1000, volume=10):
    return {"cntr_tm": f"{date}{time}", "open_pric": price, "high_pric": price,
            "low_pric": price, "cur_prc": price, "trde_qty": volume}


class _FakeApi:
    """base_dt 이하 날짜의 09:00/15:30 두 봉만 돌려주는 단일 페이지 응답."""

    def __init__(self, dates=DATES):
        self.dates = dates
        self.calls = []

    def __call__(self, symbol, scope, base_dt, cont_yn="N", next_key=""):
        self.calls.append((symbol, base_dt))
        bars = []
        for date in self.dates:
            if date <= base_dt:
                bars += [_raw(date, "090000"), _raw(date, "153000")]
        return {"bars": bars, "cont_yn": "N", "next_key": ""}


class TestFetchAndReuse(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "minute.duckdb"

    def tearDown(self):
        self._tmp.cleanup()

    def test_second_call_does_not_refetch(self):
        api = _FakeApi()
        with connect(self.db) as con:
            first = load_bars(con, TICKER, DATES, fetch_page=api)
            second = load_bars(con, TICKER, DATES, fetch_page=api)
        self.assertEqual(len(first), 6)
        self.assertEqual(first, second)
        self.assertEqual(len(api.calls), 1)          # 두 번째는 API 호출 없음

    def test_month_is_split_recent_first_and_reused(self):
        dates = [(date(2026, 6, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(30)]
        api = _FakeApi(dates)
        with connect(self.db) as con:
            self.assertEqual(fetch_into_store(con, TICKER, dates, fetch_page=api), 60)
            self.assertEqual(fetch_into_store(con, TICKER, dates, fetch_page=api), 0)
            self.assertEqual(missing_dates(con, TICKER, dates), [])
        self.assertEqual([x[1] for x in api.calls],
                         ["20260630", "20260625", "20260620", "20260615", "20260610", "20260605"])

    def test_existing_prices_are_preserved_on_overlap(self):
        row = (TICKER, "1", "20260601090000", "20260601", "090000", 100, 100, 100, 100, 10)
        changed = (*row[:5], 200, 200, 200, 200, 20)
        with connect(self.db) as con:
            self.assertEqual(_insert_bars(con, [row, row]), 1)
            self.assertEqual(_insert_bars(con, [changed]), 0)
            self.assertEqual(con.execute("SELECT close FROM minute_bars").fetchall(), [(100,)])

    def test_completed_window_survives_later_failure(self):
        dates = ["20260601", "20260610"]
        api = _FakeApi(dates)
        def fail_old(symbol, scope, base_dt, **kwargs):
            if base_dt == "20260601":
                raise RuntimeError("API failure")
            return api(symbol, scope, base_dt, **kwargs)
        with connect(self.db) as con:
            with self.assertRaisesRegex(RuntimeError, "API failure"):
                fetch_into_store(con, TICKER, dates, fetch_page=fail_old)
            self.assertEqual(missing_dates(con, TICKER, dates), ["20260601"])
            self.assertEqual(fetch_into_store(con, TICKER, dates, fetch_page=api), 2)
            self.assertEqual(con.execute("SELECT count(*) FROM minute_bars").fetchone()[0], 4)

    def test_empty_request_does_not_fetch(self):
        api = _FakeApi()
        with connect(self.db) as con:
            self.assertEqual(load_bars(con, TICKER, [], fetch_page=api), [])
        self.assertEqual(api.calls, [])

    def test_other_process_lock_times_out_without_changing_data(self):
        with connect(self.db) as con:
            load_bars(con, TICKER, DATES, fetch_page=_FakeApi())
        script = (
            "import duckdb,sys; c=duckdb.connect(sys.argv[1]); "
            "print('ready',flush=True); sys.stdin.readline(); c.close()"
        )
        proc = subprocess.Popen([sys.executable, "-c", script, str(self.db)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            with self.assertRaises(TimeoutError):
                with connect(self.db, lock_timeout=0.1):
                    self.fail("locked database opened")
        finally:
            proc.communicate(input="release\n", timeout=15)
        with connect(self.db) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM minute_bars").fetchone()[0], 6)

    def test_wider_date_window_fetches_only_the_gap(self):
        """horizon 을 늘려도 이미 가진 날짜는 다시 받지 않는다 — 기존 JSON 캐시의 핵심 결함."""
        api = _FakeApi(dates=DATES + ["20260604"])
        with connect(self.db) as con:
            load_bars(con, TICKER, DATES, fetch_page=api)
            self.assertEqual(missing_dates(con, TICKER, DATES + ["20260604"]), ["20260604"])
            load_bars(con, TICKER, DATES + ["20260604"], fetch_page=api)
        self.assertEqual([call[1] for call in api.calls], ["20260603", "20260604"])

    def test_date_without_bars_is_marked_fetched(self):
        api = _FakeApi(dates=["20260601", "20260603"])   # 20260602 는 봉 없음(거래정지)
        with connect(self.db) as con:
            load_bars(con, TICKER, DATES, fetch_page=api)
            self.assertEqual(missing_dates(con, TICKER, DATES), [])

    def test_first_requested_empty_day_is_marked_fetched(self):
        api = _FakeApi(dates=["20260529", "20260602"])
        with connect(self.db) as con:
            load_bars(con, TICKER, ["20260601", "20260602"], fetch_page=api)
            self.assertEqual(missing_dates(con, TICKER, ["20260601", "20260602"]), [])
            status = con.execute(
                "SELECT status FROM minute_fetched WHERE ticker=? AND date=?", [TICKER, "20260601"]
            ).fetchone()[0]
        self.assertEqual(status, "empty")

    def test_empty_response_without_continuation_is_marked_fetched(self):
        def no_trades(*_args, **_kwargs):
            return {"bars": [], "cont_yn": "N", "next_key": ""}

        with connect(self.db) as con:
            bars = load_bars(con, TICKER, DATES, fetch_page=no_trades)
            self.assertEqual(bars, [])
            self.assertEqual(missing_dates(con, TICKER, DATES), [])
            statuses = con.execute(
                "SELECT date, status FROM minute_fetched WHERE ticker=? ORDER BY date", [TICKER]
            ).fetchall()
        self.assertEqual(statuses, [(date, "empty") for date in DATES])

    def test_earliest_empty_day_is_complete_when_later_dates_have_bars(self):
        api = _FakeApi(dates=["20260602", "20260603"])
        with connect(self.db) as con:
            bars = load_bars(con, TICKER, DATES, fetch_page=api)
            self.assertEqual(len(bars), 4)
            self.assertEqual(missing_dates(con, TICKER, DATES), [])
            status = con.execute(
                "SELECT status FROM minute_fetched WHERE ticker=? AND date=?", [TICKER, "20260601"]
            ).fetchone()[0]
        self.assertEqual(status, "empty")

    def test_incomplete_fetch_is_not_marked_fetched(self):
        def partial(*_args, **_kwargs):
            return {"bars": [_raw("20260601", "140000")], "cont_yn": "Y", "next_key": "more"}

        with connect(self.db) as con:
            with self.assertRaises(MinuteFetchIncomplete):
                fetch_into_store(con, TICKER, ["20260601"], fetch_page=partial, max_pages=1)
            self.assertEqual(missing_dates(con, TICKER, ["20260601"]), ["20260601"])

    def test_first_listing_day_with_late_first_trade_is_complete_when_history_ends(self):
        def first_day(*_args, **_kwargs):
            return {"bars": [_raw("20260601", "090100")], "cont_yn": "N", "next_key": ""}

        with connect(self.db) as con:
            bars = load_bars(con, TICKER, ["20260601"], fetch_page=first_day)
            self.assertEqual(len(bars), 1)
            self.assertEqual(missing_dates(con, TICKER, ["20260601"]), [])

    def test_out_of_session_and_out_of_range_bars_excluded(self):
        class OddApi(_FakeApi):
            def __call__(self, symbol, scope, base_dt, cont_yn="N", next_key=""):
                self.calls.append((symbol, base_dt))
                return {"bars": [
                    _raw("20260601", "085900"),          # 장 시작 전
                    _raw("20260601", "090000"),
                    _raw("20260601", "160000"),          # 장 마감 후
                    _raw("20260530", "090000"),          # 요청 범위 밖
                ], "cont_yn": "N", "next_key": ""}

        with connect(self.db) as con:
            bars = load_bars(con, TICKER, ["20260601"], fetch_page=OddApi())
        self.assertEqual([bar["time"] for bar in bars], ["090000"])


class TestMigrateJsonCache(unittest.TestCase):
    def test_only_requested_range_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            payload = {
                "cache_version": 2, "symbol": TICKER, "scope_minutes": 1,
                "base_dt": "20260602", "earliest_requested_dt": "20260601",
                "page_count": 1, "complete": True,
                "bars": [
                    {"timestamp": "202605290900" + "00", "date": "20260529", "time": "090000",
                     "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},   # 범위 밖
                    {"timestamp": "20260601090000", "date": "20260601", "time": "090000",
                     "open": 1000, "high": 1010, "low": 990, "close": 1005, "volume": 50},
                    {"timestamp": "20260602090000", "date": "20260602", "time": "090000",
                     "open": 1005, "high": 1015, "low": 1000, "close": 1010, "volume": 60},
                ],
            }
            (cache_dir / f"{TICKER}_20260602_1m.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            with connect(Path(tmp) / "minute.duckdb") as con:
                stats = migrate_json_cache(con, cache_dir)
                self.assertEqual(stats["files"], 1)
                self.assertEqual(stats["bars_total"], 2)          # 20260529 제외
                self.assertEqual(stats["fetched_days"], 2)
                self.assertEqual(missing_dates(con, TICKER, ["20260601", "20260602"]), [])
                # 이미 적재된 날짜는 fetch_page 없이도 읽힌다
                bars = load_bars(con, TICKER, ["20260601", "20260602"])
                self.assertEqual([bar["close"] for bar in bars], [1005, 1010])


if __name__ == "__main__":
    unittest.main()
