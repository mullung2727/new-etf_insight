"""IS 드라이버 TDD — 일간 루프 + 벤치마크 + 주간 클러스터링.

실행: repo root에서
`etl\\.venv\\Scripts\\python.exe -m unittest research.cluster_closebet.tests.test_backtest -v`
"""
import unittest
from unittest import mock

import duckdb

import research.cluster_closebet.backtest as bt
from research.cluster_closebet.backtest import price_nets, run_backtest

TICKERS = ["000010", "000020", "000030", "000040"]
DAYS = [f"202401{d:02d}" for d in
        (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19)]

BASE = {"000010": 100, "000020": 200, "000030": 500, "000040": 300}
GROW = {"000010": 1.01, "000020": 1.01, "000030": 0.999, "000040": 1.0005}
TURNOVER = {"000010": 10**9, "000020": 9 * 10**8, "000030": 10**7, "000040": 9 * 10**6}


def _seed():
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE ohlcv (date VARCHAR, ticker VARCHAR, market VARCHAR,"
        " open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,"
        " market_cap BIGINT, list_shrs BIGINT, trading_value BIGINT)")
    con.execute(
        "CREATE TABLE stock_names (code VARCHAR PRIMARY KEY,"
        " name VARCHAR, updated_at VARCHAR)")
    con.executemany(
        "INSERT INTO stock_names VALUES (?,?,?)",
        [(t, f"종목{t}", "2024-01-01") for t in TICKERS])
    for i, day in enumerate(DAYS):
        for t in TICKERS:
            close = int(BASE[t] * GROW[t] ** i)
            con.execute(
                "INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [day, t, "KOSPI", close, close, close, close, 1000,
                 close * 10**6, 10**6, TURNOVER[t]])
    return con


class TestBacktest(unittest.TestCase):
    def test_runs_and_trades(self):
        con = _seed()
        out = run_backtest(con, "20240101", "20240119", [2], [2], weeks=2)
        self.assertIn((2, 2), out)
        self.assertGreater(
            out[(2, 2)]["base"]["open1"]["trades"]["strategy"]["n"], 0)
        self.assertGreater(
            out[(2, 2)]["base"]["open1"]["trades"]["bench_a"]["n"], 0)
        self.assertIn("daily", out[(2, 2)]["base"]["open1"])
        self.assertIn("bench_a_same_days", out[(2, 2)]["base"]["open1"])

    def test_deterministic(self):
        first = run_backtest(_seed(), "20240101", "20240119", [2], [2], weeks=2)
        second = run_backtest(_seed(), "20240101", "20240119", [2], [2], weeks=2)
        self.assertEqual(first, second)


class TestHaltGap(unittest.TestCase):
    def test_exit_needs_next_ms(self):
        idx = {
            ("A", "20240101"): {"ticker": "A", "date": "20240101", "ms": 1,
                                "close": 100, "open_price": 100},
            ("A", "20240103"): {"ticker": "A", "date": "20240103", "ms": 3,
                                "close": 101, "open_price": 101},
        }
        by_t = {"A": ["20240101", "20240103"]}
        nets, excluded, _ = price_nets(["A"], idx, by_t, "20240101")
        self.assertEqual(nets, [])
        self.assertEqual(excluded, 1)

    def test_prev_close_needs_prev_ms(self):
        # 직전봉이 ms-1이 아니면 상한가 체크용 prev_close=None → 진입 허용
        idx = {
            ("B", "20240101"): {"ticker": "B", "date": "20240101", "ms": 1,
                                "close": 100, "open_price": 100},
            ("B", "20240103"): {"ticker": "B", "date": "20240103", "ms": 3,
                                "close": 130, "open_price": 130},
            ("B", "20240104"): {"ticker": "B", "date": "20240104", "ms": 4,
                                "close": 131, "open_price": 131},
        }
        by_t = {"B": ["20240101", "20240103", "20240104"]}
        nets, excluded, _ = price_nets(["B"], idx, by_t, "20240103")
        self.assertEqual(excluded, 0)
        self.assertEqual(len(nets), 1)


