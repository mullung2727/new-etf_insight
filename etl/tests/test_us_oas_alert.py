import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd

from scripts.us_oas_alert import (
    compute,
    decide,
    ensure_alert_table,
    format_lines,
    format_regime_alert,
    format_risk_alert,
    main,
    mark_reported,
    proxy,
    qqq_ma_gap,
    record,
    regime_events,
    regime_frame,
)


def _alert_row(**kw):
    r = {
        "date": "20240103",
        "base_date": "20240102",
        "base": 3.00,
        "est": 3.21,
        "chg_bp": 21.0,
        "thr_bp": 25.0,
        "rank_pct": 0.99,
        "alert_fixed": True,
        "alert_pct": False,
        "alert": True,
        "n_prior": 252,
        "miss_date": "20240102",
        "miss_bp": 1.5,
        "cum7_bp": 44.7,
    }
    r.update(kw)
    return r


class TestDecide(unittest.TestCase):
    def test_a1_fixed_threshold(self):
        prior = [0.0] * 200
        self.assertTrue(decide(prior, 20.0)["alert_fixed"])
        self.assertFalse(decide(prior, 19.9)["alert_fixed"])

    def test_a2_percentile_uses_prior_only(self):
        prior = [float(i) for i in range(200)]
        thr1 = decide(prior, 5.0)["thr_bp"]
        thr2 = decide(prior, 1e9)["thr_bp"]
        self.assertEqual(thr1, thr2)
        self.assertTrue(decide(prior, 1e9)["alert_pct"])
        self.assertFalse(decide(prior, 0.0)["alert_pct"])

    def test_a2_window_caps_at_252(self):
        self.assertEqual(decide([0.0] * 300, 0.0)["n_prior"], 252)

    def test_a3_small_sample_skips_percentile(self):
        r = decide([0.0] * 100, 25.0)
        self.assertIsNone(r["thr_bp"])
        self.assertIsNone(r["rank_pct"])
        self.assertFalse(r["alert_pct"])
        self.assertTrue(r["alert_fixed"])
        self.assertTrue(r["alert"])


class TestRecord(unittest.TestCase):
    def setUp(self):
        _send = patch("scripts.us_oas_alert.send_strategy_alert", return_value=True)
        self.addCleanup(_send.stop)
        _send.start()

    def test_a4_same_date_records_once(self):
        r = _alert_row()
        with tempfile.TemporaryDirectory() as tmp:
            con = duckdb.connect(os.path.join(tmp, "alert.duckdb"))
            try:
                ensure_alert_table(con)
                self.assertTrue(record(con, r))
                self.assertTrue(record(con, r))
                self.assertTrue(mark_reported(con, r["date"]))
                self.assertFalse(record(con, r))
                n = con.execute("SELECT count(*) FROM oas_alert_log").fetchone()[0]
                self.assertEqual(n, 1)
                val = con.execute("SELECT cum7_bp FROM oas_alert_log").fetchone()[0]
                self.assertAlmostEqual(val, r["cum7_bp"])
                self.assertFalse(mark_reported(con, "19000101"))
            finally:
                con.close()

    def test_a4_main_second_run_reports_already(self):
        r = _alert_row()
        px = pd.DataFrame({"HYG": [0.001], "IEI": [0.002]}, index=["20240102"])
        oas = pd.Series([3.0], index=["20240102"], name="BAMLH0A0HYM2")
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "alert.duckdb")
            with (
                patch("scripts.us_oas_alert.load_fred", return_value=oas),
                patch("scripts.us_oas_alert.load_etf_tr", return_value=px),
                patch("scripts.us_oas_alert.compute", return_value=r),
            ):
                buf1 = io.StringIO()
                with contextlib.redirect_stdout(buf1):
                    rc1 = main(["--db-path", db])
                bufm = io.StringIO()
                with contextlib.redirect_stdout(bufm):
                    rcm = main(["--db-path", db, "--mark-reported", r["date"]])
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    rc2 = main(["--db-path", db])
        self.assertEqual(rc1, 0)
        self.assertEqual(rcm, 0)
        self.assertEqual(rc2, 0)
        self.assertIn("OAS ALERT", buf1.getvalue())
        self.assertIn("already reported", buf2.getvalue())
        self.assertNotIn("ALERT", buf2.getvalue())

    def test_a4_main_rereports_without_mark(self):
        r = _alert_row()
        px = pd.DataFrame({"HYG": [0.001], "IEI": [0.002]}, index=["20240102"])
        oas = pd.Series([3.0], index=["20240102"], name="BAMLH0A0HYM2")
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "alert.duckdb")
            with (
                patch("scripts.us_oas_alert.load_fred", return_value=oas),
                patch("scripts.us_oas_alert.load_etf_tr", return_value=px),
                patch("scripts.us_oas_alert.compute", return_value=r),
            ):
                buf1 = io.StringIO()
                with contextlib.redirect_stdout(buf1):
                    rc1 = main(["--db-path", db])
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    rc2 = main(["--db-path", db])
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertIn("OAS ALERT", buf1.getvalue())
        self.assertIn("OAS ALERT", buf2.getvalue())


