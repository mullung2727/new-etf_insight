"""수집된 텔레그램 원문 조회 — 키워드/채널/기간 필터로 telegram_posts 검색.

읽기 전용(PRAGMA query_only). 요약/가공 아님, 순수 조회.

Usage (from etl/):
    uv run python scripts/query_telegram_public.py --keyword 삼성전자 --from 2026-07-01 --to 2026-07-06
    uv run python scripts/query_telegram_public.py --channel getfeed --from 2026-07-01 --to 2026-07-06
    uv run python scripts/query_telegram_public.py --keyword 반도체 --limit 50 --full
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from scripts.collect_telegram_public import DEFAULT_DB
except ImportError:
    from collect_telegram_public import DEFAULT_DB

_COLS = "channel, post_ref, posted_at_utc, date_kst, text, links_json"


def build_query(
    keyword: str | None = None,
    channel: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> tuple[str, list]:
    """필터 조합 -> (sql, params). 없는 필터는 조건에서 생략."""
    where: list[str] = []
    params: list = []
    if keyword:
        where.append("text LIKE ?")
        params.append(f"%{keyword}%")
    if channel:
        where.append("channel = ?")
        params.append(channel)
    if start and end:
        where.append("date_kst BETWEEN ? AND ?")
        params.extend([start, end])
    elif start:
        where.append("date_kst >= ?")
        params.append(start)
    elif end:
        where.append("date_kst <= ?")
        params.append(end)

    sql = f"SELECT {_COLS} FROM telegram_posts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date_kst, channel, post_id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return sql, params


def run_query(con: sqlite3.Connection, **kwargs) -> list[dict]:
    sql, params = build_query(**kwargs)
    rows = con.execute(sql, params).fetchall()
    keys = [c.strip() for c in _COLS.split(",")]
    return [dict(zip(keys, r)) for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", help="본문 부분일치 검색어")
    ap.add_argument("--channel", help="특정 채널만")
    ap.add_argument("--from", dest="start", help="KST 시작일 YYYY-MM-DD")
    ap.add_argument("--to", dest="end", help="KST 종료일 YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--full", action="store_true", help="본문 전체 출력(기본은 120자 스니펫)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    try:
        con.execute("PRAGMA query_only=ON")
        posts = run_query(
            con, keyword=args.keyword, channel=args.channel,
            start=args.start, end=args.end, limit=args.limit,
        )
    finally:
        con.close()

    for p in posts:
        body = p["text"] if args.full else (p["text"] or "").replace("\n", " ")[:120]
        print(f"[{p['date_kst']}] {p['post_ref']}: {body}")
    print(f"--- {len(posts)}건 ---")


if __name__ == "__main__":
    main()
