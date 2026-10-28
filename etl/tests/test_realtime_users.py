"""미국 실시간 사용처 가드 (네트워크 없이).

:8003 앱키는 리서치 녹화기가 웹소켓을 쓴다(2026-10-26, docs/done/PLAN_RECORDER_US_APPKEY.md).
broker-us 가 웹소켓을 켜거나 코드가 미국 실시간·조건검색을 쓰기 시작하면 실패한다.
국내 broker 실시간 사용처 가드는 2026-10-28 삭제 — 녹화기가 :8001 앱키를 안 써서 broker 를 밀어낼 수 없음.

Usage (from etl/):
    PYTHONPATH=. uv run python -m unittest tests.test_realtime_users -v
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("etl/scripts", "etl/src", "broker", "research/private")
SKIP_DIRS = {"tests", "__pycache__", ".venv", "rejected"}  # test_*.py 파일명도 제외(아래 scan)


US_PATTERNS = [
    re.compile(r"""["'](F4|F5|FE|FT)["']|usa2028[01]|usa2029[01]"""),
]

# (저장소 기준 posix 경로, 줄 strip 문자열) — 미국 실시간이 아닌 확인된 사용처
US_ALLOWED = {
    ("research/private/rights_issue/free_dip.py", '("F1", (0,)), ("F2", (1,)), ("F3", (2,)), ("F4", (3,)),'),  # placebo 요인명, 미국 실시간 아님
}

US_NEW_USAGE_MSG = (
    "미국 실시간/조건검색을 쓰기 시작했다 — :8003 앱키는 리서치 녹화기가 웹소켓을 쓴다(docs/done/PLAN_RECORDER_US_APPKEY.md). 겹침 검토 후 이 테스트를 고칠 것"
)


def scan(root, patterns) -> list[tuple[str, int, str]]:
    """root 아래 검사 대상 .py 에서 패턴에 걸린 줄 → [(posix 경로, 줄번호, 줄)]."""
    root = Path(root)
    hits = []
    for base in SCAN_ROOTS:
        top = root / base
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith("_backup")]
            for fn in filenames:
                # 테스트 파일(test_*.py, 이 파일 포함)은 검사 제외
                if not fn.endswith(".py") or fn.startswith("test_"):
                    continue
                path = Path(dirpath) / fn
                text = path.read_text(encoding="utf-8", errors="replace")
                rel = path.relative_to(root).as_posix()
                for n, line in enumerate(text.splitlines(), 1):
                    if any(p.search(line) for p in patterns):
                        hits.append((rel, n, line))
    return hits


class ScanCase(unittest.TestCase):
    def test_scan_catches_new_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "etl" / "scripts" / "x.py"
            f.parent.mkdir(parents=True)
            f.write_text('def f(ws):\n    ws.reg("F4")\n', encoding="utf-8")
            hits = scan(tmp, patterns=US_PATTERNS)
        self.assertEqual(hits, [("etl/scripts/x.py", 2, '    ws.reg("F4")')])


class UsBrokerGuardCase(unittest.TestCase):
    def test_us_broker_ws_disabled(self):
        text = (REPO_ROOT / "scripts" / "restart_all_servers.ps1").read_text(encoding="utf-8-sig")
        lines = text.splitlines()

        def _env_block(name):
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(name + " =") or stripped.startswith(name + "="):
                    block = [line]
                    j = i + 1
                    while block[-1].rstrip().endswith("+") and j < len(lines):
                        block.append(lines[j])
                        j += 1
                    return "\n".join(block)
            return ""

        self.assertIn("KIWOOM_WS_ENABLED=0", _env_block("$usEnv"))
        self.assertNotIn("KIWOOM_WS_ENABLED=0", _env_block("$high52Env"))

    def test_no_us_realtime_types(self):
        hits = scan(REPO_ROOT, patterns=US_PATTERNS)
        new = [(p, n, line) for p, n, line in hits if (p, line.strip()) not in US_ALLOWED]
        if new:
            self.fail(US_NEW_USAGE_MSG + "\n"
                      + "\n".join(f"{p}:{n}: {line.strip()}" for p, n, line in new))


if __name__ == "__main__":
    unittest.main()