class TestWarmup(unittest.TestCase):
    def test_no_trades_before_trade_start(self):
        calls: list[str] = []
        orig = bt.run_day

        def spy(day_rows, idx, by_t, date, assignment, n_min, seed=42,
                exit="open1", predicate=None, gate_pass=True):
            calls.append(date)
            return orig(day_rows, idx, by_t, date, assignment, n_min, seed,
                        exit, predicate=predicate, gate_pass=gate_pass)

        with mock.patch.object(bt, "run_day", side_effect=spy):
            run_backtest(_seed(), "20240110", "20240119", [2], [2],
                         weeks=2, warmup_start="20240101")
        self.assertTrue(calls)
        self.assertGreaterEqual(min(calls), "20240110")


class TestDailyWeighting(unittest.TestCase):
    def test_two_picks_count_as_one_day(self):
        def fake_run_day(day_rows, idx, by_t, date, assignment, n_min,
                         seed=42, exit="open1", predicate=None,
                         gate_pass=True):
            return {
                "strategy": {"picks": ["A", "B"], "nets": [0.01, 0.03],
                             "excluded": 0, "reasons": {"open": 2}},
                "bench_a": {"picks": ["C"], "nets": [0.02], "excluded": 0,
                            "reasons": {"open": 1}},
                "bench_b": {"picks": [], "nets": [], "excluded": 0,
                            "reasons": {}},
            }

        with mock.patch.object(bt, "run_day", side_effect=fake_run_day):
            out = run_backtest(_seed(), "20240119", "20240119", [2], [2],
                               weeks=2, warmup_start="20240101")
        got = out[(2, 2)]["base"]["open1"]
        self.assertEqual(got["trades"]["strategy"]["n"], 2)
        self.assertEqual(got["daily"]["strategy"]["n"], 1)
        self.assertAlmostEqual(got["daily"]["strategy"]["mean"], 0.02)
        self.assertEqual(got["bench_a_same_days"]["n"], 1)


class TestH2Halt(unittest.TestCase):
    def _row(self, t, date, ms, close, o=None, h=None, lo=None):
        o = close if o is None else o
        h = close if h is None else h
        lo = close if lo is None else lo
        return {"ticker": t, "date": date, "ms": ms, "close": close,
                "open_price": o, "high_price": h, "low_price": lo}

    def test_h2_missing_d2_without_d1_exit_excluded(self):
        idx = {
            ("A", "20240101"): self._row("A", "20240101", 1, 100),
            ("A", "20240102"): self._row("A", "20240102", 2, 100.5,
                                         o=100, h=101, lo=99.5),
            ("A", "20240104"): self._row("A", "20240104", 4, 101),
        }
        by_t = {"A": ["20240101", "20240102", "20240104"]}
        nets, excluded, _ = price_nets(["A"], idx, by_t, "20240101",
                                       exit="h2")
        self.assertEqual(nets, [])
        self.assertEqual(excluded, 1)

    def test_h2_d1_exit_included_without_d2(self):
        idx = {
            ("A", "20240101"): self._row("A", "20240101", 1, 100),
            ("A", "20240102"): self._row("A", "20240102", 2, 103,
                                         o=100, h=104, lo=99.5),
        }
        by_t = {"A": ["20240101", "20240102"]}
        nets, excluded, reasons = price_nets(["A"], idx, by_t, "20240101",
                                             exit="h2")
        self.assertEqual(len(nets), 1)
        self.assertEqual(excluded, 0)
        self.assertEqual(reasons, {"tp": 1})


class TestVariants(unittest.TestCase):
    def test_base_and_cp3_retention(self):
        con = _seed()
        out = run_backtest(con, "20240101", "20240119", [2], [2], weeks=2,
                           variants=["base", "CP3"])
        self.assertIn("base", out[(2, 2)])
        self.assertIn("CP3", out[(2, 2)])
        base = out[(2, 2)]["base"]["open1"]
        cp3 = out[(2, 2)]["CP3"]["open1"]
        self.assertEqual(base["retention"], 1.0)
        self.assertLessEqual(cp3["retention"], 1.0)
        self.assertLessEqual(
            cp3["trades"]["strategy"]["n"], base["trades"]["strategy"]["n"])

    def test_base_computed_when_not_requested(self):
        con = _seed()
        out = run_backtest(con, "20240101", "20240119", [2], [2], weeks=2,
                           variants=["CP3"])
        self.assertIn("base", out[(2, 2)])
        self.assertEqual(out[(2, 2)]["base"]["open1"]["retention"], 1.0)

    def test_unknown_variant_rejected(self):
        with self.assertRaises(ValueError):
            run_backtest(_seed(), "20240101", "20240119", [2], [2], weeks=2,
                         variants=["NOPE"])

    def test_cs_variants_registered(self):
        for v in ("CSR50", "CSR60", "CSR70", "CSN05", "CSN10"):
            self.assertIn(v, bt.VARIANT_NAMES)
        self.assertIsNone(bt.make_variant_predicate("CSR50", "20240110", None))
        self.assertIsNone(bt.make_variant_predicate("CSN05", "20240110", None))


