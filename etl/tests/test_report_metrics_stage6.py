"""Stage 6 — R3 CLI/배치 호출 가능 (PLAN §4 Stage 6).

PDF 파싱은 parse_fn 주입으로 대체한다(파일명 규칙만 실제로 쓴다).
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.report_metrics import storage
from scripts.report_metrics.models import STATUS_NO_TARGET, STATUS_OK, ReportFacts
from scripts.report_metrics.parse import parse_path
from scripts.run_report_metrics import list_report_paths, run

TARGETS = {"k1": 5000, "k2": 6000, "k3": None, "k4": 7000}


def _fake_parse(path):
    meta = parse_path(path)
    target = TARGETS.get(meta["pdf_key"])
    status = STATUS_OK if target else STATUS_NO_TARGET
    return ReportFacts(pdf_path=str(path), target_price=target, price_at_report=4000,
                       parse_status=status, **meta), []


def _boom(path):
    raise RuntimeError("broken pdf")


class RunBatch(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        root = Path(self._tmp.name)
        self.reports = root / "reports"
        self.db = root / "t.sqlite3"
        for rel in ("AJ네트웍스_095570/2026-07-01_신한투자증권_k1.pdf",
                    "AJ네트웍스_095570/2026-08-18_신한투자증권_k2.pdf",
                    "AJ네트웍스_095570/2026-08-18_유안타증권_k3.pdf",
                    "삼성전자_005930/2026-08-01_키움증권_k4.pdf"):
            p = self.reports / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"%PDF")

    def tearDown(self):
        self._tmp.cleanup()

    def _rows(self):
        with storage.connect_ro(self.db) as con:
            return con.execute("SELECT COUNT(*) FROM report_facts").fetchone()[0]

    def test_stats_and_revision_in_date_order(self):
        stats = run(list_report_paths(self.reports), self.db, parse_fn=_fake_parse)
        self.assertEqual((stats["scanned"], stats["parsed"], stats["ok"], stats["no_target"]),
                         (4, 4, 3, 1))
        by_key = {m["pdf_key"]: m for m in stats["metrics"]}
        self.assertEqual(by_key["k1"]["direction"], "new")
        self.assertEqual(by_key["k2"]["direction"], "up")          # 같은 증권사 k1 대비
        self.assertAlmostEqual(by_key["k2"]["change_pct"], 0.2)
        self.assertAlmostEqual(by_key["k2"]["upside_at_report"], 0.5)
        self.assertIsNone(by_key["k2"]["upside_now"])              # as_of 없으면 현재가 안 봄

    def test_rerun_is_idempotent_and_skips_parsed(self):
        paths = list_report_paths(self.reports)
        run(paths, self.db, parse_fn=_fake_parse)
        stats = run(paths, self.db, parse_fn=_boom)                # 재파싱되면 터진다
        self.assertEqual(stats["skipped_existing"], 4)
        self.assertEqual(stats["parsed"], 0)
        self.assertEqual(self._rows(), 4)

    def test_one_failure_does_not_stop_batch(self):
        calls = []

        def flaky(path):
            calls.append(path)
            return _boom(path) if len(calls) == 1 else _fake_parse(path)

        stats = run(list_report_paths(self.reports), self.db, parse_fn=flaky)
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(stats["parsed"], 4)
        with storage.connect_ro(self.db) as con:
            err = con.execute("SELECT parse_error FROM report_facts WHERE parse_status='parse_error'"
                              ).fetchone()[0]
        self.assertIn("broken pdf", err)

    def test_filters(self):
        self.assertEqual(len(list_report_paths(self.reports, stock="095570")), 3)
        self.assertEqual(len(list_report_paths(self.reports, since="2026-08-01")), 3)
        self.assertEqual(len(list_report_paths(self.reports, until="2026-07-31")), 1)
        names = [p.name[:10] for p in list_report_paths(self.reports)]
        self.assertEqual(names, sorted(names))

    def test_limit_counts_new_parses(self):
        stats = run(list_report_paths(self.reports), self.db, parse_fn=_fake_parse, limit=2)
        self.assertEqual(stats["parsed"], 2)

    def test_price_now_used_when_as_of_given(self):
        stats = run(list_report_paths(self.reports), self.db, parse_fn=_fake_parse,
                    as_of="2026-09-09", price_fn=lambda code, as_of, db: 5000)
        k2 = next(m for m in stats["metrics"] if m["pdf_key"] == "k2")
        self.assertAlmostEqual(k2["upside_now"], 0.2)


if __name__ == "__main__":
    unittest.main()
