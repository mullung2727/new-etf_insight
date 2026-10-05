"""재무 응답 이력(§12) 테스트 — legacy snapshot·미래 보존·충돌·회귀·오류 구분.

네트워크 없이 FakeSession/임시 SQLite로 검증한다. 실행·검증은 호출 에이전트가 담당.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import build_financial_indicators as bfi  # noqa: E402

DartAPIError = bfi.DartAPIError
KEY = "TESTKEY-DO-NOT-LEAK"
CORP = "00126380"
RA = "20240321001031"
RB = "20240430001183"
RC = "20240515001234"

OLD_SCHEMA = """
CREATE TABLE corps (
    corp_code  TEXT PRIMARY KEY,
    stock_code TEXT NOT NULL,
    corp_name  TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE indicators (
    corp_code   TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    idx_cl_code TEXT NOT NULL,
    idx_code    TEXT NOT NULL,
    idx_nm      TEXT,
    idx_val     REAL,
    stock_code  TEXT,
    stlm_dt     TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (corp_code, bsns_year, reprt_code, idx_code)
);
CREATE TABLE accounts (
    corp_code   TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    fs_div      TEXT NOT NULL,
    sj_div      TEXT,
    account_nm  TEXT NOT NULL,
    amount      REAL,
    stock_code  TEXT,
    currency    TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div, account_nm)
);
"""


def acnt_row(rcept, amount, corp=CORP, year="2024", reprt="11011",
             sj="BS", nm="매출액", fs="CFS"):
    r = {"corp_code": corp, "bsns_year": year, "reprt_code": reprt, "fs_div": fs,
         "sj_div": sj, "account_nm": nm, "thstrm_amount": str(amount),
         "stock_code": "005930", "currency": "KRW"}
    if rcept is not None:
        r["rcept_no"] = rcept
    return r


def indx_row(val, corp=CORP, year="2024", reprt="11011", cat="M210000", code="M211550"):
    return {"corp_code": corp, "bsns_year": year, "reprt_code": reprt, "idx_cl_code": cat,
            "idx_code": code, "idx_nm": "ROE", "idx_val": str(val),
            "stock_code": "005930", "stlm_dt": "2024-12-31"}


def hist_count(con, source=None):
    if source is None:
        return con.execute("SELECT COUNT(*) FROM financial_response_history").fetchone()[0]
    return con.execute(
        "SELECT COUNT(*) FROM financial_response_history WHERE source=?", (source,)).fetchone()[0]


def hist_rows(con, source):
    return con.execute(
        "SELECT history_id, corp_code, bsns_year, reprt_code, idx_cl_code, rcept_no, rcept_dt,"
        " payload_hash, payload_json, provenance_json,"
        " first_payload_collected_at, last_observed_at"
        " FROM financial_response_history WHERE source=? ORDER BY history_id", (source,)).fetchall()


class MemDB(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        bfi.ensure_schema(self.con)
        self.con.commit()

    def tearDown(self):
        self.con.close()


class LegacyMigrationTest(unittest.TestCase):
    def _old_db(self):
        con = sqlite3.connect(":memory:")
        con.executescript(OLD_SCHEMA)
        return con

    def test_old_schema_migrates_with_legacy_snapshot(self):
        con = self._old_db()
        try:
            con.execute(
                "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (CORP, "2024", "11011", "CFS", "BS", "매출액", 100.0,
                 "005930", "KRW", "2020-01-01T00:00:00+00:00"))
            con.execute(
                "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (CORP, "2024", "11011", "CFS", "IS", "영업이익", 10.0,
                 "005930", "KRW", "2020-01-01T00:00:00+00:00"))
            con.execute(
                "INSERT INTO indicators VALUES (?,?,?,?,?,?,?,?,?,?)",
                (CORP, "2024", "11011", "M210000", "M211550", "ROE", 8.1,
                 "005930", "2024-12-31", "2020-01-01T00:00:00+00:00"))
            con.commit()
            bfi.ensure_schema(con)
            # current 값·updated_at 그대로, 신규 컬럼 NULL
            got = con.execute(
                "SELECT amount, updated_at, rcept_no, first_payload_collected_at, history_id"
                " FROM accounts WHERE account_nm='매출액'").fetchone()
            self.assertEqual(got, (100.0, "2020-01-01T00:00:00+00:00", None, None, None))
            # legacy 2행, receipt/first NULL, 원본 updated_at 보존
            self.assertEqual(hist_count(con, "legacy_accounts"), 1)
            self.assertEqual(hist_count(con, "legacy_indicators"), 1)
            leg = hist_rows(con, "legacy_accounts")[0]
            self.assertIsNone(leg[5])   # rcept_no
            self.assertIsNone(leg[10])  # first
            self.assertIsNotNone(leg[11])  # last
            self.assertIn("2020-01-01T00:00:00+00:00", leg[8])
            legi = hist_rows(con, "legacy_indicators")[0]
            self.assertEqual(legi[4], "M210000")
            self.assertIsNone(legi[5])
            self.assertIsNone(legi[10])
            # 뷰·인덱스 존재, 지표는 UNIQUE 제외
            con.execute("SELECT * FROM v_key_indicators LIMIT 1").fetchall()
            con.execute("SELECT * FROM v_key_accounts LIMIT 1").fetchall()
            idx_sql = con.execute(
                "SELECT sql FROM sqlite_master WHERE name='ux_financial_response_history'"
            ).fetchone()[0]
            self.assertIn("COALESCE(rcept_no, '')", idx_sql)
            self.assertIn("WHERE source <> 'indicators'", idx_sql)
        finally:
            con.close()

    def test_migration_idempotent_no_fresh_legacy(self):
        con = self._old_db()
        try:
            con.execute(
                "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (CORP, "2024", "11011", "CFS", "BS", "매출액", 100.0,
                 "005930", "KRW", "2020-01-01T00:00:00+00:00"))
            con.commit()
            bfi.ensure_schema(con)
            con.commit()
            self.assertEqual(hist_count(con), 1)
            bfi.ensure_schema(con)  # 재실행 무변화
            self.assertEqual(hist_count(con), 1)
            con.execute("UPDATE accounts SET amount=999 WHERE account_nm='매출액'")
            con.commit()
            bfi.ensure_schema(con)  # 바뀐 current가 새 legacy가 되면 안 됨
            self.assertEqual(hist_count(con), 1)
            leg = hist_rows(con, "legacy_accounts")[0]
            self.assertIn("2020-01-01T00:00:00+00:00", leg[8])
        finally:
            con.close()

    def test_fresh_db_no_legacy(self):
        con = sqlite3.connect(":memory:")
        try:
            bfi.ensure_schema(con)
            self.assertEqual(hist_count(con), 0)
            bfi.upsert_accounts(con, [acnt_row(RA, 100)])
            self.assertEqual(hist_count(con, "legacy_accounts"), 0)
            self.assertEqual(hist_count(con, "accounts"), 1)
        finally:
            con.close()


class AccountsHistoryTest(MemDB):
    def test_abc_three_ids_latest_c(self):
        con = self.con
        bfi.upsert_accounts(con, [acnt_row(RA, 100)])
        bfi.upsert_accounts(con, [acnt_row(RB, 200)])
        bfi.upsert_accounts(con, [acnt_row(RC, 300)])
        rows = hist_rows(con, "accounts")
        self.assertEqual(len(rows), 3)
        self.assertEqual([r[5] for r in rows], [RA, RB, RC])
        cur = con.execute(
            "SELECT amount, rcept_no, history_id, first_payload_collected_at"
            " FROM accounts WHERE account_nm='매출액'").fetchone()
        self.assertEqual(cur[0], 300.0)
        self.assertEqual(cur[1], RC)
        self.assertEqual(cur[2], rows[2][0])
        self.assertIsNotNone(cur[3])

    def test_same_payload_repeat_first_stable(self):
        con = self.con
        bfi.upsert_accounts(con, [acnt_row(RA, 100)])
        before = hist_rows(con, "accounts")[0]
        bfi.upsert_accounts(con, [acnt_row(RA, 100)])
        after = hist_rows(con, "accounts")
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0][0], before[0])
        self.assertEqual(after[0][10], before[10])
        self.assertIsNotNone(after[0][11])

    def test_older_receipt_history_only(self):
        con = self.con
        bfi.upsert_accounts(con, [acnt_row(RC, 300)])
        bfi.upsert_accounts(con, [acnt_row(RA, 111)])  # 오래된 번호·다른 내용
        self.assertEqual(hist_count(con, "accounts"), 2)
        cur = con.execute("SELECT amount, rcept_no FROM accounts WHERE account_nm='매출액'").fetchone()
        self.assertEqual(cur, (300.0, RC))
        bfi.upsert_accounts(con, [acnt_row(RA, 111)])  # 같은 내용 반복도 current 유지
        self.assertEqual(hist_count(con, "accounts"), 2)
        cur = con.execute("SELECT amount, rcept_no FROM accounts WHERE account_nm='매출액'").fetchone()
        self.assertEqual(cur, (300.0, RC))

    def test_unknown_receipt_never_wipes_known(self):
        con = self.con
        bfi.upsert_accounts(con, [acnt_row(RC, 300)])
        bfi.upsert_accounts(con, [acnt_row(None, 999)])
        self.assertEqual(hist_count(con, "accounts"), 2)
        nulls = [r for r in hist_rows(con, "accounts") if r[5] is None]
        self.assertEqual(len(nulls), 1)
        cur = con.execute("SELECT amount, rcept_no FROM accounts WHERE account_nm='매출액'").fetchone()
        self.assertEqual(cur, (300.0, RC))

    def test_missing_receipt_first_write_ok(self):
        con = self.con
        self.assertEqual(bfi.upsert_accounts(con, [acnt_row(None, 100)]), 1)
        cur = con.execute("SELECT amount, rcept_no FROM accounts WHERE account_nm='매출액'").fetchone()
        self.assertEqual(cur, (100.0, None))

    def test_trigger_a_response_c_no_misattach(self):
        con = self.con
        filing_a = {"rcept_no": RA, "rcept_dt": "20240321", "corp_code": CORP,
                    "corp_name": "삼성", "report_nm": "사업보고서 (2023.12)"}
        ctx = {"mode": "run_from_filings", "filed_on": "20240321", "trigger_filings": [filing_a]}
        bfi.upsert_accounts(con, [acnt_row(RC, 300)],
                            request_context=ctx, filings_by_rcept={RA: filing_a})
        row = hist_rows(con, "accounts")[0]
        self.assertEqual(row[5], RC)   # 응답 번호 그대로
        self.assertIsNone(row[6])      # 트리거 A 날짜를 C에 붙이면 안 됨
        prov = json.loads(row[9])
        self.assertIsNone(prov["matched_filing"])
        self.assertEqual(prov["request"]["trigger_filings"], [filing_a])
        # 일치 메타가 있을 때만 rcept_dt
        filing_c = {"rcept_no": RC, "rcept_dt": "20240515", "corp_code": CORP,
                    "corp_name": "삼성", "report_nm": "사업보고서 (2023.12)"}
        bfi.upsert_accounts(con, [acnt_row(RC, 301)],
                            request_context=ctx, filings_by_rcept={RC: filing_c})
        rows = hist_rows(con, "accounts")
        self.assertEqual(rows[1][6], "20240515")
        self.assertEqual(json.loads(rows[1][9])["matched_filing"]["rcept_no"], RC)

    def test_provenance_redacts_secret(self):
        con = self.con
        bfi.upsert_accounts(con, [acnt_row(RA, 100)],
                            request_context={"crtfc_key": KEY, "note": "x"})
        prov = hist_rows(con, "accounts")[0][9]
        self.assertNotIn(KEY, prov)
        self.assertIn("<redacted>", prov)

    def test_sj_div_collision_with_existing(self):
        con = self.con
        bfi.upsert_accounts(con, [acnt_row(RA, 100, sj="BS")])
        n = bfi.upsert_accounts(con, [acnt_row(RB, 200, sj="IS")])
        self.assertEqual(n, 0)  # 허위 쓰기 수 금지
        self.assertEqual(hist_count(con, "accounts"), 2)  # 이력은 보존
        cur = con.execute(
            "SELECT sj_div, amount, rcept_no FROM accounts WHERE account_nm='매출액'").fetchone()
        self.assertEqual(cur, ("BS", 100.0, RA))
        prov = json.loads(hist_rows(con, "accounts")[1][9])
        self.assertEqual(prov["collisions"][0]["kind"], "existing_sj_div_conflict")

    def test_sj_div_collision_within_input(self):
        con = self.con
        n = bfi.upsert_accounts(con, [acnt_row(RA, 100, sj="BS"), acnt_row(RA, 200, sj="IS")])
        self.assertEqual(n, 0)
        self.assertEqual(hist_count(con, "accounts"), 1)
        self.assertIsNone(con.execute(
            "SELECT amount FROM accounts WHERE account_nm='매출액'").fetchone())
        prov = json.loads(hist_rows(con, "accounts")[0][9])
        self.assertEqual(prov["collisions"][0]["kind"], "incoming_sj_div_conflict")

    def test_omitted_rows_not_deleted(self):
        con = self.con
        bfi.upsert_accounts(con, [acnt_row(RA, 100, nm="매출액"), acnt_row(RA, 10, nm="영업이익")])
        bfi.upsert_accounts(con, [acnt_row(RB, 150, nm="매출액")])
        kept = con.execute(
            "SELECT amount, rcept_no FROM accounts WHERE account_nm='영업이익'").fetchone()
        self.assertEqual(kept, (10.0, RA))

    def test_atomic_rollback_on_current_failure(self):
        con = self.con
        con.execute(
            "CREATE TRIGGER trg_fail BEFORE INSERT ON accounts WHEN NEW.account_nm='BOOM'"
            " BEGIN SELECT RAISE(ABORT, 'boom'); END")
        with self.assertRaises(Exception):
            bfi.upsert_accounts(con, [acnt_row(RA, 100), acnt_row(RA, 1, nm="BOOM")])
        self.assertEqual(hist_count(con, "accounts"), 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)


class IndicatorsHistoryTest(MemDB):
    def test_aba_three_observations(self):
        con = self.con
        bfi.upsert_indicators(con, [indx_row("8.1")])
        bfi.upsert_indicators(con, [indx_row("8.2")])
        bfi.upsert_indicators(con, [indx_row("8.1")])
        rows = hist_rows(con, "indicators")
        self.assertEqual(len(rows), 3)
        cur = con.execute(
            "SELECT idx_val, history_id, first_payload_collected_at"
            " FROM indicators WHERE idx_code='M211550'").fetchone()
        self.assertEqual(cur[0], 8.1)
        self.assertEqual(cur[1], rows[2][0])
        self.assertIsNotNone(cur[2])

    def test_same_content_repeat_updates_last_only(self):
        con = self.con
        bfi.upsert_indicators(con, [indx_row("8.1")])
        before = hist_rows(con, "indicators")[0]
        bfi.upsert_indicators(con, [indx_row("8.1")])
        after = hist_rows(con, "indicators")
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0][10], before[10])

    def test_no_receipt_attached(self):
        con = self.con
        bfi.upsert_indicators(con, [indx_row("8.1")], filings_by_rcept={RA: {"rcept_dt": "20240321"}})
        row = hist_rows(con, "indicators")[0]
        self.assertIsNone(row[5])
        self.assertIsNone(row[6])
        self.assertIsNone(json.loads(row[9])["matched_filing"])

    def test_atomic_rollback_on_current_failure(self):
        con = self.con
        con.execute(
            "CREATE TRIGGER trg_fail BEFORE INSERT ON indicators WHEN NEW.idx_code='BOOM'"
            " BEGIN SELECT RAISE(ABORT, 'boom'); END")
        with self.assertRaises(Exception):
            bfi.upsert_indicators(con, [indx_row("8.1"), indx_row("1.0", code="BOOM")])
        self.assertEqual(hist_count(con, "indicators"), 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM indicators").fetchone()[0], 0)


class _FakeResp:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def get(self, url, params=None, timeout=None):
        return _FakeResp(self._payload, self._error)


class WrapperErrorTest(unittest.TestCase):
    def test_wrappers_raise_typed_errors_without_key(self):
        for fn, args in (
            (bfi.fetch_indicators_chunk, ([CORP], "2024", "11011", "M210000")),
            (bfi.fetch_accounts_chunk, ([CORP], "2024", "11011")),
        ):
            with self.subTest(fn=fn.__name__):
                sess = _FakeSession({"status": "013", "message": "no data"})
                self.assertEqual(fn(*args, KEY, sess), [])
                for status in ("014", "020", "901"):
                    sess = _FakeSession({"status": status, "message": f"bad {KEY}"})
                    with self.assertRaises(DartAPIError) as cm:
                        fn(*args, KEY, sess)
                    self.assertEqual(cm.exception.status, status)
                    self.assertNotIn(KEY, str(cm.exception))
                with self.assertRaises(DartAPIError):
                    fn(*args, KEY, _FakeSession({"status": "000"}))
                boom = RuntimeError(f"500 https://x.invalid/?crtfc_key={KEY}")
                with self.assertRaises(DartAPIError) as cm:
                    fn(*args, KEY, _FakeSession(error=boom))
                self.assertNotIn(KEY, str(cm.exception))


def _filing(rcept_no, rcept_dt="20250315"):
    return {"corp_code": CORP, "corp_name": "삼성전자", "stock_code": "005930",
            "corp_cls": "Y", "report_nm": "사업보고서 (2024.12)", "rcept_no": rcept_no,
            "flr_nm": "삼성전자", "rcept_dt": rcept_dt, "rm": ""}


class RunnerHistoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_dart_list(self, fail_cats=(), fail_corps=(), fail_endpoints=()):
        def go(endpoint, params, key, session=None, timeout=None):
            for ep in fail_endpoints:
                if endpoint.endswith(ep):
                    raise DartAPIError("020", "rate limit")
            corps = str(params.get("corp_code", "")).split(",")
            if any(c in fail_corps for c in corps):
                raise DartAPIError("020", "rate limit")
            if endpoint == bfi.ACNT_API_URL:
                return [acnt_row(RC, 300, corp=c) for c in corps if c]
            cat = params.get("idx_cl_code")
            if cat in fail_cats:
                raise DartAPIError("020", "rate limit")
            code = {"M210000": "M211550", "M220000": "M221100",
                    "M230000": "M231000", "M240000": "M241000"}[cat]
            return [indx_row("8.1", corp=c, cat=cat, code=code) for c in corps if c]
        return go

    def _counts(self):
        con = sqlite3.connect(str(self.db))
        try:
            h = con.execute(
                "SELECT source, COUNT(*) FROM financial_response_history GROUP BY source").fetchall()
            a = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            i = con.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
            return dict(h), a, i
        finally:
            con.close()

    def test_run_writes_history_and_force_reuses(self):
        corps = [(CORP, "005930", "삼성전자")]
        with patch.object(bfi, "fetch_listed_corps", return_value=corps), \
             patch.object(bfi, "load_krx_listed_codes", return_value={"005930"}), \
             patch.object(bfi, "fetch_dart_list", side_effect=self._fake_dart_list()), \
             patch("time.sleep"):
            bfi.run(self.db, periods=[("2024", "11011")], key=KEY)
            h, a, i = self._counts()
            self.assertEqual(h.get("accounts"), 1)
            self.assertEqual(h.get("indicators"), 4)
            self.assertEqual(a, 1)
            self.assertEqual(i, 4)
            con = sqlite3.connect(str(self.db))
            try:
                cur = con.execute(
                    "SELECT rcept_no, history_id, first_payload_collected_at FROM accounts").fetchone()
                self.assertEqual(cur[0], RC)
                self.assertIsNotNone(cur[1])
                self.assertIsNotNone(cur[2])
            finally:
                con.close()
            bfi.run(self.db, periods=[("2024", "11011")], force=True, key=KEY)
            h2, _, _ = self._counts()
            self.assertEqual(h2, h)  # 같은 내용 반복은 새 행 없음

    def test_run_from_filings_match(self):
        filing = _filing(RC)
        corps = [(CORP, "005930", "삼성전자")]
        with patch.object(bfi, "fetch_all_filings", return_value=([filing], {})), \
             patch.object(bfi, "fetch_listed_corps", return_value=corps), \
             patch.object(bfi, "fetch_dart_list", side_effect=self._fake_dart_list()), \
             patch("time.sleep"):
            stats = bfi.run_from_filings("20250315", db_path=self.db, key=KEY)
            self.assertEqual(stats["targets"], 1)
            con = sqlite3.connect(str(self.db))
            try:
                row = con.execute(
                    "SELECT rcept_no, rcept_dt, provenance_json FROM financial_response_history"
                    " WHERE source='accounts'").fetchone()
                self.assertEqual(row[0], RC)
                self.assertEqual(row[1], "20250315")
                prov = json.loads(row[2])
                self.assertEqual(prov["matched_filing"]["rcept_no"], RC)
                self.assertEqual(prov["request"]["trigger_filings"][0]["rcept_no"], RC)
                self.assertEqual(prov["request"]["filed_on"], "20250315")
                self.assertNotIn(KEY, row[2])
            finally:
                con.close()

    def test_category_partial_error_old_values_remain(self):
        corps = [(CORP, "005930", "삼성전자")]
        with patch.object(bfi, "fetch_listed_corps", return_value=corps), \
             patch.object(bfi, "load_krx_listed_codes", return_value={"005930"}), \
             patch.object(bfi, "fetch_dart_list", side_effect=self._fake_dart_list()), \
             patch("time.sleep"):
            bfi.run(self.db, periods=[("2024", "11011")], sources=["indicators"], key=KEY)
        con = sqlite3.connect(str(self.db))
        try:
            con.execute("UPDATE indicators SET idx_val=9.9 WHERE idx_code='M221100'")
            con.commit()
        finally:
            con.close()
        with patch.object(bfi, "fetch_listed_corps", return_value=corps), \
             patch.object(bfi, "load_krx_listed_codes", return_value={"005930"}), \
             patch.object(bfi, "fetch_dart_list",
                          side_effect=self._fake_dart_list(fail_cats=("M220000",))), \
             patch("time.sleep"):
            bfi.run(self.db, periods=[("2024", "11011")], sources=["indicators"],
                    force=True, key=KEY)
        con = sqlite3.connect(str(self.db))
        try:
            kept = con.execute("SELECT idx_val FROM indicators WHERE idx_code='M221100'").fetchone()
            self.assertEqual(kept, (9.9,))  # 실패 분류 기존값 유지
            n = con.execute(
                "SELECT COUNT(*) FROM financial_response_history"
                " WHERE source='indicators' AND idx_cl_code='M220000'").fetchone()[0]
            self.assertEqual(n, 1)  # 최초 1회분만
        finally:
            con.close()

    def test_chunk_failure_keeps_previous_commits(self):
        corps = [(f"{i:08d}", f"{i:06d}", f"종목{i}") for i in range(1, 12)]  # 11사 → 2 chunk
        krx = {f"{i:06d}" for i in range(1, 12)}
        with patch.object(bfi, "fetch_listed_corps", return_value=corps), \
             patch.object(bfi, "load_krx_listed_codes", return_value=krx), \
             patch.object(bfi, "fetch_dart_list",
                          side_effect=self._fake_dart_list(fail_corps=("00000011",))), \
             patch("time.sleep"):
            with self.assertRaises(DartAPIError):
                bfi.run(self.db, periods=[("2024", "11011")], sources=["accounts"], key=KEY)
        con = sqlite3.connect(str(self.db))
        try:
            a = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            h = con.execute(
                "SELECT COUNT(*) FROM financial_response_history WHERE source='accounts'"
            ).fetchone()[0]
            self.assertEqual(a, 10)  # 첫 chunk commit 유지
            self.assertEqual(h, 10)
        finally:
            con.close()

    def test_accounts_endpoint_error_propagates(self):
        corps = [(CORP, "005930", "삼성전자")]
        with patch.object(bfi, "fetch_listed_corps", return_value=corps), \
             patch.object(bfi, "load_krx_listed_codes", return_value={"005930"}), \
             patch.object(bfi, "fetch_dart_list",
                          side_effect=self._fake_dart_list(fail_endpoints=("fnlttMultiAcnt.json",))), \
             patch("time.sleep"):
            with self.assertRaises(DartAPIError):
                bfi.run(self.db, periods=[("2024", "11011")], sources=["accounts"], key=KEY)
        _, a, _ = self._counts()
        self.assertEqual(a, 0)


if __name__ == "__main__":
    unittest.main()
