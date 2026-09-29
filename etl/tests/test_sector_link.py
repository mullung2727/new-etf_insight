"""섹터·시황 PDF → 종목 연결 판정 (규칙 v1) 테스트 (설계 §8-6, unittest).

PDF 픽스처 없이 pages 리스트 주입. 실제 DB·실제 네트워크 호출 없음.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from scripts.download_naver_research import run_sector
from scripts.report_metrics import sector_link
from scripts.report_metrics import storage
from scripts.report_metrics.sector_link import judge

CODE_TO_NAME = {
    "319660": "피에스케이",
    "403870": "HPSP",
    "140860": "파크시스템스",
    "005930": "삼성전자",
    "267250": "HD현대",
    "443060": "HD현대마린솔루션",
    "003550": "LG",
    "031980": "피에스케이홀딩스",
}
NAME_TO_CODE = {v: k for k, v in CODE_TO_NAME.items()}


def _by_code(rows):
    return {r["stock_code"]: r for r in rows}


class TestJudgeSamsungShape(unittest.TestCase):
    def test_primary_mention_ranges(self):
        pages = [
            "요약표 319660 403870 140860",
            "HPSP (403870)\n투자의견 매수\n본문",
            "이어지는 본문 내용",
            "피에스케이 (319660)\n목표주가 5만원\n본문",
            "삼성전자는 투자를 확대한다",
        ]
        rows = _by_code(judge(pages, CODE_TO_NAME, NAME_TO_CODE))
        self.assertEqual(set(rows), {"403870", "319660", "140860", "005930"})
        self.assertEqual(rows["403870"]["relation_type"], "primary")
        self.assertEqual((rows["403870"]["page_from"], rows["403870"]["page_to"]), (2, 3))
        self.assertEqual(rows["319660"]["relation_type"], "primary")
        self.assertEqual((rows["319660"]["page_from"], rows["319660"]["page_to"]), (4, 5))
        self.assertEqual(rows["140860"]["relation_type"], "mention")
        self.assertEqual((rows["140860"]["page_from"], rows["140860"]["page_to"]), (1, 1))
        self.assertEqual(rows["005930"]["relation_type"], "mention")
        self.assertEqual((rows["005930"]["page_from"], rows["005930"]["page_to"]), (5, 5))


class TestJudgeHeaderOnly(unittest.TestCase):
    def test_unknown(self):
        rows = judge(["파크시스템스 (140860)\n실적 요약 본문"], CODE_TO_NAME, NAME_TO_CODE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_code"], "140860")
        self.assertEqual(rows[0]["relation_type"], "unknown")
        self.assertEqual((rows[0]["page_from"], rows[0]["page_to"]), (1, 1))


class TestJudgeConsecutiveHeaders(unittest.TestCase):
    def test_absorbed_in_primary(self):
        pages = [
            "파크시스템스 (140860)\n투자의견 매수",
            "파크시스템스 (140860)\n실적 표",
            "다른 내용",
        ]
        rows = judge(pages, CODE_TO_NAME, NAME_TO_CODE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["relation_type"], "primary")
        self.assertEqual((rows[0]["page_from"], rows[0]["page_to"]), (1, 3))


class TestJudgeNameMatching(unittest.TestCase):
    def test_longest_name_wins(self):
        rows = _by_code(judge(["HD현대마린솔루션 수주 소식"], CODE_TO_NAME, NAME_TO_CODE))
        self.assertIn("443060", rows)
        self.assertNotIn("267250", rows)
        self.assertEqual(rows["443060"]["relation_type"], "mention")

    def test_short_name_ignored(self):
        self.assertEqual(judge(["LG이노텍 실적 발표"], CODE_TO_NAME, NAME_TO_CODE), [])

    def test_holding_suffix_longest(self):
        rows = _by_code(judge(["피에스케이홀딩스 공시"], CODE_TO_NAME, NAME_TO_CODE))
        self.assertIn("031980", rows)
        self.assertNotIn("319660", rows)

    def test_name_after_alnum_not_matched(self):
        self.assertEqual(judge(["ABC피에스케이 언급"], CODE_TO_NAME, NAME_TO_CODE), [])

    def test_empty_pages(self):
        self.assertEqual(judge(["", "   ", ""], CODE_TO_NAME, NAME_TO_CODE), [])


class TestLinkDocumentBrokerSelf(unittest.TestCase):
    def test_publisher_mention_dropped(self):
        c2n = dict(CODE_TO_NAME, **{"039490": "키움증권", "0008Z0": "신형코드종목"})
        n2c = {v: k for k, v in c2n.items()}
        pages = ["키움증권 리서치센터 " + "x" * 60 + "\n삼성전자 투자 확대, 신형코드종목 신규"]
        with TemporaryDirectory() as d, storage.connect_rw(Path(d) / "t.sqlite3") as con:
            sector_link.catalog.init_catalog(con)
            doc_id, _ = sector_link.catalog.upsert_document(
                con, document_type="industry", broker="키움증권", published_date="2026-09-29",
                file_status="saved", sha256="h", pdf_key="k")
            with mock.patch.object(sector_link, "pages_text", return_value=pages):
                n = sector_link.link_document(con, doc_id, Path("x.pdf"), c2n, n2c, broker="키움증권")
            codes = [r[0] for r in con.execute("SELECT stock_code FROM report_document_stocks")]
        self.assertEqual(n, 1)
        self.assertEqual(codes, ["005930"])


class TestSectorLinkIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.out = tmp / "out"
        self.db = tmp / "t.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def test_run_sector_links_and_rerun_skips(self):
        pages = {1: [{"researchId": 46255, "category": "반도체",
                      "writeDate": "2026-09-29", "brokerName": "A증권",
                      "title": "반도체 t1"},
                     {"researchId": 46000, "writeDate": "2026-09-10"}]}
        details = {"46255": {"attachUrl": "http://x/sector_link.pdf"}}

        def list_fetch(url):
            page = int(url.split("page=")[1].split("&")[0])
            return json.dumps(pages.get(page, [])).encode()

        def detail_fetch(url):
            rid = url.rsplit("/", 1)[-1]
            return json.dumps({"researchContent": details.get(rid, {})}).encode()

        def pdf_fetch(url):
            return b"%PDF-1.7:" + url.encode()

        kw = dict(out_dir=self.out, list_fetch=list_fetch,
                  detail_fetch=detail_fetch, pdf_fetch=pdf_fetch,
                  sleep_fn=lambda s: None, facts_db=self.db,
                  names=(CODE_TO_NAME, NAME_TO_CODE))
        mock_pages = [
            "요약 " + "x" * 60,
            "HPSP (403870)\n투자의견 매수\n" + "본문 " * 30,
            "파크시스템스 언급 " + "본문 " * 30,
        ]
        with mock.patch.object(sector_link, "pages_text", return_value=mock_pages) as mp:
            s1 = run_sector("2026-09-29", "industry", **kw)
            self.assertEqual(s1["linked"], 2)
            self.assertEqual(mp.call_count, 1)
        with storage.connect_ro(self.db) as con:
            links = con.execute(
                "SELECT stock_code, relation_type, method, page_from, page_to "
                "FROM report_document_stocks ORDER BY stock_code").fetchall()
            api_n = con.execute("SELECT COUNT(*) FROM report_api_facts").fetchone()[0]
        self.assertEqual(api_n, 0)
        self.assertEqual([(r[0], r[1], r[2]) for r in links],
                         [("140860", "mention", "rule_v1"),
                          ("403870", "primary", "rule_v1")])
        self.assertEqual([(r[3], r[4]) for r in links], [(3, 3), (2, 3)])
        with mock.patch.object(sector_link, "pages_text", return_value=mock_pages) as mp2:
            s2 = run_sector("2026-09-29", "industry", **kw)
            self.assertEqual(s2["skipped_known"], 1)
            self.assertEqual(s2["linked"], 0)
            self.assertEqual(mp2.call_count, 0)


if __name__ == "__main__":
    unittest.main()
