"""strategy_snapshot 엔진·되돌림 어댑터 테스트 (unittest, tempfile sqlite만)."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.strategy_snapshot import (
    compute,
    ensure,
    log_seed,
    merge_seed_events,
    rebound_fills,
    rebound_seed_events,
    realized_check,
    twr,
    upsert,
)

D1, D2, D3 = "20261118", "20261119", "20261120"


def _seed(date, capital, since=None, note=""):
    return {"date": date, "capital": float(capital),
            "since": since or date, "note": note}


class EngineTest(unittest.TestCase):
    def test_same_day_roundtrip(self):
        fills = [
            {"date": D1, "ticker": "000001", "side": "buy", "qty": 100,
             "price": 1000.0, "ref": "t"},
            {"date": D1, "ticker": "000001", "side": "sell", "qty": 100,
             "price": 1100.0, "ref": "t"},
        ]
        rows = compute(fills, [_seed(D1, 5_000_000)], {(D1, "000001"): 1100}, [D1])
        want = 100 * 1100 * (1 - 0.00215) - 100 * 1000 * 1.00015
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["realized_cum"], want)
        self.assertEqual(rows[0]["unrealized"], 0)
        self.assertAlmostEqual(rows[0]["equity"], 5_000_000 + want)
        self.assertEqual(rows[0]["open_n"], 0)
        self.assertEqual(rows[0]["trades_cum"], 1)

    def test_missing_close_uses_previous(self):
        fills = [{"date": D1, "ticker": "000001", "side": "buy", "qty": 10,
                  "price": 1000.0, "ref": "t"}]
        rows = compute(fills, [_seed(D1, 5_000_000)],
                       {(D1, "000001"): 1200}, [D1, D2])
        self.assertAlmostEqual(rows[1]["unrealized"], rows[0]["unrealized"])
        self.assertAlmostEqual(rows[0]["unrealized"], 10 * 1200 - 10 * 1000 * 1.00015)
        self.assertEqual(rows[1]["open_n"], 1)

    def test_seed_increase_is_flow_not_pnl(self):
        fills = [{"date": D1, "ticker": "000001", "side": "buy", "qty": 10,
                  "price": 1000.0, "ref": "t"}]
        closes = {(D1, "000001"): 1000, (D2, "000001"): 1000}
        events = [_seed(D1, 5_000_000), _seed(D2, 6_000_000, D1, "증액")]
        rows = compute(fills, events, closes, [D1, D2])
        self.assertAlmostEqual(rows[1]["flow"], 1_000_000)
        self.assertEqual(rows[1]["flow_note"], "증액")
        self.assertAlmostEqual(rows[1]["pnl"], 0.0)
        self.assertEqual(twr(rows), [0.0, 0.0])

    def test_since_reset_drops_old_realized_into_flow(self):
        fills = [
            {"date": D1, "ticker": "000001", "side": "buy", "qty": 100,
             "price": 1000.0, "ref": "t"},
            {"date": D1, "ticker": "000001", "side": "sell", "qty": 100,
             "price": 1100.0, "ref": "t"},
        ]
        events = [_seed(D1, 5_000_000), _seed(D2, 5_000_000, D2, "리셋")]
        rows = compute(fills, events, {(D1, "000001"): 1100}, [D1, D2])
        r1 = rows[0]["realized_cum"]
        self.assertGreater(abs(r1), 0.5)
        self.assertAlmostEqual(rows[1]["realized_cum"], 0.0)
        self.assertAlmostEqual(rows[1]["flow"], -r1)
        self.assertAlmostEqual(rows[1]["equity"] - rows[0]["equity"],
                               rows[1]["pnl"] + rows[1]["flow"])

    def test_oversell_raises(self):
        fills = [{"date": D1, "ticker": "000001", "side": "sell", "qty": 1,
                  "price": 1000.0, "ref": "t"}]
        with self.assertRaises(ValueError):
            compute(fills, [_seed(D1, 5_000_000)], {}, [D1])
        fills = [
            {"date": D1, "ticker": "000001", "side": "buy", "qty": 5,
             "price": 1000.0, "ref": "t"},
            {"date": D1, "ticker": "000001", "side": "sell", "qty": 6,
             "price": 1000.0, "ref": "t"},
        ]
        with self.assertRaises(ValueError):
            compute(fills, [_seed(D1, 5_000_000)], {}, [D1])


class ReboundAdapterTest(unittest.TestCase):
    def _wl(self) -> sqlite3.Connection:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        con = sqlite3.connect(str(Path(tmp) / "watchlist.sqlite3"))
        self.addCleanup(con.close)
        return con

    def test_fills_three_rows(self):
        con = self._wl()
        con.execute("CREATE TABLE rebound_positions(date TEXT, ticker TEXT,"
                    " fq1 REAL, fp1 REAL, fq2 REAL, fp2 REAL, fq3 REAL, fp3 REAL,"
                    " sell_qty REAL, sell_px REAL, sell_status TEXT, reason TEXT,"
                    " realized REAL)")
        con.execute("INSERT INTO rebound_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (D1, "000001", 0, None, 0, None, 0, None, None, None, None, None, None))
        con.execute("INSERT INTO rebound_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (D1, "000002", 10, 1000, 0, None, 0, None, 10, 1100, "filled", None, 9000.0))
        con.execute("INSERT INTO rebound_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (D1, "000003", 5, 2000, 0, None, 0, None, 5, 2100, "carried", None, None))
        con.execute("INSERT INTO rebound_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (D1, "000004", 7, 3000, 0, None, 0, None, 7, 3100, None, "carried", None))
        fills = rebound_fills(con, [D1, D2])
        by_ticker: dict[str, list[dict]] = {}
        for fill in fills:
            by_ticker.setdefault(fill["ticker"], []).append(fill)
        self.assertNotIn("000001", by_ticker)
        self.assertEqual([(f["side"], f["date"]) for f in by_ticker["000002"]],
                         [("buy", D1), ("sell", D1)])
        self.assertEqual(by_ticker["000002"][0]["qty"], 10)
        self.assertEqual(by_ticker["000002"][0]["ref"], f"rebound:{D1}:000002")
        self.assertEqual([(f["side"], f["date"]) for f in by_ticker["000003"]],
                         [("buy", D1), ("sell", D2)])
        self.assertEqual([(f["side"], f["date"]) for f in by_ticker["000004"]],
                         [("buy", D1), ("sell", D2)])

    def test_seed_events_with_msig(self):
        con = self._wl()
        con.execute("CREATE TABLE strategy_budget_resets(id INTEGER PRIMARY KEY,"
                    " date TEXT, strategy TEXT, old_capital INTEGER,"
                    " new_capital INTEGER, since TEXT, note TEXT, created_at TEXT)")
        con.execute("INSERT INTO strategy_budget_resets"
                    "(date, strategy, old_capital, new_capital, since, note)"
                    " VALUES (?,?,?,?,?,?)", (D1, "msig", 1, 2, D2, "무시됨"))
        con.execute("INSERT INTO strategy_budget_resets"
                    "(date, strategy, old_capital, new_capital, since, note)"
                    " VALUES (?,?,?,?,?,?)", (D1, "rebound", 5_000_000, 6_000_000, D2, "r1"))
        con.execute("INSERT INTO strategy_budget_resets"
                    "(date, strategy, old_capital, new_capital, since, note)"
                    " VALUES (?,?,?,?,?,?)", (D2, "rebound", 6_000_000, 7_000_000, D3, "r2"))
        con.execute("CREATE TABLE msig_settlements(date TEXT, rebound_capital REAL)")
        con.execute("INSERT INTO msig_settlements VALUES (?,?)", (D2, 6_500_000))
        events = rebound_seed_events(con, D1)
        self.assertEqual(
            [(e["date"], e["capital"], e["since"]) for e in events],
            [(D1, 5_000_000, D1), (D2, 6_000_000, D2),
             (D2, 6_500_000, D2), (D3, 7_000_000, D3)])
        self.assertEqual(events[0]["note"], "리셋 전 시드")
        self.assertEqual(events[2]["note"], "msig 정산 이체")

    def test_realized_check(self):
        con = self._wl()
        con.execute("CREATE TABLE rebound_positions(date TEXT, ticker TEXT,"
                    " fq1 REAL, fp1 REAL, fq2 REAL, fp2 REAL, fq3 REAL, fp3 REAL,"
                    " sell_qty REAL, sell_px REAL, realized REAL)")
        con.execute("INSERT INTO rebound_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (D1, "000002", 10, 1000, 0, None, 0, None, 10, 1100, 9000.0))
        con.execute("INSERT INTO rebound_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (D1, "000003", 5, 2000, 0, None, 0, None, 5, 2100, None))
        rows = realized_check(con)
        self.assertEqual(len(rows), 1)
        want = 10 * 1100 * (1 - 0.00215) - 10 * 1000 * 1.00015
        self.assertAlmostEqual(rows[0]["ours"], want)
        self.assertEqual(rows[0]["kiwoom"], 9000.0)
        self.assertAlmostEqual(rows[0]["diff"], want - 9000.0)


class SeedLogStoreTest(unittest.TestCase):
    def test_log_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            perf = Path(tmp) / "performance.sqlite3"
            ensure(perf)
            self.assertTrue(log_seed(perf, "rebound", D1, 5_000_000, D1))
            self.assertFalse(log_seed(perf, "rebound", D2, 5_000_000, D1))
            self.assertTrue(log_seed(perf, "rebound", D2, 6_000_000, D2))

    def test_merge_seed_events(self):
        adapter = [_seed(D2, 6_000_000, D2, "r1")]
        config = [
            {"date": D2, "capital": 6_000_000.0, "since": D2, "note": "config"},
            {"date": D3, "capital": 6_500_000.0, "since": D2, "note": "config"},
        ]
        merged = merge_seed_events(adapter, config)
        self.assertEqual([e["date"] for e in merged], [D2, D3])
        self.assertEqual(merged[0]["note"], "r1")
        dup = [{"date": D3, "capital": 6_000_000.0, "since": D2, "note": "config"}]
        self.assertEqual(len(merge_seed_events(adapter, dup)), 1)

    def test_upsert_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            perf = Path(tmp) / "performance.sqlite3"
            rows = compute(
                [{"date": D1, "ticker": "000001", "side": "buy", "qty": 10,
                  "price": 1000.0, "ref": "t"}],
                [_seed(D1, 5_000_000)], {(D1, "000001"): 1000}, [D1])
            upsert(perf, "rebound", "live", rows)
            rows[0]["equity"] += 123.0
            upsert(perf, "rebound", "live", rows)
            con = sqlite3.connect(str(perf))
            try:
                db_rows = con.execute(
                    "SELECT date, strategy, kind, equity FROM strategy_daily").fetchall()
            finally:
                con.close()
            self.assertEqual(len(db_rows), 1)
            self.assertAlmostEqual(db_rows[0][3], rows[0]["equity"])


if __name__ == "__main__":
    unittest.main()
