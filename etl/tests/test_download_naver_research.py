"""네이버 리서치(종목분석) 리포트 다운로더 테스트.

소스: m.stock.naver.com/api/research/company (목록) + .../company/{id} (상세, attachUrl).
네트워크는 fetch 주입으로 대체.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.download_naver_research import (
    dest_path,
    download_pdf,
    fetch_detail,
    list_reports,
    list_stock_reports,
    run,
    run_stock,
    sanitize,
)


def _stock_row(nid, title, broker, pdf, date):
    return (
        f'<tr><td><a href="/item/main.naver?code=005930" class="stock_item">삼성전자</a></td>'
        f'<td><a href="company_read.naver?nid={nid}&page=1&searchType=itemCode&itemCode=005930">{title}</a>'
        f'<img src="x.gif" class="ico_new" alt="NEW"></td>'
        f'<td>{broker}</td>'
        f'<td class="file"><a href="{pdf}" target="_blank"><img src="down.gif" alt="pdf"></a></td>'
        f'<td class="date" style="padding-left:5px">{date}</td>'
        f'<td class="date">100</td></tr>'
    )


def _fake_list(pages: dict[int, list]):
    def fetch(url: str) -> bytes:
        # url 에서 page 파라미터 추출
        page = int(url.split("page=")[1].split("&")[0])
        return json.dumps(pages.get(page, [])).encode("utf-8")
    return fetch


class TestListReports(unittest.TestCase):
    def _row(self, rid, name, code, date, broker="X증권"):
        return {"researchId": rid, "itemName": name, "itemCode": code,
                "writeDate": date, "brokerName": broker, "title": "t"}

    def test_collects_only_target_date(self):
        pages = {1: [self._row(3, "삼성전자", "005930", "2026-07-03"),
                     self._row(2, "농심", "004370", "2026-07-03"),
                     self._row(1, "카카오", "035720", "2026-07-02")]}  # 이전날 → 중단
        rows = list_reports("2026-07-03", fetch_fn=_fake_list(pages))
        self.assertEqual([r["researchId"] for r in rows], [3, 2])

    def test_paginates_until_older_date(self):
        pages = {
            1: [self._row(i, "A", "000001", "2026-07-03") for i in range(100, 100 - 3, -1)],
            2: [self._row(50, "B", "000002", "2026-07-03"),
                self._row(49, "C", "000003", "2026-07-01")],  # 이전날 → 중단
        }
        rows = list_reports("2026-07-03", fetch_fn=_fake_list(pages), page_size=3)
        self.assertEqual(len(rows), 4)  # page1 3건 + page2 1건

    def test_skips_rows_without_itemcode(self):
        # 종목코드 없는 행(시장/전략 등 혼입 대비) 제외
        pages = {1: [self._row(3, "삼성전자", "005930", "2026-07-03"),
                     {"researchId": 2, "itemName": "시황", "itemCode": "", "writeDate": "2026-07-03"},
                     self._row(1, "x", "000001", "2026-06-30")]}
        rows = list_reports("2026-07-03", fetch_fn=_fake_list(pages))
        self.assertEqual([r["researchId"] for r in rows], [3])


class TestFetchDetail(unittest.TestCase):
    def test_returns_attach_url(self):
        payload = {"researchContent": {
            "researchId": 93876, "attachUrl": "https://stock.pstatic.net/x.pdf",
            "opinion": "Buy", "goalPrice": "130000"}}
        detail = fetch_detail(93876, fetch_fn=lambda u: json.dumps(payload).encode())
        self.assertEqual(detail["attachUrl"], "https://stock.pstatic.net/x.pdf")
        self.assertEqual(detail["opinion"], "Buy")


class TestDestAndDownload(unittest.TestCase):
    def test_sanitize_strips_illegal(self):
        self.assertEqual(sanitize("삼성/전자:우"), "삼성_전자_우")

    def test_dest_path_layout(self):
        p = dest_path(Path("/out"), "삼성전자", "005930", "2026-07-03", "대신증권", 93876)
        self.assertEqual(p, Path("/out/삼성전자_005930/2026-07-03_대신증권_93876.pdf"))

    def test_download_writes_and_skips_existing(self):
        with TemporaryDirectory() as d:
            dest = Path(d) / "a.pdf"
            calls = []
            def fetch(u):
                calls.append(u)
                return b"%PDF-1.7 data"
            self.assertTrue(download_pdf("http://x/a.pdf", dest, fetch_fn=fetch))
            self.assertEqual(dest.read_bytes(), b"%PDF-1.7 data")
            # 두번째: 이미 있으면 스킵(fetch 재호출 없음)
            self.assertFalse(download_pdf("http://x/a.pdf", dest, fetch_fn=fetch))
            self.assertEqual(len(calls), 1)

    def test_download_rejects_non_pdf(self):
        with TemporaryDirectory() as d:
            dest = Path(d) / "b.pdf"
            # HTML 응답(구 stockinfo7 버그 재발 방지) → 저장 안 함
            ok = download_pdf("http://x/b", dest, fetch_fn=lambda u: b"<!doctype html><html>")
            self.assertFalse(ok)
            self.assertFalse(dest.exists())


class TestRunIdempotent(unittest.TestCase):
    def _list(self):
        row = {"researchId": 5, "itemName": "삼성전자", "itemCode": "005930",
               "writeDate": "2026-07-03", "brokerName": "대신증권", "title": "t"}
        return lambda url: json.dumps([row, {**row, "researchId": 4, "writeDate": "2026-07-02"}]).encode()

    def _detail(self, counter):
        def f(url):
            counter.append(url)
            return json.dumps({"researchContent": {"attachUrl": "http://x/a.pdf"}}).encode()
        return f

    def test_rerun_skips_without_detail_or_pdf_fetch(self):
        with TemporaryDirectory() as d:
            out = Path(d)
            detail_calls, pdf_calls = [], []
            def pdf(url):
                pdf_calls.append(url)
                return b"%PDF-1.7 x"
            # 1차: 다운로드
            s1 = run("2026-07-03", out_dir=out, list_fetch=self._list(),
                     detail_fetch=self._detail(detail_calls), pdf_fetch=pdf, sleep_fn=lambda s: None)
            self.assertEqual(s1["downloaded"], 1)
            self.assertEqual(s1["skipped_exists"], 0)
            self.assertEqual(len(detail_calls), 1)  # 대상일 1건만(다른날 제외)

            # 2차: 같은 날 재실행 → 파일 존재 → 상세/PDF 요청 0
            def boom(url):
                raise AssertionError(f"재실행에서 요청 발생: {url}")
            s2 = run("2026-07-03", out_dir=out, list_fetch=self._list(),
                     detail_fetch=boom, pdf_fetch=boom, sleep_fn=lambda s: None)
            self.assertEqual(s2["skipped_exists"], 1)
            self.assertEqual(s2["downloaded"], 0)


class TestListStockReports(unittest.TestCase):
    def _pages(self, pagemap):
        def fetch(url):
            page = int(url.split("page=")[1].split("&")[0])
            return pagemap.get(page, "")
        return fetch

    def test_parses_rows_and_normalizes_date(self):
        pdf = "https://stock.pstatic.net/stock-research/company/61/20260703_company_1.pdf"
        html = "<table>" + _stock_row(93863, "실적 상향", "iM증권", pdf, "26.07.03") + "</table>"
        rows = list_stock_reports("005930", "삼성전자", max_pages=1, fetch_fn=self._pages({1: html}))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["researchId"], "93863")
        self.assertEqual(r["brokerName"], "iM증권")
        self.assertEqual(r["pdf_url"], pdf)
        self.assertEqual(r["writeDate"], "2026-07-03")  # 26.07.03 → 정규화
        self.assertEqual(r["itemCode"], "005930")

    def test_paginates_until_empty(self):
        p = "https://stock.pstatic.net/stock-research/company/61/20260703_company_{}.pdf"
        pages = {
            1: _stock_row(2, "a", "A증권", p.format(2), "26.07.03"),
            2: _stock_row(1, "b", "B증권", p.format(1), "26.07.01"),
            3: "",  # 빈 페이지 → 중단
        }
        rows = list_stock_reports("005930", "삼성전자", max_pages=5, fetch_fn=self._pages(pages))
        self.assertEqual(len(rows), 2)

    def test_date_range_filters_and_stops(self):
        p = "https://stock.pstatic.net/stock-research/company/61/x_{}.pdf"
        # 날짜 desc: 07-05(범위밖 최신), 07-03(범위내), 07-01(범위내), 06-20(since 미만→중단)
        pages = {
            1: (_stock_row(5, "e", "E", p.format(5), "26.07.05")
                + _stock_row(3, "c", "C", p.format(3), "26.07.03")
                + _stock_row(1, "a", "A", p.format(1), "26.07.01")
                + _stock_row(0, "z", "Z", p.format(0), "26.06.20")),
        }
        rows = list_stock_reports("005930", "삼성전자", since="2026-07-01", until="2026-07-03",
                                  max_pages=3, fetch_fn=self._pages(pages))
        self.assertEqual([r["researchId"] for r in rows], ["3", "1"])  # 07-05 제외, 06-20 전 중단


class TestRunStock(unittest.TestCase):
    def test_downloads_and_idempotent(self):
        pdf = "https://stock.pstatic.net/stock-research/company/61/20260703_company_9.pdf"
        html = _stock_row(777, "제목", "대신증권", pdf, "26.07.02")
        with TemporaryDirectory() as d:
            out = Path(d)
            pdf_calls = []
            def pdf_fetch(url):
                pdf_calls.append(url)
                return b"%PDF-1.7 z"
            list_fetch = lambda url: (html if "page=1" in url else "")
            s1 = run_stock("005930", "삼성전자", out_dir=out, max_pages=2,
                           list_fetch=list_fetch, pdf_fetch=pdf_fetch, sleep_fn=lambda s: None)
            self.assertEqual(s1["downloaded"], 1)
            saved = out / "삼성전자_005930" / "2026-07-02_대신증권_777.pdf"
            self.assertTrue(saved.exists())
            # 재실행 → 스킵, PDF 요청 없음
            s2 = run_stock("005930", "삼성전자", out_dir=out, max_pages=2,
                           list_fetch=list_fetch, pdf_fetch=lambda u: (_ for _ in ()).throw(AssertionError("no refetch")),
                           sleep_fn=lambda s: None)
            self.assertEqual(s2["skipped_exists"], 1)
            self.assertEqual(s2["downloaded"], 0)
            self.assertEqual(len(pdf_calls), 1)


if __name__ == "__main__":
    unittest.main()
