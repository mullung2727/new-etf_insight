"""공시 이력 phase 0 (§10) 테스트 — 목록 수집·XBRL 실증.

네트워크 없이 FakeSession으로 검증한다. 실행·검증은 호출 에이전트가 담당.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import build_financial_indicators as bfi  # noqa: E402

import requests  # noqa: E402

KEY = "TESTKEY-DO-NOT-LEAK"


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", status_code=200, error=None):
        self._payload = payload
        self.content = content
        self.status_code = status_code
        self._error = error

    def raise_for_status(self):
        if self._error is not None:
            raise self._error
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} for url: https://example.invalid/api?crtfc_key={KEY}")

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
        return self.handler(url, params or {})


def mk_filing(rcept_no, report_nm="사업보고서 (2025.12)", rcept_dt="20260315",
              corp_code="00126380", corp_name="삼성전자"):
    return {"corp_code": corp_code, "corp_name": corp_name, "stock_code": "005930",
            "corp_cls": "Y", "report_nm": report_nm, "rcept_no": rcept_no,
            "flr_nm": corp_name, "rcept_dt": rcept_dt, "rm": ""}


def page_payload(rows, total_count, total_page):
    return {"status": "000", "page_no": 1, "page_count": 100,
            "total_count": total_count, "total_page": total_page, "list": rows}


def history_handler(routes, error_routes=None):
    error_routes = error_routes or {}

    def handle(url, params):
        key = (params.get("bgn_de"), params.get("end_de"), int(params.get("page_no")))
        if key in error_routes:
            raise error_routes[key]
        payload = routes.get(key)
        if payload is None:
            return FakeResponse(payload={"status": "013", "message": "no data"})
        return FakeResponse(payload=payload)

    return handle


def windows_of(begin_s, end_s):
    return [(b.strftime("%Y%m%d"), e.strftime("%Y%m%d")) for b, e in
            bfi._split_windows(bfi._parse_yyyymmdd(begin_s), bfi._parse_yyyymmdd(end_s))]


class TmpHistory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.hdir = Path(self._tmp.name) / "hist"

    def tearDown(self):
        self._tmp.cleanup()

    def manifest_text(self):
        return (self.hdir / "manifest.json").read_text(encoding="utf-8")


class HistoryCollectionTest(TmpHistory):
    def test_multi_window_pagination_with_amendments(self):
        wins = windows_of("20240101", "20240410")
        self.assertEqual(len(wins), 2)
        w1 = wins[0]
        page1 = [mk_filing(f"20240115{i:06d}", rcept_dt="20240115") for i in range(100)]
        amend = mk_filing("20240120999999", "[기재정정]사업보고서 (2023.12)", "20240120")
        routes = {(w1[0], w1[1], 1): page_payload(page1, 101, 2),
                  (w1[0], w1[1], 2): page_payload([amend], 101, 2)}
        sess = FakeSession(history_handler(routes))
        m = bfi.run_filings_history("20240101", "20240410", history_dir=self.hdir,
                                    key=KEY, session=sess)
        self.assertTrue(m["complete"])
        self.assertEqual(len(m["filings"]), 101)
        self.assertIn("20240120999999", {r["rcept_no"] for r in m["filings"]})
        self.assertEqual(m["total_calls"], 3)
        self.assertEqual(m["total_calls"], len(sess.calls))
        for c in sess.calls:
            p = c["params"]
            self.assertEqual(p["last_reprt_at"], "N")
            self.assertEqual(p["pblntf_ty"], "A")
            self.assertEqual(p["page_count"], 100)
            self.assertNotIn("corp_code", p)
        self.assertTrue(all(w["complete"] for w in m["windows"]))
        self.assertNotIn(KEY, self.manifest_text())
        self.assertNotIn("crtfc_key", self.manifest_text())

    def test_corp_code_sent_when_given(self):
        w = windows_of("20240101", "20240131")[0]
        routes = {(w[0], w[1], 1): page_payload([mk_filing("20240115000001")], 1, 1)}
        sess = FakeSession(history_handler(routes))
        m = bfi.run_filings_history("20240101", "20240131", corp_code="00126380",
                                    history_dir=self.hdir, key=KEY, session=sess)
        self.assertTrue(m["complete"])
        self.assertEqual(sess.calls[0]["params"]["corp_code"], "00126380")
        self.assertEqual(m["query"]["corp_code"], "00126380")

    def test_count_mismatch_not_complete(self):
        w = windows_of("20240101", "20240131")[0]
        page1 = [mk_filing(f"20240115{i:06d}") for i in range(100)]
        page2 = [mk_filing(f"20240116{i:06d}") for i in range(30)]
        routes = {(w[0], w[1], 1): page_payload(page1, 150, 2),
                  (w[0], w[1], 2): page_payload(page2, 150, 2)}
        sess = FakeSession(history_handler(routes))
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=sess)
        self.assertFalse(m["complete"])
        self.assertFalse(m["windows"][0]["complete"])
        self.assertIn("count_mismatch", m["windows"][0]["error"])
        self.assertEqual(len(m["filings"]), 130)  # 받은 건 보존
        self.assertEqual(m["groups"][0]["earliest_caveat"], bfi.EARLIEST_CAVEAT)

    def test_network_failure_partial_manifest(self):
        w1, w2 = windows_of("20240101", "20240410")
        routes = {(w1[0], w1[1], 1): page_payload([mk_filing("20240115000001")], 1, 1)}
        err = requests.exceptions.ConnectionError(
            f"dial fail url=https://opendart.fss.or.kr/api/list.json?crtfc_key={KEY}")
        sess = FakeSession(history_handler(routes, {(w2[0], w2[1], 1): err}))
        m = bfi.run_filings_history("20240101", "20240410", history_dir=self.hdir,
                                    key=KEY, session=sess)
        self.assertFalse(m["complete"])
        self.assertTrue(m["windows"][0]["complete"])
        self.assertFalse(m["windows"][1]["complete"])
        self.assertIn("http_error", m["windows"][1]["error"])
        self.assertEqual(len(m["filings"]), 1)
        text = self.manifest_text()
        self.assertNotIn(KEY, text)
        self.assertNotIn("https://", text)

    def test_api_error_status_incomplete(self):
        w = windows_of("20240101", "20240131")[0]
        page1 = [mk_filing(f"20240115{i:06d}") for i in range(60)]
        routes = {(w[0], w[1], 1): page_payload(page1, 120, 2),
                  (w[0], w[1], 2): {"status": "020", "message": "한도 초과"}}
        sess = FakeSession(history_handler(routes))
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=sess)
        self.assertFalse(m["complete"])
        self.assertIn("020", m["windows"][0]["error"])
        self.assertEqual(len(m["filings"]), 60)

    def test_non_december_quarter_retained_as_unresolved(self):
        w = windows_of("20240101", "20240131")[0]
        rows = [mk_filing("20240115000001", "분기보고서 (2026.06)", "20240115"),
                mk_filing("20240115000002", "사업보고서 (2025.12)", "20240115")]
        routes = {(w[0], w[1], 1): page_payload(rows, 2, 1)}
        sess = FakeSession(history_handler(routes))
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=sess)
        self.assertTrue(m["complete"])
        self.assertEqual(len(m["filings"]), 2)
        by_key = {(g["report_kind"], g["period_year"], g["end_month"]): g
                  for g in m["groups"]}
        q = by_key[("분기보고서", "2026", "06")]
        self.assertIsNone(q["reprt_code"])
        self.assertTrue(q["reprt_unresolved_reason"].startswith("non_december"))
        self.assertEqual(q["earliest_candidate"]["rcept_no"], "20240115000001")
        self.assertEqual(by_key[("사업보고서", "2025", "12")]["reprt_code"], "11011")

    def test_duplicate_receipts_deduplicated(self):
        w = windows_of("20240101", "20240131")[0]
        a = mk_filing("20240115000001")
        b = mk_filing("20240115000002")
        c = mk_filing("20240115000003")
        routes = {(w[0], w[1], 1): page_payload([a, b], 4, 2),
                  (w[0], w[1], 2): page_payload([b, c], 4, 2)}
        sess = FakeSession(history_handler(routes))
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=sess)
        self.assertFalse(m["complete"])
        self.assertFalse(m["current_query_complete"])
        self.assertFalse(m["windows"][0]["complete"])
        self.assertIn("count_mismatch", m["windows"][0]["error"])
        self.assertEqual([r["rcept_no"] for r in m["filings"]],
                         ["20240115000001", "20240115000002", "20240115000003"])
        self.assertEqual(m["duplicates_skipped"], 1)

    def test_rerun_keeps_first_observed(self):
        w = windows_of("20240101", "20240131")[0]
        a = mk_filing("20240115000001")
        b = mk_filing("20240115000002")

        def run(rows):
            routes = {(w[0], w[1], 1): page_payload(rows, len(rows), 1)}
            return bfi.run_filings_history(
                "20240101", "20240131", history_dir=self.hdir, key=KEY,
                session=FakeSession(history_handler(routes)))

        m1 = run([a])
        m2 = run([a, b])
        t1 = m1["filings"][0]["first_observed_at"]
        got = {r["rcept_no"]: r["first_observed_at"] for r in m2["filings"]}
        self.assertEqual(got["20240115000001"], t1)
        self.assertGreaterEqual(got["20240115000002"], t1)
        self.assertEqual(m2["carried_observations"], 1)

    def test_incompatible_previous_manifest_not_merged(self):
        w = windows_of("20240101", "20240131")[0]
        routes = {(w[0], w[1], 1): page_payload([mk_filing("20240115000001")], 1, 1)}

        def run(corp):
            return bfi.run_filings_history(
                "20240101", "20240131", corp_code=corp, history_dir=self.hdir,
                key=KEY, session=FakeSession(history_handler(routes)))

        m1 = run(None)
        self.assertTrue(m1["complete"])
        with self.assertRaises(ValueError) as ctx:
            run("00126380")
        self.assertIn("incompatible", str(ctx.exception))
        kept = json.loads((self.hdir / "manifest.json").read_text(encoding="utf-8"))
        self.assertIsNone(kept["query"]["corp_code"])
        self.assertEqual(len(kept["filings"]), 1)

    def test_corrupt_previous_manifest_starts_fresh(self):
        self.hdir.mkdir(parents=True)
        (self.hdir / "manifest.json").write_text("NOT JSON{{{", encoding="utf-8")
        w = windows_of("20240101", "20240131")[0]
        routes = {(w[0], w[1], 1): page_payload([mk_filing("20240115000001")], 1, 1)}
        sess = FakeSession(history_handler(routes))
        with self.assertRaises(ValueError) as ctx:
            bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=sess)
        self.assertIn("corrupt", str(ctx.exception).lower())
        self.assertEqual((self.hdir / "manifest.json").read_text(encoding="utf-8"),
                         "NOT JSON{{{")
        self.assertEqual(sess.calls, [])

    def test_earliest_has_range_caveat(self):
        w = windows_of("20240101", "20240131")[0]
        rows = [mk_filing("20240115000002", rcept_dt="20240120"),
                mk_filing("20240115000001", rcept_dt="20240110")]
        routes = {(w[0], w[1], 1): page_payload(rows, 2, 1)}
        sess = FakeSession(history_handler(routes))
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=sess)
        self.assertEqual(m["groups"][0]["earliest_candidate"]["rcept_no"],
                         "20240115000001")
        for g in m["groups"]:
            self.assertEqual(g["earliest_caveat"], bfi.EARLIEST_CAVEAT)
            self.assertIn("lifetime", g["earliest_caveat"])
        self.assertTrue(m["notes"])

    def test_split_windows_bounded(self):
        self.assertEqual(len(windows_of("20240101", "20240330")), 1)
        self.assertEqual(len(windows_of("20240101", "20240331")), 2)
        many = windows_of("20240101", "20241231")
        for b, e in many:
            days = (bfi._parse_yyyymmdd(e) - bfi._parse_yyyymmdd(b)).days + 1
            self.assertLessEqual(days, 90)
        for (_, e1), (b2, _) in zip(many, many[1:]):
            self.assertEqual((bfi._parse_yyyymmdd(b2) - bfi._parse_yyyymmdd(e1)).days, 1)
        self.assertEqual(many[0][0], "20240101")
        self.assertEqual(many[-1][1], "20241231")

    def test_invalid_inputs_raise_before_write(self):
        cases = [("2024-01-01", "20240131", None),
                 ("20240201", "20240101", None),
                 ("20240230", "20240301", None),
                 ("20240101", "20240131", "ABC"),
                 ("20240101", "20240131", "123")]
        for begin, end, corp in cases:
            with self.assertRaises(ValueError, msg=(begin, end, corp)):
                bfi.run_filings_history(
                    begin, end, corp_code=corp, history_dir=self.hdir, key=KEY,
                    session=FakeSession(history_handler({})))
        self.assertFalse((self.hdir / "manifest.json").exists())

    def test_no_financial_db_writes(self):
        w = windows_of("20240101", "20240131")[0]
        routes = {(w[0], w[1], 1): page_payload([mk_filing("20240115000001")], 1, 1)}
        bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                key=KEY, session=FakeSession(history_handler(routes)))
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir, key=KEY,
                       session=FakeSession(lambda u, p: FakeResponse(content=data)))
        names = [p.name for p in Path(self._tmp.name).rglob("*") if p.is_file()]
        self.assertTrue(names)
        for n in names:
            self.assertFalse(n.endswith((".sqlite3", ".db", ".tmp")), n)
        self.assertIn("manifest.json", names)
        self.assertIn("probe.json", names)

    def test_mid_window_013_incomplete(self):
        w = windows_of("20240101", "20240131")[0]
        page1 = [mk_filing(f"20240115{i:06d}") for i in range(2)]
        routes = {(w[0], w[1], 1): page_payload(page1, 5, 2),
                  (w[0], w[1], 2): {"status": "013", "message": "no data"}}
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=FakeSession(history_handler(routes)))
        self.assertFalse(m["complete"])
        self.assertFalse(m["windows"][0]["complete"])
        self.assertIn("013", m["windows"][0]["error"])
        self.assertEqual(len(m["filings"]), 2)

    def test_first_page_013_complete(self):
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=FakeSession(history_handler({})))
        self.assertTrue(m["complete"])
        self.assertTrue(m["current_query_complete"])
        self.assertEqual(m["filings"], [])
        self.assertTrue(m["windows"][0]["complete"])

    def test_malformed_000_incomplete(self):
        w = windows_of("20240101", "20240131")[0]
        good = mk_filing("20240115000001")
        cases = [
            {"status": "000", "page_no": 1, "page_count": 100,
             "total_page": 1, "list": [good]},
            {"status": "000", "page_no": 1, "page_count": 100,
             "total_count": 1, "total_page": 1},
            {"status": "000", "page_no": 1, "page_count": 100,
             "total_count": 1, "total_page": 1, "list": "not-a-list"},
            {"status": "000", "page_no": 1, "page_count": 100,
             "total_count": "bad", "total_page": 1, "list": [good]},
            page_payload([{**good, "rcept_no": ""}], 1, 1),
            page_payload([{**good, "rcept_dt": ""}], 1, 1),
        ]
        for i, payload in enumerate(cases):
            with self.subTest(i=i):
                routes = {(w[0], w[1], 1): payload}
                m = bfi.run_filings_history(
                    "20240101", "20240131", history_dir=self.hdir / f"m{i}",
                    key=KEY, session=FakeSession(history_handler(routes)))
                self.assertFalse(m["complete"], payload)
                self.assertFalse(m["windows"][0]["complete"], payload)
                self.assertIsNotNone(m["windows"][0]["error"], payload)

    def test_unstable_totals_incomplete(self):
        w = windows_of("20240101", "20240131")[0]
        p1 = [mk_filing(f"20240115{i:06d}") for i in range(2)]
        p2 = [mk_filing(f"20240116{i:06d}") for i in range(2)]
        routes = {(w[0], w[1], 1): page_payload(p1, 4, 2),
                  (w[0], w[1], 2): page_payload(p2, 5, 2)}
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=FakeSession(history_handler(routes)))
        self.assertFalse(m["complete"])
        self.assertIn("unstable", m["windows"][0]["error"])
        self.assertEqual(len(m["filings"]), 4)

    def test_rerun_full_partial_full_preserves(self):
        w = windows_of("20240101", "20240131")[0]
        a = mk_filing("20240115000001")
        b = mk_filing("20240115000002")

        def run(payload):
            routes = {(w[0], w[1], 1): payload}
            return bfi.run_filings_history(
                "20240101", "20240131", history_dir=self.hdir, key=KEY,
                session=FakeSession(history_handler(routes)))

        m1 = run(page_payload([a, b], 2, 1))
        self.assertTrue(m1["complete"])
        t1 = {r["rcept_no"]: r["first_observed_at"] for r in m1["filings"]}
        m2 = run(page_payload([a], 2, 1))
        self.assertFalse(m2["complete"])
        self.assertFalse(m2["current_query_complete"])
        self.assertEqual({r["rcept_no"] for r in m2["filings"]},
                         {"20240115000001", "20240115000002"})
        got2 = {r["rcept_no"]: r["first_observed_at"] for r in m2["filings"]}
        self.assertEqual(got2["20240115000001"], t1["20240115000001"])
        self.assertEqual(got2["20240115000002"], t1["20240115000002"])
        m3 = run(page_payload([a, b], 2, 1))
        self.assertTrue(m3["complete"])
        self.assertTrue(m3["current_query_complete"])
        got3 = {r["rcept_no"]: r["first_observed_at"] for r in m3["filings"]}
        self.assertEqual(got3, t1)
        self.assertEqual(len(list((self.hdir / "queries").glob("*.json"))), 1)

    def test_changed_range_accumulates(self):
        w1 = windows_of("20240101", "20240131")[0]
        w2 = windows_of("20240201", "20240228")[0]
        a = mk_filing("20240115000001", rcept_dt="20240115")
        b = mk_filing("20240215000002", rcept_dt="20240215")
        r1 = {(w1[0], w1[1], 1): page_payload([a], 1, 1)}
        m1 = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                     key=KEY, session=FakeSession(history_handler(r1)))
        t_a = m1["filings"][0]["first_observed_at"]
        r2 = {(w2[0], w2[1], 1): page_payload([b], 1, 1)}
        m2 = bfi.run_filings_history("20240201", "20240228", history_dir=self.hdir,
                                     key=KEY, session=FakeSession(history_handler(r2)))
        self.assertTrue(m2["complete"])
        self.assertTrue(m2["current_query_complete"])
        self.assertEqual([r["rcept_no"] for r in m2["filings"]],
                         ["20240115000001", "20240215000002"])
        got = {r["rcept_no"]: r["first_observed_at"] for r in m2["filings"]}
        self.assertEqual(got["20240115000001"], t_a)
        self.assertEqual(len(m2["queries"]), 2)
        self.assertEqual(len(m2["windows"]), 2)
        self.assertEqual(m2["total_calls"], 2)
        for g in m2["groups"]:
            self.assertEqual(g["earliest_caveat"], bfi.EARLIEST_CAVEAT)

    def test_aggregate_incomplete_if_any_snapshot_incomplete(self):
        w1 = windows_of("20240101", "20240131")[0]
        w2 = windows_of("20240201", "20240228")[0]
        a = mk_filing("20240115000001", rcept_dt="20240115")
        b = mk_filing("20240215000002", rcept_dt="20240215")
        r1 = {(w1[0], w1[1], 1): page_payload([a], 1, 1)}
        bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                key=KEY, session=FakeSession(history_handler(r1)))
        r2 = {(w2[0], w2[1], 1): page_payload([b], 2, 1)}
        m2 = bfi.run_filings_history("20240201", "20240228", history_dir=self.hdir,
                                     key=KEY, session=FakeSession(history_handler(r2)))
        self.assertFalse(m2["complete"])
        self.assertFalse(m2["current_query_complete"])
        self.assertEqual(len(m2["filings"]), 2)
        self.assertEqual(len(m2["windows"]), 2)

    def test_key_literal_redacted_in_history(self):
        w = windows_of("20240101", "20240131")[0]
        routes = {(w[0], w[1], 1): {"status": "020",
                                    "message": f"limit exceeded for {KEY} retry"}}
        m = bfi.run_filings_history("20240101", "20240131", history_dir=self.hdir,
                                    key=KEY, session=FakeSession(history_handler(routes)))
        self.assertFalse(m["complete"])
        self.assertNotIn(KEY, m["windows"][0]["error"])
        self.assertNotIn(KEY, self.manifest_text())


INSTANCE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:corp="http://dart.fss.or.kr/corp" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:xbrli="http://www.xbrl.org/2003/instance">
  <context id="c1">
    <entity><identifier scheme="http://dart.fss.or.kr">00126380</identifier></entity>
    <period><instant>2025-12-31</instant></period>
  </context>
  <context id="c2">
    <entity><identifier scheme="http://dart.fss.or.kr">00126380</identifier></entity>
    <period><startDate>2025-01-01</startDate><endDate>2025-12-31</endDate></period>
    <segment><xbrldi:explicitMember dimension="corp:FsDiv">corp:CFS</xbrldi:explicitMember></segment>
  </context>
  <unit id="u1"><measure>iso4217:KRW</measure></unit>
  <unit id="u2"><measure>xbrli:shares</measure></unit>
  <corp:Revenue contextRef="c2" unitRef="u1" decimals="0">0012300</corp:Revenue>
  <corp:Assets contextRef="c1" unitRef="u1">999</corp:Assets>
  <corp:Note contextRef="c1">text</corp:Note>
</xbrl>"""


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def err_xml(status: str) -> bytes:
    return (f'<?xml version="1.0"?><result><status>{status}</status>'
            f"<message>msg</message></result>").encode("utf-8")


