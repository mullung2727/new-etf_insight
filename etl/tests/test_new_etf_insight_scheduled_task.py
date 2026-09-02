from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "ops" / "scheduled-tasks" / "run-new-etf-insight-batch.ps1"
PYTHON = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"


def _invoke_step_function() -> str:
    text = RUNNER.read_text(encoding="utf-8-sig")
    start = text.index("function Invoke-Step {")
    end = text.index("\n\ntry {", start)
    return text[start:end]


def _run_probe(exit_code: int) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        probe = temp / "probe.py"
        probe.write_text(
            "import sys\nprint('diagnostic only', file=sys.stderr)\n"
            f"raise SystemExit({exit_code})\n",
            encoding="utf-8",
        )
        ps_script = temp / "probe.ps1"
        log_path = str(temp / "probe.log").replace("\\", "\\\\")
        ps_script.write_text(
            '$ErrorActionPreference = "Stop"\n'
            f'$log = "{log_path}"\n'
            + _invoke_step_function()
            + "\n"
            + f'Invoke-Step "probe" "{PYTHON}" @("{probe}")\n',
            encoding="utf-8-sig",
        )
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps_script),
            ],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )


class NewEtfInsightScheduledTaskTest(unittest.TestCase):
    def test_stderr_with_zero_exit_is_not_treated_as_failure(self) -> None:
        result = _run_probe(0)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("diagnostic only", result.stdout + result.stderr)

    def test_report_is_passed_by_json_file_not_fragile_command_line_message(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8-sig")

        self.assertIn('"--json", $reportJson', runner)
        self.assertNotIn('"--message", $summary', runner)

    def test_nonzero_exit_is_treated_as_failure(self) -> None:
        result = _run_probe(7)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("probe failed with exit code 7", result.stdout + result.stderr)

    def test_pipeline_chatter_is_not_put_in_report_payload(self) -> None:
        runner = RUNNER.read_text(encoding="utf-8-sig")
        start = runner.index("$runner = @'\n") + len("$runner = @'\n")
        end = runner.index("\n'@.Replace", start)
        embedded_python = runner[start:end]

        # 로그 파일은 Tee-Object 만 연다. 파이썬이 같은 파일을 붙잡으면 공유 위반으로
        # 파이프라인이 통째로 죽는다(신규 ETF 후보가 있는 날만 재현되던 배치 사망).
        self.assertNotIn("__LOG__", embedded_python)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            package = temp / "new_etf_insight"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "daily_pipeline.py").write_text(
                "def run_daily_pipeline(*args):\n"
                "    print('{\\\"fund_name\\\": \\\"name with spaces\\\"}')\n"
                "    return {\n"
                "        'begin': '20260723', 'candidate_count': 1,\n"
                "        'results': [{'action': 'created'}],\n"
                "        'db_synced': 39, 'db_path': 'db/etf.sqlite3',\n"
                "    }\n",
                encoding="utf-8",
            )
            report_path = temp / "report.json"
            code = embedded_python.replace("__DATE__", "20260723").replace(
                "__REPORT_JSON__", str(report_path)
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp)
            result = subprocess.run(
                [str(PYTHON), "-"],
                input=code,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                cwd=temp,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["messages"]), 1)
            self.assertIn("[new_etf_insight daily] 20260723", payload["messages"][0])
            self.assertNotIn("fund_name", payload["messages"][0])
            self.assertIn("fund_name", result.stdout)


if __name__ == "__main__":
    unittest.main()