def _gate_fixture():
    # breadth 2/5 = 40%: A,B 상승 / C,D,E 하락, 전봉(ms 9) 연속.
    day = "20240110"
    specs = [("A", 11, 100), ("B", 11, 90), ("C", 9, 80),
             ("D", 9, 70), ("E", 8, 60)]
    day_rows = [{"ticker": t, "date": day, "ms": 10, "close": c,
                 "open_price": c, "high_price": c, "low_price": c,
                 "turnover": to} for t, c, to in specs]
    assignment = {t: 0 for t, _, _ in specs}
    prev = {t: 10 for t, _, _ in specs}
    lookup = lambda t, ms: prev.get(t) if ms == 10 else None  # noqa: E731
    idx = {(t, day): {"ticker": t, "date": day, "ms": 10,
                      "close": c, "open_price": c}
           for t, c, _ in specs}
    by_t = {t: [day] for t, _, _ in specs}
    return day, day_rows, assignment, lookup, idx, by_t


class TestClusterBreadth(unittest.TestCase):
    def test_counts_and_strictly_greater(self):
        day, day_rows, assignment, lookup, _, _ = _gate_fixture()
        rows = {r["ticker"]: r for r in day_rows}
        self.assertEqual(bt.cluster_breadth(assignment, 0, rows, lookup),
                         (2, 5))
        # 동가(close == prev)는 up 아님.
        rows["E"]["close"] = 10
        self.assertEqual(bt.cluster_breadth(assignment, 0, rows, lookup),
                         (2, 5))

    def test_nonconsecutive_prev_excluded(self):
        day, day_rows, assignment, _, _, _ = _gate_fixture()
        rows = {r["ticker"]: r for r in day_rows}
        lookup = lambda t, ms: None if t == "A" else 10  # noqa: E731
        # A(상승)가 직전봉 없이 제외 → up 1, members 4.
        self.assertEqual(bt.cluster_breadth(assignment, 0, rows, lookup),
                         (1, 4))

    def test_no_members(self):
        _, day_rows, _, lookup, _, _ = _gate_fixture()
        rows = {r["ticker"]: r for r in day_rows}
        self.assertEqual(bt.cluster_breadth({}, 0, rows, lookup), (0, 0))
        self.assertEqual(bt.cluster_breadth({t: 99 for t in rows}, 0,
                                            rows, lookup), (0, 0))
        self.assertFalse(bt.passes_breadth("CSR50", 0, 0))
        self.assertFalse(bt.passes_breadth("CSN05", 0, 0))


class TestBreadthGate(unittest.TestCase):
    def test_csr50_no_trade_base_trades(self):
        day, day_rows, assignment, lookup, idx, by_t = _gate_fixture()
        rows = {r["ticker"]: r for r in day_rows}
        up, members = bt.cluster_breadth(assignment, 0, rows, lookup)
        self.assertAlmostEqual(up / members, 0.4)
        base = bt.run_day(day_rows, idx, by_t, day, assignment, 2)
        self.assertTrue(base["strategy"]["picks"])
        gated = bt.run_day(day_rows, idx, by_t, day, assignment, 2,
                           gate_pass=bt.passes_breadth("CSR50", up, members))
        self.assertEqual(gated["strategy"]["picks"], [])
        # 벤치는 게이트 영향 없음.
        self.assertEqual(gated["bench_a"]["picks"],
                         base["bench_a"]["picks"])
        self.assertEqual(gated["bench_b"]["picks"],
                         base["bench_b"]["picks"])

    def test_csn05_no_trade_with_two_up(self):
        day, day_rows, assignment, lookup, idx, by_t = _gate_fixture()
        rows = {r["ticker"]: r for r in day_rows}
        up, members = bt.cluster_breadth(assignment, 0, rows, lookup)
        self.assertEqual(up, 2)
        self.assertFalse(bt.passes_breadth("CSN05", up, members))
        gated = bt.run_day(day_rows, idx, by_t, day, assignment, 2,
                           gate_pass=bt.passes_breadth("CSN05", up, members))
        self.assertEqual(gated["strategy"]["picks"], [])


