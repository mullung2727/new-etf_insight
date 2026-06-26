"""Format watchlist research JSON as Discord-safe UTF-8 messages."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


DEFAULT_LIMIT = 1800
MOJIBAKE_MARKERS = ("�", "醫", "?댁", "?쒖", "?먯", "?댁뒪")


def fmt_int(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def fmt_ratio(value: Any) -> str:
    if value is None:
        return "산출 불가"
    try:
        return f"{float(value):.2f}배"
    except (TypeError, ValueError):
        return str(value)


def discord_link(url: str) -> str:
    return f"<{url}>" if url.startswith("http") else url


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def item_sources(item: dict[str, Any], count: int) -> list[str]:
    sources = [src for src in item.get("sources", []) if isinstance(src, str) and src.startswith("http")]
    board_prefix = "https://finance.naver.com/item/board.naver"
    news_like = [src for src in sources if not src.startswith(board_prefix)]
    return news_like[:count] or sources[:count]


def format_item(item: dict[str, Any], max_links: int) -> str:
    name = item.get("name") or "이름 없음"
    ticker = item.get("ticker") or "N/A"
    rank = item.get("rank") or item.get("kiwoom_rank") or "N/A"
    links = item_sources(item, max_links)

    lines = [
        f"**{name} ({ticker})** - {item.get('score', 'N/A')}점 / {item.get('category', 'N/A')}",
        f"- 키움 rank: `{rank}`",
        f"- 거래량: `{fmt_int(item.get('today_volume'))}`, 5일 평균 대비 `{fmt_ratio(item.get('ratio'))}`",
        f"- 거래대금: `{fmt_int(item.get('trading_value'))}`",
        f"- 종가: `{fmt_int(item.get('close'))}`",
        f"- 급등 원인: {truncate(str(item.get('reason_summary') or 'N/A'), 420)}",
        f"- 뉴스: {truncate(str(item.get('evidence_news') or 'N/A'), 320)}",
        f"- 웹: {truncate(str(item.get('evidence_web') or 'N/A'), 260)}",
        f"- 판단: {truncate(str(item.get('final_opinion') or 'N/A'), 420)}",
    ]
    if links:
        lines.append("- 링크: " + " ".join(discord_link(src) for src in links))
    return "\n".join(lines)


def compact_date(value: Any) -> str:
    return str(value or "").replace("-", "")


def enrich_ranks(items: list[dict[str, Any]], db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"watchlist DB not found: {db_path}")
    con = sqlite3.connect(db_path)
    try:
        for item in items:
            date = compact_date(item.get("date"))
            ticker = item.get("ticker")
            if not date or not ticker:
                continue
            row = con.execute(
                "SELECT rank FROM intraday_ranking WHERE date = ? AND ticker = ?",
                [date, ticker],
            ).fetchone()
            if row:
                item["rank"] = row[0]
    finally:
        con.close()


def split_message(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if line_len > limit:
            chunks.append(truncate(line, limit))
        else:
            current.append(line)
            current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def build_messages(doc: dict[str, Any], limit: int, max_links: int, db_path: Path | None) -> list[str]:
    date = doc.get("date") or "unknown"
    source = doc.get("source_data") or {}
    items = sorted(doc.get("items", []), key=lambda row: row.get("score") or 0, reverse=True)
    if db_path:
        enrich_ranks(items, db_path)

    header = "\n".join(
        [
            f"키움 당일 watchlist 후보 + 즉시 LLM 스코어 ({date})",
            f"- watchlist 종목 수: {len(items)}",
            f"- build_watchlist: {source.get('build_watchlist', 'N/A')}",
            f"- DB: `{source.get('watchlist_db', 'N/A')}`",
        ]
    )

    messages = [header]
    for item in items:
        messages.extend(split_message(format_item(item, max_links=max_links), limit))
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Discord messages from watchlist research JSON.")
    parser.add_argument("--json", required=True, type=Path, help="watchlist_research_YYYY-MM-DD.json")
    parser.add_argument("--out", type=Path, help="Optional UTF-8 JSON output file.")
    parser.add_argument("--db", type=Path, help="Optional watchlist.sqlite3 path for intraday rank enrichment.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max characters per Discord message.")
    parser.add_argument("--max-links", type=int, default=3, help="Max source links per item.")
    parser.add_argument("--fail-on-mojibake", action="store_true")
    args = parser.parse_args()

    doc = json.loads(args.json.read_text(encoding="utf-8"))
    messages = build_messages(doc, limit=args.limit, max_links=args.max_links, db_path=args.db)
    payload = {"messages": messages}
    output = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.fail_on_mojibake and any(marker in output for marker in MOJIBAKE_MARKERS):
        print("[format_watchlist_discord_report] mojibake marker detected", file=sys.stderr)
        return 2

    if args.out:
        args.out.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
