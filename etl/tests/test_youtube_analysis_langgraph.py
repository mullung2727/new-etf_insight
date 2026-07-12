"""youtube_analysis_langgraph G1~G8 테스트. docs/youtube_tech.md §5.9"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.collect_youtube import ensure_schema as ensure_videos
from scripts.youtube_langgraph.youtube_analysis_langgraph import (
    CHUNK_CHARS,
    build_chunks,
    chunk_by_chars,
    chunk_by_timecode,
    ensure_summary_schema,
    run_video,
)
from scripts.youtube_stock_insights import ensure_schema as ensure_insights

CH_DISC = "UCdisc0veryChannelIdzzz1"
CH_ONLY = "UCcollectOnlyChanId_zzz1"
VID = "AAAAAAAAAAA"
DATE = "2026-07-09"
NAME_TO_CODE = {"삼성전자": "005930", "카카오": "035720"}


def _seed(
    con: sqlite3.Connection,
    *,
    channel_id: str = CH_DISC,
    video_id: str = VID,
    transcript: str | None = "대본 본문 삼성전자 이야기",
    title: str = "테스트 영상",
) -> None:
    ensure_videos(con)
    ensure_summary_schema(con)
    ensure_insights(con)
    con.execute(
        """
        INSERT INTO youtube_videos(
            channel_id, video_id, title, published_at_utc, date_kst,
            url, transcript, transcript_lang, transcript_source, raw_json,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            channel_id,
            video_id,
            title,
            "2026-07-09T03:30:23+00:00",
            DATE,
            f"https://www.youtube.com/watch?v={video_id}",
            transcript,
            "ko" if transcript else None,
            "auto" if transcript else None,
            "{}",
            "t0",
            "t0",
        ),
    )
    con.commit()