class TestFormat(unittest.TestCase):
    def test_a5_all_lines_start_with_oas(self):
        cases = [
            (_alert_row(), False),
            (
                _alert_row(
                    alert=False, alert_fixed=False, chg_bp=1.0,
                    miss_bp=None, miss_date=None,
                ),
                False,
            ),
            (_alert_row(), True),
        ]
        for r, already in cases:
            with self.subTest(already=already, alert=r["alert"]):
                lines = format_lines(r, already)
                self.assertTrue(lines)
                for line in lines:
                    self.assertTrue(line.startswith("OAS "), line)

    def test_a5_alert_line_only_when_alert(self):
        alert_lines = format_lines(_alert_row(), False)
        self.assertTrue(any(line.startswith("OAS ALERT") for line in alert_lines))
        quiet = format_lines(_alert_row(alert=False, alert_fixed=False), False)
        self.assertFalse(any(line.startswith("OAS ALERT") for line in quiet))

    def test_a5_miss_line_only_when_miss(self):
        no_miss = format_lines(_alert_row(miss_bp=None, miss_date=None), False)
        self.assertFalse(any("miss" in line for line in no_miss))
        with_miss = format_lines(_alert_row(), False)
        self.assertTrue(any("miss" in line for line in with_miss))

    def test_a7b_cum7_suffix_in_base_line(self):
        r = _alert_row(
            alert=False, alert_fixed=False, chg_bp=5.1,
            miss_bp=None, miss_date=None, cum7_bp=44.7,
        )
        lines = format_lines(r, False)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("OAS "))
        self.assertRegex(lines[0], r"^OAS (\d{8}) est ")
        self.assertTrue(lines[0].endswith(", 7d +44.7bp"), lines[0])

    def test_a7b_no_cum7_suffix_when_none(self):
        r = _alert_row(
            alert=False, alert_fixed=False, chg_bp=5.1,
            miss_bp=None, miss_date=None, cum7_bp=None,
        )
        lines = format_lines(r, False)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("OAS "))
        self.assertRegex(lines[0], r"^OAS (\d{8}) est ")
        self.assertNotIn("7d", lines[0])


class TestComputeSynthetic(unittest.TestCase):
    def test_a6_miss_and_window_equivalence(self):
        days = pd.bdate_range("2024-01-01", periods=400).strftime("%Y%m%d").tolist()
        logret = pd.DataFrame(
            np.random.default_rng(7).normal(0.0, 0.003, size=(400, 2)),
            index=days,
            columns=["HYG", "IEI"],
        )
        steps = (-180.0 * logret["HYG"] + 200.0 * logret["IEI"]) / 100.0
        noise = np.random.default_rng(11).normal(0.0, 0.01, size=400)
        level = 3.0 + steps.cumsum() + pd.Series(noise, index=days)
        oas = level.loc[days[1:-1]]  # all but first day; T excluded (one day late, like real)
        oas.name = "BAMLH0A0HYM2"

        r = compute(oas, logret)
        T, B = days[-1], days[-2]
        self.assertEqual(r["date"], T)
        self.assertEqual(r["base_date"], B)
        self.assertEqual(r["miss_date"], B)
        ref_miss = (
            float(oas.loc[B]) - float(proxy.nowcast(oas, logret, [B]).loc[B, "oas"])
        ) * 100
        self.assertAlmostEqual(r["miss_bp"], ref_miss, places=6)

        full_dates = [d for d in logret.index if d > oas.index[0] and d <= T]
        est_full = float(proxy.nowcast(oas, logret, full_dates).loc[T, "oas"])
        self.assertAlmostEqual(r["est"], est_full, places=9)

    def test_a7_cum7_matches_grind_formula(self):
        days = pd.bdate_range("2024-01-01", periods=400).strftime("%Y%m%d").tolist()
        logret = pd.DataFrame(
            np.random.default_rng(7).normal(0.0, 0.003, size=(400, 2)),
            index=days,
            columns=["HYG", "IEI"],
        )
        steps = (-180.0 * logret["HYG"] + 200.0 * logret["IEI"]) / 100.0
        noise = np.random.default_rng(11).normal(0.0, 0.01, size=400)
        level = 3.0 + steps.cumsum() + pd.Series(noise, index=days)
        oas = level.loc[days[1:-1]]  # all but first day; T excluded (one day late, like real)
        oas.name = "BAMLH0A0HYM2"

        r = compute(oas, logret)
        ref = (r["est"] - oas.iloc[list(oas.index).index(r["base_date"]) - 6]) * 100
        self.assertAlmostEqual(r["cum7_bp"], ref, places=9)


