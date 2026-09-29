"""report_documents 카탈로그 + legacy 편입 테스트 (설계 §6-2, unittest)."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts import migrate_report_catalog as mig
from scripts.report_metrics import catalog, storage

_TABLES = ("report_documents", "report_sources",
           "report_document_stocks", "report_collection_runs")


def _doc_kwargs(**kw):
    base = dict(document_type="company", title="T", broker="B",
                published_date="2026-09-01", pdf_path="p.pdf",
                sha256="s", pdf_key="k", file_status="saved")
    base.update(kw)
    return base


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def test_init_idempotent(self):
        with storage.connect_rw(self.db) as con:
            catalog.init_catalog(con)
            catalog.init_catalog(con)
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in _TABLES:
            self.assertIn(t, tables)

    def test_upsert_document_dedup(self):
        with storage.connect_rw(self.db) as con:
            catalog.init_catalog(con)
            id1, c1 = catalog.upsert_document(con, **_doc_kwargs(
                title="T1", broker="B1", pdf_path="a.pdf", sha256="s1", pdf_key="k1"))
            self.assertTrue(c1)
            id2, c2 = catalog.upsert_document(con, **_doc_kwargs(
                title="T2", broker="B2", published_date="2026-09-02",
                pdf_path="b.pdf", sha256="s1", pdf_key="k2"))
            self.assertFalse(c2)
            self.assertEqual(id1, id2)
            row = con.execute(
                "SELECT title, broker, published_date, pdf_path, pdf_key "
                "FROM report_documents WHERE document_id=?", (id1,)).fetchone()
            self.assertEqual(tuple(row), ("T1", "B1", "2026-09-01", "a.pdf", "k1"))
            id3, c3 = catalog.upsert_document(con, **_doc_kwargs(
                title="T3", sha256="s9", pdf_key="k1"))
            self.assertFalse(c3)
            self.assertEqual(id1, id3)

    def test_upsert_document_title_fill(self):
        with storage.connect_rw(self.db) as con:
            catalog.init_catalog(con)
            id1, _ = catalog.upsert_document(con, **_doc_kwargs(
                title=None, sha256="s1", pdf_key="k1"))
            catalog.upsert_document(con, **_doc_kwargs(
                title="New", sha256="s1", pdf_key="k1"))
            self.assertEqual(con.execute(
                "SELECT title FROM report_documents WHERE document_id=?",
                (id1,)).fetchone()[0], "New")
            id2, _ = catalog.upsert_document(con, **_doc_kwargs(
                title="", sha256="s2", pdf_key="k2", pdf_path="b.pdf"))
            catalog.upsert_document(con, **_doc_kwargs(
                title="Filled", sha256="s2", pdf_key="k2"))
            self.assertEqual(con.execute(
                "SELECT title FROM report_documents WHERE document_id=?",
                (id2,)).fetchone()[0], "Filled")
            id3, _ = catalog.upsert_document(con, **_doc_kwargs(
                title="Old", sha256="s3", pdf_key="k3", pdf_path="c.pdf"))
            catalog.upsert_document(con, **_doc_kwargs(
                title="New2", sha256="s3", pdf_key="k3"))
            self.assertEqual(con.execute(
                "SELECT title FROM report_documents WHERE document_id=?",
                (id3,)).fetchone()[0], "Old")

    def test_upsert_source(self):
        n1, n2 = "2026-01-01T00:00:00+00:00", "2026-02-02T00:00:00+00:00"
        with storage.connect_rw(self.db) as con:
            catalog.init_catalog(con)
            doc, _ = catalog.upsert_document(con, **_doc_kwargs())
            self.assertTrue(catalog.upsert_source(
                con, source="naver_mobile", source_report_id="company:1",
                document_id=doc, now=n1))
            row = con.execute(
                "SELECT first_seen_at, last_seen_at, document_id FROM report_sources "
                "WHERE source='naver_mobile' AND source_report_id='company:1'").fetchone()
            self.assertEqual(tuple(row), (n1, n1, doc))
            doc2, _ = catalog.upsert_document(con, **_doc_kwargs(
                sha256="s2", pdf_key="k2", pdf_path="b.pdf"))
            self.assertFalse(catalog.upsert_source(
                con, source="naver_mobile", source_report_id="company:1",
                document_id=doc2, now=n2))
            row = con.execute(
                "SELECT first_seen_at, last_seen_at, document_id FROM report_sources "
                "WHERE source='naver_mobile' AND source_report_id='company:1'").fetchone()
            self.assertEqual(tuple(row), (n1, n2, doc))

    def test_document_stock(self):
        with storage.connect_rw(self.db) as con:
            catalog.init_catalog(con)
            doc, _ = catalog.upsert_document(con, **_doc_kwargs())
            catalog.upsert_document_stock(
                con, document_id=doc, stock_code="012345",
                relation_type="primary", method="naver_itemcode")
            row = con.execute(
                "SELECT stock_code, review_status FROM report_document_stocks "
                "WHERE document_id=?", (doc,)).fetchone()
            self.assertEqual(tuple(row), ("012345", "auto"))
            with self.assertRaises(ValueError):
                catalog.upsert_document_stock(
                    con, document_id=doc, stock_code="12345",
                    relation_type="primary", method="x")
            with self.assertRaises(ValueError):
                catalog.upsert_document_stock(
                    con, document_id=doc, stock_code=123456,
                    relation_type="primary", method="x")
            catalog.upsert_document_stock(
                con, document_id=doc, stock_code="012345",
                relation_type="mention", method="rule_v1")
            self.assertEqual(tuple(con.execute(
                "SELECT relation_type, method FROM report_document_stocks "
                "WHERE document_id=?", (doc,)).fetchone()), ("mention", "rule_v1"))
            con.execute(
                "UPDATE report_document_stocks SET review_status='confirmed', "
                "relation_type='primary' WHERE document_id=?", (doc,))
            catalog.upsert_document_stock(
                con, document_id=doc, stock_code="012345",
                relation_type="mention", method="rule_v1")
            self.assertEqual(con.execute(
                "SELECT relation_type FROM report_document_stocks WHERE document_id=?",
                (doc,)).fetchone()[0], "primary")


class MigrateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.db = tmp / "t.sqlite3"
        self.exp = tmp / "exports" / "stock_reports"
        files = {
            "피에스케이_319660/2026-08-18_신한투자증권_20260818_company_950573000.pdf": b"%PDF-a",
            "피에스케이홀딩스_031980/2026-08-18_X증권_k1.pdf": b"%PDF-b",
            "피에스케이_319660/bad.pdf": b"%PDF-bad",
            "피에스케이_319660/2026-09-01_Y증권_k2.pdf": b"<html>",
            "notes/readme.pdf": b"%PDF-x",
        }
        for rel, data in files.items():
            p = self.exp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        storage.init_db(self.db)
        with storage.connect_rw(self.db) as con:
            storage.upsert_api_fact(con, {
                "pdf_key": "20260818_company_950573000", "research_id": "r1",
                "stock_code": "319660", "stock_name": "피에스케이",
                "broker": "신한투자증권", "report_date": "2026-08-18",
                "title": "제목A",
            })
            catalog.init_catalog(con)

    def tearDown(self):
        self._tmp.cleanup()

    def test_migrate(self):
        with storage.connect_rw(self.db) as con:
            api_before = con.execute("SELECT COUNT(*) FROM report_api_facts").fetchone()[0]
            stats = mig.migrate(self.exp, con)
            self.assertEqual(stats, {"scanned": 3, "inserted_docs": 3, "dup_docs": 0,
                                     "skipped_known": 0, "skipped_bad_name": 1,
                                     "not_pdf": 1})
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM report_documents").fetchone()[0], 3)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM report_sources").fetchone()[0], 3)
            row = con.execute(
                "SELECT document_id, title, pdf_path, file_status, sha256 "
                "FROM report_documents WHERE pdf_key=?",
                ("20260818_company_950573000",)).fetchone()
            self.assertEqual(row[1], "제목A")
            self.assertEqual(
                row[2], "stock_reports/피에스케이_319660/"
                        "2026-08-18_신한투자증권_20260818_company_950573000.pdf")
            self.assertEqual(row[3], "saved")
            links = con.execute(
                "SELECT stock_code, relation_type FROM report_document_stocks "
                "WHERE document_id=?", (row[0],)).fetchall()
            self.assertEqual([(r[0], r[1]) for r in links], [("319660", "primary")])
            doc_k1 = con.execute(
                "SELECT document_id FROM report_documents WHERE pdf_key='k1'").fetchone()[0]
            links1 = con.execute(
                "SELECT stock_code FROM report_document_stocks WHERE document_id=?",
                (doc_k1,)).fetchall()
            self.assertEqual([r[0] for r in links1], ["031980"])
            row_k2 = con.execute(
                "SELECT file_status, sha256 FROM report_documents WHERE pdf_key='k2'").fetchone()
            self.assertEqual(row_k2[0], "not_pdf")
            self.assertEqual(row_k2[1], hashlib.sha256(b"<html>").hexdigest())
            stats2 = mig.migrate(self.exp, con)
            self.assertEqual(stats2["inserted_docs"], 0)
            self.assertEqual(stats2["skipped_known"], 3)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM report_documents").fetchone()[0], 3)
            dup = self.exp / "피에스케이_319660/2026-08-20_Z증권_kdup.pdf"
            dup.write_bytes(b"%PDF-a")
            stats3 = mig.migrate(self.exp, con)
            self.assertEqual(stats3["dup_docs"], 1)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM report_documents").fetchone()[0], 3)
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM report_sources").fetchone()[0], 4)
            api_after = con.execute("SELECT COUNT(*) FROM report_api_facts").fetchone()[0]
            self.assertEqual(api_before, api_after)
            self.assertEqual(api_after, 1)


if __name__ == "__main__":
    unittest.main()
