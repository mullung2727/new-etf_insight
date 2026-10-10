"""build_earnings_disclosures 테스트 (unittest, 네트워크 없음)."""
import sqlite3
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402
import build_earnings_disclosures as m  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "earnings_disclosures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _filing(rcept_no, report_nm, corp_cls="Y", stock_code="005930",
            rcept_dt="20250131", corp_code="00126380"):
    return {"corp_code": corp_code, "corp_name": "삼성전자", "stock_code": stock_code,
            "corp_cls": corp_cls, "report_nm": report_nm, "rcept_no": rcept_no,
            "flr_nm": "삼성전자", "rcept_dt": rcept_dt, "rm": ""}


def _memdb():
    con = sqlite3.connect(":memory:")
    m.ensure_schema(con)
    return con


class TestEarningsDisclosures(unittest.TestCase):
    def test_classify(self):
        cases = [
            ("연결재무제표기준영업(잠정)실적(공정공시)", "prelim"),
            ("영업(잠정)실적(공정공시)", "prelim"),
            ("[기재정정]매출액또는손익구조30%(대규모법인은15%)이상변경", "pnl_change"),
            ("매출액또는손익구조30%(대규모법인은15%)이상변동", "pnl_change"),
            ("매출액또는손익구조30%(대규모법인은15%)이상변경(자회사의 주요경영사항)", None),
            ("사업보고서 (2024.12)", "annual_report"),
            ("반기보고서 (2024.06)", None),
            ("단일판매ㆍ공급계약체결", None),
        ]
        for nm, want in cases:
            with self.subTest(nm=nm):
                self.assertEqual(m.classify(nm), want)

    def test_prelim_kospi_full(self):
        r = m.parse_document(_read("20250131800091.html"), "prelim", "20250131", 12)
        self.assertEqual((r["fs_basis"], r["fiscal_year"], r["period"]), ("CFS", "2024", "Q4"))
        self.assertAlmostEqual(r["revenue"], 3008709e8, delta=1)
        self.assertAlmostEqual(r["op_income"], 327260e8, delta=1)
        self.assertAlmostEqual(r["pretax_income"], 375297e8, delta=1)
        self.assertAlmostEqual(r["net_income"], 344514e8, delta=1)

    def test_prelim_kospi_partial(self):
        r = m.parse_document(_read("20250108800024.html"), "prelim", "20250108", 12)
        self.assertEqual((r["fs_basis"], r["fiscal_year"], r["period"]), ("CFS", "2024", "Q4"))
        self.assertAlmostEqual(r["revenue"], 300.08e12, delta=1)
        self.assertAlmostEqual(r["op_income"], 32.73e12, delta=1)
        self.assertIsNone(r["pretax_income"])
        self.assertIsNone(r["net_income"])

    def test_prelim_kosdaq(self):
        r = m.parse_document(_read("20240207900098.html"), "prelim", "20240207", 12)
        self.assertEqual((r["fs_basis"], r["fiscal_year"], r["period"]), ("CFS", "2023", "Q4"))
        self.assertAlmostEqual(r["revenue"], 7259049e6, delta=1)
        self.assertAlmostEqual(r["op_income"], 295225e6, delta=1)
        self.assertAlmostEqual(r["pretax_income"], 141377e6, delta=1)
        self.assertAlmostEqual(r["net_income"], 85516e6, delta=1)

    def test_prelim_kosdaq_rev_q(self):
        r = m.parse_document(_read("20240213900333.html"), "prelim", "20240213", 12)
        self.assertEqual((r["fs_basis"], r["fiscal_year"], r["period"]), ("CFS", "2023", "Q4"))
        self.assertAlmostEqual(r["revenue"], 777693e6, delta=1)
        self.assertAlmostEqual(r["op_income"], 57290e6, delta=1)

    def test_correction_doc_parses_revised_form(self):
        r = m.parse_document(_read("20240131800033.html"), "prelim", "20240131", 12)
        self.assertEqual((r["fs_basis"], r["fiscal_year"], r["period"]), ("CFS", "2023", "Q4"))
        self.assertAlmostEqual(r["revenue"], 258.94e12, delta=1)
        self.assertAlmostEqual(r["op_income"], 6.57e12, delta=1)
        self.assertIsNone(r["pretax_income"])
        self.assertIsNone(r["net_income"])

    def test_parse_pending_correction_skip(self):
        con = _memdb()
        try:
            now = m._now()
            con.executemany(
                "INSERT INTO earnings_disclosures (rcept_no, rcept_dt, corp_code, "
                "stock_code, kind, report_nm, is_correction, parse_status, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [("C1", "20240131", "00126380", "005930", "prelim",
                  "[기재정정]영업(잠정)실적(공정공시)", 1, "pending", now),
                 ("N1", "20240131", "00126380", "005930", "prelim",
                  "영업(잠정)실적(공정공시)", 0, "pending", now)])
            html = ("<table><tr><td>정정일자</td><td>2024-01-31</td></tr>"
                    "<tr><td>4. 정정사항</td><td>-</td></tr></table>")
            with mock.patch.object(m, "fetch_document_html", return_value=html):
                with mock.patch("time.sleep"):
                    m.parse_pending(con, ["005930"])
            got = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT rcept_no, parse_status, parse_note FROM earnings_disclosures")}
            self.assertEqual(got["C1"][0], "skip")
            self.assertTrue(got["C1"][1].startswith("정정공시"))
            self.assertLessEqual(len(got["C1"][1]), 200)
            self.assertEqual(got["N1"][0], "failed")
        finally:
            con.close()

    def test_pnl_samsung(self):
        r = m.parse_document(_read("20250131800097.html"), "pnl_change", "20250131", 12)
        self.assertEqual((r["fs_basis"], r["fiscal_year"], r["period"]), ("CFS", "2024", "FY"))
        self.assertAlmostEqual(r["revenue"], 300870900000e3, delta=1)
        self.assertAlmostEqual(r["op_income"], 32726000000e3, delta=1)
        self.assertAlmostEqual(r["pretax_income"], 37529700000e3, delta=1)
        self.assertAlmostEqual(r["net_income"], 34451400000e3, delta=1)

    def test_pnl_won_unit(self):
        r = m.parse_document(_read("20220314901704.html"), "pnl_change", "20220314", 12)
        self.assertEqual((r["fs_basis"], r["fiscal_year"], r["period"]), ("CFS", "2021", "FY"))
        self.assertAlmostEqual(r["revenue"], 606146777369, delta=1)
        self.assertAlmostEqual(r["op_income"], 57999788641, delta=1)
        self.assertAlmostEqual(r["pretax_income"], 45071465068, delta=1)
        self.assertAlmostEqual(r["net_income"], 38694600079, delta=1)

    def test_fye_calc(self):
        self.assertEqual(m.prelim_period_from_end_date(2024, 6, 6), ("2024", "Q4"))
        self.assertEqual(m.prelim_period_from_end_date(2024, 9, 6), ("2025", "Q1"))
        self.assertEqual(m.pnl_fiscal_year("20240815", 6), "2024")
        self.assertEqual(m.pnl_fiscal_year("20240315", 12), "2023")

    def test_num(self):
        self.assertEqual(m._num("1,234"), 1234)
        self.assertEqual(m._num("△1,234"), -1234)
        self.assertEqual(m._num("(55)"), -55)
        self.assertIsNone(m._num("-"))

    def test_upsert_idempotent(self):
        con = _memdb()
        try:
            f = _filing("20250131800091", "영업(잠정)실적(공정공시)")
            self.assertEqual(m.upsert_list_rows(con, [f]), 1)
            con.execute(
                "UPDATE earnings_disclosures SET fs_basis='CFS', fiscal_year='2024', "
                "period='Q4', revenue=1.0, op_income=2.0, parse_status='ok' "
                "WHERE rcept_no=?", (f["rcept_no"],))
            f2 = dict(f, report_nm="[기재정정]영업(잠정)실적(공정공시)")
            self.assertEqual(m.upsert_list_rows(con, [f2]), 1)
            row = con.execute(
                "SELECT report_nm, is_correction, fs_basis, fiscal_year, period, "
                "revenue, op_income, parse_status FROM earnings_disclosures").fetchone()
            self.assertEqual(row[:2], ("[기재정정]영업(잠정)실적(공정공시)", 1))  # 목록 필드 갱신
            self.assertEqual(row[2:], ("CFS", "2024", "Q4", 1.0, 2.0, "ok"))  # 파싱값 유지
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM earnings_disclosures").fetchone()[0], 1)
        finally:
            con.close()

    def test_load_list_filter_and_windows(self):
        filings = [
            _filing("P1", "연결재무제표기준영업(잠정)실적(공정공시)"),
            _filing("C1", "[기재정정]매출액또는손익구조30%(대규모법인은15%)이상변경",
                    corp_cls="K", stock_code="247540"),
            _filing("A1", "사업보고서 (2024.12)"),
            _filing("X1", "영업(잠정)실적(공정공시)", corp_cls="N"),  # 코넥스 제외
            _filing("X2", "영업(잠정)실적(공정공시)", stock_code=""),  # 빈 종목 제외
            _filing("X3", "매출액또는손익구조30%(대규모법인은15%)이상변경(자회사의 주요경영사항)"),
            _filing("X4", "단일판매ㆍ공급계약체결"),  # 무관 제외
        ]
        payload = {"status": "000", "total_count": str(len(filings))}
        con = _memdb()
        try:
            with mock.patch.object(m, "fetch_all_filings",
                                   return_value=(filings, payload)) as mf:
                m.load_list(con, "KEY", "20240101", "20240930")
            self.assertEqual(mf.call_count, 6)  # 3창 × I·A
            by_window: dict[tuple[str, str], set[str]] = {}
            for c in mf.call_args_list:
                _, ws, we = c.args[:3]
                self.assertEqual(c.kwargs.get("max_pages"), 1000)
                by_window.setdefault((ws, we), set()).add(c.kwargs.get("pblntf_ty"))
            self.assertEqual(len(by_window), 3)
            for tys in by_window.values():
                self.assertEqual(tys, {"I", "A"})
            rows = con.execute(
                "SELECT rcept_no, kind, parse_status FROM earnings_disclosures "
                "ORDER BY rcept_no").fetchall()
            self.assertEqual(rows, [("A1", "annual_report", "skip"),
                                    ("C1", "pnl_change", "pending"),
                                    ("P1", "prelim", "pending")])
            annual = con.execute(
                "SELECT fiscal_year, period FROM earnings_disclosures "
                "WHERE rcept_no='A1'").fetchone()
            self.assertEqual(annual, ("2024", "FY"))
            prelim = con.execute(
                "SELECT fs_basis, is_correction FROM earnings_disclosures "
                "WHERE rcept_no='P1'").fetchone()
            self.assertEqual(prelim, ("CFS", 0))
            # 창 분할: 3개, 각 3개월 이하, 끊김 없이 begin~end 커버
            ws = m.split_windows("20240101", "20240930")
            self.assertEqual(len(ws), 3)
            self.assertEqual(ws[0][0], "20240101")
            self.assertEqual(ws[-1][1], "20240930")
            for a, b in ws:
                da = datetime.strptime(a, "%Y%m%d")
                db = datetime.strptime(b, "%Y%m%d")
                self.assertLessEqual((db - da).days, 92)
            for (_, prev_end), (next_start, _) in zip(ws, ws[1:]):
                self.assertEqual(
                    (datetime.strptime(next_start, "%Y%m%d")
                     - datetime.strptime(prev_end, "%Y%m%d")).days, 1)
        finally:
            con.close()

    def test_partial_load_raises(self):
        con = _memdb()
        try:
            with mock.patch.object(m, "fetch_all_filings",
                                   return_value=([_filing("P1", "영업(잠정)실적(공정공시)")],
                                                 {"status": "000", "total_count": "100"})):
                with self.assertRaises(RuntimeError):
                    m.load_list(con, "KEY", "20240301", "20240331")
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
