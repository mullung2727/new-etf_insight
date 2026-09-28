"""스윙 후보 3종목 선정 배치 실행 스크립트.

매 거래일 19:00 실행. 후보 수집 → 체크리스트 판정 → 상위 3종목 GPT 요약 →
DB 저장 → 배치 채널 전송. 설계: docs/PLAN_SWING_PICK.md.

Usage (from etl/):
    uv run python scripts/run_swing_pick.py [--date YYYY-MM-DD]
        [--broker-url URL] [--dry-run] [--skip-summary]
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  (cp949 가드 + path)

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from check_krx_trading_day import is_krx_trading_day
from notify import notify
from swing_pick.graph import Deps, build_graph
from swing_pick.store import DEFAULT_DB as SWING_DB

ROOT = Path(__file__).resolve().parents[1]  # etl/
REPO_ROOT = Path(__file__).resolve().parents[2]  # new-etf_insight/
ENV_PATH = REPO_ROOT / ".env"

KST = ZoneInfo("Asia/Seoul")


def _today_kst() -> str:
    return datetime.now(KST).date().isoformat()


def main(argv: list[str] | None = None) -> int:
    # .env를 먼저 읽는다 — notify 웹훅·TypeSafe 키가 env에 있어서 안 읽으면
    # 웹훅 빈값으로 조용히 스킵되는 사고가 있었음.
    load_dotenv(ENV_PATH)
    parser = argparse.ArgumentParser(description="스윙 후보 3종목 선정 배치")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (기본 오늘 KST)")
    parser.add_argument("--broker-url", default="http://localhost:8001")
    parser.add_argument("--dry-run", action="store_true",
                        help="저장·전송 안 함 (Jev·GPT는 돌림)")
    parser.add_argument("--skip-summary", action="store_true",
                        help="GPT 요약 호출 안 함")
    args = parser.parse_args(argv)
    day = args.date or _today_kst()

    if not is_krx_trading_day(day.replace("-", "")):
        print(f"[swing_pick] 휴장일 {day} — skip")
        return 0

    try:
        # 무거운 의존성은 여기서 lazy import — 휴장일·--help 때는 안 끌어오려고.
        from typesafe_sdk import TypeSafeClient

        from collect_trading_result_evidence import fetch_filings, fetch_news
        from new_etf_insight.llm import generate_json

        deps = Deps(
            tg_db=ROOT / "db" / "telegram_public.sqlite3",
            yt_db=ROOT / "db" / "youtube_public.sqlite3",
            report_db=ROOT / "db" / "report_metrics.sqlite3",
            ohlcv_db=ROOT / "db" / "krx_ohlcv.duckdb",
            fin_db=ROOT / "db" / "financial_indicators.sqlite3",
            high52_db=ROOT / "db" / "watchlist.sqlite3",
            broker_url=args.broker_url,
            jev_client=TypeSafeClient(),
            fetch_filings=fetch_filings,
            fetch_news=fetch_news,
            generate_fn=generate_json,
            notify_fn=notify,
            store_path=SWING_DB,
            dry_run=args.dry_run,
            skip_summary=args.skip_summary,
        )
        final = build_graph(deps).invoke({"today": day})
    except Exception as exc:
        # 죽기 전 알림 시도. 여기도 best-effort — 전송 실패해도 exit 1은 유지.
        try:
            notify(f"[스윙 후보] 실패: {type(exc).__name__}", channel="batch")
        except Exception:
            pass
        print(f"[swing_pick] FAILED {day}: {exc}")
        return 1

    print(
        f"[swing_pick] date={day}"
        f" candidates={len(final.get('candidates') or [])}"
        f" picks={len(final.get('picks') or [])}"
        f" saved={final.get('saved')}"
        f" notified={final.get('notified')}"
        f" warnings={len(final.get('warnings') or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
