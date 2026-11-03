"""build_dividends 테스트 (unittest, 네트워크 없음)."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402
import build_dividends as m  # noqa: E402


def _tup(value, value_raw, src_year, fiscal_year="2024"):
    """같은 PK(fiscal 2024·주당 현금배당금·보통주 '')의 dividends 11-튜플."""
    return ("00126380", "005930", fiscal_year, "주당 현금배당금(원)", "",
            value, value_raw, src_year, "11011", "r1", "20241231")


class TestExpandRows(unittest.TestCase):
    def test_thstrm_frmtrm_lwfr(self):
        rows = [{
            "rcept_no": "20250311000123", "corp_cls": "Y", "corp_code": "00126380",
            "corp_name": "삼성전자", "se": "주당 현금배당금(원)", "stock_knd": None,
            "thstrm": "1,500", "frmtrm": "-", "lwfr": "",
            "stlm_dt": "20241231",
        }]
        got = m.expand_rows(rows, "2025", "11011", "005930")
        self.assertEqual(len(got), 2)  # lwfr "" 탈락
        by_year = {t[2]: t for t in got}
        # 당기: 쉼표 제거 float
        self.assertEqual(by_year["2025"][5], 1500.0)
        self.assertEqual(by_year["2025"][6], "1,500")
        # 전기: "-" 저장, value None
        self.assertIsNone(by_year["2024"][5])
        self.assertEqual(by_year["2024"][6], "-")
        self.assertNotIn("2023", by_year)
        for t in got:
            self.assertEqual(
                t[:5] + t[7:],
                ("00126380", "005930", t[2], "주당 현금배당금(원)", "",
                 "2025", "11011", "20250311000123", "20241231"),
            )


class TestUpsertDividends(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        m.ensure_schema(self.con)

    def tearDown(self):
        self.con.close()

    def _one(self):
        return self.con.execute(
            "SELECT value, value_raw, src_year FROM dividends"
            " WHERE corp_code='00126380' AND fiscal_year='2024'"
            " AND se='주당 현금배당금(원)' AND stock_knd=''"
        ).fetchone()

    def test_latest_src_year_wins(self):
        self.assertEqual(m.upsert_dividends(self.con, [_tup(100.0, "100", "2025")]), 1)
        # 과거 보고서(src 2024) → 기존 100 유지, 쓰기 0
        self.assertEqual(m.upsert_dividends(self.con, [_tup(90.0, "90", "2024")]), 0)
        self.assertEqual(self._one(), (100.0, "100", "2025"))
        # 최신 보고서(src 2026) → 110으로 덮어씀
        self.assertEqual(m.upsert_dividends(self.con, [_tup(110.0, "110", "2026")]), 1)
        self.assertEqual(self._one(), (110.0, "110", "2026"))


if __name__ == "__main__":
    unittest.main()
