"""YouTube 채널 영상 메타 + 대본 수집 — RSS + youtube-transcript-api → SQLite upsert.

API 키·yt-dlp 없음. 스펙: docs/youtube_tech.md §4

Usage (from etl/):
    uv run python scripts/collect_youtube.py --channel-id UCxxxx… --date 2026-07-09
"""
from __future__ import annotations

import argparse
import datetime as dt
import html as html_lib
import json
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from youtube_transcript_api import YouTubeTranscriptApi

KST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_DB = Path(__file__).resolve().parents[1] / "db" / "youtube_public.sqlite3"
FETCH_TIMEOUT = 40
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_ATOM = "http://www.w3.org/2005/Atom"
_YT = "http://www.youtube.com/xml/schemas/2015"

_CREATE_VIDEOS = """
CREATE TABLE IF NOT EXISTS youtube_videos (
  channel_id       TEXT NOT NULL,
  video_id         TEXT NOT NULL,
  title            TEXT NOT NULL,
  published_at_utc TEXT NOT NULL,
  date_kst         TEXT NOT NULL,
  url              TEXT NOT NULL,
  transcript       TEXT,
  transcript_lang  TEXT,
  transcript_source TEXT,
  raw_json         TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  UNIQUE(channel_id, video_id)
)
"""
_CREATE_VIDEOS_IDX = """
CREATE INDEX IF NOT EXISTS idx_youtube_videos_date
  ON youtube_videos(channel_id, date_kst)
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_VIDEOS)
    con.execute(_CREATE_VIDEOS_IDX)


def fetch_url(
    url: str,
    *,
    timeout: int = FETCH_TIMEOUT,
    opener=urllib.request.urlopen,
) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _DEFAULT_UA})
    with opener(req, timeout=timeout) as resp:
        data = resp.read() if hasattr(resp, "read") else resp
        return data if isinstance(data, bytes) else bytes(data)


def list_channel_videos_rss(
    channel_id: str,
    *,
    opener=urllib.request.urlopen,
) -> list[dict]:
    """각 dict: video_id, title, published_at_utc (ISO), url.

    Atom ns: yt videoId, atom title/published/link.
    순서: RSS 등장 순(보통 최신 먼저). 실측 상한 ~15.
    """
    feed_url = (
        f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    )
    raw = fetch_url(feed_url, opener=opener)
    root = ET.fromstring(raw)
    out: list[dict] = []
    for entry in root.findall(f"{{{_ATOM}}}entry"):
        vid_el = entry.find(f"{{{_YT}}}videoId")
        title_el = entry.find(f"{{{_ATOM}}}title")
        pub_el = entry.find(f"{{{_ATOM}}}published")
        link_el = entry.find(f"{{{_ATOM}}}link")
        if vid_el is None or not (vid_el.text or "").strip():
            continue
        video_id = vid_el.text.strip()
        title = (title_el.text or "").strip() if title_el is not None else ""
        published = (pub_el.text or "").strip() if pub_el is not None else ""
        url = ""
        if link_el is not None:
            url = link_el.attrib.get("href", "") or ""
        item = {
            "video_id": video_id,
            "title": title,
            "published_at_utc": published,
            "url": url,
        }
        out.append(item)
    return out


def published_to_date_kst(published_at_utc: str) -> str:
    """YYYY-MM-DD Asia/Seoul."""
    s = published_at_utc.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    when = dt.datetime.fromisoformat(s)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(KST).date().isoformat()


def fetch_duration_seconds(
    video_id: str,
    *,
    opener=urllib.request.urlopen,
) -> int | None:
    """watch 페이지에서 lengthSeconds 파싱. 실패 시 None.

    RSS에는 duration 없음 → 목록 조회용 경량 스크랩(대본/STT 아님).
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        raw = fetch_url(url, opener=opener)
    except Exception:  # noqa: BLE001
        return None
    html = raw.decode("utf-8", "replace")
    m = re.search(r'"lengthSeconds":"(\d+)"', html)
    if m:
        return int(m.group(1))
    m = re.search(r'"approxDurationMs":"(\d+)"', html)
    if m:
        return max(1, int(m.group(1)) // 1000)
    return None


_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"UC[a-zA-Z0-9_-]{22}")