def _mock_gen_factory():
    """map/reduce/stock 순 호출에 응답."""
    calls: list[str] = []

    def gen(prompt: str, *, output_schema_path, search=False, **kwargs):
        calls.append(Path(output_schema_path).name)
        name = Path(output_schema_path).name
        if name == "chunk_summary_schema.json":
            # chunk_index from prompt line if present
            idx = 0
            if "chunk_index:" in prompt:
                try:
                    idx = int(prompt.split("chunk_index:")[1].split()[0])
                except (IndexError, ValueError):
                    idx = len([c for c in calls if c == name]) - 1
            return json.dumps(
                {"chunk_index": idx, "headline": f"구간{idx}", "bullets": ["p"]},
                ensure_ascii=False,
            )
        if name == "reduce_issues_schema.json":
            return json.dumps(
                {
                    "headline": "통합 한 줄",
                    "issues": [
                        {
                            "title": "이슈1",
                            "summary": "삼성전자 실적",
                            "time_hint": None,
                        }
                    ],
                    "bullets": ["포인트"],
                    "risk_or_caveat": None,
                },
                ensure_ascii=False,
            )
        if name == "stock_extract_schema.json":
            return json.dumps(
                {
                    "stocks": [
                        {"name": "삼성전자", "note": "실적 언급"},
                        {"name": "없는종목XYZ", "note": "환각"},
                    ]
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected schema {name}")

    gen.calls = calls  # type: ignore[attr-defined]
    return gen


class ChunkTest(unittest.TestCase):
    def test_g2_timecode_10min_windows(self):
        segs = [
            {"start": 0, "text": "a"},
            {"start": 100, "text": "b"},
            {"start": 600, "text": "c"},
            {"start": 700, "text": "d"},
            {"start": 1200, "text": "e"},
        ]
        chunks = chunk_by_timecode(segs)
        self.assertEqual(len(chunks), 3)
        self.assertIn("a", chunks[0]["text"])
        self.assertIn("c", chunks[1]["text"])
        self.assertIn("e", chunks[2]["text"])

    def test_g3_char_fallback(self):
        text = "x" * (CHUNK_CHARS + 50)
        chunks = chunk_by_chars(text)
        self.assertEqual(len(chunks), 2)
        self.assertIsNone(chunks[0]["start_sec"])
        # segments empty → char
        self.assertEqual(len(build_chunks(text, None)), 2)


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = str(Path(self.tmp) / "yt.sqlite3")
        self.con = sqlite3.connect(self.db)

    def tearDown(self):
        self.con.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_g1_null_transcript_no_llm(self):
        _seed(self.con, transcript=None)
        gen = _mock_gen_factory()
        with patch(
            "scripts.youtube_langgraph.youtube_analysis_langgraph.load_discovery_channels",
            return_value={CH_DISC: {}},
        ):
            r = run_video(
                channel_id=CH_DISC,
                video_id=VID,
                db_path=self.db,
                generate_fn=gen,
                name_to_code=NAME_TO_CODE,
            )
        self.assertTrue(r.get("skip"))
        self.assertEqual(r.get("llm_calls"), 0)
        n = self.con.execute("SELECT COUNT(*) FROM youtube_video_summaries").fetchone()[0]
        self.assertEqual(n, 0)

    def test_g4_map_reduce_saves_summary(self):
        _seed(self.con, transcript="본문 " * 20)
        gen = _mock_gen_factory()
        with patch(
            "scripts.youtube_langgraph.youtube_analysis_langgraph.load_discovery_channels",
            return_value={CH_DISC: {}},
        ):
            r = run_video(
                channel_id=CH_DISC,
                video_id=VID,
                db_path=self.db,
                generate_fn=gen,
                name_to_code=NAME_TO_CODE,
                segments=[{"start": 0, "text": "hello world"}],
            )
        self.assertTrue(r.get("persisted"))
        # map 1 + reduce 1 + stock 1
        self.assertEqual(r.get("llm_calls"), 3)
        row = self.con.execute(
            "SELECT summary_json FROM youtube_video_summaries WHERE video_id=?",
            (VID,),
        ).fetchone()
        self.assertIsNotNone(row)
        obj = json.loads(row[0])
        self.assertEqual(obj["headline"], "통합 한 줄")
        self.assertIn("issues", obj)

    def test_g5_unknown_name_dropped(self):
        _seed(self.con)
        gen = _mock_gen_factory()
        with patch(
            "scripts.youtube_langgraph.youtube_analysis_langgraph.load_discovery_channels",
            return_value={CH_DISC: {}},
        ):
            r = run_video(
                channel_id=CH_DISC,
                video_id=VID,
                db_path=self.db,
                generate_fn=gen,
                name_to_code=NAME_TO_CODE,
            )
        self.assertEqual(len(r.get("stock_mentions") or []), 1)
        self.assertEqual(r["stock_mentions"][0]["ticker"], "005930")
        self.assertTrue(
            any("없는종목XYZ" in w for w in (r.get("warnings") or []))
        )
        tickers = [
            x[0]
            for x in self.con.execute(
                "SELECT ticker FROM youtube_stock_insights"
            ).fetchall()
        ]
        self.assertEqual(tickers, ["005930"])

    def test_g6_existing_summary_skip(self):
        _seed(self.con)
        gen = _mock_gen_factory()
        with patch(
            "scripts.youtube_langgraph.youtube_analysis_langgraph.load_discovery_channels",
            return_value={CH_DISC: {}},
        ):
            run_video(
                channel_id=CH_DISC,
                video_id=VID,
                db_path=self.db,
                generate_fn=gen,
                name_to_code=NAME_TO_CODE,
            )
            gen2 = _mock_gen_factory()
            r2 = run_video(
                channel_id=CH_DISC,
                video_id=VID,
                db_path=self.db,
                force=False,
                generate_fn=gen2,
                name_to_code=NAME_TO_CODE,
            )
        self.assertTrue(r2.get("skip"))
        self.assertEqual(r2.get("llm_calls"), 0)

    def test_g7_force_resummarize(self):
        _seed(self.con)
        with patch(
            "scripts.youtube_langgraph.youtube_analysis_langgraph.load_discovery_channels",
            return_value={CH_DISC: {}},
        ):
            run_video(
                channel_id=CH_DISC,
                video_id=VID,
                db_path=self.db,
                generate_fn=_mock_gen_factory(),
                name_to_code=NAME_TO_CODE,
            )
            r = run_video(
                channel_id=CH_DISC,
                video_id=VID,
                db_path=self.db,
                force=True,
                generate_fn=_mock_gen_factory(),
                name_to_code=NAME_TO_CODE,
            )
        self.assertFalse(r.get("skip"))
        self.assertTrue(r.get("persisted"))
        self.assertGreater(r.get("llm_calls") or 0, 0)

    def test_g8_non_discovery_no_stocks(self):
        _seed(self.con, channel_id=CH_ONLY)
        gen = _mock_gen_factory()
        with patch(
            "scripts.youtube_langgraph.youtube_analysis_langgraph.load_discovery_channels",
            return_value={CH_DISC: {}},
        ):
            r = run_video(
                channel_id=CH_ONLY,
                video_id=VID,
                db_path=self.db,
                generate_fn=gen,
                name_to_code=NAME_TO_CODE,
            )
        self.assertTrue(r.get("persisted"))
        # map+reduce only (no stock call)
        self.assertEqual(r.get("llm_calls"), 2)
        self.assertEqual(r.get("stock_mentions"), [])
        n = self.con.execute("SELECT COUNT(*) FROM youtube_stock_insights").fetchone()[0]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
