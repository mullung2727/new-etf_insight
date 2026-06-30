from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

CLOSE_BET_PATHS = {
    "etl/scripts/run_close_bet.py",
    "etl/scripts/report_close_bet_order.py",
    "etl/tests/test_close_bet.py",
    "etl/tests/test_close_bet_integration.py",
    "ops/scheduled-tasks/run-close-bet-order.ps1",
    "ops/scheduled-tasks/close-bet-order.xml",
    "ops/batches/daily-close-bet-order.md",
}


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def staged_files() -> set[str]:
    output = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    )
    return {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}


def ps_parse(path: Path) -> None:
    command = (
        "$ErrorActionPreference='Stop'; "
        f"$null = [scriptblock]::Create((Get-Content -Encoding UTF8 -Raw -LiteralPath '{path}'))"
    )
    run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command])


def close_bet_checks() -> None:
    ps_parse(ROOT / "ops" / "scheduled-tasks" / "run-close-bet-order.ps1")

    python = ROOT / "etl" / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = Path(sys.executable)
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    run(
        [
            str(python),
            "-m",
            "unittest",
            "tests/test_close_bet.py",
            "tests/test_close_bet_integration.py",
        ],
        cwd=ROOT / "etl",
        env=env,
    )


def main() -> int:
    changed = staged_files()
    if not changed:
        return 0

    if changed & CLOSE_BET_PATHS:
        print("[pre-commit] close-bet contract files changed; running focused checks")
        close_bet_checks()
    else:
        print("[pre-commit] no targeted batch contract files changed; skipping focused checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
