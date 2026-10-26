"""broker 실시간 연결 사용처 가드 (네트워크 없이).

실매매 코드가 broker 실시간 연결(웹소켓·SSE·체결통보 구독)을 쓰기 시작하면 실패한다.
리서치 녹화기는 '실매매는 broker 실시간 연결을 안 쓴다'는 전제로 broker 끊김에도 녹화를 계속하므로(2026-10-15),
그 전제가 깨지는 순간 이 테스트가 다시 보게 만든다.

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

PATTERNS = [
    re.compile(r"""["']/events"""),
    re.compile(r"""/realtime/|["']/realtime["']"""),
    re.compile(r"wss://"),
    re.compile(r"^\s*(import|from)\s+websockets"),
    re.compile(r"bus\.subscribe\("),
]

# (저장소 기준 posix 경로, 줄 strip 문자열) — 현재 확인된 사용처
ALLOWED = {
    ("etl/scripts/run_orderbook_recorder.py", 'return self._call("GET", "/realtime/orderbook", timeout)'),
    ("etl/scripts/run_orderbook_recorder.py", 'return self._call("POST", "/realtime/orderbook", timeout, json={"codes": codes})'),
    ("etl/scripts/run_orderbook_recorder.py", 'return self._call("DELETE", "/realtime/orderbook", timeout)'),
    ("etl/scripts/run_orderbook_recorder.py", 'reason = recorder.execute(sse_factory, cfg["broker_url"].rstrip("/") + "/events")'),
    ("etl/scripts/run_orderbook_research_recorder.py", '"real": "wss://api.kiwoom.com:10000/api/dostk/websocket",'),
    ("etl/scripts/run_orderbook_research_recorder.py", '"paper": "wss://mockapi.kiwoom.com:10000/api/dostk/websocket",'),
    ("etl/scripts/run_orderbook_research_recorder.py", 'self._url = self._base + "/events?ch=system"'),
    ("etl/scripts/run_orderbook_research_recorder.py", 'return requests.get(base + "/realtime/orderbook", timeout=5).json()'),
    ("etl/scripts/run_orderbook_research_recorder.py", "import websockets"),
    ("broker/kiwoom/conditions.py", "import websockets"),
    ("broker/kiwoom/config.py", '"paper": "wss://mockapi.kiwoom.com:10000/api/dostk/websocket",'),
    ("broker/kiwoom/config.py", '"real": "wss://api.kiwoom.com:10000/api/dostk/websocket",'),
    ("broker/kiwoom/ws/manager.py", "import websockets"),
    ("broker/routers/events.py", '@router.get("/events")'),
    ("broker/routers/events.py", "bus.subscribe(c, queue)"),
    ("broker/main.py", 'bus.subscribe("00", queue)'),  # 체결통보 → 투자노트 재동기화, 매매 판단 아님
    ("broker/routers/realtime.py", 'router = APIRouter(prefix="/realtime", tags=["realtime"])'),
}

NEW_USAGE_MSG = (
    "broker 실시간 연결(웹소켓·SSE·체결통보 구독) 사용처가 새로 생겼다.\n"
    "리서치 녹화기(run_orderbook_research_recorder.py)는 '실매매가 broker 실시간 연결을 안 쓴다'는 전제로 broker 끊김에도 녹화를 계속한다(2026-10-15).\n"
    "실매매 코드라면: 녹화기 재접속이 broker 를 밀어낼 위험부터 검토하고 docs/done/PLAN_ORDERBOOK_RESEARCH_RECORDING.md 를 고친 뒤 ALLOWED 에 추가할 것.\n"
    "실매매와 무관하면: 이유를 주석으로 남기고 ALLOWED 에 추가.\n"
)


US_PATTERNS = [
    re.compile(r"""["'](F4|F5|FE|FT)["']|usa2028[01]|usa2029[01]"""),
]

# (저장소 기준 posix 경로, 줄 strip 문자열) — 미국 실시간이 아닌 확인된 사용처
US_ALLOWED = {
    ("research/private/rights_issue/free_dip.py", '("F1", (0,)), ("F2", (1,)), ("F3", (2,)), ("F4", (3,)),'),  # placebo 요인명, 미국 실시간 아님
}

US_NEW_USAGE_MSG = (
    "미국 실시간/조건검색을 쓰기 시작했다 — :8003 앱키는 리서치 녹화기가 웹소켓을 쓴다(docs/PLAN_RECORDER_US_APPKEY.md). 겹침 검토 후 이 테스트를 고칠 것"
)


def scan(root, patterns=None) -> list[tuple[str, int, str]]:
    """root 아래 검사 대상 .py 에서 패턴에 걸린 줄 → [(posix 경로, 줄번호, 줄)]."""
    root = Path(root)
    pats = PATTERNS if patterns is None else patterns
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
                    if any(p.search(line) for p in pats):
                        hits.append((rel, n, line))
    return hits


class RealtimeUsageGuardCase(unittest.TestCase):
    def test_repo_realtime_usage_is_allowed(self):
        hits = scan(REPO_ROOT)
        found = {(p, line.strip()) for p, _, line in hits}
        new = [(p, n, line) for p, n, line in hits if (p, line.strip()) not in ALLOWED]
        stale = sorted(ALLOWED - found)
        problems = []
        if new:
            problems.append(NEW_USAGE_MSG + "\n".join(f"{p}:{n}: {line.strip()}" for p, n, line in new))
        if stale:
            problems.append("ALLOWED 에 있지만 코드에 없는 항목 — 목록 정리 필요\n"
                            + "\n".join(f"{p}: {line}" for p, line in stale))
        if problems:
            self.fail("\n\n".join(problems))

    def test_scan_catches_new_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "etl" / "scripts" / "x.py"
            f.parent.mkdir(parents=True)
            f.write_text('def f(base):\n    return requests.get(base + "/events")\n', encoding="utf-8")
            hits = scan(tmp)
        self.assertEqual(hits, [("etl/scripts/x.py", 2, '    return requests.get(base + "/events")')])


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
