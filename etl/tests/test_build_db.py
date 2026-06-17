"""build_db.sync_to_db SQLite 전환 단위 테스트.

검증 항목:
  1. 산출 파일이 유효한 SQLite DB (sqlite3로 열림) + WAL 모드
  2. etf_records / etf_holdings 스키마 생성, 행수
  3. 레코드 라운드트립 (스칼라 + JSON 컬럼 텍스트 저장)
  4. holdings 다건 저장 (seq 순서)
  5. 멱등 (두 번 실행해도 행수 동일, INSERT OR REPLACE)
  6. _load_records dedup — 같은 etf_key는 최신 rcept_dt 레코드만
"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.build_db import sync_to_db


def _record(etf_key: str, *, rcept_dt: str = "20260601", fund_name: str = "테스트ETF",
            keywords=None, holdings_items=None) -> dict:
    corp, fund = etf_key.split("_", 1)
    return {
        "route": "pdf_holdings_available",
        "summary": {
            "is_pre_listing_etf": True,
            "fund_name": fund_name,
            "asset_manager": "운용사",
            "index": {"name": "지수", "provider": "제공사", "description": "설명"},
            "market_exposure": {"primary_country": "KR"},
            "theme_classification": {
                "theme_status": "confirmed", "theme_bucket": "AI",
                "structure_tags": ["leverage", "thematic"],
                "confidence": 0.9, "evidence": "근거",
            },
            "holdings": {
                "available_in_pdf": True, "summary": "요약",
                "items": holdings_items if holdings_items is not None else [
                    {"name": "삼성전자", "ticker": "005930", "exchange": "KRX", "weight": "10%"},
                    {"name": "SK하이닉스", "ticker": "000660", "exchange": "KRX", "weight": "8%"},
                ],
            },
            "keywords": keywords if keywords is not None else ["AI", "반도체"],
            "trend_summary": "추세",
            "missing_info": [],
        },
        "source": {
            "rcept_no": "R" + rcept_dt, "rcept_dt": rcept_dt,
            "corp_code": corp, "corp_name": "운용사",
            "report_nm": "상장지수투자신탁(주식)", "fund_code": fund,
            "etf_key": etf_key, "pdf_path": "x.pdf",
        },
        "first_rcept_dt": rcept_dt,
        "revision_count": 0,
    }


def _write_record(runs_dir: Path, record: dict, range_dir: str = "20260601_20260601") -> None:
    rec_dir = runs_dir / range_dir / "records"
    rec_dir.mkdir(parents=True, exist_ok=True)
    etf_key = record["source"]["etf_key"]
    (rec_dir / f"{etf_key}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )


class BuildDbSqliteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.runs = self.tmp / "runs"
        self.db = self.tmp / "db" / "etf_insight.sqlite3"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _query(self, sql, params=()):
        con = sqlite3.connect(str(self.db))
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def test_produces_valid_sqlite_file(self):
        _write_record(self.runs, _record("00100000_A001"))
        sync_to_db(self.runs, self.db)
        self.assertTrue(self.db.exists())
        # sqlite3로 열려야 함 (duckdb 파일이면 여기서 DatabaseError)
        tables = {r[0] for r in self._query(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertIn("etf_records", tables)
        self.assertIn("etf_holdings", tables)

    def test_wal_mode(self):
        _write_record(self.runs, _record("00100000_A001"))
        sync_to_db(self.runs, self.db)
        mode = self._query("PRAGMA journal_mode")[0][0]
        self.assertEqual(mode.lower(), "wal")

    def test_row_counts_and_roundtrip(self):
        _write_record(self.runs, _record("00100000_A001", fund_name="알파ETF"))
        n = sync_to_db(self.runs, self.db)
        self.assertEqual(n, 1)
        self.assertEqual(self._query("SELECT COUNT(*) FROM etf_records")[0][0], 1)
        self.assertEqual(self._query("SELECT COUNT(*) FROM etf_holdings")[0][0], 2)
        row = self._query(
            "SELECT fund_name, primary_country, theme_bucket, keywords, structure_tags "
            "FROM etf_records WHERE etf_key=?", ("00100000_A001",)
        )[0]
        self.assertEqual(row[0], "알파ETF")
        self.assertEqual(row[1], "KR")
        self.assertEqual(row[2], "AI")
        # JSON 컬럼은 텍스트로 저장 → 파싱 가능
        self.assertEqual(json.loads(row[3]), ["AI", "반도체"])
        self.assertEqual(json.loads(row[4]), ["leverage", "thematic"])

    def test_holdings_order(self):
        _write_record(self.runs, _record("00100000_A001"))
        sync_to_db(self.runs, self.db)
        rows = self._query(
            "SELECT seq, name, ticker FROM etf_holdings WHERE etf_key=? ORDER BY seq",
            ("00100000_A001",),
        )
        self.assertEqual([r[1] for r in rows], ["삼성전자", "SK하이닉스"])
        self.assertEqual([r[0] for r in rows], [0, 1])

    def test_idempotent(self):
        _write_record(self.runs, _record("00100000_A001"))
        sync_to_db(self.runs, self.db)
        sync_to_db(self.runs, self.db)  # 재실행
        self.assertEqual(self._query("SELECT COUNT(*) FROM etf_records")[0][0], 1)
        self.assertEqual(self._query("SELECT COUNT(*) FROM etf_holdings")[0][0], 2)

    def test_dedup_keeps_latest_rcept_dt(self):
        # 같은 etf_key, 다른 날짜 — 최신만 남아야
        _write_record(self.runs, _record("00100000_A001", rcept_dt="20260601", fund_name="구버전"),
                      range_dir="20260601_20260601")
        _write_record(self.runs, _record("00100000_A001", rcept_dt="20260610", fund_name="신버전"),
                      range_dir="20260610_20260610")
        n = sync_to_db(self.runs, self.db)
        self.assertEqual(n, 1)
        row = self._query("SELECT fund_name FROM etf_records WHERE etf_key=?", ("00100000_A001",))[0]
        self.assertEqual(row[0], "신버전")


if __name__ == "__main__":
    unittest.main()
