"""strategy_snapshot 엔진·되돌림·유무상 어댑터 테스트 (unittest, tempfile sqlite만)."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import scripts.strategy_snapshot as snap
from scripts.strategy_snapshot import (
    ADAPTERS,
    compute,
    ensure,
    log_seed,
    main,
    merge_seed_events,
    rebound_fills,
    rebound_seed_events,
    realized_check,
    rights_check,
    rights_fills,
    rights_seed,
    rights_seed_events,
    twr,
    upsert,
)

D1, D2, D3 = "20261118", "20261119", "20261120"
D4 = "20261121"


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
        # 첫날 TWR에 당일 손익 포함되므로 0이 아님 (수수료만큼 미세 음수)
        r = twr(rows)
        self.assertAlmostEqual(r[0], rows[0]["pnl"] / rows[0]["flow"])
        self.assertAlmostEqual(r[1], 0.0)

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

    def test_first_day_profit_in_twr(self):
        fills = [
            {"date": D1, "ticker": "000001", "side": "buy", "qty": 100,
             "price": 1000.0, "ref": "t"},
            {"date": D1, "ticker": "000001", "side": "sell", "qty": 100,
             "price": 1100.0, "ref": "t"},
        ]
        rows = compute(fills, [_seed(D1, 1_000_000)], {(D1, "000001"): 1100}, [D1])
        profit = 100 * 1100 * (1 - 0.00215) - 100 * 1000 * 1.00015
        self.assertAlmostEqual(rows[0]["flow"], 1_000_000)
        r = twr(rows)
        self.assertAlmostEqual(r[0], profit / 1_000_000)
        cum = 1.0
        for v in r:
            cum *= 1 + v
        cum -= 1
        self.assertAlmostEqual(cum, profit / 1_000_000)
        self.assertEqual(snap.twr_mdd(rows), 0)

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

    def test_carried_sell_out_of_range_dropped(self):
        con = self._wl()
        con.execute("CREATE TABLE rebound_positions(date TEXT, ticker TEXT,"
                    " fq1 REAL, fp1 REAL, sell_qty REAL, sell_px REAL,"
                    " sell_status TEXT, reason TEXT)")
        con.execute("INSERT INTO rebound_positions VALUES (?,?,?,?,?,?,?,?)",
                    (D1, "000001", 5, 2000, 5, 2100, "carried", None))
        fills_narrow = rebound_fills(con, [D1])
        self.assertEqual([f["side"] for f in fills_narrow], ["buy"])
        fills_wide = rebound_fills(con, [D1, D2])
        self.assertEqual([(f["side"], f["date"]) for f in fills_wide],
                         [("buy", D1), ("sell", D2)])
        closes = {(D1, "000001"): 2100.0, (D2, "000001"): 2100.0}
        rows_narrow = compute(fills_narrow, [_seed(D1, 5_000_000)], closes, [D1])
        rows_wide = compute(fills_wide, [_seed(D1, 5_000_000)], closes, [D1, D2])
        self.assertEqual(rows_narrow[0], rows_wide[0])

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

    def test_log_seed_since_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            perf = Path(tmp) / "performance.sqlite3"
            ensure(perf)
            self.assertTrue(log_seed(perf, "rebound", D1, 5_000_000, D1))
            self.assertTrue(log_seed(perf, "rebound", D2, 5_000_000, D2))
            events = snap.load_config_seed_events(perf, "rebound")
            self.assertEqual([(e["date"], e["capital"], e["since"]) for e in events],
                             [(D1, 5_000_000, D1), (D2, 5_000_000, D2)])
            fills = [
                {"date": D1, "ticker": "000001", "side": "buy", "qty": 100,
                 "price": 1000.0, "ref": "t"},
                {"date": D1, "ticker": "000001", "side": "sell", "qty": 100,
                 "price": 1100.0, "ref": "t"},
            ]
            rows = compute(fills, events, {(D1, "000001"): 1100}, [D1, D2])
            self.assertGreater(abs(rows[0]["realized_cum"]), 0.5)
            self.assertAlmostEqual(rows[1]["realized_cum"], 0.0)

    def test_merge_keeps_config_roundtrip(self):
        adapter = [_seed(D1, 5_000_000)]
        config = [
            {"date": D2, "capital": 6_000_000.0, "since": D2, "note": "config"},
            {"date": D3, "capital": 5_000_000.0, "since": D3, "note": "config"},
        ]
        merged = merge_seed_events(adapter, config)
        self.assertEqual([e["date"] for e in merged], [D1, D2, D3])
        fills = [{"date": D1, "ticker": "000001", "side": "buy", "qty": 10,
                  "price": 1000.0, "ref": "t"}]
        closes = {(D1, "000001"): 1000.0, (D2, "000001"): 1000.0,
                  (D3, "000001"): 1000.0}
        rows = compute(fills, merged, closes, [D1, D2, D3])
        self.assertAlmostEqual(rows[2]["flow"], -1_000_000)

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


class ExternalCapitalTest(unittest.TestCase):
    def test_external_buy_sell_flow_and_twr(self):
        fills = [
            {"date": D2, "ticker": "000001", "side": "buy", "qty": 100,
             "price": 1000.0, "ref": "x", "external": True},
            {"date": D4, "ticker": "000001", "side": "sell", "qty": 100,
             "price": 1100.0, "ref": "x", "external": True},
        ]
        closes = {(D2, "000001"): 1000.0, (D3, "000001"): 1050.0,
                  (D4, "000001"): 1100.0}
        rows = compute(fills, [_seed(D1, 5_000_000)], closes, [D1, D2, D3, D4])
        buy_cost = 100 * 1000 * 1.00015
        proceeds = 100 * 1100 * (1 - 0.00215)
        self.assertAlmostEqual(rows[1]["flow"], buy_cost)
        self.assertEqual(rows[1]["flow_note"], "추가 자금")
        self.assertAlmostEqual(rows[3]["flow"], -proceeds)
        self.assertEqual(rows[3]["flow_note"], "추가 자금")
        self.assertAlmostEqual(rows[2]["flow"], 0.0)
        r = twr(rows)
        self.assertAlmostEqual(r[1], (100 * 1000 - buy_cost) / 5_000_000)
        self.assertAlmostEqual(r[2], (1050 - 1000) * 100 / rows[1]["equity"])

    def test_external_note_appends_to_event_note(self):
        fills = [{"date": D2, "ticker": "000001", "side": "buy", "qty": 100,
                  "price": 1000.0, "ref": "x", "external": True}]
        events = [_seed(D1, 5_000_000),
                  {"date": D2, "capital": 6_000_000.0, "since": D1, "note": "증액"}]
        rows = compute(fills, events, {(D2, "000001"): 1000.0}, [D1, D2])
        self.assertEqual(rows[1]["flow_note"], "증액 · 추가 자금")
        self.assertAlmostEqual(rows[1]["flow"], 1_000_000 + 100 * 1000 * 1.00015)


class RightsAdapterTest(unittest.TestCase):
    def _wl(self) -> sqlite3.Connection:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        con = sqlite3.connect(str(Path(tmp) / "watchlist.sqlite3"))
        self.addCleanup(con.close)
        return con

    def _capital_db(self, rows) -> sqlite3.Connection:
        con = self._wl()
        con.execute("CREATE TABLE rights_dip_capital(id INTEGER PRIMARY KEY, date TEXT,"
                    " capital REAL, pnl REAL, note TEXT, created_at TEXT)")
        con.executemany(
            "INSERT INTO rights_dip_capital(id, date, capital, pnl) VALUES (?,?,?,?)", rows)
        return con

    def test_fills_classify_side_and_external(self):
        con = self._wl()
        con.execute("CREATE TABLE rights_dip_positions(acptno TEXT, ticker TEXT, extra INTEGER)")
        con.execute("INSERT INTO rights_dip_positions VALUES (?,?,?)", ("A1", "000001", 0))
        con.execute("INSERT INTO rights_dip_positions VALUES (?,?,?)", ("A2", "000002", 1))
        con.execute("CREATE TABLE rights_dip_candidates(acptno TEXT, ticker TEXT)")
        con.execute("INSERT INTO rights_dip_candidates VALUES (?,?)", ("A3", "000003"))
        con.execute("CREATE TABLE rights_dip_orders(id INTEGER PRIMARY KEY, date TEXT,"
                    " acptno TEXT, extra INTEGER, leg TEXT, qty REAL,"
                    " filled_qty REAL, filled_price REAL)")
        con.executemany("INSERT INTO rights_dip_orders VALUES (?,?,?,?,?,?,?,?)", [
            (1, D1, "A1", 0, "b1_1", 10, 10, 1000.0),
            (2, D1, "A1", 0, "b2", 10, 5, 1100.0),
            (3, D1, "A1", 0, "b1_2", 10, 0, 1050.0),
            (4, D1, "A1", 0, "b1_3", 10, 5, None),
            (5, D2, "A1", 0, "exit", 15, 15, 1200.0),
            (6, D1, "A2", 1, "fb", 20, 20, 500.0),
            (7, D2, "A2", 1, "tp", 20, 20, 600.0),
            (8, D1, "A3", 0, "fw", 7, 7, 700.0),
            (9, D1, "A9", 0, "b1_1", 3, 3, 100.0),
        ])
        fills = rights_fills(con, [D1, D2])
        got = sorted((f["ticker"], f["side"], f["external"], f["qty"], f["price"])
                     for f in fills)
        self.assertEqual(got, [
            ("000001", "buy", False, 5, 1100.0),
            ("000001", "buy", False, 10, 1000.0),
            ("000001", "sell", False, 15, 1200.0),
            ("000002", "buy", True, 20, 500.0),
            ("000002", "sell", True, 20, 600.0),
            ("000003", "buy", False, 7, 700.0),
        ])
        by_ref = {f["ref"]: f for f in fills}
        self.assertIn("rights_dip:A1:exit:5", by_ref)
        self.assertIs(by_ref["rights_dip:A2:tp:7"]["external"], True)
        self.assertEqual({f["date"] for f in fills if f["side"] == "sell"}, {D2})

    def test_seed_events_reset_rules(self):
        con = self._wl()
        con.execute("CREATE TABLE strategy_budget_resets(id INTEGER PRIMARY KEY,"
                    " date TEXT, strategy TEXT, old_capital INTEGER,"
                    " new_capital INTEGER, since TEXT, note TEXT, created_at TEXT)")
        con.execute("INSERT INTO strategy_budget_resets"
                    "(date, strategy, old_capital, new_capital, since, note)"
                    " VALUES (?,?,?,?,?,?)", (D1, "rebound", 1, 2, D2, "무시됨"))
        con.execute("INSERT INTO strategy_budget_resets"
                    "(date, strategy, old_capital, new_capital, since, note)"
                    " VALUES (?,?,?,?,?,?)", (D1, "rights_dip", 8_000_000, 7_000_000, D2, "r1"))
        events = rights_seed_events(con, D1)
        self.assertEqual(
            [(e["date"], e["capital"], e["since"]) for e in events],
            [(D1, 8_000_000, D1), (D2, 7_000_000, D2)])
        self.assertEqual(events[0]["note"], "리셋 전 시드")

    def test_seed_sums_capital_changes(self):
        con = self._capital_db([
            (1, "20261015", 7_114_040, -885_960),
            (2, "20261020", 7_114_040, 526_848),
            (3, "20261101", 6_980_078, -133_962),
        ])
        config = {"capital": 6_980_078, "capital_since": "20261015"}
        self.assertEqual(rights_seed(config, con), 8_000_000)

    def test_seed_ignores_rows_before_since(self):
        con = self._capital_db([
            (1, "20261001", 9_000_000, 500_000),
            (2, "20261015", 7_114_040, -885_960),
            (3, "20261020", 7_114_040, 526_848),
            (4, "20261101", 6_980_078, -133_962),
        ])
        config = {"capital": 6_980_078, "capital_since": "20261015"}
        self.assertEqual(rights_seed(config, con), 8_000_000)
        early = {"capital": 6_980_078, "capital_since": "20261001"}
        self.assertNotEqual(rights_seed(early, con), 8_000_000)

    def test_seed_no_table_returns_config(self):
        con = self._wl()
        self.assertEqual(rights_seed({"capital": 5_000_000}, con), 5_000_000)

    def test_check_matches_engine(self):
        con = self._wl()
        con.execute("CREATE TABLE rights_dip_positions(acptno TEXT, ticker TEXT,"
                    " close_date TEXT, pnl REAL, pnl_source TEXT)")
        con.execute("INSERT INTO rights_dip_positions VALUES (?,?,?,?,?)",
                    ("A1", "000001", D2, 9900.0, "realized"))
        con.execute("INSERT INTO rights_dip_positions VALUES (?,?,?,?,?)",
                    ("A2", "000002", D2, 100.0, "estimated"))
        con.execute("INSERT INTO rights_dip_positions VALUES (?,?,?,?,?)",
                    ("A3", "000003", None, 50.0, "realized"))
        con.execute("CREATE TABLE rights_dip_orders(id INTEGER PRIMARY KEY, date TEXT,"
                    " acptno TEXT, extra INTEGER, leg TEXT,"
                    " filled_qty REAL, filled_price REAL)")
        con.executemany(
            "INSERT INTO rights_dip_orders(date, acptno, extra, leg, filled_qty, filled_price)"
            " VALUES (?,?,?,?,?,?)",
            [(D1, "A1", 0, "b1_1", 10, 1000.0),
             (D1, "A1", 0, "b2", 10, 2000.0),
             (D2, "A1", 0, "exit", 10, 1500.0),
             (D2, "A1", 0, "tp", 10, 2500.0)])
        rows = rights_check(con)
        self.assertEqual(len(rows), 1)
        buy_cost = (10 * 1000 + 10 * 2000) * 1.00015
        proceeds = (10 * 1500 + 10 * 2500) * (1 - 0.00215)
        want = proceeds - buy_cost / 20 * 20
        self.assertAlmostEqual(rows[0]["ours"], want)
        self.assertEqual(rows[0]["date"], D2)
        self.assertEqual(rows[0]["ticker"], "000001")
        self.assertEqual(rows[0]["kiwoom"], 9900.0)
        self.assertAlmostEqual(rows[0]["diff"], want - 9900.0)


class MainAdapterTest(unittest.TestCase):
    def test_adapters_registry(self):
        self.assertEqual(set(ADAPTERS), {"rebound", "rights_dip"})
        for adapter in ADAPTERS.values():
            self.assertEqual(set(adapter), {"fills", "events", "seed", "check"})

    def test_main_runs_both_adapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            wl_path = tmpdir / "watchlist.sqlite3"
            perf_path = tmpdir / "performance.sqlite3"
            con = sqlite3.connect(str(wl_path))
            try:
                con.execute("CREATE TABLE rebound_positions(date TEXT, ticker TEXT,"
                            " fq1 REAL, fp1 REAL, fq2 REAL, fp2 REAL, fq3 REAL, fp3 REAL,"
                            " sell_qty REAL, sell_px REAL, sell_status TEXT, reason TEXT,"
                            " realized REAL)")
                con.execute("INSERT INTO rebound_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (D1, "000001", 10, 1000, 0, None, 0, None,
                             10, 1100, "filled", None, 9000.0))
                con.execute("CREATE TABLE rights_dip_positions(acptno TEXT, ticker TEXT,"
                            " close_date TEXT, pnl REAL, pnl_source TEXT)")
                con.execute("INSERT INTO rights_dip_positions VALUES (?,?,?,?,?)",
                            ("A1", "000002", D1, 100.0, "realized"))
                con.execute("CREATE TABLE rights_dip_orders(id INTEGER PRIMARY KEY, date TEXT,"
                            " acptno TEXT, extra INTEGER, leg TEXT,"
                            " filled_qty REAL, filled_price REAL)")
                con.execute("INSERT INTO rights_dip_orders"
                            "(date, acptno, extra, leg, filled_qty, filled_price)"
                            " VALUES (?,?,?,?,?,?)", (D1, "A1", 0, "b1_1", 10, 1000.0))
                con.execute("INSERT INTO rights_dip_orders"
                            "(date, acptno, extra, leg, filled_qty, filled_price)"
                            " VALUES (?,?,?,?,?,?)", (D1, "A1", 0, "exit", 10, 1100.0))
                con.commit()
            finally:
                con.close()
            (tmpdir / "rebound.json").write_text(
                json.dumps({"capital": 5_000_000, "capital_since": D1}), encoding="utf-8")
            (tmpdir / "rights.json").write_text(
                json.dumps({"capital": 8_000_000, "capital_since": D1}), encoding="utf-8")
            strategies = {"rebound": ("http://x", "rebound"),
                          "rights_dip": ("http://x", "rights")}
            closes = {(D1, "000001"): 1100.0, (D2, "000001"): 1100.0,
                      (D1, "000002"): 1100.0, (D2, "000002"): 1100.0}
            with mock.patch.object(snap, "WL_DB", wl_path), \
                    mock.patch.object(snap, "PERF_DB", perf_path), \
                    mock.patch.object(snap, "STRATEGIES", strategies), \
                    mock.patch.object(snap, "_resolve_config",
                                       side_effect=lambda rel: tmpdir / f"{rel}.json"), \
                    mock.patch.object(snap, "load_days", return_value=[D1, D2]), \
                    mock.patch.object(snap, "load_closes", return_value=closes):
                self.assertEqual(main(["--end", D2]), 0)
                self.assertEqual(
                    main(["--strategy", "rights_dip", "--end", D2, "--check"]), 0)
            con = sqlite3.connect(str(perf_path))
            try:
                got = con.execute("SELECT DISTINCT strategy FROM strategy_daily").fetchall()
            finally:
                con.close()
            self.assertEqual({row[0] for row in got}, {"rebound", "rights_dip"})

    def test_dry_run_matches_normal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            wl_path = tmpdir / "watchlist.sqlite3"
            sqlite3.connect(str(wl_path)).close()
            (tmpdir / "fake.json").write_text(
                json.dumps({"capital": 5_000_000, "capital_since": D1}), encoding="utf-8")
            fills = [{"date": D1, "ticker": "000001", "side": "buy", "qty": 10,
                      "price": 1000.0, "ref": "fake"}]
            fake = {"fills": lambda wl, days: list(fills),
                    "events": lambda wl, first: [],
                    "seed": None, "check": None}
            closes = {(D1, "000001"): 1000.0, (D2, "000001"): 1000.0}
            now = datetime(2026, 11, 19, 15, 30)
            strategies = {"fake": ("http://x", "fake")}
            orig_compute = snap.compute
            got_normal, got_dry = {}, {}

            def cap_normal(*a, **k):
                rows = orig_compute(*a, **k)
                got_normal["rows"] = rows
                return rows

            def cap_dry(*a, **k):
                rows = orig_compute(*a, **k)
                got_dry["rows"] = rows
                return rows

            with mock.patch.dict(snap.ADAPTERS, {"fake": fake}), \
                    mock.patch.object(snap, "WL_DB", wl_path), \
                    mock.patch.object(snap, "PERF_DB", tmpdir / "normal.sqlite3"), \
                    mock.patch.object(snap, "STRATEGIES", strategies), \
                    mock.patch.object(snap, "_resolve_config",
                                       side_effect=lambda rel: tmpdir / f"{rel}.json"), \
                    mock.patch.object(snap, "load_days", return_value=[D1, D2]), \
                    mock.patch.object(snap, "load_closes", return_value=closes), \
                    mock.patch.object(snap, "now_seoul", return_value=now), \
                    mock.patch.object(snap, "compute", side_effect=cap_normal):
                self.assertEqual(main(["--strategy", "fake", "--end", D2]), 0)
            with mock.patch.dict(snap.ADAPTERS, {"fake": fake}), \
                    mock.patch.object(snap, "WL_DB", wl_path), \
                    mock.patch.object(snap, "PERF_DB", tmpdir / "dry.sqlite3"), \
                    mock.patch.object(snap, "STRATEGIES", strategies), \
                    mock.patch.object(snap, "_resolve_config",
                                       side_effect=lambda rel: tmpdir / f"{rel}.json"), \
                    mock.patch.object(snap, "load_days", return_value=[D1, D2]), \
                    mock.patch.object(snap, "load_closes", return_value=closes), \
                    mock.patch.object(snap, "now_seoul", return_value=now), \
                    mock.patch.object(snap, "compute", side_effect=cap_dry):
                self.assertEqual(main(["--strategy", "fake", "--end", D2, "--dry-run"]), 0)
            self.assertEqual(got_normal["rows"], got_dry["rows"])
            self.assertEqual(got_dry["rows"][0]["seed"], 5_000_000)

    def test_config_observation_uses_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            wl_path = tmpdir / "watchlist.sqlite3"
            sqlite3.connect(str(wl_path)).close()
            perf_path = tmpdir / "performance.sqlite3"
            (tmpdir / "fake.json").write_text(
                json.dumps({"capital": 5_000_000, "capital_since": D1}), encoding="utf-8")
            fills = [{"date": D1, "ticker": "000001", "side": "buy", "qty": 10,
                      "price": 1000.0, "ref": "fake"}]
            fake = {"fills": lambda wl, days: list(fills),
                    "events": lambda wl, first: [],
                    "seed": None, "check": None}
            closes = {(D1, "000001"): 1000.0, (D2, "000001"): 1000.0}
            now = datetime(2026, 11, 21, 9, 0)  # D4 관측, end=D2 과거
            strategies = {"fake": ("http://x", "fake")}
            with mock.patch.dict(snap.ADAPTERS, {"fake": fake}), \
                    mock.patch.object(snap, "WL_DB", wl_path), \
                    mock.patch.object(snap, "PERF_DB", perf_path), \
                    mock.patch.object(snap, "STRATEGIES", strategies), \
                    mock.patch.object(snap, "_resolve_config",
                                       side_effect=lambda rel: tmpdir / f"{rel}.json"), \
                    mock.patch.object(snap, "load_days", return_value=[D1, D2]), \
                    mock.patch.object(snap, "load_closes", return_value=closes), \
                    mock.patch.object(snap, "now_seoul", return_value=now):
                self.assertEqual(main(["--strategy", "fake", "--end", D2]), 0)
            con = sqlite3.connect(str(perf_path))
            try:
                got = con.execute(
                    "SELECT date FROM strategy_seed WHERE strategy='fake'").fetchall()
            finally:
                con.close()
            self.assertEqual([r[0] for r in got], [D4])

    def test_rerun_without_capital_since_keeps_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            wl_path = tmpdir / "watchlist.sqlite3"
            sqlite3.connect(str(wl_path)).close()
            perf_path = tmpdir / "performance.sqlite3"
            (tmpdir / "fake.json").write_text(
                json.dumps({"capital": 5_000_000}), encoding="utf-8")
            fills = [
                {"date": D1, "ticker": "000001", "side": "buy", "qty": 100,
                 "price": 1000.0, "ref": "fake"},
                {"date": D1, "ticker": "000001", "side": "sell", "qty": 100,
                 "price": 1100.0, "ref": "fake"},
            ]
            fake = {"fills": lambda wl, days: list(fills),
                    "events": lambda wl, first: [],
                    "seed": None, "check": None}
            closes = {(D1, "000001"): 1100.0, (D2, "000001"): 1100.0}
            strategies = {"fake": ("http://x", "fake")}

            def run(obs_now):
                with mock.patch.dict(snap.ADAPTERS, {"fake": fake}), \
                        mock.patch.object(snap, "WL_DB", wl_path), \
                        mock.patch.object(snap, "PERF_DB", perf_path), \
                        mock.patch.object(snap, "STRATEGIES", strategies), \
                        mock.patch.object(snap, "_resolve_config",
                                           side_effect=lambda rel: tmpdir / f"{rel}.json"), \
                        mock.patch.object(snap, "load_days", return_value=[D1, D2]), \
                        mock.patch.object(snap, "load_closes", return_value=closes), \
                        mock.patch.object(snap, "now_seoul", return_value=obs_now):
                    self.assertEqual(main(["--strategy", "fake", "--end", D2]), 0)

            run(datetime(2026, 11, 18, 15, 30))  # obs_date=D1
            run(datetime(2026, 11, 19, 15, 30))  # obs_date=D2
            con = sqlite3.connect(str(perf_path))
            try:
                got = con.execute(
                    "SELECT date, realized_cum, flow FROM strategy_daily"
                    " WHERE strategy='fake' ORDER BY date").fetchall()
            finally:
                con.close()
            self.assertEqual([r[0] for r in got], [D1, D2])
            self.assertGreater(abs(got[0][1]), 0.5)
            self.assertAlmostEqual(got[1][2], 0.0)


class TwrMddTest(unittest.TestCase):
    def test_flow_only_halving_is_zero(self):
        rows = [
            {"equity": 100.0, "flow": 100.0},
            {"equity": 50.0, "flow": -50.0},
        ]
        self.assertEqual(snap.twr_mdd(rows), 0)

    def test_ten_percent_drop_and_recovery(self):
        rows = [
            {"equity": 100.0, "flow": 100.0},
            {"equity": 90.0, "flow": 0.0},
            {"equity": 100.0, "flow": 0.0},
        ]
        self.assertAlmostEqual(snap.twr_mdd(rows), -0.10)


if __name__ == "__main__":
    unittest.main()
