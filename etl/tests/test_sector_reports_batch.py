"""네이버 산업·시황·투자전략·경제 리포트 수집 — 수집만 (설계 §6-4, unittest).

종목 연결 판정(§8-6)은 범위 밖: report_document_stocks에 쓰지 않는다.
네트워크는 fetch 주입으로 대체. 실제 DB·실제 네트워크 호출 없음.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.download_naver_research import (
    list_reports,
    run_sector,
)
from scripts.report_metrics import storage


def _row(rid, date, broker="X증권", title="t", cat="반도체"):
    # 섹터 목록 행: itemCode 없음(실측)
    return {"researchId": rid, "category": cat, "writeDate": date,
            "brokerName": broker, "title": title}


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


class _SectorCase(unittest.TestCase):
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


class TestSectorCollect(_SectorCase):
    def test_industry_two_docs(self):
        pages = {1: [_row(46255, "2026-09-29", broker="A증권", title="반도체 t1"),
                       _row(46254, "2026-09-28", broker="B증권", title="반도체 t2"),
                       _row(46000, "2026-09-10")]}
        details = {"46255": {"attachUrl": "https://stock.pstatic.net/stock-research/industry/66/20260929_industry_638804000.pdf"},
                   "46254": {"attachUrl": "https://stock.pstatic.net/stock-research/industry/66/20260928_industry_638804001.pdf"}}
        stats = run_sector("2026-09-29", "industry", out_dir=self.out,
                           list_fetch=_list_fetch(pages),
                           detail_fetch=_detail_fetch(details),
                           pdf_fetch=_pdf_fetch(),
                           sleep_fn=lambda s: None, facts_db=self.db)
        self.assertEqual(stats["listed"], 2)
        self.assertEqual(stats["downloaded"], 2)
        self.assertEqual(stats["facts_saved"], 0)
        with storage.connect_ro(self.db) as con:
            docs = con.execute(
                "SELECT document_type, pdf_path FROM report_documents").fetchall()
        self.assertEqual(len(docs), 2)
        for dtype, pdf_path in docs:
            self.assertEqual(dtype, "industry")
            self.assertTrue(pdf_path.startswith("sector_reports/industry/"))
        saved = sorted((self.out / "industry").glob("2026-09-2*_*.pdf"))
        self.assertEqual(len(saved), 2)
        self.assertTrue(saved[0].name.startswith("2026-09-28_"))
        self.assertTrue(saved[1].name.startswith("2026-09-29_"))

    def test_analysis_boundary_no_fact_or_stock_rows(self):
        pages = {1: [_row(46255, "2026-09-29"),
                       _row(46000, "2026-09-10")]}
        details = {"46255": {"attachUrl": "http://x/sector.pdf"}}
        run_sector("2026-09-29", "industry", out_dir=self.out,
                   list_fetch=_list_fetch(pages),
                   detail_fetch=_detail_fetch(details),
                   pdf_fetch=_pdf_fetch(),
                   sleep_fn=lambda s: None, facts_db=self.db)
        self.assertEqual(self._count("report_documents"), 1)
        self.assertEqual(self._count("report_api_facts"), 0)
        self.assertEqual(self._count("report_document_stocks"), 0)


class TestCategoryScopedIds(_SectorCase):
    def test_same_research_id_coexists_across_categories(self):
        ind_pages = {1: [_row(46255, "2026-09-29"),
                          _row(46000, "2026-09-10")]}
        ind_details = {"46255": {"attachUrl": "http://x/industry_46255.pdf"}}
        mkt_pages = {1: [_row(46255, "2026-09-29"),
                          _row(46000, "2026-09-10")]}
        mkt_details = {"46255": {"attachUrl": "http://x/market_46255.pdf"}}
        kw = dict(out_dir=self.out, pdf_fetch=_pdf_fetch(),
                  sleep_fn=lambda s: None, facts_db=self.db)
        run_sector("2026-09-29", "industry", list_fetch=_list_fetch(ind_pages),
                   detail_fetch=_detail_fetch(ind_details), **kw)
        run_sector("2026-09-29", "market", list_fetch=_list_fetch(mkt_pages),
                   detail_fetch=_detail_fetch(mkt_details), **kw)
        with storage.connect_ro(self.db) as con:
            srcs = con.execute(
                "SELECT source_report_id, document_id FROM report_sources "
                "ORDER BY source_report_id").fetchall()
            dtypes = sorted(
                r[0] for r in con.execute("SELECT document_type FROM report_documents"))
        self.assertEqual([s[0] for s in srcs], ["industry:46255", "market:46255"])
        self.assertNotEqual(srcs[0][1], srcs[1][1])  # 서로 다른 문서
        self.assertEqual(dtypes, ["industry", "market"])


class TestSectorRerun(_SectorCase):
    def test_second_run_skips_known_without_detail_fetch(self):
        pages = {1: [_row(46255, "2026-09-29"),
                       _row(46254, "2026-09-28"),
                       _row(46000, "2026-09-10")]}
        details = {"46255": {"attachUrl": "http://x/s1.pdf"},
                   "46254": {"attachUrl": "http://x/s2.pdf"}}
        kw = dict(out_dir=self.out, list_fetch=_list_fetch(pages),
                  pdf_fetch=_pdf_fetch(), sleep_fn=lambda s: None, facts_db=self.db)
        c1 = []
        s1 = run_sector("2026-09-29", "industry",
                        detail_fetch=_detail_fetch(details, c1), **kw)
        self.assertEqual(s1["downloaded"], 2)
        n1 = self._count("report_documents")
        c2 = []
        s2 = run_sector("2026-09-29", "industry",
                        detail_fetch=_detail_fetch(details, c2), **kw)
        self.assertEqual(s2["listed"], 2)
        self.assertEqual(s2["downloaded"], 0)
        self.assertEqual(s2["skipped_known"], 2)
        self.assertEqual(c2, [])
        self.assertEqual(self._count("report_documents"), n1)


class TestSectorList(unittest.TestCase):
    def test_codeless_rows_kept_for_sector_dropped_for_company(self):
        pages = {1: [{"researchId": 1, "writeDate": "2026-09-29",
                       "brokerName": "A", "title": "t1"},  # itemCode 키 자체 없음
                      {"researchId": 2, "itemCode": "", "writeDate": "2026-09-29",
                       "brokerName": "B", "title": "t2"},
                      {"researchId": 3, "itemCode": "005930", "itemName": "삼성전자",
                       "writeDate": "2026-09-29", "brokerName": "C", "title": "t3"},
                      {"researchId": 0, "writeDate": "2026-09-01"}]}
        out, meta = list_reports("2026-09-23", "2026-09-29",
                                 fetch_fn=_list_fetch(pages), category="market")
        self.assertEqual([r["researchId"] for r in out], [1, 2, 3])
        self.assertEqual(out[0]["itemCode"], "")
        self.assertEqual(out[0]["itemName"], "")
        self.assertEqual(meta["stop_reason"], "past_range")
        out_c, _ = list_reports("2026-09-23", "2026-09-29",
                                fetch_fn=_list_fetch(pages), category="company")
        self.assertEqual([r["researchId"] for r in out_c], [3])
        out_d, _ = list_reports("2026-09-23", "2026-09-29",
                                fetch_fn=_list_fetch(pages))
        self.assertEqual([r["researchId"] for r in out_d], [3])  # 기본값 company


class TestSectorCategory(_SectorCase):
    def test_invalid_category_raises(self):
        with self.assertRaises(ValueError):
            run_sector("2026-09-29", "debenture", out_dir=self.out,
                       facts_db=self.db)


class TestSectorRunRow(_SectorCase):
    def test_run_row_completed(self):
        pages = {1: [_row(46255, "2026-09-29"),
                       _row(46000, "2026-09-10")]}
        details = {"46255": {"attachUrl": "http://x/s3.pdf"}}
        run_sector("2026-09-29", "industry", out_dir=self.out,
                   list_fetch=_list_fetch(pages),
                   detail_fetch=_detail_fetch(details),
                   pdf_fetch=_pdf_fetch(),
                   sleep_fn=lambda s: None, facts_db=self.db)
        row = self._one("SELECT category, status FROM report_collection_runs")
        self.assertEqual(tuple(row), ("industry", "completed"))


if __name__ == "__main__":
    unittest.main()
