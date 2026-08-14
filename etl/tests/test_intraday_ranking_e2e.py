"""build_intraday_ranking e2e 테스트 — 실제 키움 API 호출 + 임시 DuckDB.

검증:
  - ka10030 실호출 → 임시 DB에 순수 거래량 top30 적재 (동전주 컷은 후보 단계)
  - ticker 6자리(suffix 제거), 종가 > 0, rank 1~30 연속, volume 내림차순
  - 같은 날 재실행 멱등 (행 수 불변, 중복 없음)

주의: 네트워크 + 키움 토큰 필요. 휴장일에도 직전 거래일 데이터로 구조 검증은 유효.
"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.build_intraday_ranking import N_TOP, _KA10030_BODY, run
from scripts.wl_sqlite import connect_ro


class TestKa10030RequestContract(unittest.TestCase):
    def test_requests_krx_only_to_match_historical_krx_ohlcv(self):
        self.assertEqual(_KA10030_BODY["stex_tp"], "1")


class TestIntradayRankingE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "watchlist_test.sqlite3"
        self.date = datetime.now().strftime("%Y%m%d")

    def tearDown(self):
        self._tmp.cleanup()

    def _rows(self):
        with connect_ro(self.db_path) as con:
            return con.execute(
                "SELECT date, rank, ticker, name, volume, close "
                "FROM intraday_ranking WHERE date = ? ORDER BY rank",
                [self.date],
            ).fetchall()

    def test_e2e_fetch_upsert_and_idempotent(self):
        # 1차 실행
        n = run(self.db_path, date=self.date)
        rows = self._rows()

        self.assertEqual(n, N_TOP, f"run() 반환 행 수 {n} != {N_TOP}")
        self.assertEqual(len(rows), N_TOP)

        ranks = [r[1] for r in rows]
        self.assertEqual(ranks, list(range(1, N_TOP + 1)), "rank 1~30 연속 아님")

        for date, rank, ticker, name, volume, close in rows:
            self.assertEqual(date, self.date)
            self.assertEqual(len(ticker), 6, f"ticker 6자리 아님: {ticker!r}")
            self.assertNotIn("_", ticker, f"suffix 미제거: {ticker!r}")
            self.assertTrue(name, f"rank {rank} 종목명 비어있음")
            self.assertGreater(volume, 0, f"{ticker} volume <= 0")
            self.assertGreater(close, 0, f"{ticker} close <= 0")

        volumes = [r[4] for r in rows]
        self.assertEqual(volumes, sorted(volumes, reverse=True), "volume 내림차순 아님")

        # 2차 실행 — 멱등 (장중이면 값은 변해도 행 수/제약은 동일)
        n2 = run(self.db_path, date=self.date)
        rows2 = self._rows()
        self.assertEqual(n2, N_TOP)
        self.assertEqual(len(rows2), N_TOP, "재실행 후 행 수 변동(중복) 발생")

        with connect_ro(self.db_path) as con:
            dup = con.execute(
                "SELECT ticker, COUNT(*) FROM intraday_ranking "
                "WHERE date = ? GROUP BY ticker HAVING COUNT(*) > 1",
                [self.date],
            ).fetchall()
        self.assertEqual(dup, [], f"중복 ticker: {dup}")


if __name__ == "__main__":
    unittest.main()
