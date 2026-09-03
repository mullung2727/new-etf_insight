from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.report_daily_trading_result import (
    format_cause_section,
    format_report,
    load_filled_sells,
    main,
    summarize_trades,
)


class DailyTradingResultTest(unittest.TestCase):
    def test_loads_filled_sells_from_both_strategies_for_requested_kst_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "watchlist.sqlite3"
            with closing(sqlite3.connect(db_path)) as con:
                con.executescript(
                    """
                    CREATE TABLE close_bet_orders (
                        ticker TEXT, cntr_price INTEGER, cntr_qty INTEGER,
                        sell_status TEXT, sell_price INTEGER, sell_qty INTEGER,
                        sold_at TEXT, exit_reason TEXT, pnl_pct REAL,
                        sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,
                        date TEXT
                    );
                    CREATE TABLE pullback_orders (
                        ticker TEXT, buy_price INTEGER, buy_qty INTEGER,
                        sell_status TEXT, sell_price INTEGER, sell_qty INTEGER,
                        sold_at TEXT, exit_reason TEXT, pnl_pct REAL,
                        sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,
                        bought_at TEXT
                    );
                    INSERT INTO close_bet_orders VALUES
                        ('005930', 70000, 2, 'filled', 73500, 2,
                         '2026-08-25T09:01:00+09:00', 'tp', 4.78, 20, 294, 6686, '20260824'),
                        ('035420', 200000, 1, 'ordered', NULL, NULL,
                         NULL, 'forced', NULL, NULL, NULL, NULL, NULL),
                        ('051910', 400000, 1, 'missing', NULL, NULL,
                         '2026-08-25T09:02:00+09:00', 'missing', NULL, NULL, NULL, NULL, NULL);
                    INSERT INTO pullback_orders VALUES
                        ('000660', 200000, 1, 'filled', 194000, 1,
                         '2026-08-25T10:02:00+09:00', 'sl', -0.031, 20, 388, -6408,
                         '2026-08-20T09:05:00+09:00'),
                        ('068270', 180000, 1, 'filled', 185000, 1,
                         '2026-08-24T10:02:00+09:00', 'tp', 0.0278, 20, 370, 4610,
                         '2026-08-21T09:05:00+09:00');
                    """
                )

            rows = load_filled_sells(db_path, "20260825")

        self.assertEqual([row["strategy"] for row in rows], ["종가베팅", "눌림목"])
        self.assertEqual(rows[0]["buy_price"], 70000)
        self.assertEqual(rows[0]["buy_qty"], 2)
        self.assertEqual(rows[1]["buy_price"], 200000)
        self.assertEqual(rows[1]["buy_qty"], 1)
        self.assertEqual(rows[0]["pnl_pct"], 4.78)
        self.assertEqual(rows[1]["pnl_pct"], -3.1)
        # 매수일 출처가 전략마다 다르다. 종가베팅은 `date`(YYYYMMDD), 눌림목은
        # `bought_at` 타임스탬프이므로 둘 다 YYYYMMDD 로 정규화돼 나와야 한다.
        self.assertEqual(rows[0]["bought_date"], "20260824")
        self.assertEqual(rows[1]["bought_date"], "20260820")

        summary, warnings = summarize_trades(rows)
        self.assertEqual(summary["전체"]["count"], 2)
        self.assertEqual(summary["전체"]["invested_amount"], 70000 * 2 + 200000)
        self.assertEqual(summary["전체"]["sell_amount"], 73500 * 2 + 194000)
        self.assertEqual(summary["전체"]["sell_cmsn"], 40)
        self.assertEqual(summary["전체"]["sell_tax"], 682)
        self.assertEqual(summary["전체"]["sell_pl_won"], 278)
        self.assertAlmostEqual(summary["전체"]["return_pct"], 278 / 340000 * 100)
        self.assertEqual(warnings, [])

        report = format_report("20260825", rows, summary, warnings)
        lines = report.splitlines()
        self.assertEqual(lines[0], "[오늘 매매 결과] 2026-08-25")
        self.assertEqual(lines[1], "최종 순실현손익: +278원")
        self.assertEqual(lines[2], "투자원금: 340,000원")
        self.assertEqual(lines[3], "투자원금 대비 손익률: +0.08%")
        self.assertIn("종가베팅: 투자원금 140,000원 / 순손익 +6,686원 (+4.78%)", report)
        self.assertIn("눌림목: 투자원금 200,000원 / 순손익 -6,408원 (-3.20%)", report)
        self.assertNotIn("005930", report)
        self.assertNotIn("000660", report)

    def test_cli_reports_empty_filled_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "watchlist.sqlite3"
            with closing(sqlite3.connect(db_path)) as con:
                con.executescript(
                    """
                    CREATE TABLE close_bet_orders (
                        ticker TEXT, cntr_price INTEGER, cntr_qty INTEGER,
                        sell_status TEXT, sell_price INTEGER, sell_qty INTEGER,
                        sold_at TEXT, exit_reason TEXT, pnl_pct REAL,
                        sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,
                        date TEXT
                    );
                    CREATE TABLE pullback_orders (
                        ticker TEXT, buy_price INTEGER, buy_qty INTEGER,
                        sell_status TEXT, sell_price INTEGER, sell_qty INTEGER,
                        sold_at TEXT, exit_reason TEXT, pnl_pct REAL,
                        sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,
                        bought_at TEXT
                    );
                    """
                )
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(["--date", "20260825", "--watchlist-db", str(db_path)])

        self.assertEqual(code, 0)
        self.assertIn("[오늘 매매 결과] 2026-08-25", stdout.getvalue())
        self.assertIn("오늘 실제 매도 체결 없음", stdout.getvalue())

    def test_missing_actual_values_are_not_reported_as_zero_and_duplicate_ticker_warns(self) -> None:
        rows = [
            {
                "strategy": "종가베팅", "ticker": "005930", "buy_price": 70000,
                "buy_qty": 1, "sell_price": 71000, "sell_qty": 1,
                "sold_at": "2026-08-25T09:00:00+09:00", "exit_reason": "tp",
                "pnl_pct": 1.43, "sell_cmsn": None, "sell_tax": None,
                "sell_pl_won": None,
            },
            {
                "strategy": "눌림목", "ticker": "005930", "buy_price": 70500,
                "buy_qty": 1, "sell_price": 71000, "sell_qty": 1,
                "sold_at": "2026-08-25T09:00:01+09:00", "exit_reason": "tp",
                "pnl_pct": 0.71, "sell_cmsn": 10, "sell_tax": 142,
                "sell_pl_won": 348,
            },
        ]

        summary, warnings = summarize_trades(rows)
        report = format_report("20260825", rows, summary, warnings)

        self.assertEqual(summary["전체"]["unconfirmed_count"], 1)
        self.assertIn("종가베팅 005930: 실제 비용/손익 미확정", warnings)
        self.assertIn("005930: 두 전략에 동시에 포함되어 합계 중복 가능", warnings)
        self.assertIn("최종 순실현손익: 미확정", report)
        self.assertIn("투자원금 대비 손익률: 미확정", report)
        self.assertIn("실제 비용/손익 미확정 1건", report)
        self.assertNotIn("최종 순실현손익: +348원", report)