def parse_video_id(url_or_id: str) -> str:
    """watch/shorts/youtu.be/embed/live 또는 bare 11자 id → video_id.

    채널 URL·@handle 은 ValueError(한글).
    """
    s = (url_or_id or "").strip()
    if not s:
        raise ValueError("영상 URL을 입력하세요.")
    if _VIDEO_ID_RE.match(s):
        return s

    low = s.lower()
    # 채널 페이지만 온 경우 거부 (영상 경로 없을 때)
    is_channelish = any(
        x in low for x in ("/channel/", "/@", "/c/", "/user/")
    )
    is_videoish = any(
        x in low for x in ("watch", "youtu.be", "/shorts/", "/embed/", "/live/")
    )
    if is_channelish and not is_videoish:
        raise ValueError("채널 주소가 아니라 영상 URL을 입력하세요.")

    if "://" not in s:
        s = "https://" + s
    u = urlparse(s)
    host = (u.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = u.path or ""

    if host == "youtu.be":
        vid = path.strip("/").split("/")[0].split("?")[0]
        if _VIDEO_ID_RE.match(vid):
            return vid

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        qs = parse_qs(u.query or "")
        if qs.get("v"):
            vid = (qs["v"][0] or "").strip()
            if _VIDEO_ID_RE.match(vid):
                return vid
        m = re.match(
            r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{11})",
            path,
        )
        if m:
            return m.group(1)

    raise ValueError("유효한 유튜브 영상 URL이 아닙니다.")


def parse_watch_meta(html: str) -> dict:
    """watch HTML → channel_id, title, published_at_utc (없으면 빈 문자열).

    channel_id 없으면 ValueError. published 실패는 호출측 폴백.
    """
    if not html:
        raise ValueError("영상 페이지를 읽지 못했습니다.")

    channel_id = ""
    for pat in (
        r'"externalChannelId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"',
        r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"',
    ):
        m = re.search(pat, html)
        if m:
            channel_id = m.group(1)
            break
    if not channel_id:
        m = _CHANNEL_ID_RE.search(html)
        if m:
            channel_id = m.group(0)
    if not channel_id:
        raise ValueError("영상 채널 정보를 찾지 못했습니다.")

    title = ""
    m = re.search(
        r'<meta\s+property="og:title"\s+content="([^"]*)"',
        html,
        flags=re.I,
    )
    if m:
        title = html_lib.unescape(m.group(1)).strip()
    if not title:
        m = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', html)
        if m:
            raw_t = m.group(1)
            try:
                title = json.loads(f'"{raw_t}"')
            except json.JSONDecodeError:
                title = raw_t.encode("utf-8", "replace").decode(
                    "unicode_escape", "replace"
                )
            title = str(title).strip()

    published = ""
    for pat in (
        r'<meta\s+itemprop="uploadDate"\s+content="([^"]+)"',
        r'<meta\s+itemprop="datePublished"\s+content="([^"]+)"',
        r'"uploadDate"\s*:\s*"([^"]+)"',
        r'"publishDate"\s*:\s*"([^"]+)"',
    ):
        m = re.search(pat, html, flags=re.I)
        if m:
            published = m.group(1).strip()
            break

    return {
        "channel_id": channel_id,
        "title": title,
        "published_at_utc": published,
    }


def _date_kst_from_published(published: str | None) -> str:
    """YYYY-MM-DD. 날짜만 오면 그대로, ISO면 KST 변환, 실패 시 오늘(KST)."""
    s = (published or "").strip()
    if not s:
        return dt.datetime.now(KST).date().isoformat()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    try:
        return published_to_date_kst(s)
    except (ValueError, TypeError):
        return dt.datetime.now(KST).date().isoformat()


