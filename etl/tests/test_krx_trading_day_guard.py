"""KRX 거래일 선확인 가드 회귀 테스트."""
from __future__ import annotations

import os
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

    def test_20261005_is_holiday(self):
        result = self._run("20261005")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("휴장일", result.stdout)

    def test_20261006_is_trading_day(self):
        result = self._run("20261006")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("거래일", result.stdout)

    def test_20261003_weekend_is_holiday(self):
        result = self._run("20261003")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("휴장일", result.stdout)


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


@unittest.skipIf(sys.platform != "win32", "Windows PowerShell runner test")
class TestJevqPrepRunnerHolidayGuard(unittest.TestCase):
    RUNNER = ETL_DIR.parent / "research" / "private" / "jev_quant_live" / "ops" / "run-jevq-prep.ps1"
    ROOT_LITERAL = r"C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
    EXE_LITERAL = r"etl\.venv\Scripts\python.exe"

    FAKE_PS1 = (
        "$marker = $env:JEVQ_MARKER\n"
        "$wanted = $env:JEVQ_PRECHECK_CODE\n"
        '$joined = ($args -join " ")\n'
        'if ($joined -like "*check_krx_trading_day.py*") {\n'
        '  "precheck" | Add-Content -Path $marker\n'
        '  Write-Output "[fake] precheck"\n'
        "  exit [int]$wanted\n"
        "}\n"
        'if ($args -contains "-m") {\n'
        '  "prep" | Add-Content -Path $marker\n'
        '  Write-Output "[fake] prep"\n'
        "  exit 0\n"
        "}\n"
        '"unknown" | Add-Content -Path $marker\n'
        "exit 0\n"
    )

    def _run_runner(self, precheck_code: int):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            marker = tmp / "invocations.txt"
            fake = tmp / "fake_python.ps1"
            fake.write_text(self.FAKE_PS1, encoding="utf-8")
            text = self.RUNNER.read_text(encoding="utf-8-sig")
            self.assertIn(self.ROOT_LITERAL, text)
            self.assertIn(self.EXE_LITERAL, text)
            text = text.replace(self.ROOT_LITERAL, str(tmp))
            text = text.replace(self.EXE_LITERAL, str(fake))
            temp_runner = tmp / "run-jevq-prep-test.ps1"
            temp_runner.write_text(text, encoding="utf-8-sig")
            env = dict(os.environ)
            env["JEVQ_MARKER"] = str(marker)
            env["JEVQ_PRECHECK_CODE"] = str(precheck_code)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(temp_runner)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                check=False,
            )
            invocations = marker.read_text(encoding="utf-8").split() if marker.exists() else []
            return result, invocations

    def test_holiday_skips_prep(self):
        result, invocations = self._run_runner(3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("precheck", invocations)
        self.assertNotIn("prep", invocations)
        self.assertIn("NON_TRADING_DAY", result.stdout)

    def test_trading_runs_prep(self):
        result, invocations = self._run_runner(0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("precheck", invocations)
        self.assertIn("prep", invocations)

    def test_precheck_error_skips_prep(self):
        result, invocations = self._run_runner(2)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("precheck", invocations)
        self.assertNotIn("prep", invocations)


if __name__ == "__main__":
    unittest.main()
