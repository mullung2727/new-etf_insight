"""2단계 나머지 — 수치 검증·목표가 원인·종목 해석·무효화·직전 문서 비교 (PLAN §20-2).

T07 연결/별도·단위·before=0 → 잘못된 증감률 없음
T08 목표가 유지·이익 상향·prevGoalPrice 오염 → 이익 변화 인식, prevGoalPrice 미사용
T09 Not Rated·문자 종목코드 → 마스터 확인 후 관찰 가능
T12 호평 다수·핵심 계약 취소 → 무효화 우선
"""
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402

from early_signals import compare, metrics, storage  # noqa: E402

MASTER = {"005930": "삼성전자", "0015G0": "그린광학", "900140": "엘브이엠씨홀딩스"}


def metric(name="영업이익", period="2026", basis="연결", before=100, after=120, unit="억원"):
    return {"name": name, "period": period, "basis": basis,
            "before": before, "after": after, "unit": unit}


class MetricValidationTest(unittest.TestCase):
    """T07 — 섞으면 안 되는 것을 섞지 않는다."""

    def test_quarter_and_annual_are_not_comparable(self):
        self.assertTrue(metrics.comparable(metric(period="2026"))[0])
        self.assertTrue(metrics.comparable(metric(period="2026Q2"))[0])
        self.assertEqual(metrics.period_kind("2026")[0], "annual")
        self.assertEqual(metrics.period_kind("2026Q2")[0], "quarter")
        self.assertNotEqual(metrics.period_kind("2026"), metrics.period_kind("2026Q2"))

    def test_unrecognized_period_blocks_calculation(self):
        result = metrics.compute_change(metric(period="내년"))
        self.assertFalse(result["comparable"])
        self.assertIsNone(result["change_pct"])
        self.assertEqual(result["reason"], "period_unrecognized")

    def test_unknown_basis_blocks_calculation(self):
        result = metrics.compute_change(metric(basis=""))
        self.assertFalse(result["comparable"])
        self.assertIsNone(result["change_pct"])

    def test_unknown_unit_blocks_calculation(self):
        result = metrics.compute_change(metric(unit="갑"))
        self.assertFalse(result["comparable"])
        self.assertEqual(result["reason"], "unit_unknown")

    def test_before_zero_gives_direction_not_ratio(self):
        result = metrics.compute_change(metric(before=0, after=50))
        self.assertIsNone(result["change_pct"])
        self.assertEqual(result["direction"], "turned_positive")
        self.assertEqual(result["reason"], "before_zero")

    def test_before_zero_negative_after(self):
        self.assertEqual(metrics.compute_change(metric(before=0, after=-30))["direction"],
                         "turned_negative")

    def test_normal_change_is_computed_by_code(self):
        result = metrics.compute_change(metric(before=100, after=120))
        self.assertAlmostEqual(result["change_pct"], 0.2)
        self.assertEqual(result["direction"], "up")
        self.assertEqual(result["abs_change"], 20)

    def test_missing_before_is_new_value_not_zero(self):
        result = metrics.compute_change(metric(before=None, after=120))
        self.assertEqual(result["direction"], "new_value")
        self.assertIsNone(result["change_pct"])

    def test_validate_collects_issues(self):
        checked, issues = metrics.validate_metrics([metric(), metric(period="내년")])
        self.assertEqual(len(checked), 2)
        self.assertEqual(len(issues), 1)
        self.assertIn("period_unrecognized", issues[0])