def _qqq_series(rets):
    idx = pd.date_range("2023-01-01", periods=len(rets)).strftime("%Y%m%d").tolist()
    return pd.Series(list(rets), index=idx)


class TestQqqMaGap(unittest.TestCase):
    def test_b1_steady_rise_positive(self):
        s = _qqq_series([0.001] * 210)
        gap = qqq_ma_gap(s, s.index[-1])
        self.assertIsNotNone(gap)
        self.assertGreater(gap, 0)

    def test_b2_recent_crash_negative(self):
        s = _qqq_series([0.001] * 180 + [-0.02] * 30)
        gap = qqq_ma_gap(s, s.index[-1])
        self.assertIsNotNone(gap)
        self.assertLess(gap, 0)

    def test_b3_short_history_none(self):
        s = _qqq_series([0.001] * 150)
        self.assertIsNone(qqq_ma_gap(s, s.index[-1]))

    def test_b4_asof_ignores_later_rows(self):
        s = _qqq_series([0.001] * 210)
        asof = s.index[-1]
        full = qqq_ma_gap(s, asof)
        later_idx = pd.date_range("2024-01-01", periods=10).strftime("%Y%m%d").tolist()
        s2 = pd.concat([s, pd.Series([0.5] * 10, index=later_idx)])
        self.assertAlmostEqual(qqq_ma_gap(s2, asof), full)
        self.assertIsNone(qqq_ma_gap(s2, s.index[149]))


class TestFormatRisk(unittest.TestCase):
    def test_c1_headline_and_numbers(self):
        msg = format_risk_alert(_alert_row(), 0.053)
        self.assertTrue(msg.splitlines()[0].startswith("🟡 [주의]"))
        self.assertIn("+0.21%p", msg)
        self.assertIn("상위", msg)
        self.assertIn("2024-01-03", msg)
        self.assertIn("2024-01-02", msg)
        self.assertIn("200일선 위", msg)

    def test_c2_qqq_below_warns(self):
        msg = format_risk_alert(_alert_row(), -0.05)
        self.assertIn("200일선 아래", msg)
        self.assertIn("⚠", msg)

    def test_c3_qqq_none(self):
        self.assertIn("확인 실패", format_risk_alert(_alert_row(), None))

    def test_c4_rank_none(self):
        msg = format_risk_alert(_alert_row(rank_pct=None), 0.01)
        self.assertIn("순위 계산 불가", msg)
        self.assertIn("+0.21%p", msg)

    def test_c5_cum7_none_omits_line(self):
        self.assertNotIn("1주 누적", format_risk_alert(_alert_row(cum7_bp=None), 0.01))