class ProbeTest(TmpHistory):
    def probe_text(self):
        return (self.hdir / "probe.json").read_text(encoding="utf-8")

    def test_zip_fixture_reports_context_unit_facts(self):
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        sess = FakeSession(lambda u, p: FakeResponse(content=data))
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=sess)
        self.assertEqual(rep["status"], "downloaded")
        self.assertEqual(rep["byte_size"], len(data))
        self.assertEqual(rep["recovery"], "no_claim_of_recovery")
        self.assertIsNone(rep["error_kind"])
        ctxs = {c["id"]: c for c in rep["instance"]["contexts"]}
        self.assertEqual(ctxs["c1"]["period"], {"instant": "2025-12-31"})
        self.assertEqual(ctxs["c1"]["entity"],
                         {"scheme": "http://dart.fss.or.kr", "identifier": "00126380"})
        self.assertEqual(ctxs["c2"]["explicit_dimensions"],
                         [{"dimension": "corp:FsDiv", "member": "corp:CFS"}])
        units = {u["id"]: u for u in rep["instance"]["units"]}
        self.assertEqual(units["u1"]["measures"], ["iso4217:KRW"])
        facts = {f["name"]: f for f in rep["instance"]["facts"]}
        self.assertEqual(facts["Revenue"]["value"], "0012300")  # 문자열 보존
        self.assertEqual(facts["Revenue"]["decimals"], "0")
        self.assertEqual(facts["Revenue"]["unitRef"], "u1")
        self.assertEqual(facts["Revenue"]["contextRef"], "c2")
        self.assertIsNone(facts["Assets"]["decimals"])
        self.assertIsNone(facts["Note"]["unitRef"])
        self.assertEqual(rep["instance"]["member"], "audit.xml")
        zp = self.hdir / "xbrl" / "20260315001234_11011.zip"
        self.assertEqual(zp.read_bytes(), data)
        entries = json.loads(self.probe_text())
        self.assertIn("20260315001234_11011", entries)
        self.assertNotIn(KEY, self.probe_text())

    def _probe_err(self, status, kind):
        sess = FakeSession(lambda u, p: FakeResponse(content=err_xml(status)))
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=sess)
        self.assertEqual(rep["status"], "error")
        self.assertEqual(rep["error_kind"], kind)
        self.assertIn(status, rep["error"])
        self.assertFalse((self.hdir / "xbrl" / "20260315001234_11011.zip").exists())
        entries = json.loads(self.probe_text())
        self.assertEqual(entries["20260315001234_11011"]["error_kind"], kind)
        self.assertNotIn(KEY, self.probe_text())

    def test_xml_014_file_not_found(self):
        self._probe_err("014", "file_not_found")

    def test_xml_020_rate_limit(self):
        self._probe_err("020", "rate_limit")

    def test_xml_auth_error(self):
        self._probe_err("011", "auth_error")

    def test_http_error_sanitized(self):
        err = requests.exceptions.HTTPError(
            f"401 for url: https://opendart.fss.or.kr/api/fnlttXbrl.xml?crtfc_key={KEY}")
        sess = FakeSession(lambda u, p: FakeResponse(error=err))
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=sess)
        self.assertEqual(rep["status"], "error")
        self.assertEqual(rep["error_kind"], "http_error")
        self.assertNotIn(KEY, json.dumps(rep, ensure_ascii=False))
        self.assertNotIn("https://", rep["error"])
        self.assertNotIn(KEY, self.probe_text())

    def test_existing_archive_reused(self):
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        zp = self.hdir / "xbrl" / "20260315001234_11011.zip"
        zp.parent.mkdir(parents=True)
        zp.write_bytes(data)

        def _boom(url, params):
            raise AssertionError("reused archive must not download")

        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=FakeSession(_boom))
        self.assertEqual(rep["status"], "reused")
        self.assertEqual(rep["byte_size"], len(data))
        self.assertEqual(len(rep["instance"]["contexts"]), 2)

    def test_corrupt_cache_retried(self):
        zp = self.hdir / "xbrl" / "20260315001234_11011.zip"
        zp.parent.mkdir(parents=True)
        zp.write_bytes(b"junk-bytes-not-zip-not-xml")
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        sess = FakeSession(lambda u, p: FakeResponse(content=data))
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=sess)
        self.assertEqual(rep["status"], "downloaded")
        self.assertEqual(len(sess.calls), 1)
        self.assertEqual(zp.read_bytes(), data)

    def test_invalid_zip_download(self):
        for i, bad in enumerate([b"\x00\x01binary-garbage", b"<foo><bar/></foo>"]):
            rcept = f"2026031500000{i}"
            sess = FakeSession(lambda u, p, b=bad: FakeResponse(content=b))
            rep = bfi.probe_xbrl(rcept, "11011", history_dir=self.hdir,
                                 key=KEY, session=sess)
            self.assertEqual(rep["error_kind"], "invalid_zip", bad)
            self.assertFalse((self.hdir / "xbrl" / f"{rcept}_11011.zip").exists())
        entries = json.loads(self.probe_text())
        self.assertEqual(entries["20260315000000_11011"]["status"], "error")

    def test_zip_members_never_extracted(self):
        data = make_zip({"../../evil.txt": b"pwn",
                         "audit.xml": INSTANCE_XML.encode("utf-8")})
        sess = FakeSession(lambda u, p: FakeResponse(content=data))
        rep = bfi.probe_xbrl("20260315009999", "11011", history_dir=self.hdir,
                             key=KEY, session=sess)
        self.assertEqual(rep["status"], "downloaded")
        self.assertFalse((Path(self._tmp.name) / "evil.txt").exists())
        self.assertEqual(list(Path(self._tmp.name).rglob("evil.txt")), [])
        self.assertIn("../../evil.txt", [m["name"] for m in rep["zip_members"]])

    def test_probe_json_accumulates_by_receipt(self):
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        sess = FakeSession(lambda u, p: FakeResponse(content=data))
        bfi.probe_xbrl("20260315000001", "11011", history_dir=self.hdir,
                       key=KEY, session=sess)
        bfi.probe_xbrl("20260315000002", "11012", history_dir=self.hdir,
                       key=KEY, session=sess)
        entries = json.loads(self.probe_text())
        self.assertEqual(set(entries),
                         {"20260315000001_11011", "20260315000002_11012"})
        bfi.probe_xbrl("20260315000001", "11011", history_dir=self.hdir,
                       key=KEY, session=sess)
        entries2 = json.loads(self.probe_text())
        self.assertEqual(set(entries2), set(entries))
        self.assertEqual(entries2["20260315000002_11012"],
                         entries["20260315000002_11012"])
        self.assertEqual(entries2["20260315000001_11011"]["status"], "reused")

    def test_probe_validation(self):
        cases = [("../evil", "11011"), ("ab12", "11011"), ("", "11011"),
                 ("20260315001234", "99999"), ("20260315001234", "")]
        for rcept, reprt in cases:
            with self.assertRaises(ValueError, msg=(rcept, reprt)):
                bfi.probe_xbrl(rcept, reprt, history_dir=self.hdir, key=KEY,
                               session=FakeSession(lambda u, p: FakeResponse(content=b"")))
        self.assertFalse(self.hdir.exists())

    def test_real_shape_xbrl_preferred_over_larger_linkbases(self):
        link = ('<?xml version="1.0"?><linkbase xmlns='
                '"http://www.xbrl.org/2003/linkbase">'
                + "<x>" + "p" * 5000 + "</x>" + "</linkbase>")
        files = {
            "entity01078178_2023-12-31.xbrl": INSTANCE_XML.encode("utf-8"),
            "entity01078178_2023-12-31_def.xml": link.encode("utf-8"),
            "entity01078178_2023-12-31_cal.xml": link.encode("utf-8"),
            "entity01078178_2023-12-31_lab.xml": link.encode("utf-8"),
        }
        data = make_zip(files)
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=FakeSession(
                                 lambda u, p: FakeResponse(content=data)))
        self.assertEqual(rep["status"], "downloaded")
        self.assertIsNone(rep["error_kind"])
        self.assertIsNone(rep["instance_skipped"])
        self.assertEqual(rep["instance"]["member"],
                         "entity01078178_2023-12-31.xbrl")
        self.assertEqual(len(rep["instances"]), 1)
        self.assertEqual(rep["instances"][0]["member"],
                         "entity01078178_2023-12-31.xbrl")
        self.assertEqual(len(rep["instance"]["facts"]), 3)

    def test_multiple_instances_all_reported(self):
        data = make_zip({"b.xbrl": INSTANCE_XML.encode("utf-8"),
                         "a.xbrl": INSTANCE_XML.encode("utf-8")})
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=FakeSession(
                                 lambda u, p: FakeResponse(content=data)))
        self.assertEqual(rep["status"], "downloaded")
        self.assertEqual(len(rep["instances"]), 2)
        self.assertEqual(rep["instance"]["member"], "a.xbrl")
        self.assertEqual([i["member"] for i in rep["instances"]],
                         ["a.xbrl", "b.xbrl"])

    def test_linkbase_only_reports_no_instance(self):
        link = ('<?xml version="1.0"?><linkbase xmlns='
                '"http://www.xbrl.org/2003/linkbase"><x>y</x></linkbase>')
        data = make_zip({"f_def.xml": link.encode("utf-8"),
                         "f_cal.xml": link.encode("utf-8")})
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=FakeSession(
                                 lambda u, p: FakeResponse(content=data)))
        self.assertEqual(rep["status"], "downloaded")
        self.assertIsNone(rep["error_kind"])
        self.assertIsNone(rep["instance"])
        self.assertEqual(rep["instances"], [])
        self.assertEqual(rep["instance_skipped"], "no_instance")

    def test_facts_keep_expanded_qname_and_namespaces(self):
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=FakeSession(
                                 lambda u, p: FakeResponse(content=data)))
        facts = {f["name"]: f for f in rep["instance"]["facts"]}
        self.assertEqual(facts["Revenue"]["qname"],
                         "{http://dart.fss.or.kr/corp}Revenue")
        ns = rep["instance"]["namespaces"]
        self.assertEqual(ns["corp"], "http://dart.fss.or.kr/corp")
        for c in rep["instance"]["contexts"]:
            for d in c["explicit_dimensions"]:
                for qn in (d["dimension"], d["member"]):
                    prefix = qn.split(":", 1)[0] if ":" in qn else ""
                    self.assertIn(prefix, ns)
        for u in rep["instance"]["units"]:
            for m in u["measures"]:
                prefix = m.split(":", 1)[0] if ":" in m else ""
                self.assertIn(prefix, ns)

    def test_xml_901_auth_error(self):
        self._probe_err("901", "auth_error")

    def test_key_literal_redacted_in_probe(self):
        xml = (f'<?xml version="1.0"?><result><status>020</status>'
               f"<message>blocked {KEY} now</message></result>").encode("utf-8")
        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=FakeSession(
                                 lambda u, p: FakeResponse(content=xml)))
        self.assertEqual(rep["status"], "error")
        self.assertNotIn(KEY, rep["error"])
        self.assertNotIn(KEY, self.probe_text())

    def test_first_payload_failure_then_download_then_reuse(self):
        rcept, reprt = "20260315001234", "11011"
        pid = f"{rcept}_{reprt}"
        rep1 = bfi.probe_xbrl(rcept, reprt, history_dir=self.hdir, key=KEY,
                              session=FakeSession(
                                  lambda u, p: FakeResponse(content=err_xml("014"))))
        self.assertEqual(rep1["status"], "error")
        self.assertIsNone(rep1["first_payload_collected_at"])
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        rep2 = bfi.probe_xbrl(rcept, reprt, history_dir=self.hdir, key=KEY,
                              session=FakeSession(
                                  lambda u, p: FakeResponse(content=data)))
        self.assertEqual(rep2["status"], "downloaded")
        self.assertEqual(rep2["first_payload_collected_at"], rep2["probed_at"])

        def _boom(url, params):
            raise AssertionError("reused must not download")

        rep3 = bfi.probe_xbrl(rcept, reprt, history_dir=self.hdir, key=KEY,
                              session=FakeSession(_boom))
        self.assertEqual(rep3["status"], "reused")
        self.assertEqual(rep3["first_payload_collected_at"],
                         rep2["first_payload_collected_at"])
        self.assertGreaterEqual(rep3["probed_at"], rep2["probed_at"])
        entries = json.loads(self.probe_text())
        self.assertEqual(entries[pid]["first_payload_collected_at"], rep2["probed_at"])

    def test_known_archive_without_prior_first_null(self):
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        zp = self.hdir / "xbrl" / "20260315001234_11011.zip"
        zp.parent.mkdir(parents=True)
        zp.write_bytes(data)

        def _boom(url, params):
            raise AssertionError("reused archive must not download")

        rep = bfi.probe_xbrl("20260315001234", "11011", history_dir=self.hdir,
                             key=KEY, session=FakeSession(_boom))
        self.assertEqual(rep["status"], "reused")
        self.assertIsNone(rep["first_payload_collected_at"])
        self.assertIsNotNone(rep["probed_at"])

    def test_preserve_first_on_later_error(self):
        rcept, reprt = "20260315001234", "11011"
        pid = f"{rcept}_{reprt}"
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})
        rep1 = bfi.probe_xbrl(rcept, reprt, history_dir=self.hdir, key=KEY,
                              session=FakeSession(
                                  lambda u, p: FakeResponse(content=data)))
        self.assertEqual(rep1["status"], "downloaded")
        first = rep1["first_payload_collected_at"]
        self.assertEqual(first, rep1["probed_at"])
        (self.hdir / "xbrl" / f"{pid}.zip").unlink()
        rep2 = bfi.probe_xbrl(rcept, reprt, history_dir=self.hdir, key=KEY,
                              session=FakeSession(
                                  lambda u, p: FakeResponse(content=err_xml("014"))))
        self.assertEqual(rep2["status"], "error")
        self.assertEqual(rep2["first_payload_collected_at"], first)
        self.assertGreaterEqual(rep2["probed_at"], rep1["probed_at"])
        entries = json.loads(self.probe_text())
        self.assertEqual(entries[pid]["first_payload_collected_at"], first)

    def test_legacy_prior_downloaded_promoted_reused_stays_null(self):
        legacy_ts = "2026-01-02T03:04:05+00:00"
        data = make_zip({"audit.xml": INSTANCE_XML.encode("utf-8")})

        def _boom(url, params):
            raise AssertionError("reused must not download")

        # 레거시 downloaded + probed_at → reused에서 first로 승격
        pid_ok = "20260315001111_11011"
        (self.hdir / "xbrl").mkdir(parents=True)
        (self.hdir / "xbrl" / f"{pid_ok}.zip").write_bytes(data)
        (self.hdir).mkdir(parents=True, exist_ok=True)
        (self.hdir / "probe.json").write_text(
            json.dumps({pid_ok: {"rcept_no": "20260315001111", "reprt_code": "11011",
                                 "probed_at": legacy_ts, "status": "downloaded"}}),
            encoding="utf-8")
        rep_ok = bfi.probe_xbrl("20260315001111", "11011", history_dir=self.hdir,
                                key=KEY, session=FakeSession(_boom))
        self.assertEqual(rep_ok["status"], "reused")
        self.assertEqual(rep_ok["first_payload_collected_at"], legacy_ts)
        # 레거시 reused/error + first 없음 → NULL 유지
        pid_null = "20260315002222_11011"
        (self.hdir / "xbrl" / f"{pid_null}.zip").write_bytes(data)
        prev = json.loads((self.hdir / "probe.json").read_text(encoding="utf-8"))
        prev[pid_null] = {"rcept_no": "20260315002222", "reprt_code": "11011",
                          "probed_at": legacy_ts, "status": "reused"}
        (self.hdir / "probe.json").write_text(json.dumps(prev), encoding="utf-8")
        rep_null = bfi.probe_xbrl("20260315002222", "11011", history_dir=self.hdir,
                                  key=KEY, session=FakeSession(_boom))
        self.assertEqual(rep_null["status"], "reused")
        self.assertIsNone(rep_null["first_payload_collected_at"])


