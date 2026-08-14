"""STEP 2 신규진입 후보 산출 e2e 테스트.

검증 2축:
  1. 동치성: 실제 krx_ohlcv.duckdb의 최신 거래일 D에 대해, 기존
     build_watchlist.compute_watchlist(원본 로직: top30 → 60거래일 신규진입 →
     동전주 컷) 결과와 신규 경로(intraday_ranking 스냅샷 + SQL 합집합) 결과가
     완전히 일치해야 한다. (당일 데이터를 krx에서 시딩해 키움 의존 제거)
  2. 실호출: STEP 1 run()으로 키움 실데이터 스냅샷 적재 → run_candidates →
     후보가 당일 top30 ⊆, 과거 합집합과 미교차, 동전주 없음, 멱등.

주의: 네트워크(테스트 2) + krx_ohlcv.duckdb 60거래일 이상 필요.
"""
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import duckdb

# build_watchlist.py 는 scripts/ 실행 기준 flat import(`from build_krx_ohlcv import`)
# 라 테스트에선 scripts/ 를 path 에 추가해야 import 가능.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.build_intraday_ranking import (
    LOOKBACK,
    N_TOP,
    PENNY_MAX,
    fetch_past_top_union,
    compute_candidates,
    run,
    run_candidates,
)
from scripts.build_watchlist import compute_watchlist
from scripts.wl_sqlite import connect_ro, connect_rw

ETL_DIR = Path(__file__).resolve().parents[1]
KRX_DB = ETL_DIR / "db" / "krx_ohlcv.duckdb"


def _krx_dates(limit_after: int = 0) -> list[str]:
    con = duckdb.connect(str(KRX_DB), read_only=True)
    try:
        return [r[0] for r in con.execute("SELECT DISTINCT date FROM ohlcv ORDER BY date").fetchall()]
    finally:
        con.close()


class TestCandidateDeduplication(unittest.TestCase):
    def test_manyeo_factory_appears_only_on_first_krx_top30_day(self):
        today_rows = [
            (1, "439090", "마녀공장", 6_398_231, 19_600),
            (2, "257720", "실리콘투", 5_000_000, 45_200),
        ]

        first_day = compute_candidates(today_rows, set())
        next_day = compute_candidates(today_rows, {"439090"})

        self.assertEqual(first_day, ["257720", "439090"])
        self.assertEqual(first_day.count("439090"), 1)
        self.assertEqual(next_day, ["257720"])
        self.assertEqual(next_day.count("439090"), 0)


class TestEquivalenceWithBuildWatchlist(unittest.TestCase):
    """원본 compute_watchlist와 신규 경로의 D일 후보 동치성 (키움 미사용)."""

    def setUp(self):
        if not KRX_DB.exists():
            self.skipTest("krx_ohlcv.duckdb 없음")
        self.dates = _krx_dates()
        if len(self.dates) < LOOKBACK + 1:
            self.skipTest(f"거래일 {len(self.dates)} < {LOOKBACK + 1}")
        self._tmp = tempfile.TemporaryDirectory()
        self.wl_db = Path(self._tmp.name) / "wl.sqlite3"

    def tearDown(self):
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def _seed_snapshot_from_krx(self, date: str) -> None:
        """D일 거래량 top30을 krx 데이터로 intraday_ranking에 시딩 (키움 대체)."""
        krx = duckdb.connect(str(KRX_DB), read_only=True)
        try:
            rows = krx.execute(
                "SELECT ticker, volume, close FROM ohlcv "
                "WHERE date = ? AND volume IS NOT NULL "
                "ORDER BY volume DESC, ticker LIMIT ?",
                [date, N_TOP],
            ).fetchall()
        finally:
            krx.close()
        with connect_rw(self.wl_db) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS intraday_ranking ("
                "date TEXT, rank INTEGER, ticker TEXT, name TEXT, "
                "volume INTEGER, close INTEGER, PRIMARY KEY (date, ticker))"
            )
            con.executemany(
                "INSERT INTO intraday_ranking VALUES (?, ?, ?, ?, ?, ?)",
                [(date, i + 1, t, "", v, c) for i, (t, v, c) in enumerate(rows)],
            )

    def test_same_candidates_as_original(self):
        d = self.dates[-1]
        from_date = self.dates[-(LOOKBACK + 1)]

        # 원본 경로
        krx = duckdb.connect(str(KRX_DB), read_only=True)
        try:
            expected = set(compute_watchlist(krx, from_date, d).get(d, []))
        finally:
            krx.close()

        # 신규 경로
        self._seed_snapshot_from_krx(d)
        got = set(run_candidates(self.wl_db, KRX_DB, d))

        self.assertEqual(got, expected)


class TestLiveCandidates(unittest.TestCase):
    """키움 실호출 스냅샷 → 후보 산출 → 제약 + 멱등."""

    def setUp(self):
        if not KRX_DB.exists():
            self.skipTest("krx_ohlcv.duckdb 없음")
        self._tmp = tempfile.TemporaryDirectory()
        self.wl_db = Path(self._tmp.name) / "wl.sqlite3"
        self.date = datetime.now().strftime("%Y%m%d")

    def tearDown(self):
        self._tmp.cleanup()

    def test_live_pipeline(self):
        n = run(self.wl_db, date=self.date)          # STEP 1: 키움 실호출
        self.assertEqual(n, N_TOP)

        candidates = run_candidates(self.wl_db, KRX_DB, self.date)

        with connect_ro(self.wl_db) as con:
            snapshot = {
                r[0]: r[1]
                for r in con.execute(
                    "SELECT ticker, close FROM intraday_ranking WHERE date = ?", [self.date]
                ).fetchall()
            }
            wl_rows = [
                r[0]
                for r in con.execute(
                    "SELECT stock_code FROM watchlist WHERE date = ? ORDER BY stock_code", [self.date]
                ).fetchall()
            ]

        krx = duckdb.connect(str(KRX_DB), read_only=True)
        try:
            union = fetch_past_top_union(krx, self.date)
        finally:
            krx.close()

        self.assertEqual(sorted(candidates), wl_rows, "반환값과 watchlist 테이블 불일치")
        for t in candidates:
            self.assertIn(t, snapshot, f"후보 {t}가 당일 top30에 없음")
            self.assertNotIn(t, union, f"후보 {t}가 과거 60거래일 top30에 있음")
            self.assertGreater(snapshot[t], PENNY_MAX, f"후보 {t} 동전주 미컷")

        # 멱등
        candidates2 = run_candidates(self.wl_db, KRX_DB, self.date)
        self.assertEqual(sorted(candidates2), wl_rows)
        with connect_ro(self.wl_db) as con:
            cnt = con.execute(
                "SELECT COUNT(*) FROM watchlist WHERE date = ?", [self.date]
            ).fetchone()[0]
        self.assertEqual(cnt, len(wl_rows), "재실행 후 행 수 변동")


if __name__ == "__main__":
    unittest.main()
