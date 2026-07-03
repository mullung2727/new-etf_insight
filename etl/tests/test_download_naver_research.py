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
    sanitize,
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


if __name__ == "__main__":
    unittest.main()
