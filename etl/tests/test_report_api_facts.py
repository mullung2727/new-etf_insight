"""네이버 리서치 API 목표가 저장 + 리포트 후보 판정 테스트.

네트워크는 fetch 주입으로 대체(기존 test_download_naver_research.py 방식).
임시 DB는 TemporaryDirectory.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.download_naver_research import dest_path, pdf_key, run
from scripts.report_metrics import storage
from scripts.report_metrics.models import ReportFacts


def _api_row(key, code="005380", broker="삼성증권", date="2026-09-23", goal=500000, **kw):
    row = {
        "pdf_key": key,
        "research_id": "1",
        "stock_code": code,
        "stock_name": "현대차",
        "broker": broker,
        "report_date": date,
        "title": "t",
        "opinion": "Buy",
        "goal_price": goal,
        "price_at_write": 371500,
        "content_html": "<p>x</p>",
    }
    row.update(kw)
    return row


def _pdf_row(key, code="005380", broker="삼성증권", date="2026-09-01", target=400000, **kw):
    facts = ReportFacts(pdf_key=key, pdf_path=f"{key}.pdf", stock_code=code,
                        stock_name="현대차", broker=broker, report_date=date,
                        target_price=target)
    for k, v in kw.items():
        setattr(facts, k, v)
    return facts


class _DbCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t.sqlite3"
        storage.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _save_api(self, *rows):
        with storage.connect_rw(self.db) as con:
            for r in rows:
                storage.upsert_api_fact(con, r)

    def _save_pdf(self, *facts_list):
        with storage.connect_rw(self.db) as con:
            for f in facts_list:
                storage.upsert_facts(con, f)

    def _prev(self, code, broker, date):
        with storage.connect_ro(self.db) as con:
            return storage.find_previous_target(con, code, broker, date)


class TestParsePrice(unittest.TestCase):
    def test_cases(self):
        cases = [
            ("500000", 500000),
            ("1,234", 1234),
            ("0", None),
            ("", None),
            (None, None),
            ("N/A", None),
            (75000, 75000),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(storage.parse_price(value), expected)


class TestClassifyReport(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(storage.classify_report(None, True, 100), "no_target")
        self.assertEqual(storage.classify_report(100, False, None), "new")
        self.assertEqual(storage.classify_report(100, True, None), "new")
        self.assertEqual(storage.classify_report(120, True, 100), "up")
        self.assertEqual(storage.classify_report(100, True, 100), "flat")
        self.assertEqual(storage.classify_report(80, True, 100), "down")


class TestFindPreviousTarget(_DbCase):
    def test_empty(self):
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (False, None))

    def test_prev_only_in_api(self):
        self._save_api(_api_row("a1", date="2026-09-01", goal=400000))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (True, 400000))

    def test_prev_only_in_pdf_facts(self):
        self._save_pdf(_pdf_row("p1", date="2026-09-01", target=400000))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (True, 400000))

    def test_skips_pdf_parse_error(self):
        self._save_pdf(_pdf_row("p_ok", date="2026-08-01", target=300000),
                       _pdf_row("p_err", date="2026-09-01", parse_status="parse_error"))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (True, 300000))

    def test_api_newer_wins(self):
        self._save_api(_api_row("a1", date="2026-09-10", goal=410000))
        self._save_pdf(_pdf_row("p1", date="2026-09-01", target=400000))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (True, 410000))

    def test_pdf_newer_wins(self):
        self._save_api(_api_row("a1", date="2026-09-01", goal=410000))
        self._save_pdf(_pdf_row("p1", date="2026-09-10", target=400000))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (True, 400000))

    def test_same_day_api_wins(self):
        self._save_api(_api_row("a1", date="2026-09-10", goal=410000))
        self._save_pdf(_pdf_row("p1", date="2026-09-10", target=400000))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (True, 410000))

    def test_same_date_not_previous(self):
        self._save_api(_api_row("same", date="2026-09-23", goal=999999))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (False, None))
        self._save_api(_api_row("old", date="2026-09-01", goal=400000))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (True, 400000))

    def test_ignores_other_broker_and_code(self):
        self._save_api(_api_row("other_b", broker="미래증권", date="2026-09-20", goal=111),
                       _api_row("other_c", code="000660", date="2026-09-20", goal=222))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (False, None))
        self._save_api(_api_row("own", date="2026-09-01", goal=400000))
        self.assertEqual(self._prev("005380", "삼성증권", "2026-09-23"), (True, 400000))


class TestReportCandidates(_DbCase):
    def test_filters_and_order(self):
        # 직전 행(기간 밖) + 당일 행. 상태 케이스마다 종목코드 분리.
        self._save_api(
            _api_row("prev_up", code="005380", date="2026-09-10", goal=400000),
            _api_row("prev_up2", code="000001", date="2026-09-10", goal=400000),
            _api_row("prev_up3", code="005380", broker="미래증권", date="2026-09-10", goal=400000),
            _api_row("prev_flat", code="035720", date="2026-09-10", goal=200000),
            _api_row("prev_down", code="051910", date="2026-09-10", goal=200000),
            _api_row("prev_nr", code="068270", date="2026-09-10", goal=100000),
            _api_row("prev_start", code="042660", date="2026-09-10", goal=100000),
            _api_row("prev_end", code="012330", date="2026-09-10", goal=100000),
            _api_row("prev_after", code="015760", date="2026-09-10", goal=100000),
        )
        self._save_api(
            _api_row("cur_up", code="005380", date="2026-09-20", goal=500000),      # up
            _api_row("cur_up2", code="000001", date="2026-09-20", goal=500000),     # up
            _api_row("cur_up3", code="005380", broker="미래증권",                    # up
                     date="2026-09-20", goal=500000),
            _api_row("cur_new", code="000660", date="2026-09-21", goal=100000),     # new
            _api_row("k1", code="009999", broker="T", date="2026-09-21", goal=100),  # new
            _api_row("k2", code="009999", broker="T", date="2026-09-21", goal=200),  # new
            _api_row("cur_flat", code="035720", date="2026-09-21", goal=200000),    # flat 제외
            _api_row("cur_down", code="051910", date="2026-09-21", goal=150000),    # down 제외
            _api_row("cur_nr", code="068270", date="2026-09-21", goal=None),        # no_target 제외
            _api_row("at_start", code="042660", date="2026-09-19", goal=999999),    # start 제외
            _api_row("at_end", code="012330", date="2026-09-22", goal=200000),      # end 포함
            _api_row("after_end", code="015760", date="2026-09-23", goal=999999),   # end 밖 제외
        )
        with storage.connect_ro(self.db) as con:
            got = storage.report_candidates(con, "2026-09-19", "2026-09-22")
        self.assertEqual(
            [(c["report_date"], c["stock_code"], c["broker"], c["pdf_key"], c["status"]) for c in got],
            [
                ("2026-09-20", "000001", "삼성증권", "cur_up2", "up"),
                ("2026-09-20", "005380", "미래증권", "cur_up3", "up"),
                ("2026-09-20", "005380", "삼성증권", "cur_up", "up"),
                ("2026-09-21", "000660", "삼성증권", "cur_new", "new"),
                ("2026-09-21", "009999", "T", "k1", "new"),
                ("2026-09-21", "009999", "T", "k2", "new"),
                ("2026-09-22", "012330", "삼성증권", "at_end", "up"),
            ],
        )
        self.assertEqual(
            set(got[0].keys()),
            {"stock_code", "stock_name", "broker", "report_date", "title", "opinion",
             "goal_price", "prev_target", "status", "pdf_key"},
        )
        by_key = {c["pdf_key"]: c for c in got}
        self.assertEqual(by_key["cur_up"]["prev_target"], 400000)
        self.assertIsNone(by_key["cur_new"]["prev_target"])
        self.assertEqual(by_key["at_end"]["prev_target"], 100000)


class TestRunFactsDb(unittest.TestCase):
    def _fetches(self, details):
        rows = [
            {"researchId": 11, "itemName": "현대차", "itemCode": "005380",
             "writeDate": "2026-09-23", "brokerName": "A/B증권", "title": "목표가 상향"},
            {"researchId": 12, "itemName": "삼성전자", "itemCode": "005930",
             "writeDate": "2026-09-23", "brokerName": "X증권", "title": "유지"},
            {"researchId": 10, "itemName": "x", "itemCode": "000001",
             "writeDate": "2026-09-10", "brokerName": "X", "title": "old"},  # 7일 창 밖 종료행
        ]
        list_fetch = lambda url: json.dumps(rows).encode()  # noqa: E731
        def detail_fetch(url):
            rid = url.rsplit("/", 1)[-1]
            return json.dumps({"researchContent": details.get(rid, {})}).encode()
        return list_fetch, detail_fetch

    def test_saves_api_facts(self):
        details = {
            "11": {"attachUrl": "http://x/a.pdf", "opinion": "Buy", "goalPrice": "500000",
                   "prevGoalPrice": "999999", "priceAtWriteDate": "371500",
                   "content": "<p>hi</p>"},
            # 12: attachUrl 없음 → 저장 안 됨
        }
        list_fetch, detail_fetch = self._fetches(details)
        with TemporaryDirectory() as d:
            tmp = Path(d)
            out, db = tmp / "out", tmp / "facts.sqlite3"
            # 11번 PDF 미리 존재 → 다운로드는 스킵해도 fact는 저장돼야 함
            dest = dest_path(out, "현대차", "005380", "2026-09-23", "A/B증권",
                             pdf_key("http://x/a.pdf"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"%PDF-1.7 old")
            def boom_pdf(url):
                raise AssertionError(f"호출되면 안 됨: {url}")
            stats = run("2026-09-23", out_dir=out, list_fetch=list_fetch,
                        detail_fetch=detail_fetch, pdf_fetch=boom_pdf,
                        sleep_fn=lambda s: None, facts_db=db)
            self.assertEqual(stats["listed"], 2)
            self.assertEqual(stats["skipped_exists"], 1)
            self.assertEqual(stats["no_pdf"], 1)
            self.assertEqual(stats["downloaded"], 0)
            self.assertEqual(stats["facts_saved"], 1)
            with storage.connect_ro(db) as con:
                rows = con.execute("SELECT * FROM report_api_facts").fetchall()
            self.assertEqual(len(rows), 1)
            got = dict(rows[0])
            self.assertEqual(got["pdf_key"], "a")
            self.assertEqual(got["research_id"], "11")
            self.assertEqual(got["stock_code"], "005380")
            self.assertEqual(got["stock_name"], "현대차")
            self.assertEqual(got["broker"], "A_B증권")  # sanitize 적용
            self.assertEqual(got["report_date"], "2026-09-23")
            self.assertEqual(got["title"], "목표가 상향")
            self.assertEqual(got["opinion"], "Buy")
            self.assertEqual(got["goal_price"], 500000)
            self.assertEqual(got["price_at_write"], 371500)
            self.assertEqual(got["content_html"], "<p>hi</p>")
            self.assertTrue(got["fetched_at"])
            for key, value in got.items():  # prevGoalPrice는 어디에도 안 씀
                self.assertNotIn("999999", str(value), key)

    def test_keeps_saved_facts_when_later_fetch_fails(self):
        details = {"11": {"attachUrl": "http://x/a.pdf", "goalPrice": "500000"},
                   "12": {"attachUrl": "http://x/b.pdf", "goalPrice": "90000"}}
        list_fetch, detail_fetch = self._fetches(details)
        def pdf_fetch(url):  # PDF 다운로드 네트워크 오류 → failed 기록 후 다음 행 계속
            raise TimeoutError(url)
        with TemporaryDirectory() as d:
            tmp = Path(d)
            db = tmp / "facts.sqlite3"
            stats = run("2026-09-23", out_dir=tmp / "out", list_fetch=list_fetch,
                        detail_fetch=detail_fetch, pdf_fetch=pdf_fetch,
                        sleep_fn=lambda s: None, facts_db=db)
            self.assertEqual(stats["failed"], 2)
            self.assertEqual(stats["downloaded"], 0)
            self.assertEqual(stats["facts_saved"], 2)
            with storage.connect_ro(db) as con:
                keys = [r["pdf_key"] for r in con.execute("SELECT pdf_key FROM report_api_facts")]
            self.assertEqual(keys, ["a", "b"])  # 실패해도 저장한 목표가는 남는다

    def test_no_db_without_facts_db(self):
        list_fetch, detail_fetch = self._fetches(
            {"11": {"attachUrl": "http://x/a.pdf", "goalPrice": "500000"}})
        with TemporaryDirectory() as d:
            tmp = Path(d)
            stats = run("2026-09-23", out_dir=tmp / "out", list_fetch=list_fetch,
                        detail_fetch=detail_fetch, pdf_fetch=lambda u: b"%PDF-1.7 x",
                        sleep_fn=lambda s: None)
            self.assertEqual(stats["facts_saved"], 0)
            self.assertEqual(stats["downloaded"], 1)
            self.assertEqual(list(tmp.rglob("*.sqlite3")), [])


if __name__ == "__main__":
    unittest.main()
