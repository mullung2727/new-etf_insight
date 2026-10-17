"""run_youtube_analysis list_videos / auto 필터."""
from __future__ import annotations

import sqlite3
import unittest

from scripts.run_youtube_analysis import (
    MAX_TRANSCRIPT_CHARS,
    MIN_TRANSCRIPT_CHARS,
    _stt_failure_message,
    _unexpected_stt_failures,
    list_videos,
)


class ListVideosTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(
            """
            CREATE TABLE youtube_videos (
              channel_id TEXT, video_id TEXT, title TEXT,
              published_at_utc TEXT, date_kst TEXT, url TEXT,
              transcript TEXT, transcript_lang TEXT, transcript_source TEXT,
              raw_json TEXT, created_at TEXT, updated_at TEXT,
              UNIQUE(channel_id, video_id)
            );
            CREATE TABLE youtube_video_summaries (
              channel_id TEXT, video_id TEXT, date_kst TEXT,
              model TEXT, summary_json TEXT,
              created_at TEXT, updated_at TEXT,
              UNIQUE(channel_id, video_id)
            );
            """
        )
        body = "본문" * (MIN_TRANSCRIPT_CHARS // 2 + 10)  # 길이 하한 통과용
        rows = [
            ("UCauto000000000000000001", "vid_auto_pend", "2026-07-11", body),
            ("UCauto000000000000000001", "vid_auto_done", "2026-07-11", body),
            ("UCmanual0000000000000001", "vid_man_pend", "2026-07-11", body),
            ("UCauto000000000000000001", "vid_old", "2026-07-09", body),
            ("UCauto000000000000000001", "vid_notr", "2026-07-11", None),
            ("UCauto000000000000000001", "vid_short", "2026-07-11", "짧은 쇼츠"),
            ("UCauto000000000000000001", "vid_long", "2026-07-11",
             "가" * (MAX_TRANSCRIPT_CHARS + 1)),
        ]
        for ch, vid, d, tr in rows:
            self.con.execute(
                "INSERT INTO youtube_videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ch, vid, "t", "2026-07-11T00:00:00+00:00", d, "u", tr, "ko", "auto", "{}", "t0", "t0"),
            )
        self.con.execute(
            "INSERT INTO youtube_video_summaries VALUES (?,?,?,?,?,?,?)",
            (
                "UCauto000000000000000001",
                "vid_auto_done",
                "2026-07-11",
                "codex",
                "{}",
                "t0",
                "t0",
            ),
        )
        self.con.commit()

    def test_pending_auto_only_same_day(self):
        vids = list_videos(
            self.con,
            date_from="2026-07-11",
            date_to="2026-07-11",
            channel_ids=["UCauto000000000000000001"],
            pending_only=True,
        )
        self.assertEqual(vids, [("UCauto000000000000000001", "vid_auto_pend")])

    def test_lookback_includes_old_pending(self):
        vids = list_videos(
            self.con,
            date_from="2026-07-09",
            date_to="2026-07-11",
            channel_ids=["UCauto000000000000000001"],
            pending_only=True,
        )
        self.assertEqual(
            set(vids),
            {
                ("UCauto000000000000000001", "vid_auto_pend"),
                ("UCauto000000000000000001", "vid_old"),
            },
        )

    def test_length_range_excludes_short_and_long(self):
        """쇼츠(하한 미만)·과장(상한 초과)은 자동 요약 대상에서 제외."""
        vids = list_videos(
            self.con,
            date_from="2026-07-11",
            date_to="2026-07-11",
            channel_ids=["UCauto000000000000000001"],
            pending_only=True,
        )
        self.assertNotIn(("UCauto000000000000000001", "vid_short"), vids)
        self.assertNotIn(("UCauto000000000000000001", "vid_long"), vids)
        self.assertIn(("UCauto000000000000000001", "vid_auto_pend"), vids)

    def test_require_transcript_false_includes_no_transcript(self):
        """STT 폴백용: 대본 없는 vid_notr 도 포함."""
        vids = list_videos(
            self.con,
            date_from="2026-07-11",
            date_to="2026-07-11",
            channel_ids=["UCauto000000000000000001"],
            pending_only=True,
            require_transcript=False,
        )
        self.assertIn(("UCauto000000000000000001", "vid_notr"), vids)

    def test_require_transcript_true_excludes_no_transcript(self):
        vids = list_videos(
            self.con,
            date_from="2026-07-11",
            date_to="2026-07-11",
            channel_ids=["UCauto000000000000000001"],
            pending_only=True,
            require_transcript=True,
        )
        self.assertNotIn(("UCauto000000000000000001", "vid_notr"), vids)

    def test_empty_channel_list(self):
        vids = list_videos(
            self.con,
            date_from="2026-07-11",
            date_to="2026-07-11",
            channel_ids=[],
            pending_only=True,
        )
        self.assertEqual(vids, [])


class SttFailureAlertTest(unittest.TestCase):
    def test_no_failures_returns_none(self):
        self.assertIsNone(
            _stt_failure_message([], 13, "2026-10-15", "2026-10-17")
        )

    def test_expected_only_filtered_out(self):
        pairs = [
            ("x", ["stt_error:yt-dlp 실패 x: ERROR: [youtube] x: Private video"]),
            ("y", ["stt_error:members-only video, skipping"]),
            ("z", ["stt_error:only available to this channel's members"]),
        ]
        failures = _unexpected_stt_failures(pairs)
        self.assertEqual(failures, [])
        self.assertIsNone(
            _stt_failure_message(failures, 3, "2026-10-15", "2026-10-17")
        )

    def test_non_stt_warnings_ignored_and_prefix_stripped(self):
        pairs = [("vid1", ["stt_error:boom", "plain warning"])]
        self.assertEqual(_unexpected_stt_failures(pairs), [("vid1", "boom")])

    def test_four_key_invalid_shows_three_plus_rest(self):
        pairs = [
            (f"vid{i}", [f"stt_error:Gemini STT 실패 vid{i}: API_KEY_INVALID"])
            for i in range(4)
        ]
        failures = _unexpected_stt_failures(pairs)
        self.assertEqual(len(failures), 4)
        msg = _stt_failure_message(failures, 13, "2026-10-15", "2026-10-17")
        assert msg is not None
        lines = msg.split("\n")
        self.assertEqual(
            lines[0], "⚠ [유튜브 분석] STT 실패 4/13건 (2026-10-15~2026-10-17)"
        )
        self.assertEqual(len(lines), 5)
        for i in range(3):
            self.assertTrue(lines[1 + i].startswith(f"- vid{i}: "))
        self.assertEqual(lines[4], "… 외 1건")

    def test_long_message_truncated_and_newlines_removed(self):
        failures = [("vid1", "에러\n상세 " + "가" * 200)]
        msg = _stt_failure_message(failures, 1, "2026-10-15", "2026-10-15")
        assert msg is not None
        example = msg.split("\n")[1]
        body = example.split(": ", 1)[1]
        self.assertEqual(len(body), 150)
        self.assertNotIn("\n", body)


if __name__ == "__main__":
    unittest.main()