class TestRiskChannel(unittest.TestCase):
    def _main(self, r, db):
        px = pd.DataFrame({"HYG": [0.001], "IEI": [0.002]}, index=["20240102"])
        oas = pd.Series([3.0], index=["20240102"], name="BAMLH0A0HYM2")
        with (
            patch("scripts.us_oas_alert.load_fred", return_value=oas),
            patch("scripts.us_oas_alert.load_etf_tr", return_value=px),
            patch("scripts.us_oas_alert.compute", return_value=r),
            patch("scripts.us_oas_alert.send_strategy_alert", return_value=True) as send,
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["--db-path", db])
        return rc, buf.getvalue(), send

    def test_d1_alert_new_sends_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, send = self._main(_alert_row(), os.path.join(tmp, "alert.duckdb"))
        self.assertEqual(rc, 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args[0][1], "US_RISK_WEBHOOK_URL")
        self.assertIn("OAS risk channel sent=True", out)

    def test_d2_quiet_sends_nothing(self):
        r = _alert_row(alert=False, alert_fixed=False, chg_bp=1.0)
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, send = self._main(r, os.path.join(tmp, "alert.duckdb"))
        self.assertEqual(rc, 0)
        self.assertEqual(send.call_count, 0)
        self.assertNotIn("risk channel", out)

    def test_d3_already_reported_sends_nothing(self):
        r = _alert_row()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "alert.duckdb")
            rc1, _, send1 = self._main(r, db)
            bufm = io.StringIO()
            with contextlib.redirect_stdout(bufm):
                rcm = main(["--db-path", db, "--mark-reported", r["date"]])
            rc2, _, send2 = self._main(r, db)
        self.assertEqual((rc1, rcm, rc2), (0, 0, 0))
        self.assertEqual(send1.call_count, 1)
        self.assertEqual(send2.call_count, 0)

    def test_d4_send_risk_resends_row(self):
        r = _alert_row()
        px = pd.DataFrame({"HYG": [0.001], "IEI": [0.002]}, index=["20240102"])
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "alert.duckdb")
            con = duckdb.connect(db)
            try:
                ensure_alert_table(con)
                record(con, r)
            finally:
                con.close()
            with (
                patch("scripts.us_oas_alert.load_etf_tr", return_value=px),
                patch("scripts.us_oas_alert.send_strategy_alert", return_value=True) as send,
            ):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = main(["--db-path", db, "--send-risk", r["date"]])
            con = duckdb.connect(db)
            try:
                reported = con.execute(
                    "SELECT reported FROM oas_alert_log WHERE date = ?", [r["date"]]
                ).fetchone()[0]
            finally:
                con.close()
        self.assertEqual(rc, 0)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args[0][1], "US_RISK_WEBHOOK_URL")
        self.assertIn("OAS risk channel sent=True", buf.getvalue())
        self.assertFalse(reported)

    def test_d5_send_risk_missing_date_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "alert.duckdb")
            with patch("scripts.us_oas_alert.send_strategy_alert") as send:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = main(["--db-path", db, "--send-risk", "20240103"])
        self.assertEqual(rc, 1)
        self.assertIn("not found", buf.getvalue())
        self.assertEqual(send.call_count, 0)


def _regime_days(n=400):
    return pd.bdate_range("2023-01-02", periods=n).strftime("%Y%m%d").tolist()


class TestRegimeFrame(unittest.TestCase):
    def test_overheat_vix_true_and_flat_false(self):
        days = _regime_days()
        qqq = pd.Series([0.002] * 400, index=days)
        dgs = pd.Series([4.0] * 400, index=days)
        icnsa = pd.Series([200.0] * 400, index=days)
        vix_up = pd.Series([20.0] * 399 + [24.0], index=days)
        frame_up = regime_frame(qqq, vix_up, dgs, icnsa)
        last = frame_up.iloc[-1]
        self.assertGreaterEqual(float(last["gap"]), 0.14)
        self.assertGreater(float(last["q10"]), 0)
        self.assertGreaterEqual(float(last["v10"]), 0.15)
        self.assertTrue(bool(last["overheat_vix"]))
        vix_flat = pd.Series([20.0] * 400, index=days)
        frame_flat = regime_frame(qqq, vix_flat, dgs, icnsa)
        self.assertFalse(bool(frame_flat.iloc[-1]["overheat_vix"]))

    def test_rec_rate_and_one_day_shift(self):
        days = _regime_days()
        qqq = pd.Series([0.002] * 400, index=days)
        vix = pd.Series([20.0] * 400, index=days)
        icnsa = pd.Series([200.0] * 400, index=days)
        dgs_up = pd.Series([4.0] * 379 + [4.5] * 21, index=days)
        frame = regime_frame(qqq, vix, dgs_up, icnsa)
        last = frame.iloc[-1]
        self.assertGreaterEqual(float(last["gap"]), 0.14)
        self.assertGreaterEqual(float(last["y20"]), 40.0)
        self.assertTrue(bool(last["rec_rate"]))
        dgs_spike = pd.Series([4.0] * 399 + [5.0], index=days)
        frame2 = regime_frame(qqq, vix, dgs_spike, icnsa)
        self.assertAlmostEqual(float(frame2.iloc[-1]["y20"]), 0.0)
        self.assertFalse(bool(frame2.iloc[-1]["rec_rate"]))

    def test_rec_jobs_yoy_and_pub_lag(self):
        days = _regime_days()
        qqq = pd.Series([0.002] * 400, index=days)
        vix = pd.Series([20.0] * 400, index=days)
        dgs = pd.Series([4.0] * 400, index=days)
        daily_end = pd.to_datetime(days[-1], format="%Y%m%d")
        sats = pd.date_range("2022-01-01", daily_end, freq="W-SAT").strftime("%Y%m%d").tolist()
        n = len(sats)
        self.assertGreater(n, 60)
        icnsa = pd.Series([200.0] * (n - 1) + [360.0], index=sats)
        frame = regime_frame(qqq, vix, dgs, icnsa)
        last = frame.iloc[-1]
        self.assertGreaterEqual(float(last["gap"]), 0.14)
        self.assertAlmostEqual(float(last["icy"]), 0.20, places=6)
        self.assertTrue(bool(last["rec_jobs"]))
        obs_last = pd.to_datetime(sats[-1], format="%Y%m%d")
        mon = (obs_last + pd.Timedelta(days=2)).strftime("%Y%m%d")
        thu = (obs_last + pd.Timedelta(days=5)).strftime("%Y%m%d")
        fri = (obs_last + pd.Timedelta(days=6)).strftime("%Y%m%d")
        self.assertIn(mon, frame.index)
        self.assertAlmostEqual(float(frame.loc[mon, "icy"]), 0.0, places=6)
        self.assertIn(thu, frame.index)
        self.assertAlmostEqual(float(frame.loc[thu, "icy"]), 0.0, places=6)
        self.assertIn(fri, frame.index)
        self.assertAlmostEqual(float(frame.loc[fri, "icy"]), 0.20, places=6)


