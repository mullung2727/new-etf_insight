"""judge 테스트 (합성 일별 값만, 실파일 읽지 않음)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from research.rl_signal import judge as judge_mod
from research.rl_signal.judge import block_boot_lower, holm2, main, pct_half, required_days, verdict


class TestPctHalf(unittest.TestCase):
    def test_half_tie(self):
        self.assertAlmostEqual(pct_half(0.5, [0.0, 0.5, 1.0]), 0.5)
        self.assertAlmostEqual(pct_half(1.0, [1.0, 1.0]), 0.5)
        self.assertAlmostEqual(pct_half(2.0, [0.0, 1.0]), 1.0)
        self.assertAlmostEqual(pct_half(-1.0, [0.0, 1.0]), 0.0)


class TestBlockBoot(unittest.TestCase):
    def test_constant_positive(self):
        lo = block_boot_lower(np.full(60, 0.002), 5, 500, np.random.default_rng(0))
        self.assertGreater(lo, 0)
        self.assertAlmostEqual(lo, 0.002)

    def test_zeros(self):
        lo = block_boot_lower(np.zeros(20), 5, 200, np.random.default_rng(0))
        self.assertAlmostEqual(lo, 0.0)


class TestRequiredDays(unittest.TestCase):
    def test_56(self):
        self.assertEqual(required_days(0.003), 56)


class TestVerdict(unittest.TestCase):
    def test_hold(self):
        self.assertEqual(verdict(59, True, True, True, True, 60), "보류")

    def test_pass(self):
        self.assertEqual(verdict(60, True, True, True, True, 60), "통과")

    def test_fail_names_criteria(self):
        v = verdict(60, True, False, True, False, 60)
        self.assertTrue(v.startswith("불통과"))
        self.assertIn("2", v)
        self.assertIn("4", v)
        self.assertNotIn("1", v)


class TestHolm(unittest.TestCase):
    def test_both_pass(self):
        self.assertEqual(holm2(0.01, 0.04), (True, True))
        self.assertEqual(holm2(0.04, 0.01), (True, True))

    def test_both_fail(self):
        self.assertEqual(holm2(0.03, 0.04), (False, False))

    def test_split(self):
        self.assertEqual(holm2(0.01, 0.06), (True, False))


def _rec(day, m_stock=0.002, m_etf=0.001):
    def e(m):
        return {"n": 1, "model": m, "trades": 10, "log_sum_slip1": 0.01,
                "ps_day": [0.0, 0.0], "sh_day": [0.0, 0.0]}
    return {"day": day, "version": "v", "model": "hgb_adv",
            "stock": e(m_stock), "etf": e(m_etf)}


def _write(td, version, model, recs):
    d = Path(td) / version / model
    d.mkdir(parents=True)
    for r in recs:
        (d / f"{r['day']}.json").write_text(json.dumps(r), encoding="utf-8")


def _run(argv, td):
    with mock.patch.object(judge_mod, "RESULTS", Path(td)):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(argv)
    return buf.getvalue()


class TestMain(unittest.TestCase):
    def test_min_days_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(judge_mod, "RESULTS", Path(td)):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main(["--version", "dev", "--model", "hgb_adv",
                              "--stratum", "stock", "--min-days", "59"])

    def test_non_main_diagnose(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "dev", "hgb", [_rec("20261110"), _rec("20261111")])
            out = _run(["--version", "dev", "--model", "hgb", "--stratum", "stock",
                        "--boot", "100"], td)
        lines = out.strip().splitlines()
        self.assertEqual(lines[-1], "진단 (주 모델 아님)")
        self.assertNotIn("통과", out)
        self.assertNotIn("불통과", out)
        self.assertNotIn("판정", out)

    def test_start_required(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(judge_mod, "RESULTS", Path(td)):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        main(["--version", "v1", "--model", "hgb_adv"])

    def test_start_filters(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "v1", "hgb_adv", [_rec("20261110"), _rec("20261111")])
            out = _run(["--version", "v1", "--model", "hgb_adv", "--stratum", "stock",
                        "--start", "20261111", "--boot", "100"], td)
        self.assertIn("days=1", out)
        self.assertIn("start=20261111", out)

    def test_hold_when_n_star_exceeds(self):
        with tempfile.TemporaryDirectory() as td:
            recs = [_rec(f"2026{i:04d}", 0.1 if i % 2 else -0.1) for i in range(60)]
            _write(td, "dev", "hgb_adv", recs)
            out = _run(["--version", "dev", "--model", "hgb_adv", "--stratum", "stock",
                        "--min-days", "60", "--boot", "100"], td)
        self.assertIn("판정 보류", out)


if __name__ == "__main__":
    unittest.main()
