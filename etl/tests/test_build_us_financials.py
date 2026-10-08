"""build_us_financials 테스트 (unittest, 네트워크 없음). PLAN §4 T1~T13."""
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402
import build_us_financials as m  # noqa: E402

TODAY = date(2026, 10, 8)


def _fact(val, form, filed, end, start=None, frame=None):
    """SEC 원본 흉내: start는 flow만, frame은 준 것만. 로직은 start/end만 쓴다."""
    d = {"val": val, "form": form, "filed": filed, "end": end}
    if start is not None:
        d["start"] = start
    if frame is not None:
        d["frame"] = frame
    return d


def _facts(concept_map):
    return {"facts": {"us-gaap": {c: {"units": {"USD": v}} for c, v in concept_map.items()}}}


def _run_facts():
    """run() 주입용: FY2024·FY2025 + 2025 Q1~Q3 (Q4 파생 유도)."""
    out = {}
    flows = {"RevenueFromContractWithCustomerExcludingAssessedTax": [4000, 5000],
             "OperatingIncomeLoss": [400, 750],
             "NetIncomeLoss": [320, 500]}
    for concept, (v24, v25) in flows.items():
        out[concept] = [
            _fact(v24, "10-K", "2025-02-01", "2024-12-31", "2024-01-01"),
            _fact(v25, "10-K", "2026-02-01", "2025-12-31", "2025-01-01"),
        ]
    stocks = {"Assets": [10000, 12000], "Liabilities": [6000, 7000],
              "StockholdersEquity": [4000, 5000]}
    for concept, (v24, v25) in stocks.items():
        out[concept] = [
            _fact(v24, "10-K", "2025-02-01", "2024-12-31"),
            _fact(v25, "10-K", "2026-02-01", "2025-12-31"),
        ]
    qflows = {"RevenueFromContractWithCustomerExcludingAssessedTax": [1000, 1200, 1300],
              "OperatingIncomeLoss": [150, 180, 200],
              "NetIncomeLoss": [100, 120, 130]}
    ends = {1: "2025-03-31", 2: "2025-06-30", 3: "2025-09-30"}
    starts = {1: "2025-01-01", 2: "2025-04-01", 3: "2025-07-01"}
    fileds = {1: "2025-04-30", 2: "2025-07-30", 3: "2025-10-30"}
    for concept, vals in qflows.items():
        out[concept] += [
            _fact(v, "10-Q", fileds[q], ends[q], starts[q])
            for q, v in enumerate(vals, 1)
        ]
    out["Assets"] += [
        _fact(11000 + q * 100, "10-Q", fileds[q], ends[q])
        for q in (1, 2, 3)
    ]
    return _facts(out)


def _pm5(with_q2=True):
    pm = {
        ("2024", "FY"): {"매출액": (1000, "fy-filed"), "자산총계": (5000, "fy-filed")},
        ("2024", "Q1"): {"매출액": (100, "q1"), "자산총계": (5100, "q1")},
        ("2024", "Q3"): {"매출액": (300, "q3"), "자산총계": (5300, "q3")},
    }
    if with_q2:
        pm[("2024", "Q2")] = {"매출액": (200, "q2"), "자산총계": (5200, "q2")}
    return pm


