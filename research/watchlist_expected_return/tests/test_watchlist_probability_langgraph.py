from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import duckdb

from research.watchlist_expected_return.watchlist_probability_langgraph import (
    _ranking_auc,
    build_market_snapshot,
    build_score_input,
    calculate_probability_score,
    calculate_negative_trend_penalty,
    compare_scores,
    evaluate_available_outcomes,
    ensure_complete_scores,
    load_telegram,
    run_date,
    to_llm_score_row,
    upsert_llm_scores,
    write_operational_report,
)


class WatchlistProbabilityLangGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.watchlist_db = root / "watchlist.sqlite3"
        self.telegram_db = root / "telegram.sqlite3"
        self.krx_db = root / "krx.duckdb"
        with closing(sqlite3.connect(self.watchlist_db)) as con, con:
            con.execute("CREATE TABLE watchlist(date TEXT, stock_code TEXT)")
            con.execute("""CREATE TABLE llm_scores(
                date TEXT, ticker TEXT, name TEXT, ratio REAL,
                today_volume INTEGER, avg5_volume INTEGER, trading_value INTEGER,
                close INTEGER, score INTEGER, category TEXT, reason_summary TEXT,
                final_opinion TEXT, evidence_board TEXT, evidence_news TEXT,
                evidence_web TEXT, sources TEXT, PRIMARY KEY(date,ticker)
            )""")
            con.execute("CREATE TABLE intraday_ranking(date TEXT, ticker TEXT, name TEXT, rank INTEGER)")
            con.execute("""CREATE TABLE watchlist_market_snapshots(
                date TEXT,ticker TEXT,snapshot_at TEXT,current_price INTEGER,
                open_price INTEGER,high_price INTEGER,volume INTEGER,
                change_rate REAL,source TEXT,PRIMARY KEY(date,ticker)
            )""")
            con.execute("INSERT INTO watchlist VALUES ('20260713','000001')")
            con.execute("""INSERT INTO llm_scores VALUES (
                '20260713','000001','테스트',10,100,10,1000,100,58,'복합',
                'reason','opinion','board','news','web','[]'
            )""")
            con.execute("INSERT INTO intraday_ranking VALUES ('20260713','000001','테스트',3)")
        with closing(sqlite3.connect(self.telegram_db)) as con, con:
            con.execute("""CREATE TABLE telegram_stock_insights(
                date_kst TEXT, session TEXT, ticker TEXT, mention_channels TEXT,
                source_post_refs TEXT, discovery_reason TEXT, analysis TEXT,
                created_at TEXT, updated_at TEXT
            )""")
            for session in ("morning", "close", "evening"):
                con.execute("INSERT INTO telegram_stock_insights VALUES (?,?,?,?,?,?,?,?,?)", (
                    "2026-07-13", session, "000001", '["ch"]', '["ch/1"]', session,
                    json.dumps({"change_type": "new", "themes": ["AI"]}), "x", "x",
                ))
        with duckdb.connect(str(self.krx_db)) as con:
            con.execute("""CREATE TABLE ohlcv(
                date VARCHAR,ticker VARCHAR,market_cap BIGINT,close INTEGER,open INTEGER,
                volume BIGINT
            )""")
            con.execute("INSERT INTO ohlcv VALUES ('20260710','000001',1000000,100,95,1000)")
            con.execute("INSERT INTO ohlcv VALUES ('20260713','000001',1100000,110,105,2000)")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_same_day_telegram_uses_morning_only(self) -> None:
        state = {
            "date": "20260713", "telegram_db": str(self.telegram_db),
            "candidates": [{"ticker": "000001"}],
        }
        result = load_telegram(state)
        self.assertEqual([row["session"] for row in result["telegram_by_ticker"]["000001"]], ["morning"])

    def test_graph_scores_without_changing_existing_db(self) -> None:
        def fake_score(_prompt: str) -> str:
            return json.dumps({
                "ticker": "000001", "name": "테스트", "target": "D+1_OPEN_ABOVE_D_CLOSE",
                "probability_score": 99, "confidence": "medium", "up_factors": ["신규 재료"],
                "score_components": {
                    "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                    "negative_event_risk": 0, "negative_trend_penalty": 0,
                    "priced_in_level": "low", "priced_in_penalty": 0,
                    "exhaustion_level": "low", "exhaustion_penalty": 0,
                },
                "down_factors": [], "news_summary": "뉴스", "telegram_summary": "텔레그램",
                "reasoning": "근거", "evidence_quality": "moderate",
            }, ensure_ascii=False)
        with patch(
            "research.watchlist_expected_return.watchlist_probability_langgraph.fetch_historical_news",
            return_value=[],
        ):
            result = run_date("20260713", self.watchlist_db, self.telegram_db, self.krx_db, fake_score)
        self.assertEqual(result["scores"][0]["probability_score"], 72)
        self.assertEqual(result["scores"][0]["llm_reported_probability_score"], 99)
        with closing(sqlite3.connect(self.watchlist_db)) as con:
            self.assertEqual(con.execute("SELECT score FROM llm_scores").fetchone()[0], 58)

    def test_probability_score_is_forced_from_components(self) -> None:
        self.assertEqual(calculate_probability_score({
            "catalyst_strength": 30, "freshness": 10, "confirmation": 5,
            "negative_event_risk": 0, "negative_trend_penalty": 0,
            "priced_in_penalty": 0, "exhaustion_penalty": 0,
        }), 95)
        self.assertEqual(calculate_probability_score({
            "catalyst_strength": 5, "freshness": 0, "confirmation": 0,
            "negative_event_risk": 20, "negative_trend_penalty": 0,
            "priced_in_penalty": 20, "exhaustion_penalty": 15,
        }), 5)

    def test_operational_date_does_not_require_existing_llm_score(self) -> None:
        with closing(sqlite3.connect(self.watchlist_db)) as con, con:
            con.execute("INSERT INTO watchlist VALUES ('20260714','000001')")
            con.execute("INSERT INTO intraday_ranking VALUES ('20260714','000001','테스트',1)")
            con.execute("""INSERT INTO watchlist_market_snapshots VALUES (
                '20260714','000001','2026-07-14T15:00:05+09:00',120,110,125,3000,9.09,'ka10001'
            )""")

        def fake_score(_prompt: str) -> str:
            return json.dumps({
                "ticker": "000001", "name": "테스트", "target": "D+1_OPEN_ABOVE_D_CLOSE",
                "probability_score": 72, "confidence": "medium", "up_factors": ["신규 재료"],
                "score_components": {
                    "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                    "negative_event_risk": 0, "negative_trend_penalty": 0,
                    "priced_in_level": "low", "priced_in_penalty": 0,
                    "exhaustion_level": "low", "exhaustion_penalty": 0,
                },
                "down_factors": [], "news_summary": "뉴스", "telegram_summary": "텔레그램",
                "reasoning": "근거", "evidence_quality": "moderate",
            }, ensure_ascii=False)

        with patch(
            "research.watchlist_expected_return.watchlist_probability_langgraph.fetch_historical_news",
            return_value=[],
        ):
            result = run_date("20260714", self.watchlist_db, self.telegram_db, self.krx_db, fake_score)
        score = result["scores"][0]
        self.assertIsNone(score["old_score"])
        self.assertEqual(score["close"], 120)
        self.assertEqual(score["today_volume"], 3000)
        self.assertEqual(score["avg5_volume"], 1500)
        self.assertEqual(score["ratio"], 2.0)

    def test_historical_input_excludes_after_cutoff_snapshot_and_untimed_evidence(self) -> None:
        state = {
            "date": "20260713",
            "news_by_ticker": {"000001": []},
            "telegram_by_ticker": {"000001": []},
        }
        result = build_score_input(state, {
            "ticker": "000001", "name": "테스트", "ratio": 30, "close": 120,
            "today_volume": 1000, "evidence_board": "시각 없음", "evidence_news": "시각 없음",
            "market_cap_previous_day": 1000000,
        })
        self.assertEqual(result["as_of"], "2026-07-13T15:00:00+09:00")
        self.assertFalse(result["market_snapshot"]["available"])
        self.assertNotIn("close", result["market_snapshot"])
        self.assertTrue(result["excluded_untimed_legacy_evidence"])

    def test_valid_1500_snapshot_builds_exhaustion_features(self) -> None:
        result = build_market_snapshot({
            "snapshot_at": "2026-07-13T15:00:05+09:00",
            "snapshot_current_price": 102,
            "snapshot_open_price": 100,
            "snapshot_high_price": 110,
            "snapshot_volume": 2000,
            "snapshot_change_rate": 2.0,
            "snapshot_source": "ka10001",
            "avg5_volume": 1000,
            "market_cap_previous_day": 1000000,
            "previous_5d_close": 80,
        }, "20260713")
        self.assertTrue(result["available"])
        self.assertEqual(result["rise_from_open_pct"], 2.0)
        self.assertEqual(result["pullback_from_high_pct"], -7.2727)
        self.assertEqual(result["volume_ratio_vs_avg5"], 2.0)
        self.assertEqual(result["return_5d_pct"], 27.5)

    def test_negative_trend_penalty_is_asymmetric_and_nonlinear(self) -> None:
        self.assertEqual(calculate_negative_trend_penalty(30), 0)
        self.assertEqual(calculate_negative_trend_penalty(-2.9), 0)
        self.assertEqual(calculate_negative_trend_penalty(-5), 3)
        self.assertEqual(calculate_negative_trend_penalty(-10), 6)
        self.assertEqual(calculate_negative_trend_penalty(-15), 10)
        self.assertEqual(calculate_negative_trend_penalty(-25), 15)

    def test_comparison_detects_score_and_rank_change(self) -> None:
        result = compare_scores([{"date": "20260713", "scores": [
            {"ticker": "1", "name": "A", "old_score": 80, "probability_score": 40, "telegram_rows": 1},
            {"ticker": "2", "name": "B", "old_score": 50, "probability_score": 70, "telegram_rows": 0},
        ]}])
        self.assertEqual(result["meaningfully_changed_count"], 2)
        self.assertGreater(result["mean_absolute_delta"], 0)

    def test_evaluates_only_available_next_day_outcome(self) -> None:
        results = [{"date": "20260710", "scores": [{
            "date": "20260710", "ticker": "000001", "name": "테스트",
            "old_score": 58, "probability_score": 70,
        }]}]
        result = evaluate_available_outcomes(results, self.krx_db)
        self.assertEqual(result["evaluated_count"], 1)
        self.assertEqual(result["pending_count"], 0)
        self.assertTrue(result["rows"][0]["actual_up"])

    def test_ranking_auc_ignores_missing_old_score(self) -> None:
        rows = [
            {"actual_up": True, "old_score": None},
            {"actual_up": True, "old_score": 80},
            {"actual_up": False, "old_score": 50},
        ]
        self.assertEqual(_ranking_auc(rows, "old_score"), 1.0)

    def test_probability_score_overwrites_existing_llm_score(self) -> None:
        score = {
            "date": "20260713", "ticker": "000001", "name": "테스트",
            "probability_score": 72, "ratio": 10, "today_volume": 100,
            "avg5_volume": 10, "trading_value": 1000, "close": 110,
            "score_components": {
                "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low",
                "priced_in_penalty": 0, "exhaustion_level": "low",
                "exhaustion_penalty": 0,
            },
            "confidence": "medium", "evidence_quality": "moderate",
            "up_factors": ["신규 재료"], "down_factors": ["선반영 가능성"],
            "reasoning": "재료강도와 선반영을 함께 평가했다.",
            "news_summary": "확인된 뉴스", "telegram_summary": "채널 언급",
            "sources": ["https://example.com/news"],
        }
        row = to_llm_score_row(score)
        self.assertEqual(upsert_llm_scores(self.watchlist_db, [row]), 1)
        with closing(sqlite3.connect(self.watchlist_db)) as con:
            saved = con.execute("""
                SELECT score,category,reason_summary,evidence_news,evidence_web
                FROM llm_scores WHERE date='20260713' AND ticker='000001'
            """).fetchone()
        self.assertEqual(saved[0], 72)
        self.assertEqual(saved[1], "D+1 시가 상승가능성")
        self.assertIn("재료강도", saved[2])
        self.assertIn("확인된 뉴스", saved[3])
        self.assertIn("채널 언급", saved[4])

    def test_operational_report_keeps_existing_formatter_shape(self) -> None:
        row = {
            "date": "20260713", "ticker": "000001", "name": "테스트",
            "score": 72, "category": "D+1 시가 상승가능성",
            "reason_summary": "근거", "final_opinion": "판단",
            "evidence_board": "제외", "evidence_news": "뉴스",
            "evidence_web": "텔레그램", "sources": [],
        }
        path = write_operational_report(Path(self.temp.name), "20260713", [row], self.watchlist_db)
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(doc["items"][0]["score"], 72)
        self.assertEqual(doc["source_data"]["definition"], "score is probability of D+1 open above D close")

    def test_operational_write_rejects_partial_scores(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "1/2"):
            ensure_complete_scores([{
                "date": "20260714", "candidate_count": 2, "scored_count": 1,
            }])


if __name__ == "__main__":
    unittest.main()
