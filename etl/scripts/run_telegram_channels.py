"""Telegram 채널 통합 러너 — config 순회, 채널별 텍스트 수집(crawl+upsert).

채널 목록은 `telegram_channels.json`, 스케줄은 ops registry cron 담당.
collect은 post_id 멱등이라 `--date 오늘`을 매시 돌려도 새 글만 반영된다.
(증권사 리포트 PDF는 네이버 리서치 원본에서 별도 배치로 받는다 — download_naver_research.py.)

Usage (from etl/):
    uv run python scripts/run_telegram_channels.py --date 2026-07-01
    uv run python scripts/run_telegram_channels.py --date 2026-07-01 --channel companyreport
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:  # 직접 실행(scripts/ on path) / 패키지 import(tests) 양쪽 지원
    from scripts.collect_telegram_public import (
        DEFAULT_DB,
        crawl_range,
        ensure_schema,
        fetch,
        upsert_channel,
        upsert_posts,
    )
    from scripts.telegram_channels import load_all_channels
except ImportError:
    from collect_telegram_public import (
        DEFAULT_DB,
        crawl_range,
        ensure_schema,
        fetch,
        upsert_channel,
        upsert_posts,
    )
    from telegram_channels import load_all_channels


def process_channel(
    con: sqlite3.Connection,
    channel: str,
    cfg: dict,
    date_kst: str,
    end_date: str | None = None,
    *,
    collect_fetch_fn=fetch,
    sleep_fn=time.sleep,
) -> dict:
    """채널 1개: [date_kst, end_date] 텍스트 수집(역방향 1패스+upsert).

    end_date 미지정 시 date_kst 하루만.
    """
    source_url = cfg.get("source_url") or f"https://t.me/s/{channel}"
    messages = crawl_range(
        channel, date_kst, end_date or date_kst,
        fetch_fn=collect_fetch_fn, sleep_fn=sleep_fn,
    )
    upsert_channel(con, channel, source_url)
    inserted, updated = upsert_posts(con, channel, messages)
    con.commit()
    return {
        "channel": channel,
        "fetched": len(messages),
        "inserted": inserted,
        "updated": updated,
    }


def run_all(
    con: sqlite3.Connection,
    channels: dict,
    date_kst: str,
    end_date: str | None = None,
    *,
    only: str | None = None,
    collect_fetch_fn=fetch,
    sleep_fn=time.sleep,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """채널 순회. 한 채널 실패해도 나머지 계속. 반환: (results, errors)."""
    targets = [only] if only else list(channels)
    results: list[dict] = []
    errors: list[tuple[str, str]] = []
    for ch in targets:
        try:
            results.append(process_channel(
                con, ch, channels[ch], date_kst, end_date,
                collect_fetch_fn=collect_fetch_fn,
                sleep_fn=sleep_fn,
            ))
        except Exception as exc:  # noqa: BLE001 — 채널 격리가 목적
            errors.append((ch, str(exc)))
    return results, errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="KST 시작일 YYYY-MM-DD")
    ap.add_argument("--end", help="KST 종료일 YYYY-MM-DD. 미지정 시 --date 하루만")
    ap.add_argument("--channel", help="지정 시 해당 채널만. 미지정 시 config 전체")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    channels = load_all_channels()
    period = args.date if not args.end else f"{args.date}~{args.end}"

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        ensure_schema(con)
        results, errors = run_all(con, channels, args.date, args.end, only=args.channel)
    finally:
        con.close()

    for r in results:
        print(
            f"[run_telegram_channels] channel={r['channel']} date={period} "
            f"fetched={r['fetched']} inserted={r['inserted']} updated={r['updated']}"
        )
    for ch, err in errors:
        print(f"[run_telegram_channels] ERROR channel={ch} date={period}: {err}", file=sys.stderr)

    # 채널 하나가 죽어도(비공개·슬러그오류) 나머지 수집이 성공하면 배치는 성공.
    # 전 채널 실패(수집 0)일 때만 비정상 종료로 판정한다.
    if errors and not results:
        sys.exit(1)


if __name__ == "__main__":
    main()