def _counts(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        a = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        i = con.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
        return (a, i)
    finally:
        con.close()


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeSession:
    """get 호출 기록 + 고정 응답. 네트워크 없음."""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return _FakeResp(self.status_code, self.payload)


class TestUsFinancials(unittest.TestCase):
    def test_t01_only_10k_forms(self):
        facts = _facts({"NetIncomeLoss": [
            _fact(999999, "DEF 14A", "2025-06-01", "2024-12-31", "2024-01-01"),
            _fact(12345, "10-K", "2025-02-01", "2024-12-31", "2024-01-01"),
        ]})
        got = m.extract_accounts(facts, ["2024"], [])
        self.assertEqual(got[("2024", "FY")]["당기순이익"][0], 12345)

    def test_t02_ytd_excluded(self):
        facts = _facts({"Revenues": [
            _fact(999, "10-Q", "2024-07-30", "2024-06-30", "2024-01-01"),
            _fact(100, "10-Q", "2024-07-30", "2024-06-30", "2024-04-01"),
        ]})
        got = m.extract_accounts(facts, [], [("2024", 2)])
        self.assertEqual(got[("2024", "Q2")]["매출액"][0], 100)

    def test_t03_concept_fallback_per_period(self):
        facts = _facts({
            "RevenueFromContractWithCustomerExcludingAssessedTax": [
                _fact(500, "10-K", "2024-02-01", "2023-12-31", "2023-01-01"),
            ],
            "Revenues": [
                _fact(700, "10-K", "2025-02-01", "2024-12-31", "2024-01-01"),
            ],
        })
        got = m.extract_accounts(facts, ["2023", "2024"], [])
        self.assertEqual(got[("2023", "FY")]["매출액"][0], 500)
        self.assertEqual(got[("2024", "FY")]["매출액"][0], 700)  # 1순위는 2024 fact 없음 → 2순위
        facts2 = _facts({
            "RevenueFromContractWithCustomerIncludingAssessedTax": [
                _fact(900, "10-K", "2025-02-01", "2024-12-31", "2024-01-01"),
            ],
        })
        got2 = m.extract_accounts(facts2, ["2024"], [])
        self.assertEqual(got2[("2024", "FY")]["매출액"][0], 900)

    def test_t04_latest_filed_wins(self):
        facts = _facts({"Assets": [
            _fact(1000, "10-K", "2025-02-01", "2024-12-31"),
            _fact(2000, "10-K", "2026-02-01", "2024-12-31"),
        ]})
        got = m.extract_accounts(facts, ["2024"], [])
        self.assertEqual(got[("2024", "FY")]["자산총계"], (2000, "2026-02-01"))

    def test_t05_q4_derivation(self):
        pm = _pm5()
        m.derive_q4(pm, [("2024", 4)])
        q4 = pm[("2024", "Q4")]
        self.assertEqual(q4["매출액"], (400, "fy-filed"))  # 1000-(100+200+300)
        self.assertEqual(q4["자산총계"], (5000, "fy-filed"))  # stock은 FY 그대로

        pm2 = _pm5(with_q2=False)
        m.derive_q4(pm2, [("2024", 4)])
        self.assertIsNone(pm2[("2024", "Q4")]["매출액"][0])  # Q2 빠지면 flow None
        self.assertEqual(pm2[("2024", "Q4")]["자산총계"], (5000, "fy-filed"))

        pm3 = _pm5()  # quarters에 (Y,4) 없으면 미생성
        m.derive_q4(pm3, [("2024", 3)])
        self.assertNotIn(("2024", "Q4"), pm3)

        pm4 = {k: v for k, v in _pm5().items() if k[1] != "FY"}  # FY 없으면 미생성
        m.derive_q4(pm4, [("2024", 4)])
        self.assertNotIn(("2024", "Q4"), pm4)

    def test_t06_bank_no_operating_income(self):
        facts = _facts({
            "Revenues": [_fact(1000, "10-K", "2025-02-01", "2024-12-31", "2024-01-01")],
            "NetIncomeLoss": [_fact(100, "10-K", "2025-02-01", "2024-12-31", "2024-01-01")],
        })
        got = m.extract_accounts(facts, ["2024"], [])
        self.assertEqual(got[("2024", "FY")]["영업이익"], (None, None))
        self.assertEqual(got[("2024", "FY")]["매출액"][0], 1000)
        self.assertEqual(got[("2024", "FY")]["당기순이익"][0], 100)
        ratios = m.compute_ratios(got)
        self.assertFalse([r for r in ratios if r[2] == "OP_MARGIN"])
        self.assertIn(("2024", "FY", "NET_MARGIN", "순이익률", 10.0), ratios)

    def test_t07_idempotent_rerun(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.sqlite3"
            uni = [("0000000001", "AAA", "AAA Inc", "NYSE")]
            facts = _run_facts()
            kw = dict(db_path=db, today=TODAY, universe=uni, fetch_fn=lambda cik: facts)
            m.run(**kw)
            n1 = _counts(db)
            self.assertGreater(n1[0], 0)
            self.assertGreater(n1[1], 0)
            m.run(**kw, force=True)
            self.assertEqual(_counts(db), n1)

    def test_t08_resume_skips_loaded(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.sqlite3"
            uni = [("0000000001", "AAA", "AAA Inc", "NYSE"),
                   ("0000000002", "BBB", "BBB Inc", "NYSE")]
            facts = _run_facts()
            calls = []

            def fetch_fn(cik):
                calls.append(cik)
                return facts

            m.run(db_path=db, today=TODAY, universe=uni, fetch_fn=fetch_fn, limit=1)
            self.assertEqual(calls, ["0000000001"])
            calls.clear()
            stats = m.run(db_path=db, today=TODAY, universe=uni, fetch_fn=fetch_fn)
            self.assertEqual(calls, ["0000000002"])
            self.assertEqual(stats["skipped"], 1)
            self.assertEqual(stats["targets"], 1)

    def test_t09_universe_filter(self):
        payload = {"fields": ["cik", "name", "ticker", "exchange"], "data": [
            [320193, "Apple Inc.", "AAPL", "Nasdaq"],
            [19617, "JPMorgan", "JPM", "NYSE"],
            [1, "Cboe Co", "CBO", "CBOE"],
            [2, "Otc Co", "OTC", "OTC"],
            [3, "NoEx", "NOX", None],
            [123, "Alphabet A", "GOOGL", "Nasdaq"],
            [123, "Alphabet C", "GOOG", "Nasdaq"],
        ]}
        self.assertEqual(m.filter_universe(payload), [
            ("0000320193", "AAPL", "Apple Inc.", "Nasdaq"),
            ("0000019617", "JPM", "JPMorgan", "NYSE"),
            ("0000000001", "CBO", "Cboe Co", "CBOE"),
            ("0000000123", "GOOGL", "Alphabet A", "Nasdaq"),
        ])

    def test_t10_ratio_values(self):
        pm = {
            ("2023", "FY"): {"매출액": (1000, "f"), "영업이익": (100, "f"), "당기순이익": (80, "f"),
                             "부채총계": (600, "f"), "자본총계": (400, "f")},
            ("2024", "FY"): {"매출액": (1500, "f"), "영업이익": (300, "f"), "당기순이익": (200, "f"),
                             "부채총계": (900, "f"), "자본총계": (600, "f")},
            ("2024", "Q1"): {"매출액": (300, "f"), "영업이익": (60, "f")},
        }
        self.assertEqual(m.compute_ratios(pm), [
            ("2023", "FY", "ROE", "ROE", 20.0),  # 전년 없음 → 기말자본
            ("2023", "FY", "DEBT_RATIO", "부채비율", 150.0),
            ("2023", "FY", "NET_MARGIN", "순이익률", 8.0),
            ("2023", "FY", "OP_MARGIN", "영업이익률", 10.0),
            ("2024", "FY", "ROE", "ROE", 40.0),  # 200/((600+400)/2)*100
            ("2024", "FY", "DEBT_RATIO", "부채비율", 150.0),
            ("2024", "FY", "REV_GROWTH", "매출증가율", 50.0),
            ("2024", "FY", "NET_MARGIN", "순이익률", 13.33),
            ("2024", "FY", "OP_MARGIN", "영업이익률", 20.0),
            ("2024", "Q1", "OP_MARGIN", "영업이익률", 20.0),  # 분기는 영업이익률만
        ])

    def test_t11_amendment_wins(self):
        facts = _facts({"NetIncomeLoss": [
            _fact(100, "10-K", "2025-02-01", "2024-12-31", "2024-01-01"),
            _fact(150, "10-K/A", "2025-03-01", "2024-12-31", "2024-01-01"),
        ]})
        got = m.extract_accounts(facts, ["2024"], [])
        self.assertEqual(got[("2024", "FY")]["당기순이익"], (150, "2025-03-01"))

    def test_t12_user_agent_and_backoff(self):
        sess = _FakeSession(200, {"facts": {}})
        out = m.fetch_facts("0000320193", sess, "UA-TEST")
        self.assertEqual(out, {"facts": {}})
        self.assertEqual(len(sess.calls), 1)
        self.assertIn("0000320193", sess.calls[0]["url"])
        self.assertEqual(sess.calls[0]["headers"], {"User-Agent": "UA-TEST"})
        self.assertEqual(sess.calls[0]["timeout"], m.REQUEST_TIMEOUT)
        self.assertGreaterEqual(m.DELAY, 0.1)
        self.assertIsNone(m.fetch_facts("0000000000", _FakeSession(404), "UA-TEST"))

    def test_t12b_network_error_becomes_fetcherror(self):
        class _ErrSession:
            def __init__(self):
                self.calls = 0

            def get(self, url, headers=None, timeout=None):
                self.calls += 1
                raise requests.ConnectionError("boom")

        sess = _ErrSession()
        with mock.patch.object(m.time, "sleep") as mock_sleep:
            with self.assertRaises(m.FetchError):
                m.fetch_facts("0000320193", sess, "UA-TEST")
        self.assertEqual(sess.calls, m.MAX_RETRIES)
        self.assertEqual(mock_sleep.call_count, m.MAX_RETRIES - 1)

    def test_t13_derived_frame(self):
        facts = _facts({"NetIncomeLoss": [
            _fact(100, "10-K", "2025-02-14", "2024-12-31", "2024-01-01"),
            _fact(100, "10-K", "2026-02-13", "2024-12-31", "2024-01-01"),
            _fact(999, "DEF 14A", "2026-04-06", "2024-12-31", "2024-01-01", "CY2024"),
        ]})
        got = m.extract_accounts(facts, ["2024"], [])
        self.assertEqual(got[("2024", "FY")]["당기순이익"], (100, "2026-02-13"))
        df = m.derive_frame
        self.assertEqual(df({"start": "2023-10-01", "end": "2024-09-28"}), "CY2024")
        self.assertEqual(df({"start": "2024-02-01", "end": "2025-01-31"}), "CY2024")
        self.assertEqual(df({"start": "2024-09-29", "end": "2024-12-28"}), "CY2024Q4")
        self.assertIsNone(df({"start": "2024-01-01", "end": "2024-09-30"}))
        self.assertEqual(df({"end": "2024-09-28"}), "CY2024Q3I")
        self.assertEqual(df({"end": "2025-01-31"}), "CY2024Q4I")
        self.assertIsNone(df({"end": "2024-05-15"}))

    def test_t14_liabilities_fallback(self):
        facts = _facts({
            "LiabilitiesAndStockholdersEquity": [
                _fact(1000, "10-K", "2025-02-10", "2024-12-31"),
            ],
            "StockholdersEquity": [
                _fact(400, "10-K", "2025-02-01", "2024-12-31"),
            ],
        })
        got = m.extract_accounts(facts, ["2024"], [])
        self.assertEqual(got[("2024", "FY")]["부채총계"], (600, "2025-02-10"))
        ratios = m.compute_ratios(got)
        self.assertIn(("2024", "FY", "DEBT_RATIO", "부채비율", 150.0), ratios)

        facts2 = _facts({
            "LiabilitiesAndStockholdersEquity": [
                _fact(1000, "10-K", "2025-02-10", "2024-12-31"),
            ],
            "StockholdersEquity": [
                _fact(400, "10-K", "2025-02-01", "2024-12-31"),
            ],
            "Liabilities": [
                _fact(700, "10-K", "2025-02-01", "2024-12-31"),
            ],
        })
        got2 = m.extract_accounts(facts2, ["2024"], [])
        self.assertEqual(got2[("2024", "FY")]["부채총계"], (700, "2025-02-01"))

        facts3 = _facts({
            "LiabilitiesAndStockholdersEquity": [
                _fact(1000, "10-K", "2025-02-10", "2024-12-31"),
            ],
            "Assets": [
                _fact(1000, "10-K", "2025-02-01", "2024-12-31"),
            ],
        })
        got3 = m.extract_accounts(facts3, ["2024"], [])
        self.assertEqual(got3[("2024", "FY")]["부채총계"], (None, None))

        facts4 = _facts({
            "LiabilitiesAndStockholdersEquity": [
                _fact(2000, "10-Q", "2024-07-30", "2024-06-30"),
            ],
            "StockholdersEquity": [
                _fact(800, "10-Q", "2024-07-30", "2024-06-30"),
            ],
        })
        got4 = m.extract_accounts(facts4, [], [("2024", 2)])
        self.assertEqual(got4[("2024", "Q2")]["부채총계"], (1200, "2024-07-30"))


if __name__ == "__main__":
    unittest.main()