class TestRegimeEvents(unittest.TestCase):
    def _frame(self, rows):
        idx = pd.date_range("2024-01-02", periods=len(rows)).strftime("%Y%m%d").tolist()
        return pd.DataFrame(rows, index=idx)

    def _base(self, **kw):
        r = {
            "gap": 0.15, "q10": 0.01, "v10": 0.0, "y20": 0.0, "icy": 0.0,
            "overheat_vix": False, "rec_rate": False, "rec_jobs": False, "recovery": False,
        }
        r.update(kw)
        return r

    def test_overheat_edge(self):
        rows = [self._base() for _ in range(6)] + [self._base(overheat_vix=True, v10=0.20)]
        frame = self._frame(rows)
        asof = frame.index[-1]
        evs = regime_events(frame, asof)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["kind"], "overheat_vix")
        self.assertEqual(evs[0]["date"], asof)
        for k in ["gap", "q10", "v10", "y20", "icy", "rec_rate", "rec_jobs"]:
            self.assertIn(k, evs[0]["row"])

    def test_recovery_on(self):
        rows = [self._base() for _ in range(6)] + [self._base(recovery=True, rec_rate=True, y20=50.0)]
        frame = self._frame(rows)
        asof = frame.index[-1]
        evs = regime_events(frame, asof)
        self.assertEqual([e["kind"] for e in evs], ["recovery_on"])
        self.assertEqual(evs[0]["date"], asof)

    def test_recovery_off_1day_gap_no_event(self):
        rows = (
            [self._base() for _ in range(6)]
            + [self._base(recovery=True, rec_rate=True, y20=50.0)]
            + [self._base()]
        )
        frame = self._frame(rows)
        self.assertEqual(regime_events(frame, frame.index[-1]), [])

    def test_recovery_off_after_6_false(self):
        rows = [self._base(recovery=True, rec_rate=True, y20=50.0)] + [self._base() for _ in range(6)]
        frame = self._frame(rows)
        self.assertEqual(regime_events(frame, frame.index[5]), [])
        evs = regime_events(frame, frame.index[6])
        self.assertEqual([e["kind"] for e in evs], ["recovery_off"])
        self.assertEqual(evs[0]["date"], frame.index[6])

    def test_recovery_reon_within_gap_no_event(self):
        rows = (
            [self._base(recovery=True, rec_rate=True, y20=50.0)]
            + [self._base() for _ in range(3)]
            + [self._base(recovery=True, rec_rate=True, y20=50.0)]
        )
        frame = self._frame(rows)
        self.assertEqual(regime_events(frame, frame.index[-1]), [])

    def test_both_true_no_event(self):
        frame = self._frame([
            self._base(overheat_vix=True, v10=0.20, recovery=True, rec_rate=True, y20=50.0),
            self._base(overheat_vix=True, v10=0.21, recovery=True, rec_rate=True, y20=51.0),
        ])
        self.assertEqual(regime_events(frame, frame.index[-1]), [])

    def test_single_row_empty(self):
        frame = pd.DataFrame(
            [self._base(overheat_vix=True, recovery=True)],
            index=["20240103"],
        )
        self.assertEqual(regime_events(frame, "20240103"), [])


