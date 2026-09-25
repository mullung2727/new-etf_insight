"""store.py 단위 테스트 — :memory: 만.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest research.jev_candidate.tests.test_store
"""
from __future__ import annotations

import unittest

from research.jev_candidate import store


def _run() -> dict:
    return {
        "run_id": "r1",
        "mode": "backtest",
        "track": "open",
        "date": "20260409",
        "as_of": "2026-04-10 08:00",
        "jev_model": "jev-1.13.0",
        "gpt_model": None,
        "question_set_ver": "v1",
        "prompt_ver": None,
        "created_at": "2026-01-01T00:00:00+00:00",
    }


class TestStore(unittest.TestCase):
    def test_왕복(self):
        con = store.connect(":memory:")
        store.ensure_schema(con)
        self.assertFalse(store.has_state(con, "r1", "005930"))
        store.save_run(con, **_run())
        store.save_posts(
            con,
            [
                {
                    "run_id": "r1", "date": "20260409", "ticker": "005930",
                    "channel": "ch", "post_id": 7, "posted_at_kst": "2026-04-09T10:00:00+09:00",
                    "section": "[신호일]", "text_hash": "h", "about_noul": 0.9, "passed": True,
                }
            ],
        )
        store.save_state(
            con,
            {
                "run_id": "r1", "date": "20260409", "ticker": "005930", "name": "삼성전자",
                "price_pct": 1.5, "state_text": "t", "state_hash": "s",
                "anon_map_json": "{}", "n_candidates": 1, "n_passed": 1,
                "n_ambiguous": 0, "n_included": 1, "truncated": False,
            },
        )
        rows = [
            {"qid": "q1", "type": "score", "value": 1.6, "confidence": 0.9, "probs": {"1": 0.5}},
            {"qid": "q5", "type": "noul", "value": 0.8, "confidence": None, "probs": {"yes": 0.8}},
        ]
        store.save_answers(con, "r1", "20260409", "005930", rows)
        self.assertTrue(store.has_state(con, "r1", "005930"))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM jev_answers").fetchone()[0], 2)
        got = con.execute(
            "SELECT value, confidence FROM jev_answers WHERE qid = 'q1'"
        ).fetchone()
        self.assertEqual(got, ("1.6", 0.9))

    def test_덮어쓰기(self):
        con = store.connect(":memory:")
        store.ensure_schema(con)
        store.save_run(con, **_run())
        store.save_run(con, **{**_run(), "track": "close"})
        self.assertEqual(
            con.execute("SELECT track FROM runs WHERE run_id = 'r1'").fetchone()[0], "close"
        )


class TestJevCache(unittest.TestCase):
    def test_필터_왕복(self):
        con = store.connect(":memory:")
        store.ensure_schema(con)
        self.assertIsNone(store.get_filter(con, "k1"))
        store.put_filter(con, "k1", 0.7)
        self.assertAlmostEqual(store.get_filter(con, "k1"), 0.7)
        store.put_filter(con, "k1", 0.2)
        self.assertAlmostEqual(store.get_filter(con, "k1"), 0.2)

    def test_답변_왕복(self):
        con = store.connect(":memory:")
        store.ensure_schema(con)
        self.assertIsNone(store.get_answers(con, "k2"))
        rows = [
            {"qid": "q1", "type": "score", "value": 1.6, "confidence": 0.9, "probs": {"1": 0.5}},
            {"qid": "q5", "type": "noul", "value": 0.8, "confidence": None, "probs": {"yes": 0.8}},
        ]
        store.put_answers(con, "k2", "jev-1.13.0", rows, {"input_tokens": 10, "output_tokens": 5})
        self.assertEqual(store.get_answers(con, "k2"), rows)


if __name__ == "__main__":
    unittest.main()