class TargetPriceTest(unittest.TestCase):
    """T08 — 목표가가 왜 바뀌었는지 가른다."""

    def test_earnings_revision(self):
        self.assertEqual(metrics.classify_target_price_change(
            [metric(name="목표주가", before=500, after=590),
             metric(name="영업이익", before=100, after=130)]), "earnings_revision")

    def test_multiple_revision(self):
        self.assertEqual(metrics.classify_target_price_change(
            [metric(name="목표주가"), metric(name="PER", unit="배")]), "multiple_revision")

    def test_mixed(self):
        self.assertEqual(metrics.classify_target_price_change(
            [metric(name="목표주가"), metric(name="EPS"), metric(name="PER", unit="배")]), "mixed")

    def test_unexplained_when_only_target_price_moves(self):
        self.assertEqual(metrics.classify_target_price_change(
            [metric(name="목표주가")]), "unexplained")

    def test_target_price_held_but_earnings_up_is_still_a_change(self):
        """목표가 유지여도 이익 추정이 올랐으면 변화다."""
        held = [metric(name="목표주가", before=500, after=500),
                metric(name="영업이익", before=100, after=130)]
        self.assertEqual(metrics.classify_target_price_change(held), "earnings_revision")
        earnings = metrics.compute_change(held[1])
        self.assertAlmostEqual(earnings["change_pct"], 0.3)

    def test_prev_goal_price_is_not_used_as_prior(self):
        """네이버 prevGoalPrice 가 작성시점 주가와 같은 값인 사례가 있어 이전 목표가로 쓰지 않는다.

        이전 값은 직전 문서 비교에서만 온다 — metrics 에 before 가 없으면 new_value 다.
        """
        result = metrics.compute_change(metric(name="목표주가", before=None, after=590))
        self.assertEqual(result["direction"], "new_value")
        self.assertIsNone(result["change_pct"])


class EntityResolutionTest(unittest.TestCase):
    """T09 — 마스터로 확인한 코드만 쓴다."""

    def test_letter_bearing_code_is_accepted(self):
        self.assertEqual(metrics.resolve_entity("0015G0", "그린광학", MASTER),
                         ("0015G0", "resolved"))

    def test_unknown_code_is_unresolved_not_guessed(self):
        code, status = metrics.resolve_entity("999999", "없는회사", MASTER)
        self.assertIsNone(code)
        self.assertEqual(status, "unresolved_entity")

    def test_name_only_resolves_through_master(self):
        self.assertEqual(metrics.resolve_entity("", "삼성전자", MASTER),
                         ("005930", "resolved_by_name"))

    def test_code_wins_over_mismatched_name(self):
        code, status = metrics.resolve_entity("005930", "다른이름", MASTER)
        self.assertEqual(code, "005930")
        self.assertEqual(status, "name_mismatch")

    def test_not_rated_report_still_resolves_entity(self):
        """투자의견 Not Rated 여도 종목 관찰은 가능하다."""
        self.assertEqual(metrics.resolve_entity("005930", "삼성전자", MASTER)[0], "005930")

    def test_empty_input_is_no_entity(self):
        self.assertEqual(metrics.resolve_entity("", "", MASTER), (None, "no_entity"))


class InvalidationTest(unittest.TestCase):
    """T12 — 호평이 많아도 취소가 있으면 취소가 우선이다."""

    def _event(self, change_type, direction):
        return {"change": {"change_type": change_type, "direction": direction}}

    def test_cancellation_detected_among_positives(self):
        events = [self._event("upgraded", "positive")] * 5
        events.append(self._event("cancelled", "negative"))
        self.assertTrue(metrics.has_invalidation(events))

    def test_all_positive_has_no_invalidation(self):
        self.assertFalse(metrics.has_invalidation([self._event("upgraded", "positive")] * 5))

    def test_delay_counts_as_invalidation(self):
        self.assertTrue(metrics.has_invalidation([self._event("delayed", "negative")]))

    def test_positive_cancellation_is_not_invalidation(self):
        """방향이 긍정인 취소(악재 계약 취소 등)는 무효화가 아니다."""
        self.assertFalse(metrics.has_invalidation([self._event("cancelled", "positive")]))