class TestBreadthBacktest(unittest.TestCase):
    def test_csr50_retention_and_same_days(self):
        out = run_backtest(_seed(), "20240101", "20240119", [2], [2],
                           weeks=2, variants=["base", "CSR50"])
        base = out[(2, 2)]["base"]["open1"]
        csr = out[(2, 2)]["CSR50"]["open1"]
        self.assertLessEqual(csr["retention"], 1.0)
        self.assertLessEqual(csr["trades"]["strategy"]["n"],
                             base["trades"]["strategy"]["n"])
        self.assertLessEqual(csr["bench_a_same_days"]["n"],
                             base["bench_a_same_days"]["n"])


class TestClusterStats(unittest.TestCase):
    def _stats_fixture(self):
        rows = {
            "A": {"ticker": "A", "ms": 10, "close": 115},
            "B": {"ticker": "B", "ms": 10, "close": 150},
            "C": {"ticker": "C", "ms": 10, "close": 101},
            "D": {"ticker": "D", "ms": 10, "close": 90},
            "E": {"ticker": "E", "ms": 10, "close": 50},
            # F: 오늘 행은 있으나 직전봉 없음 → 제외.
            "F": {"ticker": "F", "ms": 10, "close": 200},
            # G: 다른 클러스터 → 제외.
            "G": {"ticker": "G", "ms": 10, "close": 200},
        }
        assignment = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0,
                      "F": 0, "G": 1}
        prev = {"A": 100, "B": 100, "C": 100, "D": 100,
                "E": 100, "G": 100}
        lookup = lambda t, ms: prev.get(t) if ms == 10 else None  # noqa: E731
        return assignment, rows, lookup

    def test_members_up_strong_mean(self):
        assignment, rows, lookup = self._stats_fixture()
        stats = bt.cluster_stats(assignment, 0, rows, lookup)
        # A +15%, B +50%→+30% 클리핑, C +1%, D -10%, E -50%→-30% 클리핑.
        self.assertEqual(stats["members"], 5)
        self.assertEqual(stats["up"], 3)
        self.assertEqual(stats["strong_up"], 2)
        self.assertAlmostEqual(stats["mean_ret"], 0.012, places=9)

    def test_clipping_applied(self):
        assignment, rows, lookup = self._stats_fixture()
        stats = bt.cluster_stats(assignment, 0, rows, lookup)
        # B(+50%)가 +30%로 잘리므로 평균 0.012. 안 잘리면 0.052가 됨.
        self.assertLess(stats["mean_ret"], 0.05)
        self.assertAlmostEqual(stats["mean_ret"], 0.012, places=9)

    def test_nonconsecutive_prev_excluded(self):
        assignment, rows, lookup = self._stats_fixture()
        stats = bt.cluster_stats(assignment, 0, rows, lookup)
        # F(직전봉 없음), G(다른 클러스터) 제외 → members 5 유지.
        self.assertEqual(stats["members"], 5)
        # cluster_breadth도 같은 멤버 규칙 유지.
        self.assertEqual(bt.cluster_breadth(assignment, 0, rows, lookup),
                         (3, 5))

    def test_no_members(self):
        _, rows, lookup = self._stats_fixture()
        stats = bt.cluster_stats({}, 0, rows, lookup)
        self.assertEqual(stats, {"up": 0, "members": 0,
                                 "mean_ret": 0.0, "strong_up": 0})


