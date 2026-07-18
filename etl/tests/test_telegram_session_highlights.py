import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.telegram_session_highlights import (
    ensure_schema,
    fetch_session_highlights,
    replace_session_highlights,
)


class TelegramSessionHighlightsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.con = sqlite3.connect(self.tmp / "telegram.sqlite3")
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_replace_and_fetch_session_highlights(self):
        rows = [
            {
                "title": "AI 인프라 수요 논쟁",
                "summary": "저비용 AI 모델과 GPU 수요 전망이 함께 부각됐다.",
                "category": "산업",
                "importance_reason": "반도체 투자심리와 직접 연결된다.",
                "score_total": 82,
                "score_breakdown": {
                    "market_impact": 22,
                    "evidence_quality": 19,
                    "novelty": 15,
                    "investment_relevance": 18,
                    "cross_channel": 8,
                },
                "source_channels": ["getfeed", "infomarketopen"],
                "source_post_refs": ["getfeed/1", "infomarketopen/2"],
            }
        ]

        replace_session_highlights(self.con, "2026-07-18", "close", rows)
        fetched = fetch_session_highlights(self.con, "2026-07-18", "close")

        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetched[0]["rank"], 1)
        self.assertEqual(fetched[0]["score_total"], 82)
        self.assertEqual(fetched[0]["score_breakdown"]["market_impact"], 22)
        self.assertEqual(fetched[0]["source_channels"], ["getfeed", "infomarketopen"])

    def test_replace_is_idempotent_and_removes_stale_rows(self):
        first = [
            {"title": "A", "summary": "a", "category": "시장", "importance_reason": "a",
             "score_total": 70, "score_breakdown": {}, "source_channels": [],
             "source_post_refs": []},
            {"title": "B", "summary": "b", "category": "산업", "importance_reason": "b",
             "score_total": 60, "score_breakdown": {}, "source_channels": [],
             "source_post_refs": []},
        ]
        second = [
            {"title": "C", "summary": "c", "category": "정책", "importance_reason": "c",
             "score_total": 90, "score_breakdown": {}, "source_channels": [],
             "source_post_refs": []},
        ]

        replace_session_highlights(self.con, "2026-07-18", "close", first)
        replace_session_highlights(self.con, "2026-07-18", "close", second)

        fetched = fetch_session_highlights(self.con, "2026-07-18", "close")
        self.assertEqual([r["title"] for r in fetched], ["C"])
        self.assertEqual(fetched[0]["rank"], 1)


if __name__ == "__main__":
    unittest.main()
