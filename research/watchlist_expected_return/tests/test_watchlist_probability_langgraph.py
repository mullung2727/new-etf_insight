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
    apply_priced_in_policy,
    build_market_snapshot,
    build_score_input,
    build_scoring_schema,
    escalate_theme_scores,
    load_theme_dictionary,
    make_escalation_prompt,
    route_after_scoring,
    theme_escalation_reason,
    validate_theme_catalyst_consistency,
    normalize_theme_scores,
    render_theme_dictionary,
    theme_axis_names,
    theme_terminal_names,
    validate_theme_scores,
    calculate_probability_score,
    calculate_negative_trend_penalty,
    compare_scores,
    collect_news,
    evaluate_available_outcomes,
    ensure_complete_scores,
    load_telegram,
    make_prompt,
    persist_scoring_results,
    prompt_version_for_date,
    run_date,
    score_candidates,
    to_llm_score_row,
    upsert_catalyst_assessments,
    upsert_llm_scores,
    validate_catalyst_assessment,
    write_operational_report,
)


def catalyst_fields() -> dict:
    return {
        "primary_catalyst": {
            "label": "신규 공급계약",
            "description": "고객사와 공급계약 협의가 진행 중이다.",
            "category_raw": "해외 고객사 공급 협의",
            "status": "alive",
            "expected_duration": "two_to_five_trading_days",
            "alive_score": 4,
            "reason": "후속 계약 발표가 남아 있다.",
            "invalidation": "협상 중단 또는 부인",
            "evidence_refs": [],
        },
        "secondary_catalysts": [],
        "theme_scores": {
            "sector": [{"name": "메모리·반도체", "score": 100}],
            "event": [{"name": "수주·공급계약", "score": 100}],
        },
        "theme_event_direction": "positive",
        "new_theme_candidate": None,
    }


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
                    json.dumps({"change_type": "new", "themes": ["AI"]}),
                    "2026-07-13T01:00:00+00:00", "2026-07-13T01:00:00+00:00",
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

    def test_historical_date_does_not_search_live_news(self) -> None:
        state = {
            "date": "20260713",
            "candidates": [{"ticker": "000001", "name": "테스트"}],
            "warnings": [],
        }
        with patch(
            "research.watchlist_expected_return.watchlist_probability_langgraph.fetch_historical_news"
        ) as fetch:
            result = collect_news(state)
        fetch.assert_not_called()
        self.assertEqual(result["news_by_ticker"]["000001"], [])
        self.assertIn("historical_live_news_excluded:000001", result["warnings"])

    def test_telegram_rows_created_or_updated_after_as_of_are_excluded(self) -> None:
        with closing(sqlite3.connect(self.telegram_db)) as con, con:
            con.execute("INSERT INTO telegram_stock_insights VALUES (?,?,?,?,?,?,?,?,?)", (
                "2026-07-12", "morning", "000001", '["late"]', '["late/1"]',
                "late backfill", json.dumps({"change_type": "new"}),
                "2026-07-14T00:00:00+00:00", "2026-07-14T00:00:00+00:00",
            ))
        state = {
            "date": "20260713", "telegram_db": str(self.telegram_db),
            "candidates": [{"ticker": "000001"}],
        }
        result = load_telegram(state)
        self.assertEqual(
            [row["post_refs"] for row in result["telegram_by_ticker"]["000001"]],
            [["ch/1"]],
        )

    def test_graph_scores_without_changing_existing_db(self) -> None:
        calls = []

        def fake_score(prompt: str) -> str:
            calls.append(prompt)
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
                **catalyst_fields(),
            }, ensure_ascii=False)
        with patch(
            "research.watchlist_expected_return.watchlist_probability_langgraph.fetch_historical_news",
            return_value=[],
        ):
            result = run_date("20260713", self.watchlist_db, self.telegram_db, self.krx_db, fake_score)
        self.assertEqual(result["scores"][0]["probability_score"], 72)
        self.assertEqual(result["scores"][0]["llm_reported_probability_score"], 99)
        self.assertEqual(result["scores"][0]["primary_catalyst"]["alive_score"], 4)
        self.assertEqual(len(calls), 1)
        with closing(sqlite3.connect(self.watchlist_db)) as con:
            self.assertEqual(con.execute("SELECT score FROM llm_scores").fetchone()[0], 58)

    def test_graph_rejects_score_without_catalyst_assessment(self) -> None:
        def fake_score(_prompt: str) -> str:
            return json.dumps({
                "ticker": "000001", "name": "테스트", "target": "D+1_OPEN_ABOVE_D_CLOSE",
                "probability_score": 72, "confidence": "medium", "up_factors": [],
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
        self.assertEqual(result["scored_count"], 0)
        self.assertIn("score_failed:000001:ValueError", result["warnings"])

    def test_graph_rejects_catalyst_evidence_not_present_in_input(self) -> None:
        payload = {
            "ticker": "000001", "name": "테스트", "target": "D+1_OPEN_ABOVE_D_CLOSE",
            "probability_score": 72, "confidence": "medium", "up_factors": [],
            "score_components": {
                "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low", "priced_in_penalty": 0,
                "exhaustion_level": "low", "exhaustion_penalty": 0,
            },
            "down_factors": [], "news_summary": "뉴스", "telegram_summary": "텔레그램",
            "reasoning": "근거", "evidence_quality": "moderate", **catalyst_fields(),
        }
        payload["primary_catalyst"]["evidence_refs"] = ["https://hallucinated.example"]

        with patch(
            "research.watchlist_expected_return.watchlist_probability_langgraph.fetch_historical_news",
            return_value=[],
        ):
            result = run_date(
                "20260713", self.watchlist_db, self.telegram_db, self.krx_db,
                lambda _prompt: json.dumps(payload, ensure_ascii=False),
            )
        self.assertEqual(result["scored_count"], 0)
        self.assertIn("score_failed:000001:ValueError", result["warnings"])

    def test_catalyst_assessment_accepts_free_categories_and_rejects_fake_precision(self) -> None:
        valid = {
            "primary_catalyst": {
                "label": "해외 공급계약 기대",
                "description": "고객사와 공급 협의가 진행 중이다.",
                "category_raw": "글로벌 고객사 초도 공급 협의",
                "status": "alive",
                "expected_duration": "two_to_five_trading_days",
                "alive_score": 4,
                "reason": "후속 계약 발표가 남아 있다.",
                "invalidation": "협상 중단 또는 부인",
                "evidence_refs": ["https://example.com/news"],
            },
            "secondary_catalysts": [{
                "label": "정책 지원",
                "description": "산업 지원 정책이 발표됐다.",
                "category_raw": "산업 정책 지원",
                "status": "uncertain",
                "expected_duration": "one_week_or_more",
                "alive_score": 3,
                "reason": "세부 집행 일정은 미정이다.",
                "invalidation": "지원 대상 제외",
                "evidence_refs": [],
            }, {
                "label": "임상 일정",
                "description": "후속 임상 결과 발표 일정이 남아 있다.",
                "category_raw": "후속 임상 결과 일정",
                "status": "alive",
                "expected_duration": "one_week_or_more",
                "alive_score": 4,
                "reason": "공식 결과 발표가 아직 남아 있다.",
                "invalidation": "임상 중단 또는 일정 취소",
                "evidence_refs": [],
            }],
        }
        validate_catalyst_assessment(valid)

        invalid = json.loads(json.dumps(valid))
        invalid["primary_catalyst"]["alive_score"] = 6
        invalid["primary_catalyst"]["score_components"] = {"freshness": 1}
        with self.assertRaises(ValueError):
            validate_catalyst_assessment(invalid)

    def test_theme_scores_are_renormalized_to_100_per_axis(self) -> None:
        result = {"theme_scores": {
            "sector": [{"name": "메모리·반도체", "score": 40}, {"name": "IT부품·전자", "score": 20}],
            "event": [{"name": "실적·가이던스", "score": 7}],
        }}
        normalize_theme_scores(result)
        self.assertEqual(
            [item["score"] for item in result["theme_scores"]["sector"]], [67, 33]
        )
        self.assertEqual(result["theme_scores"]["event"][0]["score"], 100)

    def test_theme_scores_reject_empty_or_zero_axis(self) -> None:
        for items in ([], [{"name": "메모리·반도체", "score": 0}]):
            with self.assertRaises(ValueError):
                normalize_theme_scores({"theme_scores": {
                    "sector": items, "event": [{"name": "실적·가이던스", "score": 100}],
                }})

    def test_theme_scores_reject_name_outside_dictionary(self) -> None:
        theme_dict = load_theme_dictionary()
        result = {
            "theme_scores": {
                "sector": [{"name": "양자컴퓨팅", "score": 100}],
                "event": [{"name": "실적·가이던스", "score": 100}],
            },
            "theme_event_direction": "positive",
            "new_theme_candidate": None,
        }
        with self.assertRaises(ValueError):
            validate_theme_scores(result, theme_dict)

    def test_new_theme_candidate_is_required_only_when_not_in_dict_scored(self) -> None:
        theme_dict = load_theme_dictionary()
        base = {
            "theme_scores": {
                "sector": [{"name": "사전에없음", "score": 100}],
                "event": [{"name": "실적·가이던스", "score": 100}],
            },
            "theme_event_direction": "positive",
            "new_theme_candidate": None,
        }
        with self.assertRaises(ValueError):
            validate_theme_scores(base, theme_dict)

        base["new_theme_candidate"] = "양자컴퓨팅"
        validate_theme_scores(base, theme_dict)

        base["theme_scores"]["sector"] = [{"name": "메모리·반도체", "score": 100}]
        with self.assertRaises(ValueError):
            validate_theme_scores(base, theme_dict)

    def test_event_direction_must_be_neutral_when_no_catalyst(self) -> None:
        theme_dict = load_theme_dictionary()
        result = {
            "theme_scores": {
                "sector": [{"name": "재료없음", "score": 100}],
                "event": [{"name": "재료없음", "score": 100}],
            },
            "theme_event_direction": "positive",
            "new_theme_candidate": None,
        }
        with self.assertRaises(ValueError):
            validate_theme_scores(result, theme_dict)

        result["theme_event_direction"] = "neutral"
        validate_theme_scores(result, theme_dict)

    def test_scoring_schema_injects_dictionary_names_as_enum(self) -> None:
        theme_dict = load_theme_dictionary()
        schema = build_scoring_schema(theme_dict)
        names = theme_axis_names(theme_dict)
        for axis in ("sector", "event"):
            enum = schema["properties"]["theme_scores"]["properties"][axis]["items"][
                "properties"]["name"]["enum"]
            self.assertEqual(enum, names[axis])
            self.assertIn("재료없음", enum)
            self.assertIn("사전에없음", enum)
        self.assertNotIn(
            "enum",
            json.loads(Path(
                "research/watchlist_expected_return/watchlist_scoring_schema.json"
            ).read_text(encoding="utf-8"))["$defs"]["theme_score"]["properties"]["name"],
        )

    def test_prompt_contains_theme_dictionary_and_special_values(self) -> None:
        rendered = render_theme_dictionary(load_theme_dictionary())
        self.assertIn("재료없음", rendered)
        self.assertIn("사전에없음", rendered)
        self.assertIn("메모리·반도체", rendered)
        self.assertNotIn("기관 순매수", rendered.split("쓰지 말 것")[0])

    def test_escalation_uses_score_threshold_not_rank(self) -> None:
        clear = {"theme_scores": {
            "sector": [{"name": "메모리·반도체", "score": 60},
                       {"name": "사전에없음", "score": 40}],
            "event": [{"name": "실적·가이던스", "score": 100}],
        }}
        self.assertEqual(theme_escalation_reason(clear), "sector: 사전에없음 40점")

        ambiguous = {"theme_scores": {
            "sector": [{"name": "메모리·반도체", "score": 34},
                       {"name": "IT부품·전자", "score": 33},
                       {"name": "AI·데이터센터·클라우드", "score": 33}],
            "event": [{"name": "실적·가이던스", "score": 100}],
        }}
        self.assertEqual(theme_escalation_reason(ambiguous), "sector: 1위-2위 격차 1점")

        decided = {"theme_scores": {
            "sector": [{"name": "메모리·반도체", "score": 80},
                       {"name": "IT부품·전자", "score": 20}],
            "event": [{"name": "실적·가이던스", "score": 100}],
        }}
        self.assertIsNone(theme_escalation_reason(decided))

    def test_industry_agnostic_is_sector_only_and_terminal(self) -> None:
        theme_dict = load_theme_dictionary()
        names = theme_axis_names(theme_dict)
        self.assertIn("산업무관", names["sector"])
        self.assertNotIn("산업무관", names["event"])
        self.assertEqual(theme_terminal_names(theme_dict), {"재료없음", "산업무관"})

        # 실측 사고: 에스엠벡셀의 '최대주주 지분 매입'이 sector 사전에없음으로 찍혔다
        result = {
            "theme_scores": {
                "sector": [{"name": "산업무관", "score": 100}],
                "event": [{"name": "M&A·지배구조·주주환원", "score": 100}],
            },
            "theme_event_direction": "positive",
            "new_theme_candidate": None,
        }
        validate_theme_scores(result, theme_dict)
        validate_theme_catalyst_consistency(
            result["theme_scores"], {"category_raw": "최대주주 지분 매입"}
        )
        self.assertIsNone(theme_escalation_reason(result, theme_dict))

    def test_no_catalyst_top_never_escalates(self) -> None:
        result = {"theme_scores": {
            "sector": [{"name": "재료없음", "score": 55}, {"name": "사전에없음", "score": 45}],
            "event": [{"name": "재료없음", "score": 100}],
        }}
        self.assertIsNone(theme_escalation_reason(result))
        self.assertEqual(route_after_scoring({"scores": [
            {"theme_escalation_reason": None},
        ]}), "end")
        self.assertEqual(route_after_scoring({"scores": [
            {"theme_escalation_reason": None}, {"theme_escalation_reason": "sector: ..."},
        ]}), "escalate")

    def _escalation_state(self) -> dict:
        first = {
            "ticker": "000001", "theme_escalation_reason": "sector: 사전에없음 40점",
            "theme_scores": {
                "sector": [{"name": "메모리·반도체", "score": 60},
                           {"name": "사전에없음", "score": 40}],
                "event": [{"name": "실적·가이던스", "score": 100}],
            },
            "theme_event_direction": "positive",
            "new_theme_candidate": "양자컴퓨팅",
            "primary_catalyst": {
                "label": "양자 수주", "description": "양자 연산 장비 수주가 확인됐다.",
                "category_raw": "양자 연산 수주", "status": "alive",
            },
        }
        return {
            "date": "20260827", "candidates": [{"ticker": "000001", "name": "테스트"}],
            "news_by_ticker": {}, "telegram_by_ticker": {}, "scores": [first], "warnings": [],
        }

    def test_escalation_replaces_theme_fields_and_marks_model(self) -> None:
        state = self._escalation_state()
        seen = {}

        def escalate_fn(prompt: str) -> str:
            seen["prompt"] = prompt
            return json.dumps({"ticker": "000001", "theme_scores": {
                "sector": [{"name": "메모리·반도체", "score": 90},
                           {"name": "IT부품·전자", "score": 10}],
                "event": [{"name": "수주·공급계약", "score": 100}],
            }, "theme_event_direction": "positive", "new_theme_candidate": None})

        result = escalate_theme_scores(state, escalate_fn, model="gpt-5.6-luna")
        score = result["scores"][0]
        self.assertTrue(score["theme_escalated"])
        self.assertEqual(score["theme_escalation_model"], "gpt-5.6-luna")
        self.assertEqual(score["theme_scores"]["event"][0]["name"], "수주·공급계약")
        self.assertIsNone(score["new_theme_candidate"])
        self.assertEqual(result["warnings"], [])

    def test_escalation_failure_keeps_first_result(self) -> None:
        state = self._escalation_state()

        def escalate_fn(prompt: str) -> str:
            return json.dumps({"ticker": "000001", "theme_scores": {
                "sector": [{"name": "없는테마", "score": 100}],
                "event": [{"name": "실적·가이던스", "score": 100}],
            }, "theme_event_direction": "positive", "new_theme_candidate": None})

        result = escalate_theme_scores(state, escalate_fn)
        score = result["scores"][0]
        self.assertNotIn("theme_escalated", score)
        self.assertEqual(score["new_theme_candidate"], "양자컴퓨팅")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertTrue(result["warnings"][0].startswith("theme_escalation_failed:000001:"))

    def test_escalation_prompt_fixes_catalyst_and_states_reason(self) -> None:
        state = self._escalation_state()
        prompt = make_escalation_prompt(
            state, state["candidates"][0], state["scores"][0],
            state["scores"][0]["theme_escalation_reason"], load_theme_dictionary(),
        )
        self.assertIn("## 재판단 배경", prompt)
        self.assertIn("사전에없음 40점", prompt)
        self.assertIn("확정 사실 - 다시 판단하지 마라", prompt)
        self.assertIn("양자 연산 수주", prompt)
        self.assertIn("`재료없음`을 1위로 두면 안 된다", prompt)

    def test_escalation_prompt_requires_no_material_theme_when_catalyst_is_none(self) -> None:
        state = self._escalation_state()
        state["scores"][0]["primary_catalyst"] = {
            "label": "확인된 재료 없음", "description": "재료를 확인할 수 없다",
            "category_raw": "재료 없음", "status": "uncertain",
        }
        prompt = make_escalation_prompt(
            state, state["candidates"][0], state["scores"][0], "sector: ...", load_theme_dictionary()
        )
        self.assertIn("두 축 모두 `재료없음`이 1위여야 한다", prompt)

    def test_theme_and_catalyst_must_not_contradict(self) -> None:
        no_material = {
            "sector": [{"name": "재료없음", "score": 100}],
            "event": [{"name": "재료없음", "score": 100}],
        }
        has_material = {
            "sector": [{"name": "메모리·반도체", "score": 100}],
            "event": [{"name": "수주·공급계약", "score": 100}],
        }
        none_catalyst = {"category_raw": "재료 없음"}
        real_catalyst = {"category_raw": "소방사업 인수 성장 기대"}

        validate_theme_catalyst_consistency(no_material, none_catalyst)
        validate_theme_catalyst_consistency(has_material, real_catalyst)
        with self.assertRaises(ValueError):
            validate_theme_catalyst_consistency(no_material, real_catalyst)
        with self.assertRaises(ValueError):
            validate_theme_catalyst_consistency(has_material, none_catalyst)

    def test_escalation_rejects_flipping_catalyst_to_no_material(self) -> None:
        """실측 사고: 478560이 같은 입력으로 재판단되며 재료 있음을 재료없음으로 뒤집었다."""
        state = self._escalation_state()

        def escalate_fn(prompt: str) -> str:
            return json.dumps({"ticker": "000001", "theme_scores": {
                "sector": [{"name": "재료없음", "score": 100}],
                "event": [{"name": "재료없음", "score": 100}],
            }, "theme_event_direction": "neutral", "new_theme_candidate": None})

        result = escalate_theme_scores(state, escalate_fn)
        score = result["scores"][0]
        self.assertNotIn("theme_escalated", score)
        self.assertEqual(score["theme_scores"]["sector"][0]["name"], "메모리·반도체")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertTrue(result["warnings"][0].startswith("theme_escalation_failed:000001:"))

    def test_prompt_version_splits_v4_and_theme_v5(self) -> None:
        self.assertEqual(prompt_version_for_date("20260804"), "catalyst-survival-v3")
        self.assertEqual(prompt_version_for_date("20260805"), "catalyst-survival-v4")
        self.assertEqual(prompt_version_for_date("20260827"), "catalyst-survival-v4")
        self.assertEqual(prompt_version_for_date("20260828"), "catalyst-theme-v5")

    def test_theme_columns_are_added_to_existing_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "watchlist.sqlite3"
            with closing(sqlite3.connect(db_path)) as con:
                con.execute("""
                    CREATE TABLE llm_catalyst_assessments (
                        date TEXT NOT NULL, ticker TEXT NOT NULL, as_of TEXT NOT NULL,
                        primary_category_raw TEXT NOT NULL, primary_status TEXT NOT NULL,
                        primary_duration TEXT NOT NULL, primary_alive_score INTEGER NOT NULL,
                        max_alive_score INTEGER NOT NULL, assessment_json TEXT NOT NULL,
                        prompt_version TEXT NOT NULL, model TEXT, generated_at TEXT NOT NULL,
                        PRIMARY KEY (date, ticker)
                    )
                """)
                con.commit()

            score = {
                "date": "20260828", "ticker": "000001", "as_of": "2026-08-28T15:00:00+09:00",
                **catalyst_fields(),
                "theme_dict_version": "2026-08-28",
                "theme_escalation_reason": "sector: 사전에없음 40점",
                "theme_escalated": True, "theme_escalation_model": "gpt-5.6-luna",
            }
            upsert_catalyst_assessments(db_path, [score], model="codex")

            with closing(sqlite3.connect(db_path)) as con:
                columns = {row[1] for row in con.execute(
                    "PRAGMA table_info(llm_catalyst_assessments)")}
                self.assertTrue({"theme_scores_json", "theme_dict_version",
                                 "theme_escalated", "theme_escalation_model"} <= columns)
                row = con.execute(
                    "SELECT theme_scores_json, theme_event_direction, new_theme_candidate,"
                    " theme_dict_version, theme_escalated, theme_escalation_model"
                    " FROM llm_catalyst_assessments"
                ).fetchone()
                self.assertEqual(
                    json.loads(row[0])["event"][0]["name"], "수주·공급계약")
                self.assertEqual(row[1], "positive")
                self.assertIsNone(row[2])
                self.assertEqual(row[3], "2026-08-28")
                self.assertEqual(row[4], 1)
                self.assertEqual(row[5], "gpt-5.6-luna")
                self.assertIn("theme_dict_migrations", {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")})

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

    def test_priced_in_policy_applies_only_from_20260805(self) -> None:
        legacy = {"priced_in_level": "high", "priced_in_penalty": 20}
        apply_priced_in_policy("20260804", legacy)
        self.assertEqual(legacy["priced_in_penalty"], 20)

        expected = {"unknown": 0, "none": 0, "low": 3, "medium": 7, "high": 12}
        for level, penalty in expected.items():
            components = {"priced_in_level": level, "priced_in_penalty": 20}
            apply_priced_in_policy("20260805", components)
            self.assertEqual(components["priced_in_penalty"], penalty)

        self.assertEqual(prompt_version_for_date("20260804"), "catalyst-survival-v3")
        self.assertEqual(prompt_version_for_date("20260805"), "catalyst-survival-v4")

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
                **catalyst_fields(),
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

    def test_prompt_requires_same_day_primary_and_secondary_text_only_assessment(self) -> None:
        state = {
            "date": "20260713",
            "news_by_ticker": {"000001": []},
            "telegram_by_ticker": {"000001": []},
        }
        prompt = make_prompt(state, {
            "ticker": "000001", "name": "테스트",
            "market_cap_previous_day": 1000000,
        })
        self.assertIn("primary_catalyst", prompt)
        self.assertIn("secondary_catalysts", prompt)
        self.assertIn("category_raw", prompt)
        self.assertIn("1~5", prompt)
        self.assertIn("market_snapshot을 사용하지 마라", prompt)
        self.assertIn("세부 가점·감점", prompt)
        self.assertIn("DB 설명란", prompt)
        self.assertIn("[정성적 근거]", prompt)
        self.assertIn("[정량적 근거]", prompt)
        self.assertIn("정성적 근거에는 가격·거래량 수치를 넣지 말고", prompt)
        self.assertIn("일부 반복 보도 또는 사전 기대 | 5", prompt)
        self.assertNotIn("none=0`, `low=3`, `medium=7`, `high=12", prompt)

    def test_prompt_uses_reduced_priced_in_policy_from_20260805(self) -> None:
        state = {
            "date": "20260805",
            "news_by_ticker": {"000001": []},
            "telegram_by_ticker": {"000001": []},
        }
        prompt = make_prompt(state, {
            "ticker": "000001", "name": "테스트",
            "market_cap_previous_day": 1000000,
        })
        self.assertIn("none=0`, `low=3`, `medium=7`, `high=12", prompt)
        self.assertIn("단순 당일 상승률·거래량 증가·최근 5거래일 상승만으로", prompt)
        self.assertIn("같은 가격 움직임을 선반영과 소진에 중복 사용하지 마라", prompt)

    def test_market_snapshot_changes_do_not_mutate_catalyst_output(self) -> None:
        payload = {
            "ticker": "000001", "name": "테스트", "target": "D+1_OPEN_ABOVE_D_CLOSE",
            "probability_score": 72, "confidence": "medium", "up_factors": [],
            "score_components": {
                "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low", "priced_in_penalty": 0,
                "exhaustion_level": "low", "exhaustion_penalty": 0,
            },
            "down_factors": [], "news_summary": "뉴스", "telegram_summary": "텔레그램",
            "reasoning": "근거", "evidence_quality": "moderate", **catalyst_fields(),
        }
        base_candidate = {
            "ticker": "000001", "name": "테스트",
            "snapshot_at": "2026-07-13T15:00:05+09:00",
            "snapshot_open_price": 100, "snapshot_volume": 2000,
            "avg5_volume": 1000, "previous_5d_close": 80,
        }
        state = {
            "date": "20260713", "news_by_ticker": {"000001": []},
            "telegram_by_ticker": {"000001": []}, "warnings": [],
        }
        first = score_candidates({
            **state, "candidates": [{
                **base_candidate, "snapshot_current_price": 102, "snapshot_high_price": 110,
            }],
        }, lambda _prompt: json.dumps(payload, ensure_ascii=False))
        second = score_candidates({
            **state, "candidates": [{
                **base_candidate, "snapshot_current_price": 120, "snapshot_high_price": 125,
            }],
        }, lambda _prompt: json.dumps(payload, ensure_ascii=False))
        self.assertEqual(
            first["scores"][0]["primary_catalyst"],
            second["scores"][0]["primary_catalyst"],
        )

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
            "as_of": "2026-07-13T15:00:00+09:00",
            "change_rate_pct": 10.5, "rise_from_open_pct": 4.25,
            "pullback_from_high_pct": -1.5, "return_5d_pct": -2.75,
            **catalyst_fields(),
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
        self.assertIn("[점수 산식] 50 + 재료강도 15 + 신선도 5 + 독립확인 2", saved[2])
        self.assertIn("= 72점", saved[2])
        self.assertIn("[정성적 근거]", saved[2])
        self.assertIn("주재료: 신규 공급계약 (alive, 생존 4/5)", saved[2])
        self.assertIn("후속 계약 발표가 남아 있다.", saved[2])
        self.assertIn("[정량적 근거] 현재가 110원, 등락률 +10.50%", saved[2])
        self.assertIn("시가 대비 +4.25%, 고점 대비 -1.50%", saved[2])
        self.assertIn("거래량 배율 10.00배, 5일 수익률 -2.75%", saved[2])
        self.assertIn("[종합 판단] 재료강도와 선반영을 함께 평가했다.", saved[2])
        self.assertIn("확인된 뉴스", saved[3])
        self.assertIn("채널 언급", saved[4])

    def test_persist_rejects_invalid_catalyst_before_legacy_write(self) -> None:
        score = {
            "date": "20260713", "as_of": "2026-07-13T15:00:00+09:00",
            "ticker": "000001", "name": "테스트", "probability_score": 72,
            "score_components": {
                "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low", "priced_in_penalty": 0,
                "exhaustion_level": "low", "exhaustion_penalty": 0,
            },
            "confidence": "medium", "evidence_quality": "moderate",
            "up_factors": [], "down_factors": [], "reasoning": "근거",
            "news_summary": "뉴스", "telegram_summary": "텔레그램", "sources": [],
            **catalyst_fields(),
        }
        score["primary_catalyst"]["alive_score"] = 6
        with self.assertRaises(ValueError):
            persist_scoring_results(self.watchlist_db, [{
                "date": "20260713", "candidate_count": 1, "scored_count": 1,
                "scores": [score],
            }])
        with closing(sqlite3.connect(self.watchlist_db)) as con:
            self.assertEqual(con.execute("SELECT score FROM llm_scores").fetchone()[0], 58)

    def test_persist_rolls_back_legacy_when_catalyst_insert_fails(self) -> None:
        with closing(sqlite3.connect(self.watchlist_db)) as con, con:
            con.execute("CREATE TABLE llm_catalyst_assessments(date TEXT)")
        score = {
            "date": "20260713", "as_of": "2026-07-13T15:00:00+09:00",
            "ticker": "000001", "name": "테스트", "probability_score": 72,
            "score_components": {
                "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low", "priced_in_penalty": 0,
                "exhaustion_level": "low", "exhaustion_penalty": 0,
            },
            "confidence": "medium", "evidence_quality": "moderate",
            "up_factors": [], "down_factors": [], "reasoning": "근거",
            "news_summary": "뉴스", "telegram_summary": "텔레그램", "sources": [],
            **catalyst_fields(),
        }
        with self.assertRaises(sqlite3.OperationalError):
            persist_scoring_results(self.watchlist_db, [{
                "date": "20260713", "candidate_count": 1, "scored_count": 1,
                "scores": [score],
            }])
        with closing(sqlite3.connect(self.watchlist_db)) as con:
            self.assertEqual(con.execute("SELECT score FROM llm_scores").fetchone()[0], 58)

    def test_persist_scoring_results_writes_legacy_and_catalyst_rows(self) -> None:
        score = {
            "date": "20260713", "as_of": "2026-07-13T15:00:00+09:00",
            "ticker": "000001", "name": "테스트", "probability_score": 72,
            "score_components": {
                "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low", "priced_in_penalty": 0,
                "exhaustion_level": "low", "exhaustion_penalty": 0,
            },
            "confidence": "medium", "evidence_quality": "moderate",
            "up_factors": [], "down_factors": [], "reasoning": "근거",
            "news_summary": "뉴스", "telegram_summary": "텔레그램", "sources": [],
            **catalyst_fields(),
        }
        result = persist_scoring_results(self.watchlist_db, [{
            "date": "20260713", "candidate_count": 1, "scored_count": 1,
            "scores": [score],
        }], model="test-model")
        self.assertEqual(result, {"llm_scores": 1, "catalyst_assessments": 1})
        with closing(sqlite3.connect(self.watchlist_db)) as con:
            self.assertEqual(con.execute("SELECT score FROM llm_scores").fetchone()[0], 72)
            self.assertEqual(con.execute(
                "SELECT primary_alive_score FROM llm_catalyst_assessments"
            ).fetchone()[0], 4)

    def test_catalyst_assessment_upsert_is_idempotent_and_keeps_legacy_score(self) -> None:
        score = {
            "date": "20260713", "as_of": "2026-07-13T15:00:00+09:00",
            "ticker": "000001", **catalyst_fields(),
        }
        score["secondary_catalysts"] = [{
            "label": "정책 지원", "description": "산업 지원 정책이 발표됐다.",
            "category_raw": "산업 정책 지원", "status": "uncertain",
            "expected_duration": "one_week_or_more", "alive_score": 3,
            "reason": "세부 집행 일정은 미정이다.", "invalidation": "지원 대상 제외",
            "evidence_refs": [],
        }]
        self.assertEqual(upsert_catalyst_assessments(
            self.watchlist_db, [score], model="test-model",
            generated_at="2026-07-13T15:01:00+09:00",
        ), 1)
        score["primary_catalyst"]["alive_score"] = 5
        self.assertEqual(upsert_catalyst_assessments(
            self.watchlist_db, [score], model="test-model",
            generated_at="2026-07-13T15:02:00+09:00",
        ), 1)

        with closing(sqlite3.connect(self.watchlist_db)) as con:
            saved = con.execute("""
                SELECT primary_category_raw,primary_status,primary_duration,
                       primary_alive_score,max_alive_score,assessment_json,
                       prompt_version,model,generated_at
                FROM llm_catalyst_assessments
            """).fetchall()
            legacy_score = con.execute("""
                SELECT score FROM llm_scores
                WHERE date='20260713' AND ticker='000001'
            """).fetchone()[0]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][3:5], (5, 5))
        assessment = json.loads(saved[0][5])
        self.assertEqual(assessment["primary_catalyst"]["alive_score"], 5)
        self.assertEqual(assessment["secondary_catalysts"][0]["category_raw"], "산업 정책 지원")
        self.assertEqual(saved[0][7], "test-model")
        self.assertEqual(saved[0][8], "2026-07-13T15:02:00+09:00")
        self.assertEqual(legacy_score, 58)

    def test_operational_row_keeps_full_catalyst_assessment(self) -> None:
        score = {
            "date": "20260713", "as_of": "2026-07-13T15:00:00+09:00",
            "ticker": "000001", "name": "테스트", "probability_score": 72,
            "score_components": {
                "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low", "priced_in_penalty": 0,
                "exhaustion_level": "low", "exhaustion_penalty": 0,
            },
            "confidence": "medium", "evidence_quality": "moderate",
            "up_factors": [], "down_factors": [], "reasoning": "근거",
            "news_summary": "뉴스", "telegram_summary": "텔레그램", "sources": [],
            **catalyst_fields(),
        }
        row = to_llm_score_row(score)
        self.assertEqual(row["primary_catalyst"]["alive_score"], 4)
        self.assertEqual(row["secondary_catalysts"], [])
        self.assertEqual(row["as_of"], "2026-07-13T15:00:00+09:00")

    def test_operational_reason_keeps_sections_once(self) -> None:
        score = {
            "date": "20260713", "as_of": "2026-07-13T15:00:00+09:00",
            "ticker": "000001", "name": "테스트", "probability_score": 72,
            "score_components": {
                "catalyst_strength": 15, "freshness": 5, "confirmation": 2,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low", "priced_in_penalty": 0,
                "exhaustion_level": "low", "exhaustion_penalty": 0,
            },
            "confidence": "medium", "evidence_quality": "moderate",
            "up_factors": [], "down_factors": [],
            "reasoning": "[정성적 근거] 정성 상세. [정량적 근거] 정량 상세. [종합] 최종 결론.",
            "news_summary": "뉴스", "telegram_summary": "텔레그램", "sources": [],
            **catalyst_fields(),
        }
        reason = to_llm_score_row(score)["reason_summary"]
        self.assertEqual(reason.count("[정성적 근거]"), 1)
        self.assertEqual(reason.count("[정량적 근거]"), 1)
        self.assertEqual(reason.count("[종합 판단]"), 1)
        self.assertTrue(reason.endswith("[종합 판단] 최종 결론."))

    def test_operational_reason_explains_clamped_score(self) -> None:
        score = {
            "date": "20260713", "as_of": "2026-07-13T15:00:00+09:00",
            "ticker": "000001", "name": "테스트", "probability_score": 95,
            "score_components": {
                "catalyst_strength": 30, "freshness": 10, "confirmation": 5,
                "negative_event_risk": 0, "negative_trend_penalty": 0,
                "priced_in_level": "low", "priced_in_penalty": 0,
                "exhaustion_level": "low", "exhaustion_penalty": 0,
            },
            "confidence": "high", "evidence_quality": "strong",
            "up_factors": [], "down_factors": [], "reasoning": "최고 수준 근거",
            "news_summary": "뉴스", "telegram_summary": "텔레그램", "sources": [],
            **catalyst_fields(),
        }
        row = to_llm_score_row(score)
        self.assertIn("= 95점", row["reason_summary"])
        self.assertNotIn("clamp 최종", row["reason_summary"])

        score["score_components"]["catalyst_strength"] = 35
        row = to_llm_score_row(score)
        self.assertIn("= 100점 → clamp 최종 95점", row["reason_summary"])

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
        self.assertEqual(
            doc["source_data"]["catalyst_definition"],
            "primary and secondary catalyst survival assessment",
        )
        self.assertEqual(doc["source_data"]["catalyst_prompt_version"], "catalyst-survival-v3")

    def test_operational_write_rejects_partial_scores(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "1/2"):
            ensure_complete_scores([{
                "date": "20260714", "candidate_count": 2, "scored_count": 1,
            }])


if __name__ == "__main__":
    unittest.main()
