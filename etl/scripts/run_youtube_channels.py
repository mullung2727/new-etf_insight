"""YouTube 채널 통합 러너 — config 순회, 채널별 RSS+대본 수집.

채널 목록: `youtube_channels.json` (single source).
스펙: docs/youtube_tech.md §4.3

Usage (from etl/):
    uv run python scripts/run_youtube_channels.py --date 2026-07-09
    uv run python scripts/run_youtube_channels.py --date 2026-07-09 --channel UCxxxx…
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from scripts.collect_youtube import DEFAULT_DB, collect_channel, ensure_schema
    from scripts.youtube_channels import load_all_channels
except ImportError:
    from collect_youtube import DEFAULT_DB, collect_channel, ensure_schema
    from youtube_channels import load_all_channels


def process_channel(
    con: sqlite3.Connection,
    channel_id: str,
    date_kst: str,
    **kwargs,
) -> dict:
    stats = collect_channel(con, channel_id, date_kst, **kwargs)
    con.commit()
    return stats


def run_all(
    con: sqlite3.Connection,
    channels: dict,
    date_kst: str,
    *,
    only: str | None = None,
    **kwargs,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """채널 순회. 한 채널 실패해도 나머지 계속. 반환: (results, errors)."""
    targets = [only] if only else list(channels)
    results: list[dict] = []
    errors: list[tuple[str, str]] = []
    for ch in targets:
        if ch not in channels and only:
            errors.append((ch, f"channel not in config: {ch}"))
            continue
        try:
            results.append(process_channel(con, ch, date_kst, **kwargs))
        except Exception as exc:  # noqa: BLE001 — 채널 격리가 목적
            errors.append((ch, str(exc)))
    return results, errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="KST date YYYY-MM-DD")
    ap.add_argument("--channel", help="지정 시 해당 channel_id만. 미지정 시 config 전체")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    channels = load_all_channels()
    if args.channel and args.channel not in channels:
        # config 없어도 --channel 직접 지정 허용 (수동 스모크)
        channels = {args.channel: {"source_url": f"https://www.youtube.com/channel/{args.channel}"}}

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        ensure_schema(con)
        results, errors = run_all(con, channels, args.date, only=args.channel)
    finally:
        con.close()

    for r in results:
        print(
            f"[run_youtube_channels] channel={r['channel_id']} date={args.date} "
            f"rss_entries={r['rss_entries']} matched_date={r['matched_date']} "
            f"inserted={r['inserted']} updated={r['updated']} "
            f"skipped_no_transcript={r['skipped_no_transcript']}"
        )
    for ch, err in errors:
        print(
            f"[run_youtube_channels] ERROR channel={ch} date={args.date}: {err}",
            file=sys.stderr,
        )

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
