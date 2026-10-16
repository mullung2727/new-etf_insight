"""일일 네이버 종목리포트 배치 — 발행일 저장 + 7일 중첩 + 카탈로그 (설계 §6-3, unittest).

네트워크는 fetch 주입으로 대체. 실제 DB·실제 네트워크 호출 없음.
"""
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.download_naver_research import (
    LIST_URL,
    dest_path,
    list_reports,
    pdf_key,
    run,
)
from scripts.report_metrics import catalog, storage


def _row(rid, code, name, date, broker="X증권", title="t"):
    return {"researchId": rid, "itemCode": code, "itemName": name,
            "writeDate": date, "brokerName": broker, "title": title}


def _list_fetch(pages):
    def fetch(url):
        page = int(url.split("page=")[1].split("&")[0])
        return json.dumps(pages.get(page, [])).encode()
    return fetch


def _detail_fetch(mapping, counter=None):
    def fetch(url):
        if counter is not None:
            counter.append(url)
        rid = url.rsplit("/", 1)[-1]
        return json.dumps({"researchContent": mapping.get(rid, {})}).encode()
    return fetch


def _pdf_fetch(data=None):
    """data None이면 URL별 서로 다른 %PDF 바이트(문서 합침 방지)."""
    def fetch(url):
        if data is not None:
            return data
        return b"%PDF-1.7:" + url.encode()
    return fetch


class _BatchCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.out = tmp / "out"
        self.db = tmp / "t.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def _one(self, sql, args=()):
        with storage.connect_ro(self.db) as con:
            return con.execute(sql, args).fetchone()

    def _count(self, table):
        return self._one(f"SELECT COUNT(*) FROM {table}")[0]


class TestDetailFailureIsolated(_BatchCase):
    """상세 1건 실패가 나머지를 날리지 않고, run 은 partial, 다음 실행에서 재시도(CodeRabbit PR #34)."""

    def test_one_detail_error_keeps_others(self):
        pages = {1: [_row(101, "005930", "삼성전자", "2026-09-23"),
                       _row(102, "000660", "SK하이닉스", "2026-09-22")]}
        details = {"101": {"attachUrl": "http://x/a.pdf"}, "102": {"attachUrl": "http://x/b.pdf"}}
        ok = _detail_fetch(details)

        def flaky(url):
            if url.endswith("/101"):
                raise TimeoutError("timeout")
            return ok(url)

        kw = dict(out_dir=self.out, list_fetch=_list_fetch(pages), pdf_fetch=_pdf_fetch(),
                  sleep_fn=lambda s: None, facts_db=self.db)
        s1 = run("2026-09-23", detail_fetch=flaky, **kw)
        self.assertEqual((s1["failed"], s1["detail_failed"], s1["downloaded"]), (1, 1, 1))
        self.assertEqual(self._one("SELECT status FROM report_collection_runs ORDER BY run_id DESC")[0],
                         "partial")
        s2 = run("2026-09-23", detail_fetch=ok, **kw)
        self.assertEqual((s2["downloaded"], s2["skipped_known"]), (1, 1))
        self.assertEqual(self._one("SELECT status FROM report_collection_runs ORDER BY run_id DESC")[0],
                         "completed")


class TestWriteDateBasis(_BatchCase):
    def test_fact_date_and_filename_follow_write_date(self):
        pages = {1: [_row(101, "005930", "삼성전자", "2026-09-23"),
                       _row(102, "000660", "SK하이닉스", "2026-09-20"),
                       _row(100, "000001", "x", "2026-09-10")]}
        details = {"101": {"attachUrl": "http://x/a.pdf", "goalPrice": "90000"},
                   "102": {"attachUrl": "http://x/b.pdf", "goalPrice": "300000"}}
        stats = run("2026-09-23", out_dir=self.out, list_fetch=_list_fetch(pages),
                    detail_fetch=_detail_fetch(details), pdf_fetch=_pdf_fetch(),
                    sleep_fn=lambda s: None, facts_db=self.db)
        self.assertEqual(stats["listed"], 2)
        got = self._one("SELECT report_date FROM report_api_facts WHERE research_id = '102'")
        self.assertEqual(got[0], "2026-09-20")
        saved = list((self.out / "SK하이닉스_000660").glob("2026-09-20_*.pdf"))
        self.assertEqual(len(saved), 1)


