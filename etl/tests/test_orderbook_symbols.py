"""호가 수집기 대상 종목 — 오전 합집합·오후 완료 산출물 검증 (SPEC §4, T16~T21·T28)."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.orderbook_recorder_config import DEFAULTS
from scripts.orderbook_symbols import plan_additions, resolve_symbols

KST = timezone(timedelta(hours=9))
DATE = "20260911"
NOW = datetime(2026, 9, 11, 15, 5, tzinfo=KST)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "watchlist.sqlite3"
        self.scores = root / "recent_3day_probability_scores.json"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE llm_scores (date TEXT, ticker TEXT, score INTEGER,"
                    " PRIMARY KEY (date, ticker))")
        con.execute("CREATE TABLE close_bet_orders (date TEXT, ticker TEXT, status TEXT,"
                    " sell_status TEXT, PRIMARY KEY (date, ticker))")
        con.commit()
        con.close()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["scoring_result_path"] = self.scores

    def tearDown(self):
        self.tmp.cleanup()

    def sql(self, query, rows):
        con = sqlite3.connect(self.db)
        con.executemany(query, rows)
        con.commit()
        con.close()

    def resolve(self, window, now=NOW):
        return resolve_symbols(self.cfg, DATE, window, watchlist_db=self.db, now=now)


class MorningTest(Base):
    def orders(self, *rows):
        self.sql("INSERT INTO close_bet_orders VALUES (?,?,?,?)", rows)

    def scores_on(self, date, *tickers):
        self.sql("INSERT INTO llm_scores VALUES (?,?,50)", [(date, t) for t in tickers])

    def test_unsold_condition_matches_exit_worker(self):
        self.orders(("20260910", "111111", "confirmed", None),
                    ("20260909", "222222", "confirmed", "filled"),   # 이미 청산
                    ("20260910", "333333", "pending", None),         # 미확정
                    ("20260911", "444444", "confirmed", None))       # 당일
        self.assertEqual(self.resolve("morning")["symbols"], ["111111"])

    def test_t16_stale_candidates_dropped_but_unsold_kept(self):
        self.orders(("20260910", "111111", "confirmed", None))
        self.scores_on("20260901", "000001")                        # 10일 전 > 5일
        got = self.resolve("morning")
        self.assertEqual(got["status"], "ready")
        self.assertEqual(got["symbols"], ["111111"])
        self.assertIsNone(got["source_date"])
        self.assertIn("candidates_stale", got["source_meta"]["notes"])

    def test_lookback_boundary_is_inclusive(self):
        self.scores_on("20260906", "000001")                        # 정확히 5일
        got = self.resolve("morning")
        self.assertEqual((got["symbols"], got["source_date"]), (["000001"], "20260906"))

    def test_t17_order_and_limit(self):
        self.cfg["symbols"].update(max=2, static=["999999", "111111"])
        self.orders(("20260910", "111111", "confirmed", None))
        self.scores_on("20260910", "000002", "000001", "111111")
        got = self.resolve("morning")
        self.assertEqual(got["symbols"], ["111111", "000001"])     # 미청산 → 후보(오름차순) → static
        self.assertEqual(got["excluded"], [{"ticker": "000002", "reason": "symbol_limit"},
                                           {"ticker": "999999", "reason": "symbol_limit"}])
        self.assertEqual(got["source_date"], "20260910")

    def test_nothing_is_empty(self):
        self.assertEqual(self.resolve("morning")["status"], "empty")

    def test_missing_db_is_empty_with_note(self):
        self.db.unlink()
        got = self.resolve("morning")
        self.assertEqual(got["status"], "empty")
        self.assertIn("watchlist_db_missing", got["source_meta"]["notes"])
        self.assertFalse(self.db.exists())                          # 조회하면서 빈 DB 를 만들지 않음


def complete(tickers=("000660", "0197V0"), **over):
    doc = {"generated_at": "2026-09-11T15:02:38+09:00", "db_write": True,
           "results": [{"date": DATE, "candidate_count": len(tickers), "scored_count": len(tickers),
                        "scores": [{"date": DATE, "ticker": t, "probability_score": 40 + i}
                                   for i, t in enumerate(tickers)]}]}
    doc.update(over)
    return doc


class AfternoonTest(Base):
    def write(self, doc):
        raw = json.dumps(doc).encode("utf-8") if isinstance(doc, dict) else doc
        self.scores.write_bytes(raw)
        return raw

    def db_scores(self, pairs):
        self.sql("INSERT INTO llm_scores VALUES (?,?,?)", [(DATE, t, s) for t, s in pairs])

    def assert_wait(self, reason):
        got = self.resolve("afternoon")
        self.assertEqual(got["status"], "wait")
        self.assertEqual(got["symbols"], [])
        self.assertEqual(got["source_meta"]["reason"], reason)

    def test_t18_missing_partial_or_previous_day(self):
        self.assert_wait("file_missing")
        self.write(b'{"generated_at": "2026-09-11T15:02')
        self.assert_wait("invalid_json")
        doc = complete()
        doc["results"][0]["date"] = "20260910"
        self.write(doc)
        self.assert_wait("no_result_for_date")

    def test_generated_at_must_be_today_after_start_and_not_future(self):
        self.db_scores([("000660", 40), ("0197V0", 41)])
        for stamp, reason in (("2026-09-11T15:02:38", "generated_at_invalid"),     # tz 없음
                              ("2026-09-10T15:02:38+09:00", "generated_at_stale"),
                              ("2026-09-11T14:59:59+09:00", "generated_at_stale"),
                              ("2026-09-11T15:06:00+09:00", "generated_at_future")):
            with self.subTest(stamp=stamp):
                self.write(complete(generated_at=stamp))
                self.assert_wait(reason)

    def test_t19_db_write_false_mismatch_or_duplicates(self):
        self.db_scores([("000660", 40), ("0197V0", 99)])
        self.write(complete(db_write=False))
        self.assert_wait("db_write_false")
        self.write(complete())
        self.assert_wait("db_score_mismatch")
        dup = complete()
        dup["results"][0]["scores"][1]["ticker"] = "000660"
        self.write(dup)
        self.assert_wait("scores_invalid")

    def test_counts_dates_and_db_rows_must_agree(self):
        self.db_scores([("000660", 40)])
        counts = complete()
        counts["results"][0]["scored_count"] = 1
        self.write(counts)
        self.assert_wait("count_mismatch")
        other_day = complete()
        other_day["results"][0]["scores"][0]["date"] = "20260910"
        self.write(other_day)
        self.assert_wait("scores_invalid")
        self.write(complete())
        self.assert_wait("db_score_mismatch")                       # 0197V0 가 DB 에 없음

    def test_t20_complete_result_is_ready(self):
        self.cfg["symbols"]["static"] = ["005930", "000660"]
        self.db_scores([("0197V0", 40), ("000660", 41), ("111111", 10)])   # 111111 = 재실행 잔여 행
        raw = self.write(complete(tickers=("0197V0", "000660")))
        got = self.resolve("afternoon")
        self.assertEqual(got["status"], "ready")
        self.assertEqual(got["symbols"], ["000660", "0197V0", "005930"])
        self.assertEqual(got["source_date"], DATE)
        self.assertEqual(got["source_meta"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(got["source_meta"]["generated_at"], "2026-09-11T15:02:38+09:00")

    def test_t28_zero_candidates_uses_static_or_stays_empty(self):
        self.write(complete(tickers=()))
        self.assertEqual(self.resolve("afternoon")["status"], "empty")
        self.cfg["symbols"]["static"] = ["005930"]
        got = self.resolve("afternoon")
        self.assertEqual((got["status"], got["symbols"]), ("ready", ["005930"]))


class PlanAdditionsTest(unittest.TestCase):
    def test_t21_only_new_codes_within_limit_including_current(self):
        add, excluded = plan_additions(["000001", "000002"], ["000001", "000003", "000004"], 3)
        self.assertEqual(add, ["000003"])
        self.assertEqual(excluded, [{"ticker": "000004", "reason": "symbol_limit"}])

    def test_nothing_new(self):
        self.assertEqual(plan_additions(["000001"], ["000001"], 20), ([], []))


if __name__ == "__main__":
    unittest.main()
