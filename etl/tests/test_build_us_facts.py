"""build_us_facts 테스트 (unittest, 네트워크 없음). PLAN_US_FACTS 테스트 표 T1~T12."""
import gzip
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402
import build_us_facts as m  # noqa: E402

NOW = "2026-10-11T00:00:00+00:00"


def _entry(val, form, filed, end, accn, start=None, fy=None, fp=None):
    d = {"val": val, "form": form, "filed": filed, "end": end, "accn": accn}
    if start is not None:
        d["start"] = start
    if fy is not None:
        d["fy"] = fy
    if fp is not None:
        d["fp"] = fp
    return d


def _facts(entries, taxonomy="us-gaap", tag="Assets", unit="USD", label="Assets", entity="Test Inc"):
    return {"entityName": entity,
            "facts": {taxonomy: {tag: {"label": label, "units": {unit: entries}}}}}


@contextmanager
def _temp_env():
    """임시 sqlite + 임시 raw_root."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        con = sqlite3.connect(str(td / "t.sqlite3"))
        try:
            m.ensure_schema(con)
            yield con, td / "raw"
        finally:
            con.close()


class TestUsFacts(unittest.TestCase):
    def test_t01_changed_values_only(self):
        base = [
            _entry(1000, "10-K", "2025-02-01", "2024-12-31", "0001"),
            _entry(1000, "10-K", "2026-02-01", "2024-12-31", "0002"),
            _entry(1000, "10-K", "2027-02-01", "2024-12-31", "0003"),
        ]
        rows = m.fact_versions(_facts(base))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][6], "2025-02-01")  # filed
        self.assertEqual(rows[0][7], 1000)  # val
        rows2 = m.fact_versions(_facts(base + [
            _entry(1200, "10-K/A", "2027-03-01", "2024-12-31", "0004")]))
        self.assertEqual(len(rows2), 2)
        self.assertEqual([r[6] for r in rows2], ["2025-02-01", "2027-03-01"])
        self.assertEqual([r[7] for r in rows2], [1000, 1200])

    def test_t02_same_filed_picks_max_accn(self):
        rows = m.fact_versions(_facts([
            _entry(100, "10-K", "2025-02-01", "2024-12-31", "0001"),
            _entry(200, "10-K", "2025-02-01", "2024-12-31", "0002"),
        ]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][7], 200)
        self.assertEqual(rows[0][8], "0002")

    def test_t03_periodic_forms_only(self):
        rows = m.fact_versions(_facts([
            _entry(100, "10-K", "2025-02-01", "2024-12-31", "0001"),
            _entry(999, "8-K", "2025-03-01", "2024-12-31", "0002"),
            _entry(999, "S-1", "2025-04-01", "2024-12-31", "0003"),
            _entry(999, "DEF 14A", "2025-05-01", "2024-12-31", "0004"),
        ]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][7], 100)

    def test_t04_missing_start_is_empty(self):
        rows = m.fact_versions(_facts([
            _entry(100, "10-K", "2025-02-01", "2024-12-31", "0001")]))
        self.assertEqual(rows[0][4], "")

    def test_t05_replace_company_idempotent(self):
        with _temp_env() as (con, _raw):
            facts = _facts([
                _entry(1000, "10-K", "2025-02-01", "2024-12-31", "0001"),
                _entry(1200, "10-K/A", "2025-03-01", "2024-12-31", "0002"),
            ])
            n1 = m.replace_company(con, "0000000111", facts, NOW)
            con.commit()
            rows1 = con.execute("SELECT * FROM facts ORDER BY concept_id, unit, start, end, filed").fetchall()
            self.assertGreater(n1, 0)
            n2 = m.replace_company(con, "0000000111", facts, NOW)
            con.commit()
            rows2 = con.execute("SELECT * FROM facts ORDER BY concept_id, unit, start, end, filed").fetchall()
            self.assertEqual(n1, n2)
            self.assertEqual(rows1, rows2)

    def test_t06_save_raw_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "raw"
            data = b'{"cik":"0000000111","facts":{}}'
            m.save_raw(raw, "0000000111", data)
            p = m.raw_path(raw, "0000000111")
            self.assertTrue(p.exists())
            with gzip.open(p, "rb") as f:
                self.assertEqual(f.read(), data)
            leftovers = [q for q in p.parent.iterdir() if q.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_t07_parse_daily_index(self):
        text = (
            "Header line 1\n"
            "Header line 2\n"
            "--------------------------------------------------------------------------------\n"
            "10-K foo 320193 bar baz\n"
            "10-Q/A foo 19617 bar baz\n"
            "20-F foo 123 bar baz\n"
            "8-K foo 999 bar baz\n"
            "4 foo 888 bar baz\n"
            "SC 13D foo 777 bar baz\n"
        )
        self.assertEqual(m.parse_daily_index(text), {"0000320193", "0000019617", "0000000123"})

    def test_t08_holiday_skip_and_unpublished_stop(self):
        today = date(2026, 10, 14)  # 수
        last = date(2026, 10, 5)  # 월
        empty_idx = b"head\n-----\n"  # 구분선만, CIK 없음
        good = {"20261006", "20261007", "20261008", "20261012"}
        calls = []

        def get_bytes(url):
            calls.append(url)
            if "daily-index" in url:
                ymd = url.split("form.")[1].split(".idx")[0]
                return empty_idx if ymd in good else None
            raise AssertionError(f"unexpected facts call: {url}")

        with _temp_env() as (con, raw):
            m.set_state(con, "last_index_date", last.isoformat())
            con.commit()
            r = m.update(con, raw, today, get_bytes, NOW)
            self.assertEqual(r["dates"], 4)
            # 10-09(지난 평일, 오늘-5) + 주말 2일만 휴일 넘김. 어제(10-13) 404는 중단이라 미포함.
            self.assertEqual(r["skipped"], 3)
            self.assertEqual(r["ciks"], 0)
            self.assertEqual(m.get_state(con, "last_index_date"), "2026-10-12")
            self.assertTrue(any("20261013" in u for u in calls))

    def test_t09_overlap_days_included(self):
        with _temp_env() as (con, raw):
            # 첫 대상(10-06) 직전 3일 중 2일에 저장 목록. 10-03 파일 없음 → 무시.
            m.save_index(raw, date(2026, 10, 4), b"h\n-----\n10-K foo 111 bar baz\n")
            m.save_index(raw, date(2026, 10, 5), b"h\n-----\n10-Q foo 222 bar baz\n")
            fact_calls = []

            def get_bytes(url):
                if "daily-index" in url:
                    return b"h\n-----\n10-K foo 333 bar baz\n"
                if "companyfacts" in url:
                    fact_calls.append(url.split("CIK")[1].split(".json")[0])
                    return b'{"entityName":"X","facts":{}}'
                if "submissions" in url:
                    return b'{}'
                raise AssertionError(url)

            m.set_state(con, "last_index_date", "2026-10-05")
            con.commit()
            r = m.update(con, raw, date(2026, 10, 7), get_bytes, NOW)
            self.assertEqual(set(fact_calls), {"0000000111", "0000000222", "0000000333"})
            self.assertEqual(r["ciks"], 3)

    def test_t10_facts_404_keeps_rows(self):
        with _temp_env() as (con, raw):
            cik = "0000000111"
            m.replace_company(con, cik, _facts([
                _entry(100, "10-K", "2025-02-01", "2024-12-31", "0001")]), NOW)
            con.commit()
            before = con.execute("SELECT * FROM facts WHERE cik=?", (cik,)).fetchall()
            self.assertEqual(len(before), 1)
            m.set_state(con, "last_index_date", "2026-10-05")
            con.commit()

            def get_bytes(url):
                if "daily-index" in url:
                    return b"h\n-----\n10-K foo 111 bar baz\n"
                if "companyfacts" in url:
                    return None
                raise AssertionError(url)

            r = m.update(con, raw, date(2026, 10, 7), get_bytes, NOW)
            self.assertEqual(r["not_found"], 1)
            self.assertEqual(r["updated"], 0)
            after = con.execute("SELECT * FROM facts WHERE cik=?", (cik,)).fetchall()
            self.assertEqual(before, after)

    def test_t11_fetch_error_keeps_state(self):
        with _temp_env() as (con, raw):
            m.set_state(con, "last_index_date", "2026-10-05")
            con.commit()

            def get_bytes(url):
                if "daily-index" in url:
                    return b"h\n-----\n10-K foo 111 bar baz\n"
                raise m.FetchError("boom")

            with self.assertRaises(m.FetchError):
                m.update(con, raw, date(2026, 10, 7), get_bytes, NOW)
            self.assertEqual(m.get_state(con, "last_index_date"), "2026-10-05")

    def test_t12_value_as_of(self):
        with _temp_env() as (con, _raw):
            m.replace_company(con, "0000000111", _facts([
                _entry(100, "10-K", "2024-02-01", "2023-12-31", "0001"),
                _entry(120, "10-K/A", "2024-05-01", "2023-12-31", "0002"),
            ]), NOW)
            q = dict(cik10="0000000111", taxonomy="us-gaap", tag="Assets",
                     unit="USD", start="", end="2023-12-31")
            self.assertEqual(m.value_as_of(con, as_of="2024-03-01", **q), 100)
            self.assertEqual(m.value_as_of(con, as_of="2024-06-01", **q), 120)
            self.assertIsNone(m.value_as_of(con, as_of="2024-01-01", **q))

    def test_t13_refresh_companies_listing(self):
        with _temp_env() as (con, _raw):
            m.replace_company(con, "0000000111", _facts(
                [_entry(1, "10-K", "2025-02-01", "2024-12-31", "0001")], entity="A Inc"), NOW)
            m.replace_company(con, "0000000222", _facts(
                [_entry(2, "10-K", "2025-02-01", "2024-12-31", "0001")], entity="B Inc"), NOW)
            payload = {"fields": ["cik", "name", "ticker", "exchange"], "data": [
                [222, "B Inc", "BBB", "NYSE"],
                [222, "B Inc dup", "BB2", "NYSE"],  # cik 중복 → 첫 행만
                [333, "C Inc", "CCC", "Nasdaq"],  # 신규
            ]}
            m.refresh_companies(con, payload, NOW)
            a = con.execute("SELECT is_listed, ticker FROM companies WHERE cik=?",
                            ("0000000111",)).fetchone()
            self.assertEqual(tuple(a), (0, None))
            b = con.execute("SELECT is_listed, ticker, exchange FROM companies WHERE cik=?",
                            ("0000000222",)).fetchone()
            self.assertEqual(tuple(b), (1, "BBB", "NYSE"))
            c = con.execute("SELECT is_listed, ticker, entity_name FROM companies WHERE cik=?",
                            ("0000000333",)).fetchone()
            self.assertEqual(tuple(c), (1, "CCC", "C Inc"))

    def test_t14_huge_int_stored_as_real(self):
        with _temp_env() as (con, _raw):
            m.replace_company(con, "0000000111", _facts(
                [_entry(999999999999000000000, "10-K", "2025-02-01", "2024-12-31", "0001")]), NOW)
            con.commit()
            val, typ = con.execute("SELECT val, typeof(val) FROM facts").fetchone()
            self.assertEqual(val, float(999999999999000000000))
            self.assertEqual(typ, "real")

    def test_t15_parse_ff_siccodes(self):
        text = (" 1 Agric  Agriculture\r\n"
                "          0100-0199 Agricultural production - crops\r\n"
                "          2048-2048 Prepared feeds\r\n"
                "\r\n"
                " 2 Other  Other -- Mines\r\n")
        industries, ranges = m.parse_ff_siccodes(text)
        self.assertEqual(industries, [(1, "Agric", "Agriculture"),
                                      (2, "Other", "Other -- Mines")])
        self.assertEqual(ranges, [(1, 100, 199, "Agricultural production - crops"),
                                  (1, 2048, 2048, "Prepared feeds")])

    def test_t16_ff_industry(self):
        with _temp_env() as (con, _raw):
            con.executemany("INSERT INTO ff_industries(scheme, ind_no, ind_short, ind_name)"
                            " VALUES (?,?,?,?)",
                            [(12, 1, "Agric", "Agriculture"),
                             (12, 2, "Other", "Other -- Mines"),
                             (48, 1, "Agric", "Agriculture")])
            con.executemany("INSERT INTO ff_sic_ranges(scheme, ind_no, sic_lo, sic_hi, sic_desc)"
                            " VALUES (?,?,?,?,?)",
                            [(12, 1, 100, 199, "Agricultural production - crops"),
                             (12, 1, 2048, 2048, "Prepared feeds"),
                             (48, 1, 100, 199, "Agricultural production - crops")])
            con.commit()
            self.assertEqual(m.ff_industry(con, 12, 150), (1, "Agric"))
            self.assertEqual(m.ff_industry(con, 12, 5000), (2, "Other"))
            self.assertIsNone(m.ff_industry(con, 48, 5000))
            self.assertIsNone(m.ff_industry(con, 12, None))

    def test_t17_load_sic(self):
        with _temp_env() as (con, raw):
            m.replace_company(con, "0000000111", _facts(
                [_entry(1, "10-K", "2025-02-01", "2024-12-31", "0001")]), NOW)
            m.replace_company(con, "0000000222", _facts(
                [_entry(2, "10-K", "2025-02-01", "2024-12-31", "0001")]), NOW)
            con.commit()

            def get_bytes(url):
                if "0000000111" in url:
                    return b'{"sic":"3571","sicDescription":"Electronic Computers"}'
                if "0000000222" in url:
                    return None
                raise AssertionError(url)

            u, nf = m.load_sic(con, raw, get_bytes, ["0000000111", "0000000222"], NOW)
            self.assertEqual((u, nf), (1, 1))
            row = con.execute("SELECT sic, sic_desc FROM companies WHERE cik=?",
                              ("0000000111",)).fetchone()
            self.assertEqual(tuple(row), (3571, "Electronic Computers"))
            row2 = con.execute("SELECT sic FROM companies WHERE cik=?",
                               ("0000000222",)).fetchone()
            self.assertIsNone(row2[0])
            self.assertTrue(m.submissions_path(raw, "0000000111").exists())

    def test_t18_ensure_schema_migrates(self):
        with tempfile.TemporaryDirectory() as td:
            con = sqlite3.connect(str(Path(td) / "t.sqlite3"))
            try:
                con.execute("CREATE TABLE companies (cik TEXT PRIMARY KEY, entity_name TEXT,"
                            " ticker TEXT, exchange TEXT, is_listed INTEGER NOT NULL,"
                            " raw_updated TEXT, updated_at TEXT)")
                con.execute("INSERT INTO companies(cik, entity_name, is_listed) VALUES (?,?,?)",
                            ("0000000111", "A Inc", 1))
                con.commit()
                m.ensure_schema(con)
                cols = [r[1] for r in con.execute("PRAGMA table_info(companies)")]
                self.assertIn("sic", cols)
                self.assertIn("sic_desc", cols)
                row = con.execute("SELECT cik, entity_name, is_listed FROM companies").fetchone()
                self.assertEqual(tuple(row), ("0000000111", "A Inc", 1))
            finally:
                con.close()

    def test_t19_update_calls_submissions(self):
        with _temp_env() as (con, raw):
            m.set_state(con, "last_index_date", "2026-10-05")
            con.commit()
            sub_calls = []

            def get_bytes(url):
                if "daily-index" in url:
                    return b"h\n-----\n10-K foo 111 bar baz\n"
                if "companyfacts" in url:
                    return b'{"entityName":"X","facts":{}}'
                if "submissions" in url:
                    sub_calls.append(url)
                    return b'{"sic":"3571","sicDescription":"Electronic Computers"}'
                raise AssertionError(url)

            r = m.update(con, raw, date(2026, 10, 7), get_bytes, NOW)
            self.assertEqual(len(sub_calls), 1)
            self.assertIn("CIK0000000111", sub_calls[0])
            self.assertEqual(r["sic"], 1)


if __name__ == "__main__":
    unittest.main()
