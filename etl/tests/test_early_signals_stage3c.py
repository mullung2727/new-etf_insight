"""3단계 — 테마 형성과 후보 수명주기 (PLAN §8, §11).

T17 같은 단어·다른 이익 메커니즘 → 테마 병합 거부
T20 사건+7일·근거30일·episode84일 → 강등/종료 실행
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402

from early_signals import themes  # noqa: E402

CUTOFF = "2026-07-01"


def event(mechanism, entity="005930", published="2026-06-20", group="g1", event_id="e1"):
    return {"event_id": event_id, "published_at": published + "T00:00:00+00:00",
            "origin_group_id": group, "entity_ids": [entity],
            "change": {"mechanism": mechanism, "claim": "", "category_raw": ""}}


class ThemeLinkTest(unittest.TestCase):
    def test_t17_same_word_different_mechanism_is_not_merged(self):
        """'AI' 라는 단어가 같아도 이익 연결이 다르면 같은 테마가 아니다."""
        events = {
            "005930": [event("AI 데이터센터 향 메모리 출하 증가", entity="005930")],
            "035420": [event("AI 광고 타게팅 개선으로 광고 단가 상승", entity="035420",
                             group="g2", event_id="e2")],
        }
        theme = {"theme_id": "t1", "name": "AI", "mechanism": "AI 데이터센터 향 메모리 출하 증가",
                 "entity_ids": ["005930", "035420"]}
        result = themes.validate_theme_links(theme, events, CUTOFF)
        self.assertEqual([l["entity_id"] for l in result["linked"]], ["005930"])
        self.assertEqual(result["dropped_entities"], ["035420"])
        self.assertEqual(result["status"], "single_company")

    def test_entity_without_supporting_evidence_is_dropped(self):
        theme = {"theme_id": "t1", "mechanism": "HBM 가격 상승",
                 "entity_ids": ["005930", "000660"]}
        events = {"005930": [event("HBM 가격 상승")]}
        result = themes.validate_theme_links(theme, events, CUTOFF)
        self.assertEqual(result["dropped_entities"], ["000660"])

    def test_stale_evidence_outside_30_days_does_not_link(self):
        theme = {"theme_id": "t1", "mechanism": "HBM 가격 상승", "entity_ids": ["005930"]}
        events = {"005930": [event("HBM 가격 상승", published="2026-05-01")]}
        result = themes.validate_theme_links(theme, events, CUTOFF)
        self.assertEqual(result["linked"], [])
        self.assertEqual(result["status"], "inactive")

    def test_preferred_share_does_not_inflate_company_count(self):
        """같은 기업의 다른 주식코드로 기업 수를 늘리지 않는다."""
        theme = {"theme_id": "t1", "mechanism": "HBM 가격 상승",
                 "entity_ids": ["005930", "005935"]}
        events = {
            "005930": [event("HBM 가격 상승", entity="005930")],
            "005935": [event("HBM 가격 상승", entity="005935", group="g2", event_id="e2")],
        }
        result = themes.validate_theme_links(theme, events, CUTOFF)
        self.assertEqual(result["ticker_count"], 2)
        self.assertEqual(result["company_count"], 1)      # 회사 수는 1
        self.assertEqual(result["status"], "single_company")


class ThemeStatusTest(unittest.TestCase):
    def test_forming_needs_two_companies(self):
        self.assertEqual(themes.theme_status(2, 1, [{"x": 1}]), "forming")

    def test_supported_needs_three_companies_and_two_groups(self):
        self.assertEqual(themes.theme_status(3, 2, [{"x": 1}]), "supported")
        self.assertEqual(themes.theme_status(3, 1, [{"x": 1}]), "forming")

    def test_no_link_is_inactive(self):
        self.assertEqual(themes.theme_status(0, 0, []), "inactive")

    def test_counterevidence_weakens_supported(self):
        self.assertEqual(themes.apply_counterevidence("supported", True), "weakening")
        self.assertEqual(themes.apply_counterevidence("supported", False), "supported")
        self.assertEqual(themes.apply_counterevidence("single_company", True), "single_company")


class LifecycleTest(unittest.TestCase):
    """T20 — 기한이 지나면 코드가 강등한다."""

    def test_confirmation_overdue_releases_timing_high(self):
        result = themes.lifecycle(
            cutoff_date=CUTOFF, latest_evidence_date="2026-06-25",
            expected_end="2026-06-20", episode_started="2026-06-01",
            has_open_condition=True)
        self.assertIn("confirmation_overdue", result["flags"])
        self.assertTrue(result["timing_hold"])
        self.assertTrue(result["block_review_buy"])

    def test_within_grace_period_is_not_overdue(self):
        result = themes.lifecycle(
            cutoff_date=CUTOFF, latest_evidence_date="2026-06-25",
            expected_end="2026-06-28", episode_started="2026-06-01",
            has_open_condition=True)
        self.assertNotIn("confirmation_overdue", result["flags"])

    def test_evidence_older_than_30_days_is_stale(self):
        result = themes.lifecycle(
            cutoff_date=CUTOFF, latest_evidence_date="2026-05-20",
            expected_end=None, episode_started="2026-05-20", has_open_condition=True)
        self.assertIn("stale_evidence", result["flags"])
        self.assertTrue(result["block_review_buy"])

    def test_60_days_without_open_condition_is_inactive(self):
        result = themes.lifecycle(
            cutoff_date=CUTOFF, latest_evidence_date="2026-04-01",
            expected_end=None, episode_started="2026-04-01", has_open_condition=False)
        self.assertIn("inactive", result["flags"])

    def test_open_condition_prevents_inactive(self):
        result = themes.lifecycle(
            cutoff_date=CUTOFF, latest_evidence_date="2026-04-01",
            expected_end=None, episode_started="2026-04-01", has_open_condition=True)
        self.assertNotIn("inactive", result["flags"])

    def test_episode_expires_after_84_days(self):
        result = themes.lifecycle(
            cutoff_date=CUTOFF, latest_evidence_date="2026-06-30",
            expected_end=None, episode_started="2026-03-01", has_open_condition=True)
        self.assertIn("episode_expired", result["flags"])
        self.assertTrue(result["episode_over"])

    def test_fresh_candidate_has_no_flags(self):
        result = themes.lifecycle(
            cutoff_date=CUTOFF, latest_evidence_date="2026-06-30",
            expected_end="2026-08-15", episode_started="2026-06-25",
            has_open_condition=True)
        self.assertEqual(result["flags"], [])
        self.assertFalse(result["block_review_buy"])

    def test_month_precision_expected_end_is_accepted(self):
        result = themes.lifecycle(
            cutoff_date=CUTOFF, latest_evidence_date="2026-06-30",
            expected_end="2026-05", episode_started="2026-06-01",
            has_open_condition=True)
        self.assertIn("confirmation_overdue", result["flags"])


if __name__ == "__main__":
    unittest.main()
