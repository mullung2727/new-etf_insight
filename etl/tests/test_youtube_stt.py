"""youtube_stt: fetch_transcript_stt 계약 + backfill DB 갱신 (네트워크 없음)."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.youtube_stt import (
    STT_SOURCE,
    _yt_dlp_cmd,
    backfill,
    download_audio,
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


class YtDlpCmdTest(unittest.TestCase):
    def test_prefers_sibling_of_sys_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
            sibling = Path(tmp) / exe_name
            sibling.touch()
            fake_py = str(Path(tmp) / "python.exe")
            with patch.object(sys, "executable", fake_py):
                self.assertEqual(_yt_dlp_cmd(), [str(sibling)])

    def test_falls_back_to_path_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_py = str(Path(tmp) / "python.exe")
            with patch.object(sys, "executable", fake_py), patch(
                "scripts.youtube_stt.shutil.which", return_value="C:/bin/yt-dlp"
            ):
                self.assertEqual(_yt_dlp_cmd(), ["C:/bin/yt-dlp"])

    def test_falls_back_to_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_py = str(Path(tmp) / "python.exe")
            with patch.object(sys, "executable", fake_py), patch(
                "scripts.youtube_stt.shutil.which", return_value=None
            ):
                self.assertEqual(_yt_dlp_cmd(), [fake_py, "-m", "yt_dlp"])

    def test_download_audio_uses_resolved_cmd(self):
        with tempfile.TemporaryDirectory() as tmp:
            captured: dict = {}

            def fake_run(cmd, capture_output=True, text=True):
                captured["cmd"] = cmd
                (Path(tmp) / "vid123.mp3").touch()
                m = MagicMock()
                m.returncode = 0
                m.stderr = ""
                return m

            with patch(
                "scripts.youtube_stt._yt_dlp_cmd",
                return_value=["C:/fake/yt-dlp.exe"],
            ):
                out = download_audio("vid123", tmp, run=fake_run)
            self.assertEqual(captured["cmd"][0], "C:/fake/yt-dlp.exe")
            self.assertEqual(captured["cmd"][1], "-x")
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
