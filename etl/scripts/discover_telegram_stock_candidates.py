"""크로스채널 종목탐색 — discovery_source 채널 원문에서 종목 후보 추출.

`telegram_channels.json`에서 `feed_role=discovery_source`인 채널들의 해당일
원문(`telegram_posts`)을 스캔해 종목명/코드 언급을 찾고 `telegram_stock_insights`에
upsert한다(analysis는 채우지 않음 — 분석 단계는 analyze_telegram_stock_candidates.py).

매칭은 종목명 substring + 6자리 코드 정규식, 규칙 기반(LLM 미사용) — 저비용 1차 필터.
# ponytail: substring 매칭이라 짧은/흔한 종목명은 오탐 가능. 오탐 많아지면 그때 word-boundary나
# NER 도입 검토.

Usage (from etl/):
    uv run python scripts/discover_telegram_stock_candidates.py --date 2026-07-01
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from scripts.collect_telegram_public import DEFAULT_DB
    from scripts.stock_names import DEFAULT_DB_PATH as DEFAULT_STOCK_DB
    from scripts.stock_names import load_code_to_name, load_name_to_code
    from scripts.telegram_channels import load_discovery_channels
    from scripts.telegram_stock_insights import ensure_schema, upsert_candidate
except ImportError:
    from collect_telegram_public import DEFAULT_DB
    from stock_names import DEFAULT_DB_PATH as DEFAULT_STOCK_DB
    from stock_names import load_code_to_name, load_name_to_code
    from telegram_channels import load_discovery_channels
    from telegram_stock_insights import ensure_schema, upsert_candidate

_CODE_RE = re.compile(r"\b(\d{6})\b")


def extract_mentions(
    text: str, name_to_code: dict[str, str], code_to_name: dict[str, str]
) -> list[tuple[str, str]]:
    """텍스트 안 종목명/코드 언급 -> [(code, name), ...] 중복 제거, 순서 보존."""
    found: dict[str, str] = {}
    for name, code in name_to_code.items():
        if name and name in text:
            found.setdefault(code, name)
    for code in _CODE_RE.findall(text):
        name = code_to_name.get(code)
        if name:
            found.setdefault(code, name)
    return list(found.items())


def fetch_discovery_posts(con: sqlite3.Connection, channels: list[str], date_kst: str) -> list[dict]:
    if not channels:
        return []
    placeholders = ",".join("?" * len(channels))
    rows = con.execute(
        f"SELECT channel, post_ref, text FROM telegram_posts "
        f"WHERE channel IN ({placeholders}) AND date_kst = ?",
        (*channels, date_kst),
    ).fetchall()
    return [{"channel": r[0], "post_ref": r[1], "text": r[2]} for r in rows]


def aggregate_candidates(
    posts: list[dict], name_to_code: dict[str, str], code_to_name: dict[str, str]
) -> dict[str, dict]:
    """종목(code) 기준 취합: mention_channels/source_post_refs/discovery_reason."""
    result: dict[str, dict] = {}
    for post in posts:
        for code, name in extract_mentions(post["text"], name_to_code, code_to_name):
            entry = result.setdefault(code, {
                "name": name,
                "mention_channels": [],
                "source_post_refs": [],
            })
            if post["channel"] not in entry["mention_channels"]:
                entry["mention_channels"].append(post["channel"])
            entry["source_post_refs"].append(post["post_ref"])

    for code, entry in result.items():
        n = len(entry["source_post_refs"])
        channels = ", ".join(entry["mention_channels"])
        entry["discovery_reason"] = f"이름/코드 언급 {n}건 ({channels})"
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="KST date YYYY-MM-DD")
    ap.add_argument("--session", default="close", help="세션 라벨 morning|close|evening")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--stock-db", default=str(DEFAULT_STOCK_DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import duckdb

    channels = list(load_discovery_channels())

    con = sqlite3.connect(args.db)
    try:
        posts = fetch_discovery_posts(con, channels, args.date)

        stock_con = duckdb.connect(args.stock_db, read_only=True)
        try:
            name_to_code = load_name_to_code(stock_con)
            code_to_name = load_code_to_name(stock_con)
        finally:
            stock_con.close()

        candidates = aggregate_candidates(posts, name_to_code, code_to_name)

        if args.dry_run:
            print(
                f"[discover_telegram_stock_candidates] date={args.date} "
                f"posts={len(posts)} candidates={len(candidates)} (dry-run, no DB write)"
            )
            for code, entry in candidates.items():
                print(f"  {code} {entry['name']}: {entry['discovery_reason']}")
            return

        ensure_schema(con)
        for code, entry in candidates.items():
            upsert_candidate(
                con, args.date, args.session, code, entry["name"],
                mention_channels=entry["mention_channels"],
                source_post_refs=entry["source_post_refs"],
                discovery_reason=entry["discovery_reason"],
            )
        con.commit()
    finally:
        con.close()

    print(
        f"[discover_telegram_stock_candidates] date={args.date} "
        f"posts={len(posts)} candidates={len(candidates)}"
    )


if __name__ == "__main__":
    main()