class TestFormatRegime(unittest.TestCase):
    def test_overheat_phrases(self):
        ev = {
            "kind": "overheat_vix", "date": "20240103",
            "row": {"gap": 0.15, "q10": 0.02, "v10": 0.20, "y20": 0.0, "icy": 0.0,
                    "rec_rate": False, "rec_jobs": False},
        }
        msg = format_regime_alert(ev)
        self.assertIn("과열 중 공포지수", msg)
        self.assertIn("매매 규칙(v38)은 바뀌지 않음", msg)
        self.assertIn("2024-01-03", msg)
        self.assertIn("+15.0%", msg)
        self.assertIn("+2.0%", msg)
        self.assertIn("+20.0%", msg)

    def test_recovery_on_rate_only(self):
        ev = {
            "kind": "recovery_on", "date": "20240103",
            "row": {"gap": 0.15, "q10": 0.01, "v10": 0.0, "y20": 50.0, "icy": 0.0,
                    "rec_rate": True, "rec_jobs": False},
        }
        msg = format_regime_alert(ev)
        self.assertIn("회복기 표지 켜짐", msg)
        self.assertIn("매매 규칙(v38)은 바뀌지 않음", msg)
        self.assertIn("10년물", msg)
        self.assertNotIn("실업수당", msg)
        self.assertIn("+0.50%p", msg)
        self.assertIn("6거래일 연속", msg)

    def test_recovery_on_jobs_only(self):
        ev = {
            "kind": "recovery_on", "date": "20240103",
            "row": {"gap": 0.15, "q10": 0.01, "v10": 0.0, "y20": 0.0, "icy": 0.20,
                    "rec_rate": False, "rec_jobs": True},
        }
        msg = format_regime_alert(ev)
        self.assertIn("회복기 표지 켜짐", msg)
        self.assertIn("실업수당", msg)
        self.assertNotIn("10년물", msg)
        self.assertIn("+20.0%", msg)

    def test_recovery_on_both(self):
        ev = {
            "kind": "recovery_on", "date": "20240103",
            "row": {"gap": 0.15, "q10": 0.01, "v10": 0.0, "y20": 50.0, "icy": 0.20,
                    "rec_rate": True, "rec_jobs": True},
        }
        msg = format_regime_alert(ev)
        self.assertIn("10년물", msg)
        self.assertIn("실업수당", msg)

    def test_recovery_off_gap_low(self):
        ev = {
            "kind": "recovery_off", "date": "20240103",
            "row": {"gap": 0.10, "q10": 0.0, "v10": 0.0, "y20": 0.0, "icy": 0.0,
                    "rec_rate": False, "rec_jobs": False},
        }
        msg = format_regime_alert(ev)
        self.assertIn("꺼짐", msg)
        self.assertIn("매매 규칙(v38)은 바뀌지 않음", msg)
        self.assertIn("과열권(+14%) 아래", msg)
        self.assertIn("6거래일 연속", msg)

    def test_recovery_off_conditions_released(self):
        ev = {
            "kind": "recovery_off", "date": "20240103",
            "row": {"gap": 0.15, "q10": 0.0, "v10": 0.0, "y20": 10.0, "icy": 0.02,
                    "rec_rate": False, "rec_jobs": False},
        }
        msg = format_regime_alert(ev)
        self.assertIn("꺼짐", msg)
        self.assertIn("조건이 풀림", msg)
        self.assertIn("6거래일 연속", msg)

    def test_nan_becomes_na(self):
        ev = {
            "kind": "recovery_off", "date": "20240103",
            "row": {"gap": float("nan"), "q10": float("nan"), "v10": float("nan"),
                    "y20": float("nan"), "icy": float("nan"),
                    "rec_rate": False, "rec_jobs": False},
        }
        msg = format_regime_alert(ev)
        self.assertIn("n/a", msg)
        ev2 = {
            "kind": "overheat_vix", "date": "20240103",
            "row": {"gap": float("nan"), "q10": float("nan"), "v10": float("nan"),
                    "y20": float("nan"), "icy": float("nan"),
                    "rec_rate": False, "rec_jobs": False},
        }
        self.assertIn("n/a", format_regime_alert(ev2))


