from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from research.watchlist_expected_return.phase6_extreme_gap_causes import (
    build_blind_inputs,
    classify_blind_inputs,
    evaluate,
    load_telegram_context,
)


class ExtremeGapCauseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.telegram_db = Path(self.temp.name) / "telegram.sqlite3"
        with closing(sqlite3.connect(self.telegram_db)) as con, con:
            con.execute("""CREATE TABLE telegram_stock_insights(
                date_kst TEXT, session TEXT, ticker TEXT, mention_channels TEXT,
                discovery_reason TEXT, analysis TEXT
            )""")
            for session in ("morning", "close", "evening"):
                con.execute("INSERT INTO telegram_stock_insights VALUES (?,?,?,?,?,?)", (
                    "2026-07-13", session, "000001", '["ch"]', session,
                    json.dumps({"summary": session}),
                ))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_same_day_cutoff_uses_morning_telegram_only(self) -> None:
        rows = load_telegram_context(self.telegram_db, "000001", "20260713")
        self.assertEqual([row["session"] for row in rows], ["morning"])

    def test_blind_input_excludes_outcome_fields(self) -> None:
        case = {
            "case_id": "20260713_000001", "date": "20260713", "ticker": "000001",
            "name": "테스트", "next_date": "20260714", "d_close": 100,
            "d1_open": 120, "d1_open_return_pct": 20.0, "outcome_group": "up",
        }
        from unittest.mock import patch
        with patch(
            "research.watchlist_expected_return.phase6_extreme_gap_causes.fetch_historical_news",
            return_value=[],
        ):
            blind = build_blind_inputs([case], self.telegram_db)[0]
        self.assertFalse(any("d1" in key or "outcome" in key for key in blind))
        self.assertEqual(blind["as_of"], "2026-07-13T15:00:00+09:00")

    def test_classification_happens_before_outcome_evaluation(self) -> None:
        inputs = [{"case_id": "x", "as_of": "2026-07-13T15:00:00+09:00"}]
        classified = classify_blind_inputs(inputs, lambda _prompt: json.dumps({"cases": [{
            "case_id": "x", "identifiable": "clear", "cause_type": "legal_regulatory",
            "direction_bias": "negative", "priced_in_risk": "low", "downside_risk": "high",
            "evidence_summary": "악재", "reasoning": "하락 위험",
        }]}, ensure_ascii=False))
        result = evaluate([{
            "case_id": "x", "outcome_group": "down", "date": "20260713",
            "ticker": "000001", "name": "테스트", "next_date": "20260714",
            "d_close": 100, "d1_open": 90, "d1_open_return_pct": -10,
        }], classified)
        self.assertTrue(result["rows"][0]["pre_cutoff_signal_matches"])
        self.assertEqual(result["down"]["negative_bias_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