class TestNewClusterGates(unittest.TestCase):
    def test_registered(self):
        for v in ("CSR75", "CSR80", "CSM1", "CSM2", "CSM3",
                  "CSS30", "CSS50"):
            self.assertIn(v, bt.VARIANT_NAMES)
            self.assertIn(v, bt.CS_VARIANTS)

    def test_csr75(self):
        self.assertTrue(bt.passes_breadth("CSR75", 3, 4))
        self.assertFalse(bt.passes_breadth("CSR75", 2, 4))

    def test_csr80(self):
        self.assertTrue(bt.passes_breadth("CSR80", 4, 5))
        self.assertFalse(bt.passes_breadth("CSR80", 3, 5))

    def test_csm1(self):
        self.assertTrue(bt.passes_breadth("CSM1", 1, 5, mean_ret=0.012))
        self.assertFalse(bt.passes_breadth("CSM1", 1, 5, mean_ret=0.005))

    def test_csm2(self):
        self.assertTrue(bt.passes_breadth("CSM2", 1, 5, mean_ret=0.025))
        self.assertFalse(bt.passes_breadth("CSM2", 1, 5, mean_ret=0.012))

    def test_csm3(self):
        self.assertTrue(bt.passes_breadth("CSM3", 1, 5, mean_ret=0.03))
        self.assertFalse(bt.passes_breadth("CSM3", 1, 5, mean_ret=0.029))

    def test_css30(self):
        self.assertTrue(bt.passes_breadth("CSS30", 2, 5, strong_up=2))
        self.assertFalse(bt.passes_breadth("CSS30", 2, 5, strong_up=1))

    def test_css50(self):
        self.assertTrue(bt.passes_breadth("CSS50", 3, 5, strong_up=3))
        self.assertFalse(bt.passes_breadth("CSS50", 3, 5, strong_up=2))

    def test_no_members_all_fail(self):
        for v in ("CSR75", "CSR80", "CSM1", "CSM2", "CSM3",
                  "CSS30", "CSS50"):
            self.assertFalse(bt.passes_breadth(v, 0, 0))


class TestCSM2Backtest(unittest.TestCase):
    def test_csm2_retention(self):
        out = run_backtest(_seed(), "20240101", "20240119", [2], [2],
                           weeks=2, variants=["base", "CSM2"])
        base = out[(2, 2)]["base"]["open1"]
        csm2 = out[(2, 2)]["CSM2"]["open1"]
        self.assertLessEqual(csm2["retention"], 1.0)


class TestVolumeProfileVariants(unittest.TestCase):
    def test_registered(self):
        for v in ("VP05", "VP10", "VP20", "VPB"):
            self.assertIn(v, bt.VARIANT_NAMES)

    def test_overhead_thresholds(self):
        row = {"close": 120}
        self.assertTrue(bt.passes_variant("VP10", row, {"vp_overhead": 0.05}))
        self.assertFalse(bt.passes_variant("VP10", row, {"vp_overhead": 0.15}))
        self.assertTrue(bt.passes_variant("VP05", row, {"vp_overhead": 0.05}))
        self.assertFalse(bt.passes_variant("VP05", row, {"vp_overhead": 0.06}))
        self.assertFalse(bt.passes_variant("VP10", row, {"vp_overhead": None}))

    def test_vpb_fresh_breakout_only(self):
        self.assertFalse(bt.passes_variant(
            "VPB", {"close": 103},
            {"vp_poc_high": 101, "vp_prev_close": 102}))
        self.assertTrue(bt.passes_variant(
            "VPB", {"close": 102},
            {"vp_poc_high": 101, "vp_prev_close": 100}))
        self.assertFalse(bt.passes_variant(
            "VPB", {"close": 100.5},
            {"vp_poc_high": 101, "vp_prev_close": 100}))
        self.assertFalse(bt.passes_variant(
            "VPB", {"close": 102},
            {"vp_poc_high": 101, "vp_prev_close": None}))
        self.assertFalse(bt.passes_variant(
            "VPB", {"close": 102},
            {"vp_poc_high": None, "vp_prev_close": 100}))

    def test_run_backtest_vp10_retention(self):
        out = run_backtest(_seed(), "20240101", "20240119", [2], [2],
                           weeks=2, variants=["base", "VP10"])
        self.assertIn("VP10", out[(2, 2)])
        base = out[(2, 2)]["base"]["open1"]
        vp10 = out[(2, 2)]["VP10"]["open1"]
        self.assertLessEqual(vp10["retention"], 1.0)
        self.assertLessEqual(vp10["trades"]["strategy"]["n"],
                             base["trades"]["strategy"]["n"])


class TestDumpDaily(unittest.TestCase):
    def test_dump_matches_daily(self):
        con = _seed()
        dump: dict = {}
        out = run_backtest(con, "20240101", "20240119", [2], [2], weeks=2,
                           dump_daily=dump)
        base = out[(2, 2)]["base"]["open1"]
        dumped = dump["K2_N2"]["base"]["open1"]
        daily = base["daily"]["strategy"]
        self.assertEqual(len(dumped), daily["n"])
        vals = [v for _, v in dumped]
        if vals:
            self.assertAlmostEqual(sum(vals) / len(vals), daily["mean"])
        dates = [d for d, _ in dumped]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), len(set(dates)))


if __name__ == "__main__":
    unittest.main()
