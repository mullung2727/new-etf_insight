"""KRX 거래일 선확인 가드 회귀 테스트."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_intraday_ranking import run
from scripts.wl_sqlite import connect_ro

ETL_DIR = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ETL_DIR / "scripts" / "check_krx_trading_day.py"
SCHEDULED_TASK = ETL_DIR.parent / "ops" / "scheduled-tasks" / "run-watchlist-intraday.ps1"


class TestScheduledTaskGuard(unittest.TestCase):
    def test_trading_day_check_precedes_all_market_steps(self):
        script = SCHEDULED_TASK.read_text(encoding="utf-8-sig")
        check_at = script.index("check_krx_trading_day.py")
        build_at = script.index('Invoke-Step "build intraday ranking"')
        self.assertLess(check_at, build_at)
        self.assertIn("$LASTEXITCODE -eq 3", script)
        self.assertIn("exit 0", script[check_at:build_at])


class TestTradingDayCheckCli(unittest.TestCase):
    def _run(self, date: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECK_SCRIPT), "--date", date],
            cwd=ETL_DIR,
            text=True,
            encoding="utf-8",  # 자식이 한글을 UTF-8로 출력 — 부모 기본 cp949 디코드 크래시 방지
            capture_output=True,
            check=False,
        )

    def test_constitution_day_2026_is_holiday(self):
        result = self._run("20260717")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("휴장일", result.stdout)

    def test_adjacent_weekday_is_trading_day(self):
        result = self._run("20260716")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("거래일", result.stdout)


class TestIntradayRankingTradingDayGuard(unittest.TestCase):
    def test_holiday_stops_before_kiwoom_fetch_and_db_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "watchlist.sqlite3"
            with patch(
                "scripts.build_intraday_ranking.fetch_top_volume",
                side_effect=AssertionError("휴장일에는 키움 API를 호출하면 안 됨"),
            ):
                written = run(db, date="20260717")

            self.assertEqual(written, 0)
            self.assertFalse(db.exists(), "휴장일에는 DB 파일도 만들지 않아야 함")


if __name__ == "__main__":
    unittest.main()