class CauseSectionTest(unittest.TestCase):
    def _record(self, ticker: str, name: str, pnl: float, judgement: dict | None) -> dict:
        return {
            "ticker": ticker,
            "name": name,
            "trades": [{"strategy": "눌림목", "pnl_pct": pnl}],
            "judgement": judgement,
        }

    def test_lists_grade_distribution_and_orders_by_return(self) -> None:
        evidence = {
            "grade_counts": {"A": 1, "B": 0, "C": 0, "D": 0, "E": 1},
            "warnings": ["buy_rationale_missing:000660", "downgraded_no_source:000660:A"],
            "tickers": [
                self._record("000660", "하이닉스", -3.49, {
                    "grade": "E", "cause": "원인 불명", "evidence_refs": [],
                    "buy_rationale_match": "unknown", "reasoning": "근거 없음",
                }),
                self._record("005930", "삼성전자", 4.84, {
                    "grade": "A", "cause": "공급계약 체결", "evidence_refs": ["R1"],
                    "buy_rationale_match": "same_sustained", "reasoning": "공시 확인",
                }),
            ],
        }

        section = format_cause_section(evidence)
        lines = section.splitlines()

        self.assertIn("등락 원인 (A 1 / B 0 / C 0 / D 0 / E 1)", lines[1])
        self.assertEqual(lines[2], "- [A] 삼성전자 +4.84% · 공급계약 체결 (매수근거 그대로)")
        self.assertEqual(lines[3], "- [E] 하이닉스 -3.49% · 원인 불명")
        # 수집 단계 경고는 보고문에 안 넣는다. 등급 신뢰도 경고만 사람이 봐야 한다.
        self.assertIn("- ⚠️ downgraded_no_source:000660:A", lines)
        self.assertNotIn("- ⚠️ buy_rationale_missing:000660", lines)

    def test_failed_judgement_is_shown_without_inventing_a_cause(self) -> None:
        evidence = {
            "grade_counts": {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0},
            "warnings": ["grade_failed:005930:RuntimeError"],
            "tickers": [self._record("005930", "삼성전자", 1.0, None)],
        }

        section = format_cause_section(evidence)

        self.assertIn("- [–] 삼성전자 +1.00% · 판정 실패", section)
        self.assertIn("- ⚠️ grade_failed:005930:RuntimeError", section)

    def test_cause_analysis_failure_never_kills_the_money_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "watchlist.sqlite3"
            with closing(sqlite3.connect(db_path)) as con:
                con.executescript(
                    """
                    CREATE TABLE close_bet_orders (
                        ticker TEXT, cntr_price REAL, cntr_qty INTEGER, sell_status TEXT,
                        sell_price REAL, sell_qty INTEGER, sold_at TEXT, exit_reason TEXT,
                        pnl_pct REAL, sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,
                        date TEXT
                    );
                    CREATE TABLE pullback_orders (
                        ticker TEXT, buy_price REAL, buy_qty INTEGER, sell_status TEXT,
                        sell_price REAL, sell_qty INTEGER, sold_at TEXT, exit_reason TEXT,
                        pnl_pct REAL, sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,
                        bought_at TEXT
                    );
                    """
                )
                con.execute(
                    "INSERT INTO close_bet_orders VALUES "
                    "('005930',70000,10,'filled',71000,10,'2026-08-28 15:19:03','tp',"
                    "0.0142,100,110,9790,'20260827')"
                )
                con.commit()

            buffer = StringIO()
            with redirect_stdout(buffer):
                # llm_scores 테이블이 없어 원인 분석이 통째로 터진다.
                code = main(["--date", "20260828", "--watchlist-db", str(db_path), "--cause"])

            output = buffer.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("최종 순실현손익: +9,790원", output)
            self.assertIn("등락 원인 분석 실패: OperationalError", output)


if __name__ == "__main__":
    unittest.main()