class CliTest(unittest.TestCase):
    def test_history_and_probe_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                bfi.build_parser().parse_args(
                    ["--filings-history", "20240101", "20240131",
                     "--probe-xbrl", "20260315001234", "11011"])
        self.assertEqual(ctx.exception.code, 2)

    def test_history_args_and_defaults(self):
        args = bfi.build_parser().parse_args(["--filings-history", "20240101", "20240131"])
        self.assertEqual(args.filings_history, ["20240101", "20240131"])
        self.assertIsNone(args.corp_code)
        self.assertEqual(args.history_dir, bfi.DEFAULT_HISTORY_DIR)

    def test_probe_args(self):
        args = bfi.build_parser().parse_args(["--probe-xbrl", "20260315001234", "11012"])
        self.assertEqual(args.probe_xbrl, ["20260315001234", "11012"])

    def test_existing_defaults_unchanged(self):
        args = bfi.build_parser().parse_args([])
        self.assertEqual(args.source, "both")
        self.assertEqual(args.reprt, "11011")
        self.assertIsNone(args.filings_on)
        self.assertIsNone(args.filings_history)
        self.assertIsNone(args.probe_xbrl)

    def test_help_exits_zero(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                bfi.build_parser().parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_mode_flags_mutually_exclusive(self):
        pairs = [
            ["--self-check", "--filings-history", "20240101", "20240131"],
            ["--self-check", "--probe-xbrl", "20260315001234", "11011"],
            ["--self-check", "--filings-on", "20240101"],
            ["--filings-on", "20240101",
             "--filings-history", "20240101", "20240131"],
            ["--filings-on", "20240101",
             "--probe-xbrl", "20260315001234", "11011"],
        ]
        for argv in pairs:
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        bfi.build_parser().parse_args(argv)
                self.assertEqual(ctx.exception.code, 2)

    def test_history_incomplete_main_exits_nonzero(self):
        fake = {"complete": False, "filings": [], "windows": [], "total_calls": 1}
        with patch.object(bfi, "run_filings_history", return_value=fake):
            with patch.object(sys, "argv",
                              ["prog", "--filings-history", "20240101", "20240131"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as ctx:
                        bfi.main()
                self.assertEqual(ctx.exception.code, 1)
                out = buf.getvalue()
                self.assertIn("INCOMPLETE", out)
                self.assertNotIn("DONE", out)

    def test_history_complete_main_exits_zero(self):
        fake = {"complete": True, "filings": [], "windows": [], "total_calls": 1}
        with patch.object(bfi, "run_filings_history", return_value=fake):
            with patch.object(sys, "argv",
                              ["prog", "--filings-history", "20240101", "20240131"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    bfi.main()
                out = buf.getvalue()
                self.assertIn("DONE", out)
                self.assertNotIn("INCOMPLETE", out)

    def test_probe_error_main_exits_nonzero(self):
        fake = {"status": "error", "byte_size": 10, "error_kind": "http_error"}
        with patch.object(bfi, "probe_xbrl", return_value=fake):
            with patch.object(sys, "argv",
                              ["prog", "--probe-xbrl", "20260315001234", "11011"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as ctx:
                        bfi.main()
                self.assertEqual(ctx.exception.code, 1)
                out = buf.getvalue()
                self.assertIn("FAILED", out)
                self.assertNotIn("DONE", out)

    def test_probe_success_main_exits_zero(self):
        fake = {"status": "downloaded", "byte_size": 10, "error_kind": None}
        with patch.object(bfi, "probe_xbrl", return_value=fake):
            with patch.object(sys, "argv",
                              ["prog", "--probe-xbrl", "20260315001234", "11011"]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    bfi.main()
                out = buf.getvalue()
                self.assertIn("DONE", out)
                self.assertNotIn("FAILED", out)


if __name__ == "__main__":
    unittest.main()