class CompareHistoryTest(unittest.TestCase):
    def _change(self, **kw):
        base = {"change_type": "upgraded", "direction": "positive", "claim": "이익 상향",
                "confirmation_condition": "", "metrics": [metric()]}
        base.update(kw)
        return base

    def test_no_prior_is_baseline_missing_not_new(self):
        result = compare.compare_history(self._change(), [])
        self.assertEqual(result["novelty"], "baseline_missing")
        self.assertEqual(result["prior_documents"], 0)

    def test_same_metric_different_value_is_changed(self):
        priors = [{"changes": [{"claim": "x", "metrics": [metric(after=100)]}]}]
        result = compare.compare_history(self._change(metrics=[metric(after=130)]), priors)
        self.assertEqual(result["novelty"], "changed")
        self.assertIn("100->130", result["reason"])

    def test_same_values_is_repeated(self):
        priors = [{"changes": [{"claim": "x", "metrics": [metric(after=120)]}]}]
        result = compare.compare_history(self._change(metrics=[metric(after=120)]), priors)
        self.assertEqual(result["novelty"], "repeated")

    def test_same_claim_without_metrics_is_repeated(self):
        priors = [{"changes": [{"claim": "이익 상향", "metrics": []}]}]
        result = compare.compare_history(self._change(metrics=[]), priors)
        self.assertEqual(result["novelty"], "repeated")
        self.assertEqual(result["reason"], "same_claim")

    def test_different_period_is_not_matched_as_prior(self):
        """직전 문서의 2026 연간과 지금의 2026Q2 를 같은 항목으로 보지 않는다."""
        priors = [{"changes": [{"claim": "x", "metrics": [metric(period="2026", after=100)]}]}]
        result = compare.compare_history(
            self._change(metrics=[metric(period="2026Q2", after=130)]), priors)
        self.assertEqual(result["novelty"], "new_in_corpus")

    def test_needs_comparison_skips_plain_praise(self):
        praise = {"change_type": "repeated", "metrics": [], "confirmation_condition": ""}
        self.assertFalse(compare.needs_comparison(praise))
        self.assertTrue(compare.needs_comparison(self._change()))

    def test_needs_comparison_on_confirmation_condition(self):
        item = {"change_type": "confirmed", "metrics": [],
                "confirmation_condition": "3분기 양산 공시"}
        self.assertTrue(compare.needs_comparison(item))


class PriorDocumentQueryTest(unittest.TestCase):
    """같은 PDF 재게시를 직전 독립 문서 2건으로 채우지 않는다(§3.2)."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "es.sqlite3"
        storage.ensure_schema(self.db)
        with storage.connect_rw(self.db) as con:
            for index, (sv, group, published) in enumerate([
                ("sv1", "g1", "2026-06-01T00:00:00+00:00"),
                ("sv2", "g1", "2026-06-02T00:00:00+00:00"),   # 같은 원문 재게시
                ("sv3", "g2", "2026-06-03T00:00:00+00:00"),
            ]):
                con.execute(
                    "INSERT INTO source_versions (source_version_id, source_type, source_key,"
                    " content_hash, origin_group_id, independence, published_at,"
                    " first_observed_at, available_at, extracted_text, raw_json, entity_ids_json)"
                    " VALUES (?,?,?,?,?,'known',?,?,?,?,?,?)",
                    (sv, "report", f"005930/{index}", f"h{index}", group, published,
                     published, published, "본문", '{"broker": "미래에셋"}', '["005930"]'))
                con.execute(
                    "INSERT INTO events (event_id, source_version_id, event_fingerprint,"
                    " change_key, extract_version, entity_ids_json, anchor_locators_json,"
                    " change_json, first_detected_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"e{index}", sv, f"f{index}", "k", "ex1", '["005930"]', "[]",
                     '{"claim": "c", "metrics": []}', published))

    def tearDown(self):
        self.tmp.cleanup()

    def test_reposts_collapse_to_one_prior_document(self):
        with storage.connect_ro(self.db) as con:
            priors = compare.load_prior_documents(
                con, "005930", "미래에셋", "2026-06-30T00:00:00+00:00")
        self.assertEqual(len(priors), 2)
        self.assertEqual({p["origin_group_id"] for p in priors}, {"g1", "g2"})

    def test_other_publisher_is_excluded(self):
        with storage.connect_ro(self.db) as con:
            priors = compare.load_prior_documents(
                con, "005930", "삼성증권", "2026-06-30T00:00:00+00:00")
        self.assertEqual(priors, [])


if __name__ == "__main__":
    unittest.main()
