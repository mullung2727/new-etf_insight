"""Telegram 공개 채널(`t.me/s/<channel>`) 수집 — 원문을 SQLite에 upsert.

파서는 검증된 스크립트를 포팅한 것이다:
`C:/Users/mullu/AppData/Local/hermes/scripts/telegram_public_channel_scrape.py`
(2026-07-01 butler_works 45개 수집 검증 완료). 요약/전송은 이 스크립트 책임이 아니다.

Usage (from etl/):
    uv run python scripts/collect_telegram_public.py --channel-url https://t.me/butler_works --date 2026-07-01
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_DB = Path(__file__).resolve().parents[1] / "db" / "telegram_public.sqlite3"
FETCH_TIMEOUT = 40
PAGE_DELAY = 0.3

_BLOCK_START_RE = re.compile(r'<div class="tgme_widget_message[^>]*data-post="([^"]+)"')
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^>]*>([\s\S]*?)</div>')
LINK_RE = re.compile(r'href="([^"]+)"')

_CREATE_CHANNELS = """
CREATE TABLE IF NOT EXISTS telegram_channels (
    channel TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE_POSTS = """
CREATE TABLE IF NOT EXISTS telegram_posts (
    channel TEXT NOT NULL,
    post_id INTEGER NOT NULL,
    post_ref TEXT NOT NULL,
    posted_at_utc TEXT NOT NULL,
    date_kst TEXT NOT NULL,
    text TEXT NOT NULL,
    links_json TEXT NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, post_id)
)
"""
_CREATE_POSTS_IDX = """
CREATE INDEX IF NOT EXISTS idx_telegram_posts_date
ON telegram_posts(channel, date_kst)
"""


def normalize_channel(channel_url: str | None, channel: str | None) -> str:
    """`--channel-url`(`/s/` 유무 무관) 또는 `--channel` slug -> channel slug."""
    if channel:
        return channel.lstrip("@")
    if not channel_url:
        raise ValueError("--channel-url or --channel required")
    return channel_url.rstrip("/").split("/")[-1].lstrip("@")


def fetch(channel: str, before: int | None = None, timeout: int = FETCH_TIMEOUT) -> str:
    url = f"https://t.me/s/{channel}"
    if before:
        url += f"?before={before}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def clean_text(raw: str) -> str:
    raw = re.sub(r"<br\s*/?>", "\n", raw)
    raw = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(raw).strip()


def parse_messages(page: str) -> list[dict]:
    """페이지 하나에서 글 목록 추출. 미디어만 있는 글은 text=''로 유지한다.

    footer의 <time>이 message_text div보다 뒤에 오는 마크업도 있어(실측 확인),
    한 개 regex로 순서를 가정하지 않고 data-post 경계로 블록을 나눈 뒤
    블록 안에서 time/text를 각각 찾는다.
    """
    starts = list(_BLOCK_START_RE.finditer(page))
    out = []
    for i, m in enumerate(starts):
        post = m.group(1)
        block_end = starts[i + 1].start() if i + 1 < len(starts) else len(page)
        block = page[m.end():block_end]
        time_m = _TIME_RE.search(block)
        if not time_m:
            continue
        posted_utc = dt.datetime.fromisoformat(time_m.group(1))
        posted_kst = posted_utc.astimezone(KST)
        text_m = _TEXT_RE.search(block)
        raw_text = text_m.group(1) if text_m else ""
        links = [html.unescape(x) for x in LINK_RE.findall(raw_text)]
        out.append({
            "id": int(post.split("/")[-1]),
            "post": post,
            "posted_at_utc": posted_utc.isoformat(),
            "date_kst": posted_kst.date().isoformat(),
            "text": clean_text(raw_text),
            "links": links,
        })
    return out


def crawl_range(
    channel: str,
    start_date: str,
    end_date: str,
    max_pages: int = 200,
    fetch_fn=fetch,
    sleep_fn=time.sleep,
) -> list[dict]:
    """[start_date, end_date](KST) 글을 역방향 1회 패스로 모아 반환.

    t.me/s 미리보기는 최신→과거로 20글씩만 준다. 날짜별로 따로 되감으면 최근 페이지를
    날짜 수만큼 중복 요청하게 되므로, 여기서 한 번만 역방향으로 내려가며 범위 내 글을
    전부 버킷팅한다. start_date 미만 글에 닿으면 중단.
    웹 미리보기가 막힌 채널은 RuntimeError.
    """
    start = dt.date.fromisoformat(start_date)
    before = None
    seen: set[int] = set()
    result: list[dict] = []
    for page_no in range(max_pages):
        page = fetch_fn(channel, before)
        msgs = parse_messages(page)
        if not msgs:
            if page_no == 0:
                raise RuntimeError(
                    f"channel '{channel}' has no accessible public message list "
                    f"(empty first page — private/no-preview channel or wrong slug)"
                )
            break
        new = [m for m in msgs if m["id"] not in seen]
        for m in new:
            seen.add(m["id"])
            if start_date <= m["date_kst"] <= end_date:
                result.append(m)
        # 글은 시간역순. 이 페이지 최소 날짜가 하한 미만이면 이후 페이지는 전부 범위 밖 -> 중단
        # (범위 내 글은 이미 위에서 버킷팅됨)
        dates = [dt.date.fromisoformat(m["date_kst"]) for m in new]
        if dates and min(dates) < start:
            break
        before = min(m["id"] for m in msgs)
        sleep_fn(PAGE_DELAY)
    return sorted(result, key=lambda m: m["id"])


def crawl_date(
    channel: str,
    target_date: str,
    max_pages: int = 30,
    fetch_fn=fetch,
    sleep_fn=time.sleep,
) -> list[dict]:
    """target_date(KST) 글만 모아 반환. crawl_range의 단일 날짜 특수형."""
    return crawl_range(
        channel, target_date, target_date,
        max_pages=max_pages, fetch_fn=fetch_fn, sleep_fn=sleep_fn,
    )


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_CHANNELS)
    con.execute(_CREATE_POSTS)
    con.execute(_CREATE_POSTS_IDX)


