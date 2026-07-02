"""Telegram 채널의 증권사 종목 리포트 PDF 다운로드.

리포트 링크/종목코드 패턴은 `telegram_channels.json`의 채널별 `attachments`에서 읽는다.
companyreport 예: `stockinfo7.com/stock/report/url/<id>` 링크 + `종목명(코드.KS/의견)` 텍스트.
종목코드 없는 글([시장] 리포트 등)은 폴더를 만들 수 없어 스킵한다.

기본 패턴 상수(REPORT_LINK_RE/TICKER_RE)는 companyreport 값 — config 없이 호출 시 fallback.

Usage (from etl/):
    uv run python scripts/download_telegram_report_attachments.py --channel companyreport --date 2026-04-14
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:  # 직접 실행(scripts/ on path) / 패키지 import(tests) 양쪽 지원
    from scripts.collect_telegram_public import DEFAULT_DB
    from scripts.telegram_channels import load_channel_config
except ImportError:
    from collect_telegram_public import DEFAULT_DB
    from telegram_channels import load_channel_config

CHANNEL = "companyreport"
FETCH_TIMEOUT = 40
DEFAULT_EXPORT_BASE = Path(__file__).resolve().parents[1] / "exports" / "telegram"

REPORT_LINK_RE = re.compile(r"stockinfo7\.com/stock/report/url/\d+")
TICKER_RE = re.compile(r"([^()\[\]]+?)\((\d{6})\.(?:KS|KQ)[^)]*\)")
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def extract_ticker(text: str, ticker_re: re.Pattern = TICKER_RE) -> tuple[str, str] | None:
    m = ticker_re.search(text)
    if not m:
        return None
    return sanitize_name(m.group(1)), m.group(2)


def sanitize_name(name: str) -> str:
    return _ILLEGAL_CHARS_RE.sub("", name).strip()


def is_report_link(url: str, link_re: re.Pattern = REPORT_LINK_RE) -> bool:
    return bool(link_re.search(url))


def dest_path(out_dir: Path, name: str, code: str, date_kst: str, post_id: int) -> Path:
    return out_dir / f"{name}_{code}" / f"{date_kst}_{post_id}.pdf"


def _urlopen_fetch(url: str, timeout: int = FETCH_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download_attachment(url: str, dest: Path, fetch_fn=_urlopen_fetch) -> bool:
    """다운로드하면 True, 이미 존재해서 스킵하면 False."""
    if dest.exists():
        return False
    content = fetch_fn(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return True


def run(
    con: sqlite3.Connection,
    channel: str,
    date_kst: str,
    out_dir: Path,
    link_re: re.Pattern = REPORT_LINK_RE,
    ticker_re: re.Pattern = TICKER_RE,
    fetch_fn=_urlopen_fetch,
) -> dict:
    rows = con.execute(
        "SELECT post_id, text, links_json FROM telegram_posts WHERE channel=? AND date_kst=? ORDER BY post_id",
        (channel, date_kst),
    ).fetchall()

    import json
    stats = {"matched": 0, "downloaded": 0, "skipped_exists": 0, "skipped_no_ticker": 0}
    for post_id, text, links_json in rows:
        ticker = extract_ticker(text, ticker_re)
        if not ticker:
            stats["skipped_no_ticker"] += 1
            continue
        links = [u for u in json.loads(links_json) if is_report_link(u, link_re)]
        if not links:
            stats["skipped_no_ticker"] += 1
            continue
        name, code = ticker
        stats["matched"] += 1
        dest = dest_path(out_dir, name, code, date_kst, post_id)
        downloaded = download_attachment(links[0], dest, fetch_fn=fetch_fn)
        if downloaded:
            stats["downloaded"] += 1
        else:
            stats["skipped_exists"] += 1
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=CHANNEL)
    ap.add_argument("--date", required=True, help="KST date YYYY-MM-DD")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out-dir", help="기본: exports/telegram/<config out_subdir>")
    args = ap.parse_args()

    att = load_channel_config(args.channel).get("attachments")
    if not att:
        print(f"[download_telegram_report_attachments] channel={args.channel}: no attachments configured, skip")
        return
    link_re = re.compile(att["link_pattern"])
    ticker_re = re.compile(att["ticker_pattern"])
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_EXPORT_BASE / att["out_subdir"]

    con = sqlite3.connect(args.db)
    try:
        con.execute("PRAGMA query_only=ON")
        stats = run(con, args.channel, args.date, out_dir, link_re=link_re, ticker_re=ticker_re)
    finally:
        con.close()

    print(
        f"[download_telegram_report_attachments] channel={args.channel} date={args.date} "
        f"matched={stats['matched']} downloaded={stats['downloaded']} "
        f"skipped_exists={stats['skipped_exists']} skipped_no_ticker={stats['skipped_no_ticker']}"
    )


if __name__ == "__main__":
    main()
