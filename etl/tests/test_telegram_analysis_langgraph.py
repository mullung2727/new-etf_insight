"""telegram_analysis_langgraph.py 노드 단위 테스트 (Step 2: load_posts, extract_stock_mentions).

검증 항목:
  1. load_posts: date_kst 필터 + discovery 채널만 + post_id > 워터마크(증분)
  2. load_posts: PRAGMA query_only 커넥션에서 워터마크 읽기 동작
  3. extract_stock_mentions: 마스터(name/code) 매칭, 오탐 코드(202403) 걸러짐, 빈 rows → []
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from scripts.telegram_langgraph.telegram_analysis_langgraph import (
    analyze_telegram_session,
    build_final_report,
    build_graph,
    call_session_overview_llm,
    call_extract_llm,
    call_stock_insight_llm,
    ensure_schema,
    load_posts,
    load_stock_history,
    make_session_overview_prompt,
    make_extract_prompt,
    make_stock_insight_prompt,
    parse_and_validate_overview,
    parse_extract,
    parse_stock_insight,
    persist_and_advance,
)
from scripts.telegram_analysis_watermark import advance_watermarks
from scripts.telegram_stock_insights import ensure_schema as ensure_insights_schema
from scripts.telegram_stock_insights import upsert_candidate, update_analysis

MODULE = "scripts.telegram_langgraph.telegram_analysis_langgraph"


def _base_state(db_path, stock_db=""):
    return {
        "date_kst": "2026-07-03",
        "start_date_kst": "2026-07-03",
        "session": "close",
        "db_path": db_path,
        "stock_db_path": stock_db,
        "history_days": 7,
        "min_text_length": 30,
        "watermark_in": {},
        "rows": [],
        "channel_post_counts": {},
        "overview_prompt": "",
        "overview_llm_output": "",
        "session_highlights": [],
        "overview_warnings": [],
        "extract_prompt": "",
        "extract_llm_output": "",
        "stock_mentions": [],
        "stock_history": {},
        "stock_prompt": "",
        "stock_llm_output": "",
        "stock_insights": [],
        "final_report": {},
        "persisted_count": 0,
        "warnings": [],
    }


class SessionOverviewNodesTest(unittest.TestCase):
    def _state_with_rows(self):
        state = _base_state("unused")
        state["rows"] = [
            {"channel": "getfeed", "post_id": 1, "post_ref": "getfeed/1",
             "text": "중국 AI 모델 등장으로 GPU 수요 논쟁이 확대됐다."},
            {"channel": "infomarketopen", "post_id": 2, "post_ref": "infomarketopen/2",
             "text": "저비용 AI 모델에도 HBM과 네트워크 투자는 이어질 전망이다."},
        ]
        return state

    def test_prompt_calls_llm_and_empty_rows_skip(self):
        built = make_session_overview_prompt(self._state_with_rows())
        self.assertIn("getfeed/1", built["overview_prompt"])
        self.assertIn("GPU 수요", built["overview_prompt"])
        with patch(f"{MODULE}.generate_json", return_value='{"highlights": []}') as gen:
            called = call_session_overview_llm(built)
        gen.assert_called_once()
        self.assertEqual(called["overview_llm_output"], '{"highlights": []}')

        empty = make_session_overview_prompt(_base_state("unused"))
        with patch(f"{MODULE}.generate_json") as gen:
            skipped = call_session_overview_llm(empty)
        gen.assert_not_called()
        self.assertEqual(skipped["overview_llm_output"], "")

    def test_parse_recalculates_scores_filters_bad_refs_and_sorts(self):
        state = self._state_with_rows()
        state["overview_llm_output"] = json.dumps({"highlights": [
            {
                "title": "낮은 점수", "summary": "요약", "category": "시장",
                "importance_reason": "근거", "score_total": 99,
                "score_breakdown": {"market_impact": 10, "evidence_quality": 10,
                    "novelty": 10, "investment_relevance": 10, "cross_channel": 5},
                "source_channels": ["getfeed"], "source_post_refs": ["getfeed/1"],
            },
            {
                "title": "높은 점수", "summary": "요약", "category": "산업",
                "importance_reason": "근거", "score_total": 1,
                "score_breakdown": {"market_impact": 25, "evidence_quality": 20,
                    "novelty": 18, "investment_relevance": 19, "cross_channel": 10},
                "source_channels": ["getfeed", "fake"],
                "source_post_refs": ["getfeed/1", "fake/999"],
            },
        ]}, ensure_ascii=False)

        out = parse_and_validate_overview(state)

        self.assertEqual([h["title"] for h in out["session_highlights"]], ["높은 점수", "낮은 점수"])
        self.assertEqual(out["session_highlights"][0]["score_total"], 92)
        self.assertEqual(out["session_highlights"][0]["source_post_refs"], ["getfeed/1"])
        self.assertEqual(out["session_highlights"][0]["source_channels"], ["getfeed"])
        self.assertTrue(any("fake/999" in w for w in out["overview_warnings"]))

    def test_parse_rejects_out_of_range_breakdown(self):
        state = self._state_with_rows()
        state["overview_llm_output"] = json.dumps({"highlights": [{
            "title": "잘못된 점수", "summary": "요약", "category": "시장",
            "importance_reason": "근거", "score_total": 100,
            "score_breakdown": {"market_impact": 26, "evidence_quality": 25,
                "novelty": 20, "investment_relevance": 20, "cross_channel": 10},
            "source_channels": ["getfeed"], "source_post_refs": ["getfeed/1"],
        }]}, ensure_ascii=False)

        out = parse_and_validate_overview(state)

        self.assertEqual(out["session_highlights"], [])
        self.assertTrue(any("score_out_of_range" in w for w in out["overview_warnings"]))


class LoadPostsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = str(self.tmp / "tg.sqlite3")
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE telegram_posts (channel TEXT, post_id INTEGER, post_ref TEXT, "
            "posted_at_utc TEXT, date_kst TEXT, text TEXT, links_json TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        rows = [
            # channel, post_id, post_ref, date_kst, text
            ("getfeed", 3, "getfeed/3", "2026-07-03", "워터마크 이하 → 제외"),
            ("getfeed", 8, "getfeed/8", "2026-07-03", "삼성전자 신고가"),
            ("getfeed", 9, "getfeed/9", "2026-07-02", "다른날 → 제외"),
            ("corevalue", 2, "corevalue/2", "2026-07-03", "카카오 목표가"),
            ("butler_works", 5, "butler_works/5", "2026-07-03", "discovery 아님 → 제외"),
        ]
        con.executemany(
            "INSERT INTO telegram_posts (channel, post_id, post_ref, date_kst, text) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        con.commit()
        con.close()
        ensure_schema(self.db)
        wcon = sqlite3.connect(self.db)
        advance_watermarks(wcon, {"getfeed": 5})  # getfeed는 post_id<=5 처리됨
        wcon.commit()
        wcon.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_incremental_and_discovery_filter(self):
        discovery = {"getfeed": {}, "corevalue": {}, "infomarketopen": {},
                     "awake_realtimeCheck": {}, "kimcharger": {}}
        with patch(f"{MODULE}.load_discovery_channels", return_value=discovery):
            out = load_posts(_base_state(self.db))
        refs = {r["post_ref"] for r in out["rows"]}
        # getfeed/8(신규) + corevalue/2 만. getfeed/3(워터마크이하), getfeed/9(다른날),
        # butler_works/5(discovery아님) 전부 제외.
        self.assertEqual(refs, {"getfeed/8", "corevalue/2"})
        self.assertEqual(out["watermark_in"], {"getfeed": 5})
        self.assertEqual(out["channel_post_counts"], {"getfeed": 1, "corevalue": 1})

    def test_two_day_window_keeps_only_posts_after_watermark(self):
        discovery = {"getfeed": {}, "corevalue": {}}
        state = _base_state(self.db)
        state["start_date_kst"] = "2026-07-02"
        with patch(f"{MODULE}.load_discovery_channels", return_value=discovery):
            out = load_posts(state)
        refs = {r["post_ref"] for r in out["rows"]}
        self.assertEqual(refs, {"getfeed/8", "getfeed/9", "corevalue/2"})


class ExtractNodesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.stock_db = str(self.tmp / "stock.duckdb")
        con = duckdb.connect(self.stock_db)
        con.execute(
            "CREATE TABLE stock_names (code VARCHAR PRIMARY KEY, name VARCHAR, updated_at VARCHAR)"
        )
        con.executemany(
            "INSERT INTO stock_names VALUES (?,?,?)",
            [("005930", "삼성전자", "x"), ("035720", "카카오", "x")],
        )
        con.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows_state(self):
        state = _base_state("unused", stock_db=self.stock_db)
        state["rows"] = [
            {"channel": "getfeed", "post_ref": "getfeed/8", "text": "삼성전자 메모리 신고가 " * 3},
            {"channel": "corevalue", "post_ref": "corevalue/2", "text": "카카오 목표가 상향 " * 3},
            {"channel": "getfeed", "post_ref": "getfeed/9", "text": "삼성전자 또 언급"},
        ]
        return state

    def test_make_extract_prompt_and_empty(self):
        out = make_extract_prompt(self._rows_state())
        self.assertIn("삼성전자", out["extract_prompt"])
        self.assertIn("[getfeed · macro_essay]", out["extract_prompt"])
        self.assertEqual(make_extract_prompt(_base_state("unused"))["extract_prompt"], "")

    def test_call_extract_skips_when_empty(self):
        with patch(f"{MODULE}.generate_json") as gen:
            out = call_extract_llm({**_base_state("unused"), "extract_prompt": ""})
        gen.assert_not_called()
        self.assertEqual(out["extract_llm_output"], "")

    def test_parse_extract_resolves_names_and_aggregates(self):
        state = self._rows_state()
        # LLM이 삼성전자·카카오 뽑고, 마스터에 없는 '없는종목'도 하나 뱉음 → 버려야
        state["extract_llm_output"] = json.dumps({"stocks": [
            {"name": "삼성전자", "note": "메모리 신고가"},
            {"name": "카카오", "note": "목표가 상향"},
            {"name": "없는종목", "note": "환각"},
        ]})
        out = parse_extract(state)
        by_ticker = {m["ticker"]: m for m in out["stock_mentions"]}
        self.assertEqual(set(by_ticker), {"005930", "035720"})
        sec = by_ticker["005930"]
        self.assertEqual(sec["name"], "삼성전자")
        self.assertEqual(sec["mention_count"], 2)          # getfeed/8, getfeed/9
        self.assertEqual(sec["mention_channels"], ["getfeed"])
        self.assertEqual(sec["discovery_reason"], "메모리 신고가")  # LLM note
        # 마스터에 없는 이름은 warning + 제외
        self.assertTrue(any("없는종목" in w for w in out["warnings"]))

    def test_parse_extract_empty_output(self):
        out = parse_extract({**_base_state("unused", stock_db=self.stock_db), "extract_llm_output": ""})
        self.assertEqual(out["stock_mentions"], [])


class LoadStockHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = str(self.tmp / "tg.sqlite3")
        ensure_schema(self.db)
        con = sqlite3.connect(self.db)
        # 005930: 윈도우 안(07-01) / 경계 밖(06-25=7일 이전, 제외) / 당일(07-03, 제외)
        for date_kst, sess in [("2026-06-25", "close"), ("2026-07-01", "morning"),
                               ("2026-07-01", "close"), ("2026-07-03", "morning")]:
            upsert_candidate(con, date_kst, sess, "005930", "삼성전자",
                             mention_channels=["getfeed"], source_post_refs=[f"getfeed/{date_kst}"],
                             discovery_reason="언급")
        update_analysis(con, "2026-07-01", "close", "005930", '{"change_type":"new"}')
        con.commit()
        con.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_history_window_and_new_stock(self):
        state = _base_state(self.db)
        state["stock_mentions"] = [
            {"ticker": "005930", "name": "삼성전자"},
            {"ticker": "999999", "name": "신규종목"},
        ]
        out = load_stock_history(state)
        hist = out["stock_history"]
        # 999999 이력 없음 → 신규(빈 리스트)
        self.assertEqual(hist["999999"], [])
        # 005930: 07-01 두 행만. 06-25(7일이전 경계 밖), 07-03(당일) 제외
        dates = [(r["date_kst"], r["session"]) for r in hist["005930"]]
        self.assertEqual(dates, [("2026-07-01", "morning"), ("2026-07-01", "close")])
        # analysis 채워진 행 확인
        self.assertEqual(hist["005930"][1]["analysis"], '{"change_type":"new"}')

    def test_empty_mentions(self):
        state = _base_state(self.db)
        out = load_stock_history(state)
        self.assertEqual(out["stock_history"], {})


class LlmNodesTest(unittest.TestCase):
    def _state_with_mentions(self):
        state = _base_state("unused")
        state["rows"] = [
            {"post_ref": "getfeed/8", "text": "삼성전자 메모리 가격 상승, 목표가 상향 " * 3},
        ]
        state["stock_mentions"] = [{
            "ticker": "005930", "name": "삼성전자",
            "mention_channels": ["getfeed"], "source_post_refs": ["getfeed/8"],
            "mention_count": 1, "discovery_reason": "언급 1건",
        }]
        state["stock_history"] = {"005930": []}
        return state

    def test_make_prompt_builds_and_empty_skips(self):
        out = make_stock_insight_prompt(self._state_with_mentions())
        self.assertIn("005930", out["stock_prompt"])
        self.assertIn("삼성전자", out["stock_prompt"])
        self.assertIn("메모리", out["stock_prompt"])  # 샘플 본문 포함
        # 빈 후보 → 빈 프롬프트
        empty = make_stock_insight_prompt(_base_state("unused"))
        self.assertEqual(empty["stock_prompt"], "")

    def test_stock_llm_skips_codex_when_empty(self):
        with patch(f"{MODULE}.generate_json") as gen:
            out = call_stock_insight_llm({**_base_state("unused"), "stock_prompt": ""})
        gen.assert_not_called()
        self.assertEqual(out["stock_llm_output"], "")

    def test_stock_llm_calls_and_parses(self):
        payload = json.dumps({"stocks": [{
            "name": "삼성전자", "code": "005930", "change_type": "new",
            "change_summary": "메모리 반등", "themes": ["반도체"], "evidence_summary": "가격상승 언급",
        }]})
        with patch(f"{MODULE}.generate_json", return_value=payload) as gen:
            out = call_stock_insight_llm({**_base_state("unused"), "stock_prompt": "PROMPT"})
        gen.assert_called_once()
        parsed = parse_stock_insight(out)
        self.assertEqual(parsed["stock_insights"][0]["code"], "005930")
        self.assertEqual(parsed["stock_insights"][0]["change_type"], "new")


class BuildFinalReportTest(unittest.TestCase):
    def test_joins_python_and_llm_and_drops_hallucinated(self):
        state = _base_state("unused")
        state["rows"] = [{"channel": "getfeed", "post_id": 8, "post_ref": "getfeed/8"}]
        state["channel_post_counts"] = {"getfeed": 1}
        state["stock_mentions"] = [{
            "ticker": "005930", "name": "삼성전자", "mention_channels": ["getfeed", "corevalue"],
            "source_post_refs": ["getfeed/8"], "mention_count": 3, "discovery_reason": "언급",
        }]
        state["stock_insights"] = [
            {"code": "005930", "change_type": "new", "change_summary": "메모리 반등",
             "themes": ["반도체"], "evidence_summary": "가격상승", "mention_count": 999},
            {"code": "111111", "change_type": "new"},  # 후보에 없음 → 버림 + warning
        ]
        state["session_highlights"] = [{"title": "AI 투자 논쟁", "score_total": 84}]
        state["overview_warnings"] = ["overview-note"]
        out = build_final_report(state)
        rep = out["final_report"]
        self.assertEqual(rep["post_count"], 1)
        self.assertEqual(rep["session_highlights"][0]["score_total"], 84)
        self.assertEqual(len(rep["notable_stocks"]), 1)
        s = rep["notable_stocks"][0]
        self.assertEqual(s["code"], "005930")
        self.assertEqual(s["mention_count"], 3)  # LLM의 999 아님, 파이썬값
        self.assertEqual(s["channels"], ["getfeed", "corevalue"])
        self.assertEqual(s["change_summary"], "메모리 반등")
        self.assertTrue(any("111111" in w for w in rep["warnings"]))
        self.assertIn("overview-note", rep["warnings"])


class PersistAndAdvanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = str(self.tmp / "tg.sqlite3")
        ensure_schema(self.db)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state(self):
        state = _base_state(self.db)
        state["rows"] = [
            {"channel": "getfeed", "post_id": 8, "post_ref": "getfeed/8"},
            {"channel": "getfeed", "post_id": 12, "post_ref": "getfeed/12"},
            {"channel": "corevalue", "post_id": 5, "post_ref": "corevalue/5"},
        ]
        state["stock_mentions"] = [{
            "ticker": "005930", "name": "삼성전자", "mention_channels": ["getfeed"],
            "source_post_refs": ["getfeed/8"], "mention_count": 1, "discovery_reason": "언급",
        }]
        state["stock_insights"] = [{
            "code": "005930", "change_type": "new", "change_summary": "반등",
            "themes": ["반도체"], "evidence_summary": "근거",
        }]
        state["session_highlights"] = [{
            "title": "메모리 업황", "summary": "가격 상승 언급", "category": "산업",
            "importance_reason": "반도체 실적과 연결", "score_total": 80,
            "score_breakdown": {"market_impact": 20, "evidence_quality": 20,
                "novelty": 15, "investment_relevance": 18, "cross_channel": 7},
            "source_channels": ["getfeed"], "source_post_refs": ["getfeed/8"],
        }]
        return state

    def _read_rows(self):
        con = sqlite3.connect(self.db)
        r = con.execute(
            "SELECT date_kst, session, ticker, analysis FROM telegram_stock_insights"
        ).fetchall()
        con.close()
        return r

    def _read_wm(self):
        con = sqlite3.connect(self.db)
        r = dict(con.execute("SELECT channel, last_post_id FROM telegram_analysis_watermark").fetchall())
        con.close()
        return r

    def test_persist_and_advance(self):
        out = persist_and_advance(self._state())
        self.assertEqual(out["persisted_count"], 1)
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "005930")
        self.assertIn("change_type", rows[0][3])  # analysis 채워짐
        con = sqlite3.connect(self.db)
        highlight = con.execute(
            "SELECT title, score_total FROM telegram_session_highlights "
            "WHERE date_kst='2026-07-03' AND session='close'"
        ).fetchone()
        con.close()
        self.assertEqual(highlight, ("메모리 업황", 80))
        # 워터마크 = 채널별 max(post_id)
        self.assertEqual(self._read_wm(), {"getfeed": 12, "corevalue": 5})

    def test_idempotent_rerun(self):
        persist_and_advance(self._state())
        persist_and_advance(self._state())  # 같은 세션 재실행
        self.assertEqual(len(self._read_rows()), 1)  # 중복 없음

    def test_no_candidates_still_advances_watermark(self):
        state = self._state()
        state["stock_mentions"] = []
        state["stock_insights"] = []
        out = persist_and_advance(state)
        self.assertEqual(out["persisted_count"], 0)
        self.assertEqual(self._read_rows(), [])          # insight 행 없음
        self.assertEqual(self._read_wm(), {"getfeed": 12, "corevalue": 5})  # 워터마크는 전진

    def test_empty_rows_does_nothing(self):
        state = self._state()
        state["rows"] = []
        out = persist_and_advance(state)
        self.assertEqual(out["persisted_count"], 0)
        self.assertEqual(self._read_rows(), [])
        self.assertEqual(self._read_wm(), {})  # 워터마크 안 밀림


class GraphE2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = str(self.tmp / "tg.sqlite3")
        con = sqlite3.connect(self.db)
        con.execute(
            "CREATE TABLE telegram_posts (channel TEXT, post_id INTEGER, post_ref TEXT, "
            "posted_at_utc TEXT, date_kst TEXT, text TEXT, links_json TEXT, "
            "created_at TEXT, updated_at TEXT)"
        )
        con.executemany(
            "INSERT INTO telegram_posts (channel, post_id, post_ref, date_kst, text) VALUES (?,?,?,?,?)",
            [
                ("getfeed", 8, "getfeed/8", "2026-07-03", "삼성전자 메모리 가격 상승 목표가 상향 " * 3),
                ("corevalue", 5, "corevalue/5", "2026-07-03", "카카오 반등 기대 " * 5),
            ],
        )
        con.commit()
        con.close()
        # stock master duckdb
        self.stock_db = str(self.tmp / "stock.duckdb")
        sc = duckdb.connect(self.stock_db)
        sc.execute("CREATE TABLE stock_names (code VARCHAR PRIMARY KEY, name VARCHAR, updated_at VARCHAR)")
        sc.executemany("INSERT INTO stock_names VALUES (?,?,?)",
                       [("005930", "삼성전자", "x"), ("035720", "카카오", "x")])
        sc.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_run(self):
        discovery = {"getfeed": {}, "corevalue": {}, "infomarketopen": {},
                     "awake_realtimeCheck": {}, "kimcharger": {}}
        overview_payload = json.dumps({"highlights": [{
            "title": "AI 반도체 수요", "summary": "메모리 가격 상승이 반복 언급됐다.",
            "category": "산업", "importance_reason": "반도체 실적과 연결된다.",
            "score_total": 80,
            "score_breakdown": {"market_impact": 20, "evidence_quality": 20,
                "novelty": 15, "investment_relevance": 18, "cross_channel": 7},
            "source_channels": ["getfeed"], "source_post_refs": ["getfeed/8"],
        }]})
        # 콜①: 세션 개괄. 콜②: LLM 추출(이름+note). 콜③: 변화판단.
        extract_payload = json.dumps({"stocks": [
            {"name": "삼성전자", "note": "메모리 신고가"},
            {"name": "카카오", "note": "반등 기대"},
        ]})
        insight_payload = json.dumps({"stocks": [
            {"name": "삼성전자", "code": "005930", "change_type": "new",
             "change_summary": "메모리 반등", "themes": ["반도체"], "evidence_summary": "가격상승"},
            {"name": "카카오", "code": "035720", "change_type": "new",
             "change_summary": "반등 기대", "themes": ["플랫폼"], "evidence_summary": "기대감"},
        ]})
        with patch(f"{MODULE}.load_discovery_channels", return_value=discovery), \
             patch(f"{MODULE}.generate_json", side_effect=[overview_payload, extract_payload, insight_payload]) as gen:
            report = analyze_telegram_session(
                "2026-07-03", "close", db_path=Path(self.db), stock_db_path=self.stock_db,
            )
        # LLM 3콜(개괄 + 추출 + 변화판단)
        self.assertEqual(gen.call_count, 3)
        self.assertEqual(report["session_highlights"][0]["score_total"], 80)
        codes = {s["code"] for s in report["notable_stocks"]}
        self.assertEqual(codes, {"005930", "035720"})
        self.assertEqual(report["post_count"], 2)
        # DB 저장 + 워터마크 전진 확인
        con = sqlite3.connect(self.db)
        n = con.execute("SELECT COUNT(*) FROM telegram_stock_insights WHERE session='close'").fetchone()[0]
        wm = dict(con.execute("SELECT channel, last_post_id FROM telegram_analysis_watermark").fetchall())
        con.close()
        self.assertEqual(n, 2)
        self.assertEqual(wm, {"getfeed": 8, "corevalue": 5})

    def test_graph_compiles(self):
        build_graph()  # 컴파일 에러 없이 통과


if __name__ == "__main__":
    unittest.main()
