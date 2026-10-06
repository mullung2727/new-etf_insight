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
    main,
    mark_reported,
    proxy,
    record,
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


if __name__ == "__main__":
    unittest.main()