def upsert_channel(con: sqlite3.Connection, channel: str, source_url: str) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute(
        """
        INSERT INTO telegram_channels(channel, source_url, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel) DO UPDATE SET
            source_url=excluded.source_url, updated_at=excluded.updated_at
        """,
        (channel, source_url, now, now),
    )


def upsert_posts(con: sqlite3.Connection, channel: str, messages: list[dict]) -> tuple[int, int]:
    """(channel, post_id) upsert. 반환: (inserted, updated)."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    inserted = updated = 0
    for m in messages:
        exists = con.execute(
            "SELECT 1 FROM telegram_posts WHERE channel=? AND post_id=?",
            (channel, m["id"]),
        ).fetchone() is not None
        con.execute(
            """
            INSERT INTO telegram_posts(
                channel, post_id, post_ref, posted_at_utc, date_kst,
                text, links_json, raw_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel, post_id) DO UPDATE SET
                post_ref=excluded.post_ref,
                posted_at_utc=excluded.posted_at_utc,
                date_kst=excluded.date_kst,
                text=excluded.text,
                links_json=excluded.links_json,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                channel, m["id"], m["post"], m["posted_at_utc"], m["date_kst"],
                m["text"], json.dumps(m["links"], ensure_ascii=False),
                json.dumps(m, ensure_ascii=False), now, now,
            ),
        )
        if exists:
            updated += 1
        else:
            inserted += 1
    return inserted, updated


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--channel-url")
    g.add_argument("--channel")
    ap.add_argument("--date", required=True, help="KST date YYYY-MM-DD")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--limit-pages", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    channel = normalize_channel(args.channel_url, args.channel)
    source_url = args.channel_url or f"https://t.me/s/{channel}"

    try:
        messages = crawl_date(channel, args.date, max_pages=args.limit_pages)
    except Exception as exc:
        print(f"[collect_telegram_public] ERROR channel={channel} date={args.date}: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(
            f"[collect_telegram_public] channel={channel} date={args.date} "
            f"fetched={len(messages)} (dry-run, no DB write)"
        )
        return

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        ensure_schema(con)
        upsert_channel(con, channel, source_url)
        inserted, updated = upsert_posts(con, channel, messages)
        con.commit()
    finally:
        con.close()

    print(
        f"[collect_telegram_public] channel={channel} date={args.date} "
        f"fetched={len(messages)} inserted={inserted} updated={updated} skipped=0"
    )


if __name__ == "__main__":
    main()
