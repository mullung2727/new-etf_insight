"""Stage 3 — R2 동일 증권사 직전 리포트 대비 목표가 변화율 (PLAN §4 Stage 3)."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.report_metrics import storage
from scripts.report_metrics.metrics import target_revision
from scripts.report_metrics.models import ReportFacts


def _facts(pdf_key, broker="신한투자증권", report_date="2026-08-18", target=None,
           stock_code="095570", **kw) -> ReportFacts:
    return ReportFacts(pdf_key=pdf_key, pdf_path=f"/{pdf_key}.pdf", stock_code=stock_code,
                       stock_name="AJ네트웍스", broker=broker, report_date=report_date,
                       target_price=target, **kw)


class FindPrevious(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t.sqlite3"
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, *facts):
        with storage.connect_rw(self.db) as con:
            for f in facts:
                storage.upsert_facts(con, f)

    def test_picks_latest_earlier_report(self):
        self._seed(
            _facts("a", report_date="2026-06-01", target=5000),
            _facts("b", report_date="2026-07-01", target=6000),
        )
        with storage.connect_ro(self.db) as con:
            prev = storage.find_previous_report(con, "095570", "신한투자증권", "2026-08-18")
        self.assertEqual(prev["pdf_key"], "b")

    def test_other_broker_is_not_previous(self):
        """다른 증권사 리포트가 직전으로 잡히면 안 된다 (부정 요구 차단)."""
        self._seed(_facts("x", broker="유진투자증권", report_date="2026-07-01", target=9000))
        with storage.connect_ro(self.db) as con:
            self.assertIsNone(
                storage.find_previous_report(con, "095570", "신한투자증권", "2026-08-18"))

    def test_other_stock_is_not_previous(self):
        self._seed(_facts("y", stock_code="005930", report_date="2026-07-01", target=9000))
        with storage.connect_ro(self.db) as con:
            self.assertIsNone(
                storage.find_previous_report(con, "095570", "신한투자증권", "2026-08-18"))

    def test_same_date_is_not_previous(self):
        """같은 날 같은 증권사 리포트는 상·하향 판단 근거가 아니다 (차단)."""
        self._seed(_facts("z", report_date="2026-08-18", target=9000))
        with storage.connect_ro(self.db) as con:
            self.assertIsNone(
                storage.find_previous_report(con, "095570", "신한투자증권", "2026-08-18"))

    def test_upsert_is_idempotent(self):
        f = _facts("a", target=5000)
        self._seed(f, f)
        with storage.connect_ro(self.db) as con:
            n = con.execute("SELECT COUNT(*) FROM report_facts").fetchone()[0]
        self.assertEqual(n, 1)


class TargetRevision(unittest.TestCase):
    def test_up_down_flat(self):
        cases = {6000: "up", 4000: "down", 5000: "flat"}
        for target, direction in cases.items():
            got = target_revision(_facts("c", target=target), {"target_price": 5000,
                                                               "pdf_key": "p",
                                                               "report_date": "2026-07-01"})
            self.assertEqual(got["direction"], direction)
            self.assertAlmostEqual(got["change_pct"], target / 5000 - 1, places=9)
            self.assertEqual(got["prev_target"], 5000)
            self.assertEqual(got["prev_pdf_key"], "p")
            self.assertEqual(got["prev_report_date"], "2026-07-01")

    def test_no_previous(self):
        got = target_revision(_facts("c", target=6000), None)
        self.assertEqual(got["direction"], "new")
        self.assertIsNone(got["change_pct"])
        self.assertIsNone(got["prev_target"])

    def test_previous_had_no_target(self):
        got = target_revision(_facts("c", target=6000),
                              {"target_price": None, "pdf_key": "p", "report_date": "2026-07-01"})
        self.assertEqual(got["direction"], "new_target")
        self.assertIsNone(got["change_pct"])

    def test_current_has_no_target(self):
        got = target_revision(_facts("c", target=None),
                              {"target_price": 5000, "pdf_key": "p", "report_date": "2026-07-01"})
        self.assertEqual(got["direction"], "no_target")
        self.assertIsNone(got["change_pct"])

    def test_printed_prev_mismatch_flag(self):
        prev = {"target_price": 5000, "pdf_key": "p", "report_date": "2026-07-01"}
        same = target_revision(_facts("c", target=6000, prev_target_printed=5000), prev)
        self.assertFalse(same["prev_target_mismatch"])
        diff = target_revision(_facts("c", target=6000, prev_target_printed=4000), prev)
        self.assertTrue(diff["prev_target_mismatch"])
        none = target_revision(_facts("c", target=6000), prev)
        self.assertFalse(none["prev_target_mismatch"])


if __name__ == "__main__":
    unittest.main()
