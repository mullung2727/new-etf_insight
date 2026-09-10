"""Stage 0 — 뼈대 계약 검증 (PLAN §4 Stage 0).

여기서 확정하는 것: 공개 심볼 존재, ReportFacts ↔ report_facts 컬럼 1:1, 스키마 멱등.
"""
import sqlite3
import unittest
from dataclasses import fields
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.report_metrics import metrics, parse, rim, storage
from scripts.report_metrics.models import ReportFacts, RimInputs, YearEstimate


class Stage0Contract(unittest.TestCase):
    def test_public_symbols_exist(self):
        for mod, names in (
            (parse, ["parse_path", "parse_header", "parse_estimates", "extract_text", "parse_report"]),
            (metrics, ["upside", "resolve_price_now", "report_upside", "target_revision"]),
            (rim, ["build_rim_inputs", "rim_value"]),
            (storage, ["init_db", "upsert_facts", "replace_estimates",
                       "find_previous_report", "load_estimates", "connect_rw", "connect_ro"]),
        ):
            for name in names:
                self.assertTrue(callable(getattr(mod, name, None)), f"{mod.__name__}.{name}")

    def test_dataclasses_instantiable(self):
        f = ReportFacts(pdf_key="k", pdf_path="p", stock_code="095570",
                        stock_name="AJ네트웍스", broker="신한투자증권", report_date="2026-08-18")
        self.assertIsNone(f.target_price)
        self.assertEqual(YearEstimate(2026, True).fiscal_year, 2026)
        self.assertEqual(RimInputs(base_equity=100.0).r, 0.08)

    def test_facts_fields_match_table_columns(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite3"
            storage.init_db(db)
            with storage.connect_ro(db) as con:
                cols = [r[1] for r in con.execute("PRAGMA table_info(report_facts)")]
        self.assertEqual([f.name for f in fields(ReportFacts)], cols)

    def test_schema_objects_and_idempotent_init(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite3"
            storage.init_db(db)
            storage.init_db(db)  # 재실행 멱등
            with storage.connect_ro(db) as con:
                names = {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
        self.assertIn("report_facts", names)
        self.assertIn("report_estimates", names)
        self.assertIn("idx_facts_stock_broker_date", names)

    def test_estimates_fk_blocks_orphan_row(self):
        """존재하지 않는 pdf_key 로 추정치만 넣는 우회 경로는 거부돼야 한다."""
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite3"
            storage.init_db(db)
            with storage.connect_rw(db) as con:
                with self.assertRaises(sqlite3.IntegrityError):
                    storage.replace_estimates(con, "no_such_key", [YearEstimate(2026, True)])


if __name__ == "__main__":
    unittest.main()