def _summary_exists(
    con: sqlite3.Connection, channel_id: str, video_id: str
) -> bool:
    tables = {
        r[0]
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "youtube_video_summaries" not in tables:
        return False
    row = con.execute(
        "SELECT 1 FROM youtube_video_summaries WHERE channel_id=? AND video_id=?",
        (channel_id, video_id),
    ).fetchone()
    return row is not None


def collect_url(
    con: sqlite3.Connection,
    url: str,
    *,
    opener=urllib.request.urlopen,
    transcript_fn: Callable[[str], tuple[str | None, str | None, str | None]]
    | None = None,
    meta_fn: Callable[[str], dict] | None = None,
) -> dict:
    """영상 URL 1건 → watch 메타 + 대본 → youtube_videos upsert.

    반환: video_id, channel_id, date_kst, title, status, has_transcript, url
    status ∈ inserted | updated | already_summarized
    채널 URL 등 잘못된 입력 → ValueError(한글).
    """
    video_id = parse_video_id(url)
    watch_url = f"https://www.youtube.com/watch?v={video_id}"

    if meta_fn is not None:
        meta = meta_fn(video_id)
    else:
        try:
            raw = fetch_url(watch_url, opener=opener)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"영상 페이지 요청 실패: {exc}") from exc
        meta = parse_watch_meta(raw.decode("utf-8", "replace"))

    channel_id = (meta.get("channel_id") or "").strip()
    if not channel_id:
        raise ValueError("영상 채널 정보를 찾지 못했습니다.")
    title = (meta.get("title") or "").strip() or video_id
    published = (meta.get("published_at_utc") or "").strip()
    date_kst = _date_kst_from_published(published)
    if not published:
        published = f"{date_kst}T00:00:00+09:00"

    get_tr = transcript_fn or fetch_transcript
    text, lang, source = get_tr(video_id)

    ensure_schema(con)
    existed = (
        con.execute(
            "SELECT 1 FROM youtube_videos WHERE channel_id=? AND video_id=?",
            (channel_id, video_id),
        ).fetchone()
        is not None
    )
    already = _summary_exists(con, channel_id, video_id)

    upsert_videos(
        con,
        [
            {
                "channel_id": channel_id,
                "video_id": video_id,
                "title": title,
                "published_at_utc": published,
                "date_kst": date_kst,
                "url": watch_url,
                "transcript": text,
                "transcript_lang": lang,
                "transcript_source": source,
                "raw_json": json.dumps(
                    {
                        "video_id": video_id,
                        "title": title,
                        "published_at_utc": published,
                        "url": watch_url,
                        "source": "collect_url",
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    )

    if already:
        status = "already_summarized"
    elif existed:
        status = "updated"
    else:
        status = "inserted"

    has_tr = text is not None and str(text).strip() != ""
    return {
        "video_id": video_id,
        "channel_id": channel_id,
        "date_kst": date_kst,
        "title": title,
        "status": status,
        "has_transcript": has_tr,
        "url": watch_url,
    }


def list_channel_catalog(
    channel_id: str,
    *,
    with_duration: bool = True,
    opener=urllib.request.urlopen,
    duration_fn: Callable[[str], int | None] | None = None,
    max_workers: int = 6,
) -> list[dict]:
    """RSS 최근 영상 + (옵션) duration_sec.

    각 dict: channel_id, video_id, title, published_at_utc, date_kst, url, duration_sec.
    duration 실패 → null. STT/대본 수집 없음.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    entries = list_channel_videos_rss(channel_id, opener=opener)
    get_dur = duration_fn
    if get_dur is None and with_duration:
        get_dur = lambda vid: fetch_duration_seconds(vid, opener=opener)  # noqa: E731

    out: list[dict] = []
    for e in entries:
        try:
            date_kst = published_to_date_kst(e["published_at_utc"])
        except (ValueError, TypeError):
            date_kst = ""
        url = e.get("url") or f"https://www.youtube.com/watch?v={e['video_id']}"
        out.append(
            {
                "channel_id": channel_id,
                "video_id": e["video_id"],
                "title": e.get("title") or "",
                "published_at_utc": e.get("published_at_utc") or "",
                "date_kst": date_kst,
                "url": url,
                "duration_sec": None,
            }
        )

    if not with_duration or not out or get_dur is None:
        return out

    by_id = {row["video_id"]: row for row in out}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(get_dur, vid): vid for vid in by_id}
        for fut in as_completed(futs):
            vid = futs[fut]
            try:
                by_id[vid]["duration_sec"] = fut.result()
            except Exception:  # noqa: BLE001
                by_id[vid]["duration_sec"] = None
    return out


def fetch_transcript(
    video_id: str,
) -> tuple[str | None, str | None, str | None]:
    """(text, lang, source) source in {'manual','auto', None}.

    언어 우선: ko → en → list() 첫 성공.
    실패/없음: (None, None, None).
    plain text = snippet text 를 '\\n'.join.
    """
    api = YouTubeTranscriptApi()

    def _from_fetched(fetched) -> tuple[str, str, str]:
        texts = [sn.text for sn in fetched.snippets]
        plain = "\n".join(texts)
        lang = fetched.language_code
        source = "auto" if fetched.is_generated else "manual"
        return plain, lang, source

    for langs in (["ko"], ["en"]):
        try:
            fetched = api.fetch(video_id, languages=langs)
            return _from_fetched(fetched)
        except Exception:  # noqa: BLE001 — 다음 언어/list 폴백
            continue

    try:
        listing = api.list(video_id)
        for tr in listing:
            try:
                fetched = tr.fetch()
                return _from_fetched(fetched)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    return (None, None, None)


def upsert_videos(con: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    """UNIQUE(channel_id, video_id) upsert. 반환: (inserted, updated)."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    inserted = updated = 0
    for r in rows:
        exists = (
            con.execute(
                "SELECT 1 FROM youtube_videos WHERE channel_id=? AND video_id=?",
                (r["channel_id"], r["video_id"]),
            ).fetchone()
            is not None
        )
        con.execute(
            """
            INSERT INTO youtube_videos(
                channel_id, video_id, title, published_at_utc, date_kst,
                url, transcript, transcript_lang, transcript_source, raw_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, video_id) DO UPDATE SET
                title=excluded.title,
                published_at_utc=excluded.published_at_utc,
                date_kst=excluded.date_kst,
                url=excluded.url,
                transcript=excluded.transcript,
                transcript_lang=excluded.transcript_lang,
                transcript_source=excluded.transcript_source,
                raw_json=excluded.raw_json,
                updated_at=excluded.updated_at
            """,
            (
                r["channel_id"],
                r["video_id"],
                r["title"],
                r["published_at_utc"],
                r["date_kst"],
                r["url"],
                r.get("transcript"),
                r.get("transcript_lang"),
                r.get("transcript_source"),
                r.get("raw_json"),
                now,
                now,
            ),
        )
        if exists:
            updated += 1
        else:
            inserted += 1
    return inserted, updated


def collect_channel(
    con: sqlite3.Connection,
    channel_id: str,
    date_kst: str,
    *,
    opener=urllib.request.urlopen,
    transcript_fn: Callable[[str], tuple[str | None, str | None, str | None]]
    | None = None,
) -> dict:
    """단일 채널: RSS → date 필터 → 대본 → upsert.

    반환 stats: rss_entries, matched_date, inserted, updated, skipped_no_transcript
    """
    get_transcript = transcript_fn or fetch_transcript
    entries = list_channel_videos_rss(channel_id, opener=opener)
    matched: list[dict] = []
    skipped_no_transcript = 0

    for e in entries:
        try:
            d = published_to_date_kst(e["published_at_utc"])
        except (ValueError, TypeError):
            continue
        if d != date_kst:
            continue
        text, lang, source = get_transcript(e["video_id"])
        if text is None:
            skipped_no_transcript += 1
        row = {
            "channel_id": channel_id,
            "video_id": e["video_id"],
            "title": e["title"],
            "published_at_utc": e["published_at_utc"],
            "date_kst": d,
            "url": e["url"],
            "transcript": text,
            "transcript_lang": lang,
            "transcript_source": source,
            "raw_json": json.dumps(e, ensure_ascii=False),
        }
        matched.append(row)

    inserted, updated = upsert_videos(con, matched)
    return {
        "channel_id": channel_id,
        "rss_entries": len(entries),
        "matched_date": len(matched),
        "inserted": inserted,
        "updated": updated,
        "skipped_no_transcript": skipped_no_transcript,
    }


def collect_channel_range(
    con: sqlite3.Connection,
    channel_id: str,
    from_date: str,
    to_date: str,
    *,
    opener=urllib.request.urlopen,
    transcript_fn: Callable[[str], tuple[str | None, str | None, str | None]]
    | None = None,
) -> dict:
    """단일 채널: RSS 중 date_kst 가 [from_date, to_date] 인 것만 수집.

    RSS 밖(오래된 영상)은 의도적으로 0건 — backfill 없음.
    """
    get_transcript = transcript_fn or fetch_transcript
    entries = list_channel_videos_rss(channel_id, opener=opener)
    matched: list[dict] = []
    skipped_no_transcript = 0

    for e in entries:
        try:
            d = published_to_date_kst(e["published_at_utc"])
        except (ValueError, TypeError):
            continue
        if d < from_date or d > to_date:
            continue
        text, lang, source = get_transcript(e["video_id"])
        if text is None:
            skipped_no_transcript += 1
        matched.append(
            {
                "channel_id": channel_id,
                "video_id": e["video_id"],
                "title": e["title"],
                "published_at_utc": e["published_at_utc"],
                "date_kst": d,
                "url": e["url"],
                "transcript": text,
                "transcript_lang": lang,
                "transcript_source": source,
                "raw_json": json.dumps(e, ensure_ascii=False),
            }
        )

    inserted, updated = upsert_videos(con, matched)
    return {
        "channel_id": channel_id,
        "rss_entries": len(entries),
        "matched_date": len(matched),
        "inserted": inserted,
        "updated": updated,
        "skipped_no_transcript": skipped_no_transcript,
    }


def collect_videos(
    con: sqlite3.Connection,
    items: list[dict],
    *,
    transcript_fn: Callable[[str], tuple[str | None, str | None, str | None]]
    | None = None,
) -> dict:
    """선택 영상만 대본 수집(유튜브 자막 API). STT 없음.

    items 각 원소 최소: channel_id, video_id.
    선택: title, published_at_utc, date_kst, url.
    """
    get_transcript = transcript_fn or fetch_transcript
    matched: list[dict] = []
    skipped_no_transcript = 0
    skipped_bad = 0

    for raw in items:
        cid = (raw.get("channel_id") or "").strip()
        vid = (raw.get("video_id") or "").strip()
        if not cid or not vid:
            skipped_bad += 1
            continue
        published = (raw.get("published_at_utc") or "").strip()
        date_kst = (raw.get("date_kst") or "").strip()
        if not date_kst and published:
            try:
                date_kst = published_to_date_kst(published)
            except (ValueError, TypeError):
                date_kst = ""
        if not date_kst:
            # 날짜 없으면 KST 오늘 (목록에서 온 경우 보통 있음)
            date_kst = dt.datetime.now(KST).date().isoformat()
        url = (raw.get("url") or "").strip() or f"https://www.youtube.com/watch?v={vid}"
        title = (raw.get("title") or "").strip() or vid
        text, lang, source = get_transcript(vid)
        if text is None:
            skipped_no_transcript += 1
        matched.append(
            {
                "channel_id": cid,
                "video_id": vid,
                "title": title,
                "published_at_utc": published or f"{date_kst}T00:00:00+09:00",
                "date_kst": date_kst,
                "url": url,
                "transcript": text,
                "transcript_lang": lang,
                "transcript_source": source,
                "raw_json": json.dumps(
                    {
                        "video_id": vid,
                        "title": title,
                        "published_at_utc": published,
                        "url": url,
                        "source": "manual_selected",
                    },
                    ensure_ascii=False,
                ),
            }
        )

    inserted, updated = upsert_videos(con, matched)
    return {
        "targets": len(items),
        "matched": len(matched),
        "inserted": inserted,
        "updated": updated,
        "skipped_no_transcript": skipped_no_transcript,
        "skipped_bad": skipped_bad,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel-id", required=True, help="YouTube channel_id UC…")
    ap.add_argument("--date", required=True, help="KST date YYYY-MM-DD")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        ensure_schema(con)
        stats = collect_channel(con, args.channel_id, args.date)
        con.commit()
    except Exception as exc:
        print(
            f"[collect_youtube] ERROR channel={args.channel_id} "
            f"date={args.date}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        con.close()

    print(
        f"[collect_youtube] channel={args.channel_id} date={args.date} "
        f"rss_entries={stats['rss_entries']} matched_date={stats['matched_date']} "
        f"inserted={stats['inserted']} updated={stats['updated']} "
        f"skipped_no_transcript={stats['skipped_no_transcript']}"
    )


if __name__ == "__main__":
    main()
