"""pullback Windows 스케줄·PowerShell 래퍼 계약 테스트."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.run_pullback_order import build_arg_parser


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "scheduled-tasks"


class PullbackScheduleTest(unittest.TestCase):
    def test_order_cli_defaults_to_dry_run(self):
        self.assertEqual(build_arg_parser().parse_args([]).dry_run, "true")

    def test_trading_wrappers_call_only_pullback_scripts(self):
        expected = {
            "run-trading-order.ps1": "scripts\\run_pullback_order.py",
            "run-trading-verify.ps1": "scripts\\run_pullback_verify.py",
            "run-trading-exit.ps1": "scripts\\run_pullback_exit.py",
        }
        forbidden = (
            "scripts\\run_close_bet.py",
            "scripts\\run_verify.py",
            "scripts\\run_close_bet_exit.py",
        )
        for filename, script in expected.items():
            with self.subTest(filename=filename):
                text = (OPS / filename).read_text(encoding="utf-8")
                self.assertIn(script, text)
                for close_bet_script in forbidden:
                    self.assertNotIn(close_bet_script, text)
                self.assertIn("broker-url", text)

    def test_trading_exit_declares_regular_session_start(self):
        # Task는 08:50에 시작해 기동 실패를 장전에 드러낸다. 09:00 정책은 Python 기본값에만
        # 두지 않고 운영 action에도 적어, 인자를 읽는 것만으로 정책이 보이게 한다.
        text = (OPS / "run-trading-exit.ps1").read_text(encoding="utf-8")
        self.assertIn('"--window-start", "09:00:00"', text)

    def test_trading_exit_starts_one_pullback_worker(self):
        text = (OPS / "run-trading-exit.ps1").read_text(encoding="utf-8")
        self.assertEqual(text.count("Start-Process"), 1)
        # ExitCode 판정 전 워커 종료를 기다려야 한다. PS 5.1은 -Wait 없이 -PassThru만
        # 쓰면 ExitCode가 $null이라 $null -ne 0 으로 항상 rc=1(거짓 실패).
        # 주석에도 "-Wait"이 있어 주석 제외 후 검사한다.
        code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
        self.assertIn("-Wait", code)

    def test_xml_schedule_times_match_design(self):
        expected = {
            "trading-order.xml": "T15:19:00",
            "trading-verify.xml": "T16:00:00",
            "trading-exit.xml": "T08:50:00",
        }
        for filename, start in expected.items():
            with self.subTest(filename=filename):
                text = (OPS / filename).read_text(encoding="utf-8")
                self.assertIn(start, text)
                self.assertIn("run-trading-", text)


if __name__ == "__main__":
    unittest.main()
