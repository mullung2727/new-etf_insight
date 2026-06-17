"""STEP 3 휴장일 가드 e2e 테스트.

휴장일에 ka10030이 직전 거래일 데이터를 그대로 반환하는 상황 검증:
직전 스냅샷과 (ticker, volume)이 완전 동일하면 저장 skip.

실 API 데이터를 1회 수집해 rows 주입으로 재현 (장중엔 호출 간 거래량이
변해서 실호출 2회 방식은 비결정적 — 데이터 자체는 실데이터).
"""
import tempfile
import unittest
from pathlib import Path

from scripts.build_intraday_ranking import (
    N_TOP,
    _HOSTS,
    _kiwoom_env,
    fetch_top_volume,
    get_token,
    parse_ranking,
    run,
)
from scripts.wl_sqlite import connect_ro
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


class TestHolidayGuardE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv(ROOT / ".env")
        env = _kiwoom_env()
        host = _HOSTS[env]
        token = get_token(host, env)
        cls.rows = parse_ranking(fetch_top_volume(token, host))  # 실데이터 1회

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "wl.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def _count(self, date: str) -> int:
        with connect_ro(self.db) as con:
            return con.execute(
                "SELECT COUNT(*) FROM intraday_ranking WHERE date = ?", [date]
            ).fetchone()[0]

    def test_skip_when_identical_to_prev_snapshot(self):
        """휴장일 재현: 전일 스냅샷과 완전 동일 → skip (0 반환, 미저장)."""
        n1 = run(self.db, date="20990101", rows=self.rows)
        self.assertEqual(n1, N_TOP)

        n2 = run(self.db, date="20990102", rows=self.rows)  # 동일 데이터 = 휴장일
        self.assertEqual(n2, 0, "휴장일 가드 미동작")
        self.assertEqual(self._count("20990102"), 0, "휴장일인데 스냅샷 저장됨")
        self.assertEqual(self._count("20990101"), N_TOP, "기존 스냅샷 훼손")

    def test_store_when_volumes_differ(self):
        """정상 거래일: 거래량 1건이라도 다르면 저장."""
        n1 = run(self.db, date="20990101", rows=self.rows)
        self.assertEqual(n1, N_TOP)

        changed = [
            (rank, ticker, name, volume + (1 if rank == 1 else 0), close)
            for rank, ticker, name, volume, close in self.rows
        ]
        n2 = run(self.db, date="20990102", rows=changed)
        self.assertEqual(n2, N_TOP)
        self.assertEqual(self._count("20990102"), N_TOP)

    def test_store_when_no_prev_snapshot(self):
        """콜드스타트: 직전 스냅샷 없으면 가드 통과, 정상 저장."""
        n = run(self.db, date="20990101", rows=self.rows)
        self.assertEqual(n, N_TOP)
        self.assertEqual(self._count("20990101"), N_TOP)

    def test_same_date_rerun_not_blocked(self):
        """같은 날 재실행은 가드 대상 아님 (직전 '다른' 날짜와만 비교)."""
        run(self.db, date="20990101", rows=self.rows)
        n = run(self.db, date="20990101", rows=self.rows)  # 동일 데이터, 같은 날짜
        self.assertEqual(n, N_TOP, "같은 날 멱등 재실행이 가드에 막힘")
        self.assertEqual(self._count("20990101"), N_TOP)


if __name__ == "__main__":
    unittest.main()