class TestLookbackWindow(_BatchCase):
    def test_seven_day_window(self):
        # 9/16(=23-7)은 7일 창 [9/17, 9/23] 밖, 9/17은 안
        pages = {1: [_row(201, "005930", "삼성전자", "2026-09-17"),
                       _row(200, "000660", "SK하이닉스", "2026-09-16"),
                       _row(199, "000001", "x", "2026-09-10")]}
        details = {"201": {"attachUrl": "http://x/c.pdf"},
                   "200": {"attachUrl": "http://x/d.pdf"}}
        stats = run("2026-09-23", out_dir=self.out, list_fetch=_list_fetch(pages),
                    detail_fetch=_detail_fetch(details), pdf_fetch=_pdf_fetch(),
                    sleep_fn=lambda s: None, facts_db=self.db)
        self.assertEqual(stats["listed"], 1)
        self.assertEqual(stats["stop_reason"], "past_range")
        with storage.connect_ro(self.db) as con:
            rids = [r[0] for r in con.execute("SELECT research_id FROM report_api_facts")]
        self.assertEqual(rids, ["201"])
        self.assertEqual(self._count("report_documents"), 1)


class TestRerunIdempotent(_BatchCase):
    def test_second_run_skips_known_without_detail_fetch(self):
        pages = {1: [_row(301, "005930", "삼성전자", "2026-09-23"),
                       _row(300, "000001", "x", "2026-09-10")]}
        details = {"301": {"attachUrl": "http://x/e.pdf", "goalPrice": "90000"}}
        kw = dict(out_dir=self.out, list_fetch=_list_fetch(pages),
                  pdf_fetch=_pdf_fetch(), sleep_fn=lambda s: None, facts_db=self.db)
        c1 = []
        s1 = run("2026-09-23", detail_fetch=_detail_fetch(details, c1), **kw)
        self.assertEqual(s1["downloaded"], 1)
        n1 = self._count("report_documents")
        c2 = []
        s2 = run("2026-09-23", detail_fetch=_detail_fetch(details, c2), **kw)
        self.assertEqual(s2["listed"], 1)
        self.assertEqual(s2["downloaded"], 0)
        self.assertEqual(s2["skipped_known"], s2["listed"])
        self.assertEqual(c2, [])
        self.assertEqual(self._count("report_documents"), n1)


class TestMaxPages(_BatchCase):
    def test_list_unit_reports_max_pages(self):
        pages = {1: [_row(401, "005930", "삼성전자", "2026-09-23"),
                       _row(402, "000660", "SK하이닉스", "2026-09-22")],
                 2: [_row(403, "005930", "삼성전자", "2026-09-21")],
                 3: [_row(404, "005930", "삼성전자", "2026-09-20")]}
        out, meta = list_reports("2026-09-17", "2026-09-23",
                                 fetch_fn=_list_fetch(pages), max_pages=2)
        self.assertEqual(meta["stop_reason"], "max_pages")
        self.assertEqual(meta["pages_fetched"], 2)
        self.assertEqual(meta["last_page"], 2)
        self.assertEqual(meta["rows_listed"], 3)
        self.assertEqual(len(out), 3)

    def test_run_marks_partial(self):
        pages = {p: [_row(500 + p, "005930", "삼성전자", "2026-09-23")]
                 for p in range(1, 21)}
        details = {str(500 + p): {"attachUrl": f"http://x/f{p}.pdf"}
                   for p in range(1, 21)}
        stats = run("2026-09-23", out_dir=self.out, list_fetch=_list_fetch(pages),
                    detail_fetch=_detail_fetch(details), pdf_fetch=_pdf_fetch(),
                    sleep_fn=lambda s: None, facts_db=self.db)
        self.assertEqual(stats["listed"], 20)
        self.assertEqual(stats["stop_reason"], "max_pages")
        row = self._one("SELECT status, pages_fetched FROM report_collection_runs")
        self.assertEqual(tuple(row), ("partial", 20))


class TestNotPdfRetry(_BatchCase):
    def test_retry_promotes_same_document_to_saved(self):
        pages = {1: [_row(601, "005930", "삼성전자", "2026-09-23"),
                       _row(600, "000001", "x", "2026-09-10")]}
        details = {"601": {"attachUrl": "http://x/g.pdf", "goalPrice": "90000"}}
        kw = dict(out_dir=self.out, list_fetch=_list_fetch(pages),
                  sleep_fn=lambda s: None, facts_db=self.db)
        c1 = []
        s1 = run("2026-09-23", detail_fetch=_detail_fetch(details, c1),
                 pdf_fetch=_pdf_fetch(b"<html>"), **kw)
        self.assertEqual(s1["not_pdf"], 1)
        self.assertEqual(s1["downloaded"], 0)
        doc1 = self._one("SELECT document_id, file_status, pdf_path, sha256 FROM report_documents")
        self.assertEqual(doc1[1], "not_pdf")
        self.assertIsNone(doc1[2])
        self.assertIsNone(doc1[3])
        c2 = []
        s2 = run("2026-09-23", detail_fetch=_detail_fetch(details, c2),
                 pdf_fetch=_pdf_fetch(b"%PDF-x"), **kw)
        self.assertEqual(s2["downloaded"], 1)
        self.assertEqual(s2["catalog_dup"], 1)
        self.assertEqual(s2["catalog_new"], 0)
        self.assertTrue(c2)  # 상세 재조회 발생
        doc2 = self._one("SELECT document_id, file_status, pdf_path, sha256 FROM report_documents")
        self.assertEqual(self._count("report_documents"), 1)
        self.assertEqual(doc2[0], doc1[0])
        self.assertEqual(doc2[1], "saved")
        self.assertTrue(doc2[2].endswith(".pdf"))
        self.assertEqual(doc2[3], hashlib.sha256(b"%PDF-x").hexdigest())


