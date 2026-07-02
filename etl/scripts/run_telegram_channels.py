"""Telegram 채널 통합 러너 — config 순회, 채널별 수집 + (attachments 있으면) 다운로드.

채널 목록/처리규칙은 `telegram_channels.json`, 스케줄은 ops registry cron 담당.
시간단위 실행: collect은 post_id 멱등, download은 파일 존재 스킵이라 `--date 오늘`을
매시 돌려도 새 글/PDF만 반영된다(코드 무변경).

Usage (from etl/):
    uv run python scripts/run_telegram_channels.py --date 2026-07-01
    uv run python scripts/run_telegram_channels.py --date 2026-07-01 --channel companyreport
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:  # 직접 실행(scripts/ on path) / 패키지 import(tests) 양쪽 지원
    from scripts.collect_telegram_public import (
        DEFAULT_DB,
        crawl_date,
        ensure_schema,
        fetch,
        upsert_channel,
        upsert_posts,
    )
    from scripts.download_telegram_report_attachments import _urlopen_fetch
    from scripts.download_telegram_report_attachments import run as download_run
    from scripts.telegram_channels import load_all_channels
except ImportError:
    from collect_telegram_public import (
        DEFAULT_DB,
        crawl_date,
        ensure_schema,
        fetch,
        upsert_channel,
        upsert_posts,
    )
    from download_telegram_report_attachments import _urlopen_fetch
    from download_telegram_report_attachments import run as download_run
    from telegram_channels import load_all_channels

DEFAULT_EXPORT_BASE = Path(__file__).resolve().parents[1] / "exports" / "telegram"


def process_channel(
    con: sqlite3.Connection,
    channel: str,
    cfg: dict,
    date_kst: str,
    out_base: Path,
    *,
    collect_fetch_fn=fetch,
    download_fetch_fn=_urlopen_fetch,
    sleep_fn=time.sleep,
) -> dict:
    """채널 1개: 수집(crawl+upsert) 후 attachments 설정 있으면 다운로드."""
    source_url = cfg.get("source_url") or f"https://t.me/s/{channel}"
    messages = crawl_date(channel, date_kst, fetch_fn=collect_fetch_fn, sleep_fn=sleep_fn)
    upsert_channel(con, channel, source_url)
    inserted, updated = upsert_posts(con, channel, messages)
    con.commit()

    result = {
        "channel": channel,
        "fetched": len(messages),
        "inserted": inserted,
        "updated": updated,
        "attachments": None,
    }

    att = cfg.get("attachments")
    if att:
        out_dir = out_base / att["out_subdir"]
        result["attachments"] = download_run(
            con, channel, date_kst, out_dir,
            link_re=re.compile(att["link_pattern"]),
            ticker_re=re.compile(att["ticker_pattern"]),
            fetch_fn=download_fetch_fn,
        )
    return result


def run_all(
    con: sqlite3.Connection,
    channels: dict,
    date_kst: str,
    out_base: Path,
    *,
    only: str | None = None,
    collect_fetch_fn=fetch,
    download_fetch_fn=_urlopen_fetch,
    sleep_fn=time.sleep,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """채널 순회. 한 채널 실패해도 나머지 계속. 반환: (results, errors)."""
    targets = [only] if only else list(channels)
    results: list[dict] = []
    errors: list[tuple[str, str]] = []
    for ch in targets:
        try:
            results.append(process_channel(
                con, ch, channels[ch], date_kst, out_base,
                collect_fetch_fn=collect_fetch_fn,
                download_fetch_fn=download_fetch_fn,
                sleep_fn=sleep_fn,
            ))
        except Exception as exc:  # noqa: BLE001 — 채널 격리가 목적
            errors.append((ch, str(exc)))
    return results, errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="KST date YYYY-MM-DD")
    ap.add_argument("--channel", help="지정 시 해당 채널만. 미지정 시 config 전체")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out-base", default=str(DEFAULT_EXPORT_BASE))
    args = ap.parse_args()

    channels = load_all_channels()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        ensure_schema(con)
        results, errors = run_all(
            con, channels, args.date, Path(args.out_base), only=args.channel,
        )
    finally:
        con.close()

    for r in results:
        att = r["attachments"]
        att_str = (
            f" attachments(matched={att['matched']} downloaded={att['downloaded']} "
            f"skipped_exists={att['skipped_exists']} skipped_no_ticker={att['skipped_no_ticker']})"
            if att else ""
        )
        print(
            f"[run_telegram_channels] channel={r['channel']} date={args.date} "
            f"fetched={r['fetched']} inserted={r['inserted']} updated={r['updated']}{att_str}"
        )
    for ch, err in errors:
        print(f"[run_telegram_channels] ERROR channel={ch} date={args.date}: {err}", file=sys.stderr)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
