"""walkforward 테스트 (합성 날짜만, 실 parquet 읽지 않음)."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from research.rl_signal.bars import FEATURES
from research.rl_signal.dataset import samples
from research.rl_signal.evaluate import summarize
from research.rl_signal.oracle import BUY, HOLD, SELL
from research.rl_signal import walkforward as wf_mod
from research.rl_signal.walkforward import _nanmean, eval_rng, main, splits, src_hash

DAYS = ["2026110%d" % i for i in range(1, 8)]


class TestSplits(unittest.TestCase):
    def test_basic(self):
        sp = splits(DAYS, 5)
        self.assertEqual(len(sp), 2)
        self.assertEqual(sp[0], (DAYS[:5], DAYS[5]))
        self.assertEqual(sp[1], (DAYS[:6], DAYS[6]))

    def test_no_leak(self):
        for train, test in splits(DAYS, 5):
            self.assertNotIn(test, train)

    def test_min_train_too_big(self):
        self.assertEqual(splits(DAYS, 7), [])
        self.assertEqual(splits(DAYS, 99), [])


class TestNanmean(unittest.TestCase):
    def test_mean(self):
        self.assertAlmostEqual(_nanmean([0.1, 0.2, 0.3]), 0.2)

    def test_skips_nan(self):
        self.assertAlmostEqual(_nanmean([0.1, float("nan"), 0.3]), 0.2)

    def test_all_nan(self):
        self.assertTrue(np.isnan(_nanmean([float("nan")])))
        self.assertTrue(np.isnan(_nanmean([])))


def _entry(model, bh, oracle, pr, pt, trades=1):
    return {"model": model, "model_slip1": model - 0.001, "trades": trades,
            "log_total": model, "log_total_slip1": model - 0.001,
            "bh": bh, "oracle": oracle,
            "pr": np.array(pr, dtype=float), "pt": np.array(pt, dtype=float),
            "ps": np.array(pr, dtype=float)}


class TestSeenUnseenContract(unittest.TestCase):
    """main()이 의존하는 식의 계약 고정 (main 자체는 parquet 없이 실행 불가).

    seen 합집합 분리 + summarize(per, seen/unseen) + json seen/unseen 키(n·model·bh·diag_capture).
    """

    def test_split_and_json_keys(self):
        codes = ["A", "B", "C"]
        seen = {"A", "C"}
        per = {
            "A": _entry(0.02, 0.01, 0.05, [0.0, 0.02], [0.01, 0.01]),
            "B": _entry(0.04, -0.01, 0.07, [0.02, 0.02], [0.0, 0.06]),
            "C": _entry(0.00, 0.02, 0.03, [0.01, 0.01], [0.02, 0.02]),
        }
        s_seen = summarize(per, [c for c in codes if c in seen])
        s_unseen = summarize(per, [c for c in codes if c not in seen])
        self.assertEqual(s_seen["n"], 2)
        self.assertAlmostEqual(s_seen["model"], 0.01)
        self.assertEqual(s_unseen["n"], 1)
        self.assertAlmostEqual(s_unseen["model"], 0.04)
        for s in (s_seen, s_unseen):
            for k in ("n", "model", "bh", "diag_capture"):
                self.assertIn(k, s)

    def test_empty_side(self):
        per = {"A": _entry(0.02, 0.01, 0.05, [0.0], [0.01])}
        s = summarize(per, [])
        self.assertEqual(s["n"], 0)
        self.assertTrue(np.isnan(s["model"]))


class TestShuffleSeeds(unittest.TestCase):
    """main()의 일자별 시드 계약: 같은 day 섞기 표본 X = 원 표본 X, 라벨만 다름."""

    def test_same_day_same_x(self):
        n = 8
        q = np.full((n, 2, 6, 3), -np.inf)
        for t in range(n):
            q[t, :, :, HOLD] = 0.0
            q[t, :, :, BUY] = float(t)
            q[t, :, :, SELL] = float(t)
        cells = {"AAA": {"feat": np.random.default_rng(0).normal(size=(n, len(FEATURES))).astype(np.float32),
                         "q": q, "q_train": q}}
        seed, day = 0, "20261110"
        X0, y0, _, _ = samples(cells, np.random.default_rng([seed, int(day)]))
        X1, y1, _, _ = samples(cells, np.random.default_rng([seed, int(day)]),
                               np.random.default_rng([seed, int(day), 1000]))
        np.testing.assert_array_equal(X1, X0)
        self.assertFalse((y1 == y0).all())


class TestEvalRng(unittest.TestCase):
    """일자별 평가 난수 계약: 같은 (seed, 일) → 같은 스트림, 다른 일 → 다른 스트림."""

    def test_same_day_same_stream(self):
        a = eval_rng(0, "20261110").random(5)
        b = np.random.default_rng([1, 20261110]).random(5)
        np.testing.assert_array_equal(a, b)

    def test_diff_day_diff_stream(self):
        a = eval_rng(0, "20261110").random(5)
        b = eval_rng(0, "20261111").random(5)
        self.assertFalse(bool((a == b).all()))


def _fake_eval(model, scaler, names, cells, codes, reps, rng):
    """rng 1회 소비 + 그 값으로 결과 생성: 순차 rng 면 전체≠증분, 일자별이면 전체=증분."""
    v = float(rng.random())
    return {c: {"model": v, "model_slip1": v, "trades": 2,
                "log_total": v, "log_total_slip1": v,
                "bh": 0.001, "oracle": 0.05,
                "pr": np.zeros(2), "pt": np.zeros(2), "ps": np.zeros(2)}
            for c in codes}


class TestIncremental(unittest.TestCase):
    """--incremental: 기존 json 유지 + 있는 날 건너뛰기, 재계산 결과는 전체 실행과 동일."""

    def _patches(self, td):
        days = ["20261109", "20261110", "20261111"]
        return [
            mock.patch.object(wf_mod, "RESULTS", Path(td)),
            mock.patch.object(wf_mod, "_trade_days", return_value=days),
            mock.patch.object(wf_mod, "load_px", return_value=None),
            mock.patch.object(wf_mod.daily_mod, "daily_table", return_value={}),
            mock.patch.object(wf_mod.daily_mod, "load_etf_px", return_value=None),
            mock.patch.object(wf_mod, "day_cells", return_value={"AAA": {}}),
            mock.patch.object(wf_mod, "day_reasons", return_value={}),
            mock.patch.object(wf_mod, "samples",
                              return_value=(np.zeros((2, 3)), np.zeros(2),
                                            [], np.zeros(2))),
            mock.patch.object(wf_mod, "Scaler"),
            mock.patch.object(wf_mod, "make"),
            mock.patch.object(wf_mod, "eval_codes", side_effect=_fake_eval),
        ]

    def _run_all(self, td, *extra):
        argv = ["--model", "hgb_adv", "--version", "t", "--min-train", "1",
               "--reps", "0", "--shuffle-reps", "1", "--seed", "0", *extra]
        with contextlib.ExitStack() as st:
            for p in self._patches(td):
                st.enter_context(p)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(argv)
            return buf.getvalue()

    def _run_argv(self, td, argv):
        with contextlib.ExitStack() as st:
            for p in self._patches(td):
                st.enter_context(p)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(argv)
            return buf.getvalue()

    def test_full_then_incremental_same_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "t" / "hgb_adv"
            self._run_all(td)
            before = {p.name: p.read_bytes() for p in sorted(d.glob("*.json"))
                      if p.name != "_config.json"}
            self.assertEqual(set(before), {"20261110.json", "20261111.json"})
            self.assertTrue((d / "_config.json").exists())
            (d / "20261111.json").unlink()
            out = self._run_all(td, "--incremental")
            self.assertIn("20261110 skip: exists (incremental)", out)
            self.assertEqual((d / "20261110.json").read_bytes(), before["20261110.json"])
            self.assertEqual((d / "20261111.json").read_bytes(), before["20261111.json"])

    def test_incremental_keeps_existing_json(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "t" / "hgb_adv"
            d.mkdir(parents=True)
            (d / "19990101.json").write_text("{}", encoding="utf-8")
            self._run_all(td, "--incremental")
            self.assertTrue((d / "19990101.json").exists())
            self.assertTrue((d / "20261110.json").exists())

    def test_full_wipes_existing_json(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "t" / "hgb_adv"
            d.mkdir(parents=True)
            (d / "19990101.json").write_text("{}", encoding="utf-8")
            self._run_all(td)
            self.assertFalse((d / "19990101.json").exists())

    def test_incremental_same_config_passes(self):
        with tempfile.TemporaryDirectory() as td:
            self._run_all(td)
            try:
                out = self._run_all(td, "--incremental")
            except SystemExit as e:
                self.fail(f"same config should not exit, got {e.code}")
            self.assertNotIn("코드·설정이 바뀜", out)

    def test_incremental_reps_diff_exits_2(self):
        with tempfile.TemporaryDirectory() as td:
            self._run_all(td)
            argv = ["--model", "hgb_adv", "--version", "t", "--min-train", "1",
                    "--reps", "1", "--shuffle-reps", "1", "--seed", "0",
                    "--incremental"]
            with contextlib.ExitStack() as st:
                for p in self._patches(td):
                    st.enter_context(p)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as cm:
                        main(argv)
                out = buf.getvalue()
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("reps", out)
            self.assertIn("코드·설정이 바뀜 — 새 판정 버전 필요", out)

    def test_dev_skips_config(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "dev" / "hgb_adv"
            full = ["--model", "hgb_adv", "--version", "dev", "--min-train", "1",
                    "--reps", "0", "--shuffle-reps", "1", "--seed", "0"]
            self._run_argv(td, full)
            self.assertFalse((d / "_config.json").exists())
            inc = ["--model", "hgb_adv", "--version", "dev", "--min-train", "1",
                   "--reps", "1", "--shuffle-reps", "1", "--seed", "0",
                   "--incremental"]
            try:
                self._run_argv(td, inc)
            except SystemExit as e:
                self.fail(f"dev should skip config check, got exit {e.code}")
            self.assertFalse((d / "_config.json").exists())


class TestSrcHash(unittest.TestCase):
    def test_excludes_tests(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "a.py").write_text("x=1", encoding="utf-8")
            (base / "tests").mkdir()
            (base / "tests" / "test_a.py").write_text("y=2", encoding="utf-8")
            h1 = src_hash(base)
            (base / "tests" / "test_a.py").write_text("y=3", encoding="utf-8")
            self.assertEqual(src_hash(base), h1)
            (base / "a.py").write_text("x=2", encoding="utf-8")
            self.assertNotEqual(src_hash(base), h1)


if __name__ == "__main__":
    unittest.main()