class TestNoPdf(_BatchCase):
    def test_missing_attach_url_creates_no_document(self):
        pages = {1: [_row(701, "005930", "삼성전자", "2026-09-23"),
                       _row(700, "000001", "x", "2026-09-10")]}
        stats = run("2026-09-23", out_dir=self.out, list_fetch=_list_fetch(pages),
                    detail_fetch=_detail_fetch({}), pdf_fetch=_pdf_fetch(),
                    sleep_fn=lambda s: None, facts_db=self.db)
        self.assertEqual(stats["no_pdf"], 1)
        self.assertEqual(stats["facts_saved"], 0)
        self.assertEqual(self._count("report_documents"), 0)
        self.assertEqual(self._count("report_api_facts"), 0)


class TestCatalogLinks(_BatchCase):
    def test_source_stock_and_run_rows(self):
        pages = {1: [_row(801, "005930", "삼성전자", "2026-09-23"),
                       _row(800, "000001", "x", "2026-09-10")]}
        details = {"801": {"attachUrl": "http://x/h.pdf", "goalPrice": "90000"}}
        run("2026-09-23", out_dir=self.out, list_fetch=_list_fetch(pages),
            detail_fetch=_detail_fetch(details), pdf_fetch=_pdf_fetch(),
            sleep_fn=lambda s: None, facts_db=self.db)
        doc = self._one("SELECT document_id FROM report_documents")[0]
        src = self._one("SELECT source, source_report_id, document_id, list_url, "
                        "detail_url, pdf_url FROM report_sources")
        self.assertEqual(src[0], "naver_mobile")
        self.assertEqual(src[1], "company:801")
        self.assertEqual(src[2], doc)
        self.assertEqual(src[3], LIST_URL.format(page=1, size=100))
        self.assertIn("801", src[4])
        self.assertEqual(src[5], "http://x/h.pdf")
        link = self._one("SELECT stock_code, relation_type, method FROM report_document_stocks")
        self.assertEqual(tuple(link), ("005930", "primary", "naver_itemcode"))
        runrow = self._one("SELECT source, category, date_from, date_to, status "
                           "FROM report_collection_runs")
        self.assertEqual(tuple(runrow),
                         ("naver_mobile", "company", "2026-09-17", "2026-09-23", "completed"))
        self.assertEqual(self._count("report_collection_runs"), 1)


class TestLegacyMerge(_BatchCase):
    def test_existing_file_and_legacy_doc_dedup(self):
        url = "http://x/i.pdf"
        dest = dest_path(self.out, "삼성전자", "005930", "2026-09-23", "X증권", pdf_key(url))
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob = b"%PDF-legacy"
        dest.write_bytes(blob)
        with storage.connect_rw(self.db) as con:
            catalog.init_catalog(con)
            legacy, created = catalog.upsert_document(
                con, document_type="company", title="legacy", broker="X증권",
                published_date="2026-09-23",
                pdf_path=f"stock_reports/{dest.parent.name}/{dest.name}",
                sha256=hashlib.sha256(blob).hexdigest(),
                pdf_key="other_key", file_status="saved")
            self.assertTrue(created)
        pages = {1: [_row(901, "005930", "삼성전자", "2026-09-23"),
                       _row(900, "000001", "x", "2026-09-10")]}
        details = {"901": {"attachUrl": url, "goalPrice": "90000"}}

        def boom_pdf(u):
            raise AssertionError(f"호출되면 안 됨: {u}")

        stats = run("2026-09-23", out_dir=self.out, list_fetch=_list_fetch(pages),
                    detail_fetch=_detail_fetch(details), pdf_fetch=boom_pdf,
                    sleep_fn=lambda s: None, facts_db=self.db)
        self.assertEqual(stats["skipped_exists"], 1)
        self.assertEqual(stats["catalog_dup"], 1)
        self.assertEqual(stats["catalog_new"], 0)
        self.assertEqual(self._count("report_documents"), 1)
        src = self._one("SELECT document_id FROM report_sources "
                        "WHERE source = 'naver_mobile' AND source_report_id = 'company:901'")
        self.assertEqual(src[0], legacy)


if __name__ == "__main__":
    unittest.main()
