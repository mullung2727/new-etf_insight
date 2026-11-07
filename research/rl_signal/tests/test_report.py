"""report 테스트 (합성 results json, send mock, 실전송 없음)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from research.rl_signal import judge as judge_mod
from research.rl_signal import report as report_mod
from research.rl_signal.report import build_message, main


def _entry(model=0.001, trades=5, bh=0.0005):
    return {"n": 2, "codes": ["AAA", "BBB"], "model": model, "model_slip1": model,
            "trades": trades, "log_sum": 0.002, "log_sum_slip1": 0.002, "bh": bh,
            "ps_day": [0.0, 0.0], "sh_day": [0.0, 0.0],
            "diag_capture": 0.5, "diag_pct_random": 0.9,
            "seen_n": 2, "unseen_n": 0, "seen_model": model, "unseen_model": 0.0}


def _rec(day, ms=0.001, me=0.002):
    return {"day": day, "version": "v2", "model": "hgb_adv", "n_train_days": 5,
            "reasons": {}, "stock": _entry(ms), "etf": _entry(me)}


def _write(td, version, model, recs):
    d = Path(td) / version / model
    d.mkdir(parents=True)
    for r in recs:
        (d / f"{r['day']}.json").write_text(json.dumps(r), encoding="utf-8")


def _run_main(argv):
    with mock.patch.object(report_mod, "send_strategy_alert", return_value=True) as send:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(argv)
    return send


class TestBuildMessage(unittest.TestCase):
    def test_success_first_line_and_body(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "v2", "hgb_adv", [_rec("20261109"), _rec("20261110")])
            with mock.patch.object(report_mod, "RESULTS", Path(td)), \
                    mock.patch.object(judge_mod, "RESULTS", Path(td)):
                msg = build_message("v2", "hgb_adv", "20261110", "20261109")
        first, *rest = msg.splitlines()
        self.assertTrue(first.startswith("✅"))
        self.assertIn("RL신호 야간학습", first)
        self.assertIn("20261110", first)
        self.assertIn("종목 +0.10%", first)
        self.assertIn("5건", first)
        self.assertIn("장중보유 +0.05%", first)
        self.assertIn("ETF +0.20%", first)
        self.assertEqual(len(rest), 2)
        self.assertIn("종목 누적 2/60일", rest[0])
        self.assertIn("ETF 누적 2/60일", rest[1])
        self.assertIn("기준", rest[0])
        self.assertIn("기준", rest[1])

    def test_missing_json_warn(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(report_mod, "RESULTS", Path(td)), \
                    mock.patch.object(report_mod, "day_reasons",
                                      return_value={"no_exec": 3}):
                msg = build_message("v2", "hgb_adv", "20261110", "20261109")
        first = msg.splitlines()[0]
        self.assertTrue(first.startswith("⚠"))
        self.assertIn("20261110 결과 없음", first)
        self.assertIn("no_exec 3건", msg)

    def test_missing_json_no_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(report_mod, "RESULTS", Path(td)), \
                    mock.patch.object(report_mod, "day_reasons", return_value={}):
                msg = build_message("v2", "hgb_adv", "20261110", "20261109")
        self.assertTrue(msg.startswith("⚠"))
        self.assertIn("없음", msg)

    def test_uses_judge_both(self):
        fake = {
            "stock": {"n": 60, "mean": 0.001, "c1": False, "c2": True,
                      "c3": True, "c4": True, "verdict": "불통과: 기준 1"},
            "etf": {"n": 60, "mean": 0.002, "c1": False, "c2": True,
                    "c3": True, "c4": True, "verdict": "불통과: 기준 1"},
        }
        with tempfile.TemporaryDirectory() as td:
            _write(td, "v2", "hgb_adv", [_rec("20261110")])
            with mock.patch.object(report_mod, "RESULTS", Path(td)), \
                    mock.patch.object(report_mod, "judge_both",
                                      return_value=fake) as jb:
                msg = build_message("v2", "hgb_adv", "20261110", "20261109")
                jb.assert_called_once_with("v2", "hgb_adv", "20261109")
        self.assertIn("1미달", msg)
        self.assertIn("2충족", msg)


class TestMain(unittest.TestCase):
    def test_failed(self):
        send = _run_main(["--version", "v2", "--model", "hgb_adv", "--day", "20261110",
                          "--start", "20261109", "--failed", "walkforward exit 1"])
        send.assert_called_once()
        msg, env = send.call_args.args
        self.assertTrue(msg.startswith("❌"))
        self.assertIn("RL신호 야간학습", msg)
        self.assertIn("실패: walkforward exit 1", msg)
        self.assertEqual(env, "RL_SIGNAL_DISCORD_WEBHOOK_URL")

    def test_success_sends_ok(self):
        with tempfile.TemporaryDirectory() as td:
            _write(td, "v2", "hgb_adv", [_rec("20261110")])
            with mock.patch.object(report_mod, "RESULTS", Path(td)), \
                    mock.patch.object(judge_mod, "RESULTS", Path(td)):
                send = _run_main(["--version", "v2", "--model", "hgb_adv",
                                  "--day", "20261110", "--start", "20261109"])
        self.assertTrue(send.call_args.args[0].startswith("✅"))

    def test_send_failure_prints_only(self):
        with mock.patch.object(report_mod, "send_strategy_alert",
                               side_effect=RuntimeError("boom")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(["--version", "v2", "--model", "hgb_adv", "--day", "20261110",
                      "--start", "20261109", "--failed", "x"])
        self.assertIn("alert send failed", buf.getvalue())

    def test_build_failure_exits_1(self):
        with mock.patch.object(report_mod, "build_message",
                               side_effect=RuntimeError("boom")):
            with mock.patch.object(report_mod, "send_strategy_alert",
                                   return_value=True) as send:
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    with self.assertRaises(SystemExit) as cm:
                        main(["--version", "v2", "--model", "hgb_adv",
                              "--day", "20261110", "--start", "20261109"])
                self.assertEqual(cm.exception.code, 1)
        send.assert_called_once()
        msg, env = send.call_args.args
        self.assertTrue(msg.startswith("❌"))
        self.assertIn("RL신호 야간학습", msg)
        self.assertIn("알림 생성 실패", msg)
        self.assertIn("boom", msg)


if __name__ == "__main__":
    unittest.main()
