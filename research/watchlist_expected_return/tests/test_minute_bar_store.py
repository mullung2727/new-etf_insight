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
from pathlib import Path

from research.watchlist_expected_return.minute_bar_store import (
    connect,
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
