"""배치 결과를 설정된 알림 채널로 보낸다 (채널 교체는 notify.py 참고).

리포트 배치 runner(ops/scheduled-tasks/*.ps1)가 OpenClaw message 대신 이걸 호출해,
게이트웨이가 꺼져 있어도 결과를 보고한다. 채널(Discord/Telegram/…)은 코드가 아니라
`.env`의 NOTIFY_CHANNEL로 정해진다 — notify.notify()가 디스패치한다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from scripts.notify import notify
except ImportError:
    from notify import notify


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"


def _load_messages(path: Path) -> list[str]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        return [str(message) for message in payload["messages"]]
    if isinstance(payload, list):
        return [str(message) for message in payload]
    raise ValueError(f"Unsupported message payload: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send batch report messages to the configured channel")
    parser.add_argument("--json", type=Path, help="JSON file with a messages array")
    parser.add_argument("--message", help="Single message to send")
    parser.add_argument("--channel", help="Override NOTIFY_CHANNEL (discord/telegram/…)")
    parser.add_argument("--best-effort", action="store_true")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)

    messages: list[str] = []
    if args.json:
        messages.extend(_load_messages(args.json))
    if args.message:
        messages.append(args.message)
    if not messages:
        raise SystemExit("No messages to send")

    ok = True
    for index, message in enumerate(messages, start=1):
        sent = notify(message, channel=args.channel)
        print(f"[send_report_messages] {index}/{len(messages)} sent={sent}")
        ok = ok and sent

    if ok or args.best_effort:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
