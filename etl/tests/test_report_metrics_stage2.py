"""Stage 2 — R1 상승여력 (PLAN §4 Stage 2)."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.report_metrics.metrics import report_upside, resolve_price_now, upside
from scripts.report_metrics.models import ReportFacts


def _facts(**kw) -> ReportFacts:
    base = dict(pdf_key="k", pdf_path="p", stock_code="095570", stock_name="AJ네트웍스",
                broker="신한투자증권", report_date="2026-08-18")
    base.update(kw)
    return ReportFacts(**base)


class Upside(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(upside(6500, 4520), 0.438053, places=6)

    def test_guards_return_none(self):
        for target, price in ((6500, 0), (6500, None), (6500, -10), (None, 4520), (None, None)):
            self.assertIsNone(upside(target, price), f"{target}/{price}")


class ReportUpside(unittest.TestCase):
    def test_split_at_report_and_now(self):
        got = report_upside(_facts(target_price=6500, price_at_report=4520), price_now=5000)
        self.assertAlmostEqual(got["upside_at_report"], 0.438053, places=6)
        self.assertAlmostEqual(got["upside_now"], 0.3000, places=4)

    def test_price_now_missing_stays_none_not_zero(self):
        got = report_upside(_facts(target_price=6500, price_at_report=4520), price_now=None)
        self.assertIsNone(got["upside_now"])
        self.assertAlmostEqual(got["upside_at_report"], 0.438053, places=6)

    def test_printed_mismatch_flag(self):
        ok = report_upside(_facts(target_price=6500, price_at_report=4520, upside_printed=0.438))
        self.assertFalse(ok["upside_printed_mismatch"])
        bad = report_upside(_facts(target_price=6500, price_at_report=4520, upside_printed=0.10))
        self.assertTrue(bad["upside_printed_mismatch"])

    def test_no_printed_upside_means_no_mismatch(self):
        got = report_upside(_facts(target_price=6500, price_at_report=4520))
        self.assertFalse(got["upside_printed_mismatch"])


class ResolvePriceNow(unittest.TestCase):
    def _db(self, tmp: str) -> Path:
        import duckdb

        db = Path(tmp) / "krx.duckdb"
        with duckdb.connect(str(db)) as con:
            con.execute("CREATE TABLE ohlcv (date VARCHAR, ticker VARCHAR, close INTEGER)")
            con.execute("INSERT INTO ohlcv VALUES ('20260904','095570',5000),"
                        "('20260907','095570',5100)")
        return db

    def test_exact_and_previous_trading_day(self):
        with TemporaryDirectory() as tmp:
            db = self._db(tmp)
            self.assertEqual(resolve_price_now("095570", "2026-09-07", db), 5100)
            # 휴장일(토요일) → 직전 거래일 종가
            self.assertEqual(resolve_price_now("095570", "2026-09-05", db), 5000)

    def test_unknown_stock_or_stale_data_returns_none(self):
        with TemporaryDirectory() as tmp:
            db = self._db(tmp)
            self.assertIsNone(resolve_price_now("000000", "2026-09-07", db))
            # 조회일에서 너무 먼 과거 종가는 '현재가'가 아니다
            self.assertIsNone(resolve_price_now("095570", "2026-12-31", db))

    def test_missing_db_returns_none(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_price_now("095570", "2026-09-07", Path(tmp) / "none.duckdb"))


if __name__ == "__main__":
    unittest.main()
