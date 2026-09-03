from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "ops" / "scheduled-tasks" / "run-daily-trading-result.ps1"
XML = ROOT / "ops" / "scheduled-tasks" / "daily-trading-result.xml"
REGISTRY = ROOT / "ops" / "batches" / "openclaw-cron.registry.json"
README = ROOT / "ops" / "batches" / "README.md"
INSTRUCTION = ROOT / "ops" / "batches" / "daily-trading-result.md"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class DailyTradingResultScheduleTest(unittest.TestCase):
    def test_windows_task_runs_report_only_at_1620(self) -> None:
        tree = ET.parse(XML)
        root = tree.getroot()
        start = root.findtext(".//t:CalendarTrigger/t:StartBoundary", namespaces=NS)
        uri = root.findtext(".//t:RegistrationInfo/t:URI", namespaces=NS)
        arguments = root.findtext(".//t:Actions/t:Exec/t:Arguments", namespaces=NS) or ""

        self.assertTrue(start.endswith("T16:20:00"), start)
        self.assertEqual(uri, r"\new-etf_insight\daily-trading-result")
        self.assertIn("run-daily-trading-result.ps1", arguments)

    def test_runner_reuses_report_transport_and_never_calls_order_scripts(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8-sig")

        self.assertIn('.venv\\Scripts\\python.exe', runner)
        self.assertIn('scripts\\report_daily_trading_result.py', runner)
        self.assertIn('scripts\\send_report_messages.py', runner)
        self.assertIn('param(', runner)
        self.assertIn('[string]$Date', runner)
        self.assertIn('@("--date", $Date)', runner)
        self.assertNotIn('run_close_bet.py', runner)
        self.assertNotIn('run_pullback_order.py', runner)
        self.assertNotIn('run_close_bet_exit.py', runner)
        self.assertNotIn('run_pullback_exit.py', runner)

    def test_registry_instruction_xml_runner_and_readme_match(self) -> None:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        job = next(item for item in registry["jobs"] if item["name"] == "daily-trading-result")

        self.assertFalse(job["enabled"])
        self.assertEqual(job["schedule"]["expr"], "20 16 * * 1-5")
        self.assertEqual(job["schedule"]["tz"], "Asia/Seoul")
        self.assertTrue(job["windowsTask"]["enabled"])
        self.assertEqual(job["instructionFile"], "ops/batches/daily-trading-result.md")
        self.assertEqual(job["windowsTask"]["xmlFile"], "ops/scheduled-tasks/daily-trading-result.xml")
        self.assertEqual(job["windowsTask"]["runnerScript"], "ops/scheduled-tasks/run-daily-trading-result.ps1")
        self.assertIn("16:20:00", job["runNote"])
        self.assertIn("daily-trading-result", README.read_text(encoding="utf-8"))
        instruction = INSTRUCTION.read_text(encoding="utf-8")
        self.assertIn("보고 전용", instruction)
        self.assertIn("-Date 20260824", instruction)


if __name__ == "__main__":
    unittest.main()
