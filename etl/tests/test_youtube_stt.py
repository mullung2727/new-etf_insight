"""youtube_stt: fetch_transcript_stt 계약 + backfill DB 갱신 (네트워크 없음)."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from scripts.youtube_stt import (
    STT_SOURCE,
    backfill,
    fetch_transcript_stt,
    list_missing,
)


def _make_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE youtube_videos (
          channel_id TEXT, video_id TEXT, title TEXT,
          published_at_utc TEXT, date_kst TEXT, url TEXT,
          transcript TEXT, transcript_lang TEXT, transcript_source TEXT,
          raw_json TEXT, created_at TEXT, updated_at TEXT,
          UNIQUE(channel_id, video_id)
        );
        """
    )
    rows = [
        ("UCx0000000000000000000001", "vid_missing", "2026-07-14", None),
        ("UCx0000000000000000000001", "vid_blank", "2026-07-13", "   "),
        ("UCx0000000000000000000001", "vid_has", "2026-07-12", "이미 대본 있음"),
    ]
    for ch, vid, d, tr in rows:
        con.execute(
            "INSERT INTO youtube_videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ch, vid, "t", "2026-07-14T00:00:00+00:00", d, "u", tr, None, None, "{}", "t0", "t0"),
        )
    return con


class FetchTranscriptSttTest(unittest.TestCase):
    def test_returns_text_lang_source(self):
        def dl(vid, work):
            return Path(work) / f"{vid}.mp3"

        def tr(audio):
            return "  전사된 대본  "

        text, lang, source = fetch_transcript_stt(
            "abc", tmp_dir=".", download_fn=dl, transcribe_fn=tr
        )
        self.assertEqual(text, "전사된 대본")
        self.assertEqual(lang, "ko")
        self.assertEqual(source, STT_SOURCE)

    def test_empty_transcript_is_none(self):
        out = fetch_transcript_stt(
            "abc",
            tmp_dir=".",
            download_fn=lambda vid, work: Path(work),
            transcribe_fn=lambda audio: "   ",
        )
        self.assertEqual(out, (None, None, None))


class ListMissingTest(unittest.TestCase):
    def test_only_null_or_blank(self):
        con = _make_db()
        got = {v for _, v in list_missing(con, limit=10)}
        self.assertEqual(got, {"vid_missing", "vid_blank"})

    def test_channel_and_limit(self):
        con = _make_db()
        rows = list_missing(con, channel_id="UCx0000000000000000000001", limit=1)
        self.assertEqual(len(rows), 1)
        # date_kst DESC → vid_missing(07-14) 우선
        self.assertEqual(rows[0][1], "vid_missing")


class BackfillTest(unittest.TestCase):
    def test_fills_db_row(self):
        con = _make_db()
        rows = list_missing(con, limit=10)

        def fake_stt(video_id):
            return (f"{video_id} 전사", "ko", STT_SOURCE)

        stats = backfill(con, rows, stt_fn=fake_stt)
        self.assertEqual(stats["filled"], 2)
        self.assertEqual(stats["errors"], 0)

        r = con.execute(
            "SELECT transcript, transcript_source FROM youtube_videos WHERE video_id='vid_missing'"
        ).fetchone()
        self.assertEqual(r[0], "vid_missing 전사")
        self.assertEqual(r[1], STT_SOURCE)

    def test_no_text_skips_without_error(self):
        con = _make_db()
        rows = list_missing(con, limit=10)
        stats = backfill(con, rows, stt_fn=lambda vid: (None, None, None))
        self.assertEqual(stats["filled"], 0)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["errors"], 0)

    def test_one_failure_does_not_kill_batch(self):
        con = _make_db()
        rows = list_missing(con, limit=10)

        def flaky(video_id):
            if video_id == "vid_missing":
                raise RuntimeError("boom")
            return ("ok 전사", "ko", STT_SOURCE)

        stats = backfill(con, rows, stt_fn=flaky)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["filled"], 1)


if __name__ == "__main__":
    unittest.main()
