"""텔레그램 크로스채널 종목 요약 파이프라인 오케스트레이터.

discover → analyze(LLM) → digest 3단계를 순차 실행하고 실패 시 중단·전파한다.
기존엔 이 순서/에러처리가 PS 러너(run-telegram-stock-digest.ps1)에만 있어 재현·테스트가
어려웠다. 여기로 올려 단위테스트(순서/에러전파)를 붙인다. PS 러너는 이 진입점 1개만 호출.

각 단계는 별 프로세스(sys.executable)로 실행 — analyze 는 무거운 LangGraph 라 in-process
결합보다 프로세스 격리가 안전(한 단계 크래시가 다음 실행 상태 오염 안 함). cwd=etl 전제.

Usage (from etl/):
    uv run python scripts/run_telegram_pipeline.py --date 2026-07-06 --session close
    uv run python scripts/run_telegram_pipeline.py --date 2026-07-06 --session close --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path)

# (단계명, 스크립트 경로). cwd=etl 기준 상대경로.
STAGES = [
    ("discover", "scripts/discover_telegram_stock_candidates.py"),
    ("analyze", "scripts/telegram_langgraph/telegram_analysis_langgraph.py"),
    ("digest", "scripts/send_telegram_stock_digest.py"),
]


def run_pipeline(
    date: str,
    session: str,
    *,
    start_date: str | None = None,
    digest_channel: str | None = None,
    dry_run: bool = False,
    runner=subprocess.run,
) -> None:
    """3단계 순차 실행. 한 단계라도 returncode≠0 이면 즉시 RuntimeError 로 중단.

    runner 는 테스트용 주입(기본 subprocess.run). digest 단계에만 --channel/--dry-run 부착.
    """
    for name, script in STAGES:
        cmd = [sys.executable, script, "--date", date, "--session", session]
        if start_date and name in {"discover", "analyze"}:
            cmd += ["--start-date", start_date]
        if name == "digest":
            if digest_channel:
                cmd += ["--channel", digest_channel]
            if dry_run:
                cmd += ["--dry-run"]
        result = runner(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"telegram pipeline stage '{name}' failed (rc={result.returncode})")


def main() -> int:
    p = argparse.ArgumentParser(description="텔레그램 종목 요약 파이프라인 (discover→analyze→digest)")
    p.add_argument("--date", required=True, help="KST 일자 YYYY-MM-DD")
    p.add_argument("--session", default="close", help="morning|close|evening")
    p.add_argument("--start-date", help="증분 조회 시작 KST 일자(기본: --date)")
    p.add_argument("--channel", help="digest notify 채널 (discord/telegram/…)")
    p.add_argument("--dry-run", action="store_true", help="digest 전송 없이 메시지만")
    args = p.parse_args()
    run_pipeline(
        args.date, args.session, start_date=args.start_date,
        digest_channel=args.channel, dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
