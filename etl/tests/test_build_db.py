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

from scripts.build_db import get_db_latest_snapshot, sync_to_db, write_history_observation


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


class BuildDbHistoryTest(unittest.TestCase):
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

    def _history_full_rows(self):
        return self._query(
            "SELECT etf_key, rcept_no, rcept_dt, first_collected_at, action, reason, filing_json, record_json "
            "FROM etf_filing_history ORDER BY rcept_no"
        )

    def test_history_preserves_every_dated_json_before_dedup(self):
        old = _record("00200000_B001", rcept_dt="20260601", fund_name="구버전")
        new = _record("00200000_B001", rcept_dt="20260610", fund_name="신버전")
        new["revision_count"] = 1
        new["first_rcept_dt"] = "20260601"
        _write_record(self.runs, old, range_dir="20260601_20260601")
        _write_record(self.runs, new, range_dir="20260610_20260610")

        n = sync_to_db(self.runs, self.db)
        rows = self._history_full_rows()

        self.assertEqual(n, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r[1] for r in rows], ["R20260601", "R20260610"])
        self.assertTrue(all(r[7] is not None for r in rows))
        # legacy JSON에 수집시각 없음 → 위조 없이 NULL
        self.assertTrue(all(r[3] is None for r in rows))
        self.assertEqual(
            self._query("SELECT fund_name FROM etf_records WHERE etf_key=?", ("00200000_B001",))[0][0],
            "신버전",
        )

    def test_first_meta_resolved_from_earliest_when_latest_missing(self):
        old = _record("00200000_B002", rcept_dt="20260601", fund_name="구버전")
        new = _record("00200000_B002", rcept_dt="20260610", fund_name="신버전")
        new["revision_count"] = 1
        new["first_rcept_dt"] = "20260601"
        _write_record(self.runs, old, range_dir="20260601_20260601")
        _write_record(self.runs, new, range_dir="20260610_20260610")

        sync_to_db(self.runs, self.db)
        row = self._query(
            "SELECT first_rcept_no, first_rcept_dt, first_collected_at FROM etf_records WHERE etf_key=?",
            ("00200000_B002",),
        )[0]

        self.assertEqual(row[0], "R20260601")
        self.assertEqual(row[1], "20260601")
        self.assertIsNone(row[2])

    def test_sync_twice_keeps_history_first_and_snapshot_identical(self):
        old = _record("00200000_B003", rcept_dt="20260601", fund_name="구버전")
        new = _record("00200000_B003", rcept_dt="20260610", fund_name="신버전")
        new["revision_count"] = 1
        new["first_rcept_dt"] = "20260601"
        new["first_rcept_no"] = "R20260601"
        new["first_collected_at"] = "2026-06-01T00:00:00+00:00"
        new["collected_at"] = "2026-06-10T00:00:00+00:00"
        _write_record(self.runs, old, range_dir="20260601_20260601")
        _write_record(self.runs, new, range_dir="20260610_20260610")

        sync_to_db(self.runs, self.db)
        first_history = self._history_full_rows()
        first_meta = self._query(
            "SELECT first_rcept_no, first_rcept_dt, first_collected_at, revision_count "
            "FROM etf_records WHERE etf_key=?", ("00200000_B003",)
        )
        sync_to_db(self.runs, self.db)
        second_history = self._history_full_rows()
        second_meta = self._query(
            "SELECT first_rcept_no, first_rcept_dt, first_collected_at, revision_count "
            "FROM etf_records WHERE etf_key=?", ("00200000_B003",)
        )

        self.assertEqual(first_history, second_history)
        self.assertEqual(first_meta, second_meta)
        self.assertEqual(len(second_history), 2)

    def test_existing_db_first_preserved_when_latest_json_loses_meta(self):
        record = _record("00200000_B004", rcept_dt="20260610", fund_name="신버전")
        record["first_rcept_no"] = "R20260601"
        record["first_rcept_dt"] = "20260601"
        record["first_collected_at"] = "2026-06-01T00:00:00+00:00"
        record["collected_at"] = "2026-06-10T00:00:00+00:00"
        _write_record(self.runs, record, range_dir="20260610_20260610")
        sync_to_db(self.runs, self.db)

        stripped = _record("00200000_B004", rcept_dt="20260610", fund_name="신버전")
        stripped["first_rcept_dt"] = "20260601"
        _write_record(self.runs, stripped, range_dir="20260610_20260610")
        sync_to_db(self.runs, self.db)
        row = self._query(
            "SELECT first_rcept_no, first_collected_at FROM etf_records WHERE etf_key=?",
            ("00200000_B004",),
        )[0]

        self.assertEqual(row[0], "R20260601")
        self.assertEqual(row[1], "2026-06-01T00:00:00+00:00")

    def test_new_format_times_flow_into_history(self):
        record = _record("00200000_B005", rcept_dt="20260601", fund_name="최초")
        record["first_rcept_no"] = "R20260601"
        record["first_collected_at"] = "2026-06-01T00:00:00+00:00"
        record["collected_at"] = "2026-06-01T00:00:00+00:00"
        _write_record(self.runs, record, range_dir="20260601_20260601")

        sync_to_db(self.runs, self.db)
        rows = self._history_full_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], "2026-06-01T00:00:00+00:00")
        self.assertEqual(rows[0][4], "created")

    def test_backfill_skips_json_without_rcept_no(self):
        record = _record("00200000_B006", rcept_dt="20260601", fund_name="깨진레코드")
        del record["source"]["rcept_no"]
        _write_record(self.runs, record, range_dir="20260601_20260601")

        n = sync_to_db(self.runs, self.db)

        self.assertEqual(n, 1)
        self.assertEqual(self._history_full_rows(), [])

    def test_new_db_legacy_first_dt_self_dates_preserves_earliest(self):
        old = _record("00200000_B101", rcept_dt="20260601", fund_name="구버전")
        new = _record("00200000_B101", rcept_dt="20260610", fund_name="신버전")
        new["revision_count"] = 1
        old["first_rcept_dt"] = "20260601"
        new["first_rcept_dt"] = "20260610"
        if "first_rcept_no" in old:
            del old["first_rcept_no"]
        if "first_rcept_no" in new:
            del new["first_rcept_no"]
        _write_record(self.runs, old, range_dir="20260601_20260601")
        _write_record(self.runs, new, range_dir="20260610_20260610")

        sync_to_db(self.runs, self.db)
        row = self._query(
            "SELECT first_rcept_no, first_rcept_dt FROM etf_records WHERE etf_key=?",
            ("00200000_B101",),
        )[0]

        self.assertEqual(row[0], "R20260601")
        self.assertEqual(row[1], "20260601")

    def test_existing_db_first_meta_wins_over_conflicting_latest_json(self):
        record = _record("00200000_B102", rcept_dt="20260610", fund_name="신버전")
        record["revision_count"] = 1
        record["first_rcept_no"] = "R20260601"
        record["first_rcept_dt"] = "20260601"
        record["first_collected_at"] = "2026-06-01T00:00:00+00:00"
        record["collected_at"] = "2026-06-10T00:00:00+00:00"
        _write_record(self.runs, record, range_dir="20260610_20260610")
        sync_to_db(self.runs, self.db)

        conflicting = _record("00200000_B102", rcept_dt="20260610", fund_name="신버전")
        conflicting["revision_count"] = 1
        conflicting["source"]["rcept_no"] = record["source"]["rcept_no"]
        conflicting["first_rcept_no"] = "R99999999"
        conflicting["first_rcept_dt"] = "20260610"
        conflicting["first_collected_at"] = "2026-06-10T00:00:00+00:00"
        conflicting["collected_at"] = "2026-06-10T00:00:00+00:00"
        _write_record(self.runs, conflicting, range_dir="20260610_20260610")
        sync_to_db(self.runs, self.db)
        row = self._query(
            "SELECT first_rcept_no, first_rcept_dt, first_collected_at FROM etf_records WHERE etf_key=?",
            ("00200000_B102",),
        )[0]

        self.assertEqual(row[0], "R20260601")
        self.assertEqual(row[1], "20260601")
        self.assertEqual(row[2], "2026-06-01T00:00:00+00:00")

    def test_newer_db_ignores_older_only_json_for_current_row(self):
        new_items = [
            {"name": "신규종목", "ticker": "999999", "exchange": "KRX", "weight": "20%"},
        ]
        new = _record("00200000_B103", rcept_dt="20260610", fund_name="신버전", holdings_items=new_items)
        new["revision_count"] = 1
        new["first_rcept_no"] = "R20260601"
        new["first_rcept_dt"] = "20260601"
        new_range = self.runs / "20260610_20260610" / "records"
        new_range.mkdir(parents=True, exist_ok=True)
        (new_range / "00200000_B103.json").write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        sync_to_db(self.runs, self.db)
        before = self._query(
            "SELECT rcept_no, rcept_dt, fund_name, first_rcept_no, first_rcept_dt FROM etf_records WHERE etf_key=?",
            ("00200000_B103",),
        )[0]
        before_holdings = self._query(
            "SELECT name, ticker FROM etf_holdings WHERE etf_key=? ORDER BY seq",
            ("00200000_B103",),
        )

        (new_range / "00200000_B103.json").unlink()
        old = _record("00200000_B103", rcept_dt="20260601", fund_name="구버전")
        _write_record(self.runs, old, range_dir="20260601_20260601")
        sync_to_db(self.runs, self.db)
        after = self._query(
            "SELECT rcept_no, rcept_dt, fund_name, first_rcept_no, first_rcept_dt FROM etf_records WHERE etf_key=?",
            ("00200000_B103",),
        )[0]
        after_holdings = self._query(
            "SELECT name, ticker FROM etf_holdings WHERE etf_key=? ORDER BY seq",
            ("00200000_B103",),
        )
        history = self._history_full_rows()

        self.assertEqual(after, before)
        self.assertEqual(after_holdings, before_holdings)
        self.assertEqual(len(history), 2)

    def test_same_date_multiple_rcept_no_keeps_largest_regardless_of_order(self):
        for layout, first_dir, second_dir in [
            ("ab", "20260601_a", "20260601_b"),
            ("ba", "20260601_b", "20260601_a"),
        ]:
            with self.subTest(layout=layout):
                import shutil
                import tempfile as _tempfile
                tmp = Path(_tempfile.mkdtemp())
                try:
                    runs = tmp / "runs"
                    db = tmp / "db" / "etf_insight.sqlite3"
                    small = _record("00200000_B104", rcept_dt="20260601", fund_name="작은번호")
                    small["source"]["rcept_no"] = "20260601000001"
                    large = _record("00200000_B104", rcept_dt="20260601", fund_name="큰번호")
                    large["source"]["rcept_no"] = "20260601000002"
                    if layout == "ab":
                        _write_record(runs, small, range_dir=first_dir)
                        _write_record(runs, large, range_dir=second_dir)
                    else:
                        _write_record(runs, large, range_dir=first_dir)
                        _write_record(runs, small, range_dir=second_dir)
                    sync_to_db(runs, db)
                    con = sqlite3.connect(str(db))
                    try:
                        fund = con.execute(
                            "SELECT fund_name FROM etf_records WHERE etf_key=?",
                            ("00200000_B104",),
                        ).fetchone()[0]
                    finally:
                        con.close()
                    self.assertEqual(fund, "큰번호")
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)

    def test_legacy_missing_first_no_with_earlier_first_dt_keeps_null(self):
        record = _record("00200000_B105", rcept_dt="20260610", fund_name="레거시")
        record["first_rcept_dt"] = "20260601"
        if "first_rcept_no" in record:
            del record["first_rcept_no"]
        if "first_collected_at" in record:
            del record["first_collected_at"]
        if "collected_at" in record:
            del record["collected_at"]
        _write_record(self.runs, record, range_dir="20260610_20260610")

        sync_to_db(self.runs, self.db)
        row = self._query(
            "SELECT first_rcept_no, first_rcept_dt, first_collected_at FROM etf_records WHERE etf_key=?",
            ("00200000_B105",),
        )[0]

        self.assertIsNone(row[0])
        self.assertEqual(row[1], "20260601")
        self.assertIsNone(row[2])

    def test_correction_only_first_time_not_fabricated_with_db(self):
        etf_key = "00200000_B201"
        sync_to_db(self.runs, self.db)
        con = sqlite3.connect(str(self.db))
        try:
            con.execute(
                "INSERT INTO etf_records (etf_key, first_rcept_no, first_rcept_dt, first_collected_at) VALUES (?,?,?,?)",
                [etf_key, None, "20260601", None],
            )
            con.commit()
        finally:
            con.close()
        record = _record(etf_key, rcept_dt="20260610", fund_name="정정")
        record["revision_count"] = 1
        record["first_rcept_dt"] = "20260601"
        record.pop("first_rcept_no", None)
        record.pop("first_collected_at", None)
        record["collected_at"] = "2026-06-10T00:00:00+00:00"
        _write_record(self.runs, record, range_dir="20260610_20260610")

        sync_to_db(self.runs, self.db)
        row = self._query(
            "SELECT first_rcept_no, first_rcept_dt, first_collected_at FROM etf_records WHERE etf_key=?",
            (etf_key,),
        )[0]
        history = self._history_full_rows()

        self.assertIsNone(row[0])
        self.assertEqual(row[1], "20260601")
        self.assertIsNone(row[2])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0][1], "R20260610")
        self.assertEqual(history[0][4], "updated")
        self.assertEqual(history[0][3], "2026-06-10T00:00:00+00:00")

    def test_correction_only_first_time_not_fabricated_without_db(self):
        etf_key = "00200000_B202"
        record = _record(etf_key, rcept_dt="20260610", fund_name="정정")
        record["revision_count"] = 1
        record["first_rcept_dt"] = "20260601"
        record.pop("first_rcept_no", None)
        record.pop("first_collected_at", None)
        record["collected_at"] = "2026-06-10T00:00:00+00:00"
        _write_record(self.runs, record, range_dir="20260610_20260610")

        sync_to_db(self.runs, self.db)
        row = self._query(
            "SELECT first_rcept_no, first_rcept_dt, first_collected_at FROM etf_records WHERE etf_key=?",
            (etf_key,),
        )[0]
        history = self._history_full_rows()

        self.assertIsNone(row[0])
        self.assertEqual(row[1], "20260601")
        self.assertIsNone(row[2])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0][3], "2026-06-10T00:00:00+00:00")

    def test_get_db_latest_snapshot_clears_wrong_first_no_when_db_null(self):
        etf_key = "00200000_B203"
        record = _record(etf_key, rcept_dt="20260610", fund_name="정정")
        record["revision_count"] = 1
        record["first_rcept_no"] = "R20260610"
        record["first_rcept_dt"] = "20260601"
        record["first_collected_at"] = "2026-06-10T00:00:00+00:00"
        record["collected_at"] = "2026-06-10T00:00:00+00:00"
        write_history_observation(
            self.db, etf_key, "R20260610", "20260610",
            "2026-06-10T00:00:00+00:00", "updated", "backfilled_from_json",
            dict(record["source"]), record,
        )
        con = sqlite3.connect(str(self.db))
        try:
            con.execute(
                "INSERT INTO etf_records (etf_key, first_rcept_no, first_rcept_dt, first_collected_at) VALUES (?,?,?,?)",
                [etf_key, None, "20260601", None],
            )
            con.commit()
        finally:
            con.close()

        snap = get_db_latest_snapshot(self.db, etf_key)

        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertIsNone(snap.get("first_rcept_no"))
        self.assertEqual(snap.get("first_rcept_dt"), "20260601")
        self.assertIsNone(snap.get("first_collected_at"))

    def test_skipped_no_update_success_json_reconciles_snapshot(self):
        etf_key = "00200000_B204"
        rcept_no, rcept_dt = "R20260610", "20260610"
        t1, t2 = "2026-06-01T00:00:00+00:00", "2026-06-02T00:00:00+00:00"
        write_history_observation(
            self.db, etf_key, rcept_no, rcept_dt, t1, "skipped", "오타 정정",
            {"rcept_no": rcept_no, "custom": "original_filing"}, None,
        )
        record = _record(etf_key, rcept_dt="20260610", fund_name="성공")
        record["source"]["rcept_no"] = rcept_no
        record["revision_count"] = 1
        record["collected_at"] = t2
        _write_record(self.runs, record, range_dir="20260610_20260610")

        sync_to_db(self.runs, self.db)
        history = self._history_full_rows()

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0][1], rcept_no)
        self.assertEqual(history[0][4], "updated")
        self.assertEqual(history[0][3], t1)
        self.assertEqual(json.loads(history[0][6])["custom"], "original_filing")
        self.assertIsNotNone(history[0][7])
        self.assertEqual(json.loads(history[0][7])["summary"]["fund_name"], "성공")


if __name__ == "__main__":
    unittest.main()