class TestRegimeMain(unittest.TestCase):
    def _px_oas(self):
        px = pd.DataFrame({"HYG": [0.001], "IEI": [0.002]}, index=["20240102"])
        oas = pd.Series([3.0], index=["20240102"], name="BAMLH0A0HYM2")
        return px, oas

    def test_inserted_calls_send_regime(self):
        r = _alert_row(alert=False, alert_fixed=False, chg_bp=1.0)
        px, oas = self._px_oas()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "alert.duckdb")
            with (
                patch("scripts.us_oas_alert.load_fred", return_value=oas),
                patch("scripts.us_oas_alert.load_etf_tr", return_value=px),
                patch("scripts.us_oas_alert.compute", return_value=r),
                patch("scripts.us_oas_alert.send_strategy_alert", return_value=True),
                patch("scripts.us_oas_alert.send_regime_alerts",
                      return_value=["OAS regime 20240103 gap n/a overheat_vix=off recovery=off"]) as reg,
            ):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = main(["--db-path", db])
        self.assertEqual(rc, 0)
        reg.assert_called_once_with(r["date"])
        self.assertIn("OAS regime", buf.getvalue())

    def test_exception_still_exit0(self):
        r = _alert_row(alert=False, alert_fixed=False, chg_bp=1.0)
        px, oas = self._px_oas()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "alert.duckdb")
            with (
                patch("scripts.us_oas_alert.load_fred", return_value=oas),
                patch("scripts.us_oas_alert.load_etf_tr", return_value=px),
                patch("scripts.us_oas_alert.compute", return_value=r),
                patch("scripts.us_oas_alert.send_strategy_alert", return_value=True),
                patch("scripts.us_oas_alert.send_regime_alerts", side_effect=RuntimeError("boom")),
            ):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = main(["--db-path", db])
        self.assertEqual(rc, 0)
        self.assertIn("OAS regime failed", buf.getvalue())

    def test_rerun_does_not_call(self):
        r = _alert_row(alert=False, alert_fixed=False, chg_bp=1.0)
        px, oas = self._px_oas()
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "alert.duckdb")
            with (
                patch("scripts.us_oas_alert.load_fred", return_value=oas),
                patch("scripts.us_oas_alert.load_etf_tr", return_value=px),
                patch("scripts.us_oas_alert.compute", return_value=r),
                patch("scripts.us_oas_alert.send_strategy_alert", return_value=True),
                patch("scripts.us_oas_alert.send_regime_alerts", return_value=["OAS regime x"]) as reg1,
            ):
                buf1 = io.StringIO()
                with contextlib.redirect_stdout(buf1):
                    rc1 = main(["--db-path", db])
                bufm = io.StringIO()
                with contextlib.redirect_stdout(bufm):
                    rcm = main(["--db-path", db, "--mark-reported", r["date"]])
            self.assertEqual((rc1, rcm), (0, 0))
            self.assertEqual(reg1.call_count, 1)
            with (
                patch("scripts.us_oas_alert.load_fred", return_value=oas),
                patch("scripts.us_oas_alert.load_etf_tr", return_value=px),
                patch("scripts.us_oas_alert.compute", return_value=r),
                patch("scripts.us_oas_alert.send_strategy_alert", return_value=True),
                patch("scripts.us_oas_alert.send_regime_alerts") as reg2,
            ):
                buf2 = io.StringIO()
                with contextlib.redirect_stdout(buf2):
                    rc2 = main(["--db-path", db])
        self.assertEqual(rc2, 0)
        reg2.assert_not_called()
        self.assertIn("already reported", buf2.getvalue())


class TestSendRegimeArg(unittest.TestCase):
    def test_send_regime_arg(self):
        with (
            patch("scripts.us_oas_alert.send_strategy_alert", return_value=True),
            patch("scripts.us_oas_alert.send_regime_alerts",
                  return_value=["OAS regime overheat_vix sent=True"]) as reg,
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["--send-regime", "20240103"])
        self.assertEqual(rc, 0)
        reg.assert_called_once_with("20240103")
        self.assertIn("OAS regime", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
