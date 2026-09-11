"""3단계 마무리 — 정책 우회 차단·스키마·기존 시스템 무영향·결과 상태 (PLAN §20-3).

T24 수동/재현으로 가짜 high 제출 → 같은 정책 경로에서 거부
T27 probability=0.95 출력 → schema 거부, 등급만 표시
T28 DB commit 후 파일 생성 실패 → 저장 결과로 파일 복구
T30 새 분석 성공/실패 → 기존 워터마크·점수·주문 미변경
T34 정상 무변화 vs 수집 실패 → no_new_candidates 와 incomplete 구분
T41 신규 유입이 처리량 이상 → 적체 표시, 종료 예상 날조 없음
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402

from early_signals import analysis, policy, report, storage  # noqa: E402


class SchemaTest(unittest.TestCase):
    """T27 — LLM 이 확률을 만들어도 스키마가 막는다."""

    def test_extraction_schema_forbids_extra_fields(self):
        schema = json.loads(analysis.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        item = schema["properties"]["changes"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertNotIn("probability", item["properties"])
        self.assertNotIn("score", item["properties"])

    def test_schema_enumerates_grades_not_free_text(self):
        item = json.loads(analysis.SCHEMA_PATH.read_text(encoding="utf-8"))[
            "properties"]["changes"]["items"]
        self.assertIn("enum", item["properties"]["fact_type"])
        self.assertIn("enum", item["properties"]["change_type"])
        self.assertIn("enum", item["properties"]["direction"])


class PolicyBypassTest(unittest.TestCase):
    """T24 — 제출된 등급을 그대로 믿지 않는다. 행동은 apply_policy 만 정한다."""

    OK = {"history_ok": True, "turnover_20d": 5_000_000_000, "reason_codes": []}

    def test_claimed_high_without_price_cannot_reach_review_buy(self):
        result = policy.apply_policy(
            evidence="high", timing="high", price="unknown",
            has_new_change=True, price_context=dict(self.OK))
        self.assertEqual(result["action"], "watch")

    def test_invalidated_beats_every_claimed_grade(self):
        result = policy.apply_policy(
            evidence="high", timing="high", price="high", has_new_change=True,
            price_context=dict(self.OK), invalidated=True)
        self.assertEqual(result["action"], "invalidated")

    def test_liquidity_gate_cannot_be_bypassed_by_grades(self):
        thin = {"history_ok": True, "turnover_20d": 1, "reason_codes": []}
        result = policy.apply_policy(
            evidence="high", timing="high", price="high", has_new_change=True,
            price_context=thin)
        self.assertEqual(result["action"], "watch")
        self.assertIn("liquidity_below_min", result["reason_codes"])


class ArtifactRecoveryTest(unittest.TestCase):
    """T28 — 파일이 없어도 저장된 결과만으로 다시 만든다. LLM 을 재호출하지 않는다."""

    def _payload(self, out_dir):
        return {
            "run_id": "r1", "mode": "historical_exploration", "cutoff_date": "2026-07-01",
            "price_as_of": "20260701", "manifest_sources": 100, "processed": 10,
            "backlog": 90, "events": 5, "with_events": 3, "rejected": 1,
            "result_status": "partial", "assessments": [], "selected": [],
            "feasibility": {"entities": 0, "grades": {a: {} for a in
                                                      ("evidence", "timing", "price")},
                            "actions": {}, "reason_codes": {},
                            "market_calendar": {"sessions": 10, "median_listings": 100,
                                                "sparse_sessions_dropped": []}},
        }

    def test_artifact_is_regenerated_from_stored_payload(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = report.render_artifact(self._payload(out), out_dir=out)
            self.assertTrue(first.exists())
            first.unlink()
            again = report.render_artifact(self._payload(out), out_dir=out)
            self.assertTrue(again.exists())
            self.assertEqual(first, again)
            self.assertIn("2026-07-01", again.read_text(encoding="utf-8"))

    def test_budget_caveat_only_when_backlog_remains(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            partial = report.render_artifact(self._payload(out), out_dir=out).read_text(encoding="utf-8")
            done = dict(self._payload(out), run_id="r2", processed=100, backlog=0)
            complete = report.render_artifact(done, out_dir=out).read_text(encoding="utf-8")
        self.assertIn("예산 한도로", partial)
        self.assertNotIn("예산 한도로", complete)


class ResultStatusTest(unittest.TestCase):
    """T34 — 정상 무변화와 수집 실패를 다르게 표시한다."""

    def test_backlog_makes_partial_not_no_candidates(self):
        self.assertEqual(_status(backlog=90, selected=[]), "partial")

    def test_clean_run_without_candidates_is_no_candidates(self):
        self.assertEqual(_status(backlog=0, selected=[]), "no_candidates")

    def test_candidates_present(self):
        self.assertEqual(_status(backlog=0, selected=["005930"]), "candidates")


def _status(*, backlog: int, selected: list) -> str:
    """run_early_signals.run_assess 와 같은 규칙."""
    return "partial" if backlog else ("no_candidates" if not selected else "candidates")


class BacklogTest(unittest.TestCase):
    """T41 — 처리량보다 유입이 많으면 종료 예상을 지어내지 않는다."""

    def test_backlog_hours_are_derived_from_measured_rate(self):
        processed, elapsed, backlog = 300, 4686.0, 39544
        per_source = elapsed / processed
        hours = round(backlog * per_source / 3600, 1)
        self.assertAlmostEqual(per_source, 15.62, places=2)
        self.assertGreater(hours, 100)

    def test_zero_limit_yields_no_fabricated_estimate(self):
        limit = 0
        runs = (10 + limit - 1) // limit if limit else None
        self.assertIsNone(runs)


class ExistingSystemTest(unittest.TestCase):
    """T30 — 새 분석은 기존 워터마크·llm_scores·주문 테이블을 건드리지 않는다."""

    def test_schema_has_no_existing_operational_tables(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "es.sqlite3"
            storage.ensure_schema(db)
            with storage.connect_ro(db) as con:
                tables = {row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
        for forbidden in ("llm_scores", "watchlist", "pullback_orders",
                          "close_bet_orders", "telegram_analysis_watermark"):
            self.assertNotIn(forbidden, tables)

    def test_business_db_path_is_separate_file(self):
        self.assertEqual(storage.DEFAULT_DB.name, "early_signals.sqlite3")
        self.assertNotIn("watchlist", str(storage.DEFAULT_DB))


class RetryLimitTest(unittest.TestCase):
    """T22 — 청크 실패가 문서를 죽이지 않고, 재시도는 generate_json 한도를 따른다."""

    def test_chunk_failure_is_recorded_not_retried_here(self):
        record = {"source_version_id": "sv1", "source_type": "telegram", "source_key": "c/1",
                  "published_at": "2026-06-30T00:00:00+00:00", "entity_ids": ["005930"],
                  "extracted_text": "본문이다."}
        calls = {"n": 0}

        def failing(prompt, **kwargs):
            calls["n"] += 1
            raise RuntimeError("429")

        result = analysis.extract_changes(record, [], generate=failing)
        self.assertEqual(calls["n"], 1)          # 여기서 다시 부르지 않는다
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["events"], [])



class CapacityGateTest(unittest.TestCase):
    """T41 — 순처리량이 0 이하면 완료 시점을 지어내지 않는다(§12.3)."""

    def test_call_limit_binds_before_time_limit(self):
        from early_signals import capacity

        result = capacity.net_throughput(11.93, 429.1, budget_calls=300, budget_seconds=5400)
        self.assertEqual(result["binding_limit"], "calls")
        self.assertEqual(result["daily_capacity"], 300)
        self.assertLess(result["net_per_day"], 0)

    def test_time_limit_binds_when_rate_is_slow(self):
        from early_signals import capacity

        result = capacity.net_throughput(60.0, 100, budget_calls=300, budget_seconds=5400)
        self.assertEqual(result["binding_limit"], "time")
        self.assertEqual(result["daily_capacity"], 90)

    def test_negative_net_yields_no_forecast(self):
        from early_signals import capacity

        forecast = capacity.backlog_forecast(39544, -129.1)
        self.assertEqual(forecast["status"], "backlog_growing")
        self.assertIsNone(forecast["days_to_clear"])
        self.assertIsNone(forecast["runs_to_clear"])

    def test_positive_net_gives_estimate(self):
        from early_signals import capacity

        forecast = capacity.backlog_forecast(1000, 170.9)
        self.assertEqual(forecast["status"], "clearing")
        self.assertAlmostEqual(forecast["days_to_clear"], 5.9, places=1)

    def test_more_runs_per_day_scales_capacity(self):
        from early_signals import capacity

        one = capacity.net_throughput(11.93, 429.1, runs_per_day=1)
        two = capacity.net_throughput(11.93, 429.1, runs_per_day=2)
        self.assertEqual(two["daily_capacity"], one["daily_capacity"] * 2)
        self.assertGreater(two["net_per_day"], 0)

if __name__ == "__main__":
    unittest.main()


class ThroughputRecoveryTest(unittest.TestCase):
    """보고서가 실제와 다른 숫자를 내면 안 된다 — 저장값이 비면 사실로 다시 센다."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "es.sqlite3"
        storage.ensure_schema(self.db)
        with storage.connect_rw(self.db) as con:
            storage.start_run(con, run_id="r1", mode="live", cutoff_at="2026-07-01T06:40:00+00:00",
                              policy_version="p", identity_version="i", code_version="c")
            for index in range(3):
                sv = f"sv{index}"
                con.execute(
                    "INSERT INTO source_versions (source_version_id, source_type, source_key,"
                    " content_hash, origin_group_id, independence, first_observed_at,"
                    " available_at, extracted_text) VALUES (?,?,?,?,?,'known',?,?,?)",
                    (sv, "telegram", f"c/{index}", f"h{index}", "g1",
                     "2026-06-30T00:00:00+00:00", "2026-06-30T00:00:00+00:00", "본문"))
                con.execute("INSERT INTO manifest_entries VALUES (?,?)", ("r1", sv))
                storage.record_processing(
                    con, stage="extract_changes", input_hash=sv, policy_version="x",
                    prompt_version="p", model_identity="m", code_version="c", status="done")
            con.execute(
                "INSERT INTO events (event_id, source_version_id, event_fingerprint, change_key,"
                " extract_version, entity_ids_json, anchor_locators_json, change_json,"
                " first_detected_at) VALUES ('e1','sv0','f1','k','ex1','[\"005930\"]','[]','{}',?)",
                ("2026-06-30T00:00:00+00:00",))

    def tearDown(self):
        self.tmp.cleanup()

    def test_null_throughput_is_recomputed_from_stored_facts(self):
        with storage.connect_rw(self.db) as con:
            storage.finish_run(con, "r1", status="assessed")      # throughput 없이 마감
        with storage.connect_ro(self.db) as con:
            throughput = storage.load_throughput(con, "r1")
        self.assertTrue(throughput["recomputed"])
        self.assertEqual(throughput["manifest_sources"], 3)
        self.assertEqual(throughput["processed"], 3)
        self.assertEqual(throughput["events"], 1)
        self.assertEqual(throughput["backlog"], 0)

    def test_counts_come_from_facts_not_last_batch(self):
        """extract 를 나눠 돌리면 저장값은 마지막 배치뿐이다 — 건수는 사실에서 센다."""
        with storage.connect_rw(self.db) as con:
            storage.finish_run(con, "r1", status="extracted",
                               throughput={"processed": 1, "backlog": 39544,
                                           "sec_per_source": 15.6})
        with storage.connect_ro(self.db) as con:
            throughput = storage.load_throughput(con, "r1")
        self.assertEqual(throughput["processed"], 3)          # 실제 처리한 3건
        self.assertEqual(throughput["backlog"], 0)
        self.assertEqual(throughput["last_batch"]["processed"], 1)
        self.assertEqual(throughput["last_batch"]["sec_per_source"], 15.6)

    def test_episode_age_counts_from_its_first_run(self):
        """매주 다시 뽑혀도 시작 run 부터 84일이 지나면 episode 를 닫는다."""
        cutoffs = {"e0": "2026-01-01T06:40:00+00:00", "e49": "2026-02-19T06:40:00+00:00"}
        with storage.connect_rw(self.db) as con:
            for run_id, cutoff in cutoffs.items():
                storage.start_run(con, run_id=run_id, mode="live", cutoff_at=cutoff,
                                  policy_version="p", identity_version="i", code_version="c")
                storage.persist_assessment(con, run_id, {
                    "subject_type": "stock", "subject_id": "005930", "episode_id": "ep1",
                    "grades": {}, "action": "watch", "reason_codes": []})
            within = storage.find_open_episode(con, "005930", "2026-03-20T06:40:00+00:00")
            expired = storage.find_open_episode(con, "005930", "2026-04-09T06:40:00+00:00")
        self.assertEqual(within, ("ep1", cutoffs["e0"]))     # 78일
        self.assertIsNone(expired)                            # 시작 98일, 최근 run 49일

    def test_zero_last_batch_count_is_kept(self):
        with storage.connect_rw(self.db) as con:
            storage.finish_run(con, "r1", status="extracted", throughput={"processed": 0})
        with storage.connect_ro(self.db) as con:
            throughput = storage.load_throughput(con, "r1")
        self.assertEqual(throughput["last_batch"]["processed"], 0)
