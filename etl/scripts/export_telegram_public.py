"""telegram_public.sqlite3 -> JSON/Markdown export (debug/공유용, 운영 저장소 아님).

Usage (from etl/):
    uv run python scripts/export_telegram_public.py --channel butler_works --date 2026-07-01 \
        --format json --out exports/telegram/butler_works_2026-07-01.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:  # 직접 실행(scripts/ on path) / 패키지 import(tests) 양쪽 지원
    from scripts.collect_telegram_public import DEFAULT_DB, KST
except ImportError:
    from collect_telegram_public import DEFAULT_DB, KST


def fetch_posts(con: sqlite3.Connection, channel: str, date_kst: str) -> list[dict]:
    rows = con.execute(
        """
        SELECT post_id, post_ref, posted_at_utc, date_kst, text, links_json
        FROM telegram_posts
        WHERE channel = ? AND date_kst = ?
        ORDER BY post_id
        """,
        (channel, date_kst),
    ).fetchall()
    return [
        {
            "id": post_id,
            "post": post_ref,
            "posted_at_utc": posted_at_utc,
            "date_kst": date_kst_val,
            "text": text,
            "links": json.loads(links_json),
        }
        for post_id, post_ref, posted_at_utc, date_kst_val, text, links_json in rows
    ]


def to_json_str(posts: list[dict]) -> str:
    """기존 임시 JSON과 유사한 키 구조. posted_at_kst는 export 시점에 파생한다."""
    out = []
    for p in posts:
        posted_utc = dt.datetime.fromisoformat(p["posted_at_utc"])
        out.append({
            "id": p["id"],
            "post": p["post"],
            "posted_at_utc": p["posted_at_utc"],
            "posted_at_kst": posted_utc.astimezone(KST).isoformat(),
            "date_kst": p["date_kst"],
            "text": p["text"],
            "links": p["links"],
        })
    return json.dumps(out, ensure_ascii=False, indent=2)


def to_markdown(channel: str, date_kst: str, posts: list[dict]) -> str:
    lines = [f"# {channel} — {date_kst}", "", f"수집 글: {len(posts)}개", ""]
    for p in posts:
        posted_utc = dt.datetime.fromisoformat(p["posted_at_utc"])
        tm = posted_utc.astimezone(KST).strftime("%H:%M")
        first_line = p["text"].splitlines()[0] if p["text"] else "(본문 없음)"
        lines.append(f"## {tm} #{p['id']}")
        lines.append(first_line)
        if p["links"]:
            lines.append("링크: " + ", ".join(p["links"]))
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--date", required=True, help="KST date YYYY-MM-DD")
    ap.add_argument("--format", choices=["json", "md"], default="json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    try:
        con.execute("PRAGMA query_only=ON")
        posts = fetch_posts(con, args.channel, args.date)
    finally:
        con.close()

    content = to_json_str(posts) if args.format == "json" else to_markdown(args.channel, args.date, posts)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"[export_telegram_public] channel={args.channel} date={args.date} posts={len(posts)} out={out_path}")


if __name__ == "__main__":
    main()
