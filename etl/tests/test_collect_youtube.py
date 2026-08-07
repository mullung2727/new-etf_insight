"""collect_youtube.py (수집 코어) 단위 테스트.

스펙: docs/youtube_tech.md §4.5 D1~D7
네트워크 live 제외 — RSS/transcript 전부 mock·fixture.
"""
from __future__ import annotations

import io
import sqlite3
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from scripts.collect_youtube import (
    DEFAULT_DB,
    collect_channel,
    collect_url,
    collect_videos,
    ensure_schema,
    fetch_duration_seconds,
    fetch_transcript,
    fetch_url,
    list_channel_catalog,
    list_channel_videos_rss,
    parse_video_id,
    parse_watch_meta,
    published_to_date_kst,
    upsert_videos,
)

# 형식만 맞는 UC (실존 불필요)
CH = "UCe2etestChannelId_zzzz1"
VID_A = "AAAAAAAAAAA"  # 11자
VID_B = "BBBBBBBBBBB"
VID_SHORT = "F-UgZE6QZiQ"


def _rss_xml(entries: list[dict]) -> bytes:
    """Atom + yt namespace fixture. entries: video_id, title, published, url."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"',
        '      xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>test feed</title>",
    ]
    for e in entries:
        parts.append("  <entry>")
        parts.append(f"    <id>yt:video:{e['video_id']}</id>")
        parts.append(f"    <yt:videoId>{e['video_id']}</yt:videoId>")
        parts.append(f"    <title>{e['title']}</title>")
        parts.append(f"    <published>{e['published']}</published>")
        parts.append(f'    <link rel="alternate" href="{e["url"]}"/>')
        parts.append("  </entry>")
    parts.append("</feed>")
    return "\n".join(parts).encode("utf-8")


class SchemaTest(unittest.TestCase):
    """D1: schema ensure 후 tables 존재."""

    def test_d1_ensure_schema_creates_youtube_videos(self):
        con = sqlite3.connect(":memory:")
        ensure_schema(con)
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("youtube_videos", tables)
        idxs = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        self.assertIn("idx_youtube_videos_date", idxs)


class UpsertTest(unittest.TestCase):
    """D2: upsert 동일 video_id 2회 → row 1, 최신 title."""

    def test_d2_upsert_idempotent_updates_title(self):
        con = sqlite3.connect(":memory:")
        ensure_schema(con)
        row1 = {
            "channel_id": CH,
            "video_id": VID_A,
            "title": "old title",
            "published_at_utc": "2026-07-09T03:30:23+00:00",
            "date_kst": "2026-07-09",
            "url": f"https://www.youtube.com/watch?v={VID_A}",
            "transcript": "hello",
            "transcript_lang": "ko",
            "transcript_source": "auto",
            "raw_json": "{}",
        }
        row2 = {**row1, "title": "new title", "transcript": "updated"}
        upsert_videos(con, [row1])
        upsert_videos(con, [row2])
        con.commit()
        rows = con.execute(
            "SELECT title, transcript FROM youtube_videos WHERE channel_id=? AND video_id=?",
            (CH, VID_A),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "new title")
        self.assertEqual(rows[0][1], "updated")


class DateFilterTest(unittest.TestCase):
    """D3: date 필터 — 다른 날짜 published 제외."""

    def test_d3_other_date_excluded(self):
        xml = _rss_xml(
            [
                {
                    "video_id": VID_A,
                    "title": "other day",
                    "published": "2026-07-08T10:00:00+00:00",
                    "url": f"https://www.youtube.com/watch?v={VID_A}",
                },
                {
                    "video_id": VID_B,
                    "title": "target day",
                    "published": "2026-07-09T03:30:23+00:00",
                    "url": f"https://www.youtube.com/watch?v={VID_B}",
                },
            ]
        )

        def opener(req, timeout=40):
            return io.BytesIO(xml)

        def fake_transcript(video_id: str):
            return ("text", "ko", "auto")

        con = sqlite3.connect(":memory:")
        ensure_schema(con)
        stats = collect_channel(
            con,
            CH,
            date_kst="2026-07-09",
            opener=opener,
            transcript_fn=fake_transcript,
        )
        con.commit()
        rows = con.execute(
            "SELECT video_id FROM youtube_videos ORDER BY video_id"
        ).fetchall()
        self.assertEqual([r[0] for r in rows], [VID_B])
        self.assertEqual(stats["matched_date"], 1)
        self.assertEqual(stats["rss_entries"], 2)


class TranscriptTest(unittest.TestCase):
    """D4: fetch_transcript mock snippets → newline join plain text."""

    def test_d4_plain_text_join_no_timecodes(self):
        snippet1 = MagicMock()
        snippet1.text = "첫 줄"
        snippet2 = MagicMock()
        snippet2.text = "둘째 줄"
        fetched = MagicMock()
        fetched.snippets = [snippet1, snippet2]
        fetched.language_code = "ko"
        fetched.is_generated = True

        api = MagicMock()
        api.fetch.return_value = fetched

        with patch("scripts.collect_youtube.YouTubeTranscriptApi", return_value=api):
            text, lang, source = fetch_transcript(VID_A)

        self.assertEqual(text, "첫 줄\n둘째 줄")
        self.assertEqual(lang, "ko")
        self.assertEqual(source, "auto")
        api.fetch.assert_called()
        first_call = api.fetch.call_args_list[0]
        self.assertEqual(first_call.args[0], VID_A)
        self.assertEqual(list(first_call.kwargs.get("languages", [])), ["ko"])


class CollectRssTest(unittest.TestCase):
    """D5: mock RSS → collect N rows UNIQUE. D7: shorts url."""

    def test_d5_collect_from_rss_fixture(self):
        xml = _rss_xml(
            [
                {
                    "video_id": VID_A,
                    "title": "vid A",
                    "published": "2026-07-09T01:00:00+00:00",
                    "url": f"https://www.youtube.com/watch?v={VID_A}",
                },
                {
                    "video_id": VID_B,
                    "title": "vid B",
                    "published": "2026-07-09T05:00:00+00:00",
                    "url": f"https://www.youtube.com/watch?v={VID_B}",
                },
            ]
        )

        def opener(req, timeout=40):
            return io.BytesIO(xml)

        def fake_transcript(video_id: str):
            return (f"body-{video_id}", "ko", "manual")

        con = sqlite3.connect(":memory:")
        ensure_schema(con)
        stats = collect_channel(
            con,
            CH,
            date_kst="2026-07-09",
            opener=opener,
            transcript_fn=fake_transcript,
        )
        # 같은 날짜 재실행 → UNIQUE 유지
        collect_channel(
            con,
            CH,
            date_kst="2026-07-09",
            opener=opener,
            transcript_fn=fake_transcript,
        )
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM youtube_videos").fetchone()[0]
        self.assertEqual(n, 2)
        self.assertEqual(stats["matched_date"], 2)
        self.assertEqual(stats["rss_entries"], 2)

    def test_d7_shorts_url_saved(self):
        shorts_url = f"https://www.youtube.com/shorts/{VID_SHORT}"
        xml = _rss_xml(
            [
                {
                    "video_id": VID_SHORT,
                    "title": "shorts title",
                    "published": "2026-07-09T03:30:23+00:00",
                    "url": shorts_url,
                }
            ]
        )

        def opener(req, timeout=40):
            return io.BytesIO(xml)

        con = sqlite3.connect(":memory:")
        ensure_schema(con)
        collect_channel(
            con,
            CH,
            date_kst="2026-07-09",
            opener=opener,
            transcript_fn=lambda _v: ("t", "ko", "auto"),
        )
        con.commit()
        row = con.execute(
            "SELECT video_id, url FROM youtube_videos"
        ).fetchone()
        self.assertEqual(row[0], VID_SHORT)
        self.assertEqual(row[1], shorts_url)


class TranscriptFailTest(unittest.TestCase):
    """D6: 자막 실패 → transcript NULL, 수집 성공."""

    def test_d6_no_transcript_still_succeeds(self):
        xml = _rss_xml(
            [
                {
                    "video_id": VID_A,
                    "title": "no sub",
                    "published": "2026-07-09T12:00:00+00:00",
                    "url": f"https://www.youtube.com/watch?v={VID_A}",
                }
            ]
        )

        def opener(req, timeout=40):
            return io.BytesIO(xml)

        def fail_transcript(video_id: str):
            return (None, None, None)

        con = sqlite3.connect(":memory:")
        ensure_schema(con)
        stats = collect_channel(
            con,
            CH,
            date_kst="2026-07-09",
            opener=opener,
            transcript_fn=fail_transcript,
        )
        con.commit()
        row = con.execute(
            "SELECT transcript, transcript_lang, transcript_source FROM youtube_videos"
        ).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])
        self.assertEqual(stats["skipped_no_transcript"], 1)
        self.assertEqual(stats["matched_date"], 1)


class FetchRetryTest(unittest.TestCase):
    """유튜브 RSS 백엔드가 유효한 URL에도 404/500을 랜덤 반환하는 구간 대응."""

    @staticmethod
    def _http_error(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError("https://x.test/y", code, "boom", {}, None)

    def test_transient_404_then_success(self):
        calls = []

        def opener(req, timeout=40):
            calls.append(1)
            if len(calls) < 3:
                raise self._http_error(404)
            return io.BytesIO(b"ok")

        self.assertEqual(fetch_url("https://x.test/y", opener=opener, _sleep=lambda s: None), b"ok")
        self.assertEqual(len(calls), 3)

    def test_retry_count_is_capped(self):
        calls = []

        def opener(req, timeout=40):
            calls.append(1)
            raise self._http_error(500)

        with self.assertRaises(urllib.error.HTTPError):
            fetch_url("https://x.test/y", opener=opener, retries=2, _sleep=lambda s: None)
        self.assertEqual(len(calls), 3)  # 최초 1회 + 재시도 2회, 그 이상 없음

    def test_non_retryable_status_fails_immediately(self):
        calls = []

        def opener(req, timeout=40):
            calls.append(1)
            raise self._http_error(403)

        with self.assertRaises(urllib.error.HTTPError):
            fetch_url("https://x.test/y", opener=opener, _sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_backoff_grows_and_is_jittered(self):
        waits = []

        def opener(req, timeout=40):
            raise self._http_error(500)

        with self.assertRaises(urllib.error.HTTPError):
            fetch_url("https://x.test/y", opener=opener, retries=2, _sleep=waits.append)
        self.assertEqual(len(waits), 2)
        self.assertLess(waits[0], waits[1])
        self.assertGreaterEqual(waits[0], 4.0)


class ListRssTest(unittest.TestCase):
    def test_list_channel_videos_rss_parses_entries(self):
        xml = _rss_xml(
            [
                {
                    "video_id": VID_A,
                    "title": "t1",
                    "published": "2026-07-09T03:30:23+00:00",
                    "url": f"https://www.youtube.com/watch?v={VID_A}",
                }
            ]
        )

        def opener(req, timeout=40):
            return io.BytesIO(xml)

        items = list_channel_videos_rss(CH, opener=opener)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["video_id"], VID_A)
        self.assertEqual(items[0]["title"], "t1")
        self.assertEqual(items[0]["published_at_utc"], "2026-07-09T03:30:23+00:00")

    def test_published_to_date_kst(self):
        # 2026-07-09T03:30:23 UTC → KST +9 → 2026-07-09 12:30
        self.assertEqual(
            published_to_date_kst("2026-07-09T03:30:23+00:00"), "2026-07-09"
        )
        # 2026-07-08T16:00:00 UTC → KST 2026-07-09 01:00
        self.assertEqual(
            published_to_date_kst("2026-07-08T16:00:00+00:00"), "2026-07-09"
        )


class CollectSelectedTest(unittest.TestCase):
    """선택 영상만 자막 수집."""

    def test_collect_videos_upserts_selected(self):
        con = sqlite3.connect(":memory:")
        ensure_schema(con)
        items = [
            {
                "channel_id": CH,
                "video_id": VID_A,
                "title": "a",
                "published_at_utc": "2026-07-09T03:30:23+00:00",
                "date_kst": "2026-07-09",
                "url": f"https://www.youtube.com/watch?v={VID_A}",
            },
            {
                "channel_id": CH,
                "video_id": VID_B,
                "title": "b",
                "published_at_utc": "2026-07-08T10:00:00+00:00",
                "date_kst": "2026-07-08",
                "url": f"https://www.youtube.com/watch?v={VID_B}",
            },
        ]

        def fake_tr(vid: str):
            if vid == VID_A:
                return ("hello", "ko", "auto")
            return (None, None, None)

        stats = collect_videos(con, items, transcript_fn=fake_tr)
        con.commit()
        self.assertEqual(stats["matched"], 2)
        self.assertEqual(stats["inserted"], 2)
        self.assertEqual(stats["skipped_no_transcript"], 1)
        rows = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT video_id, transcript FROM youtube_videos"
            )
        }
        self.assertEqual(rows[VID_A], "hello")
        self.assertIsNone(rows[VID_B])


class CatalogListTest(unittest.TestCase):
    """영상 목록 + duration (STT 없음)."""

    def test_fetch_duration_seconds_parses_length(self):
        html = b'{"lengthSeconds":"338"} other junk'

        def opener(req, timeout=40):
            return io.BytesIO(html)

        self.assertEqual(fetch_duration_seconds(VID_A, opener=opener), 338)

    def test_list_channel_catalog_with_duration_fn(self):
        xml = _rss_xml(
            [
                {
                    "video_id": VID_A,
                    "title": "short",
                    "published": "2026-07-09T03:30:23+00:00",
                    "url": f"https://www.youtube.com/watch?v={VID_A}",
                },
                {
                    "video_id": VID_B,
                    "title": "long",
                    "published": "2026-07-08T10:00:00+00:00",
                    "url": f"https://www.youtube.com/watch?v={VID_B}",
                },
            ]
        )

        def opener(req, timeout=40):
            return io.BytesIO(xml)

        def fake_dur(vid: str):
            return 90 if vid == VID_A else 1364

        rows = list_channel_catalog(
            CH, with_duration=True, opener=opener, duration_fn=fake_dur
        )
        self.assertEqual(len(rows), 2)
        by_id = {r["video_id"]: r for r in rows}
        self.assertEqual(by_id[VID_A]["duration_sec"], 90)
        self.assertEqual(by_id[VID_B]["duration_sec"], 1364)
        self.assertEqual(by_id[VID_A]["date_kst"], "2026-07-09")
        self.assertEqual(by_id[VID_A]["channel_id"], CH)


class DefaultPathTest(unittest.TestCase):
    def test_default_db_name(self):
        self.assertEqual(DEFAULT_DB.name, "youtube_public.sqlite3")


class CollectUrlTest(unittest.TestCase):
    """영상 URL 1건 가져오기 (network mock)."""

    def test_parse_video_id_forms(self):
        self.assertEqual(
            parse_video_id("https://www.youtube.com/watch?v=jdZls-7iVps"),
            "jdZls-7iVps",
        )
        self.assertEqual(parse_video_id("https://youtu.be/jdZls-7iVps"), "jdZls-7iVps")
        self.assertEqual(
            parse_video_id("https://www.youtube.com/shorts/jdZls-7iVps"),
            "jdZls-7iVps",
        )
        self.assertEqual(parse_video_id("jdZls-7iVps"), "jdZls-7iVps")

    def test_parse_video_id_rejects_channel(self):
        with self.assertRaises(ValueError) as cm:
            parse_video_id("https://www.youtube.com/@someone")
        self.assertIn("채널", str(cm.exception))

    def test_parse_watch_meta(self):
        html = (
            '<meta property="og:title" content="테스트 제목">'
            '<meta itemprop="uploadDate" content="2026-07-12">'
            '{"externalChannelId":"' + CH + '"}'
        )
        meta = parse_watch_meta(html)
        self.assertEqual(meta["channel_id"], CH)
        self.assertEqual(meta["title"], "테스트 제목")
        self.assertEqual(meta["published_at_utc"], "2026-07-12")

    def test_collect_url_inserts_and_already_summarized(self):
        con = sqlite3.connect(":memory:")
        ensure_schema(con)
        con.execute(
            """
            CREATE TABLE youtube_video_summaries (
              channel_id TEXT, video_id TEXT, date_kst TEXT,
              model TEXT, summary_json TEXT,
              created_at TEXT, updated_at TEXT,
              UNIQUE(channel_id, video_id)
            )
            """
        )

        def meta_fn(vid: str):
            return {
                "channel_id": CH,
                "title": "hello title",
                "published_at_utc": "2026-07-12",
            }

        def tr_fn(vid: str):
            return ("대본 본문", "ko", "auto")

        r1 = collect_url(
            con,
            f"https://www.youtube.com/watch?v={VID_A}",
            meta_fn=meta_fn,
            transcript_fn=tr_fn,
        )
        con.commit()
        self.assertEqual(r1["status"], "inserted")
        self.assertEqual(r1["date_kst"], "2026-07-12")
        self.assertTrue(r1["has_transcript"])

        r2 = collect_url(
            con,
            f"https://youtu.be/{VID_A}",
            meta_fn=meta_fn,
            transcript_fn=tr_fn,
        )
        self.assertEqual(r2["status"], "updated")

        con.execute(
            "INSERT INTO youtube_video_summaries VALUES (?,?,?,?,?,?,?)",
            (CH, VID_A, "2026-07-12", "codex", "{}", "t0", "t0"),
        )
        r3 = collect_url(
            con,
            VID_A,
            meta_fn=meta_fn,
            transcript_fn=tr_fn,
        )
        self.assertEqual(r3["status"], "already_summarized")


if __name__ == "__main__":
    unittest.main()
