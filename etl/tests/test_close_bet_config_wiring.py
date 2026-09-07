"""close_bet.json config 배선 검증.

배치가 CLI 인자 없이 실행되면 close_bet.json 값을 쓰는지 확인:
  - run_close_bet: --score-threshold 미지정 시 default None (→ main 에서 config)
  - budget_for: 주입한 budget_map 을 사용 (config budget_by_count 반영 경로)
  - run_close_bet_exit: --force-exit-time 미지정 시 config exit_time (ps1 인자 아님)
  - 눌림목(run_pullback_exit)의 청산 시각은 이 변경에 영향받지 않는다
"""
import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_close_bet_exit, run_pullback_exit
from scripts.run_close_bet import budget_for, build_arg_parser

_CFG = {"tp": None, "sl": None, "exit_time": "09:01:00"}


class TestScoreThresholdDefaultNone(unittest.TestCase):
    def test_default_is_none_so_config_applies(self):
        # 인자 없으면 None → main 이 close_bet_config.load()["score_threshold"] 사용.
        args = build_arg_parser().parse_args([])
        self.assertIsNone(args.score_threshold)

    def test_cli_override_kept(self):
        args = build_arg_parser().parse_args(["--score-threshold", "80"])
        self.assertEqual(args.score_threshold, 80)


class TestBudgetFromConfigMap(unittest.TestCase):
    def test_budget_for_uses_injected_map(self):
        cfg_budget = {1: 1_000_000, 2: 2_500_000, 3: 900_000}
        self.assertEqual(budget_for(1, cfg_budget), 1_000_000)
        self.assertEqual(budget_for(3, cfg_budget), 900_000)
        self.assertEqual(budget_for(4, cfg_budget), 0)  # 정의역 밖

    def test_budget_for_default_map_unchanged(self):
        # 인자 없이 부르면 기존 하드코딩 기본값(하위호환).
        self.assertEqual(budget_for(1), 3_000_000)
        self.assertEqual(budget_for(2), 2_000_000)


class TestExitTimeFromConfig(unittest.TestCase):
    """청산 시각의 단일 소스는 close_bet.json — ps1 이 인자로 주지 않아도 반영돼야 한다."""

    def _args_seen(self, cfg: dict, argv: list[str]) -> argparse.Namespace:
        seen = {}
        with patch.object(run_close_bet_exit, "load_close_bet_config", return_value=cfg),              patch.object(run_close_bet_exit, "run_loop",
                          side_effect=lambda args, url: seen.update({"args": args})),              patch.object(sys, "argv", ["run_close_bet_exit.py", *argv]):
            run_close_bet_exit.main()
        return seen["args"]

    def test_uses_config_exit_time_when_flag_absent(self):
        args = self._args_seen(_CFG, [])
        self.assertEqual(args.force_exit_time, "09:01:00")

    def test_config_change_is_followed(self):
        args = self._args_seen({**_CFG, "exit_time": "10:30:00"}, [])
        self.assertEqual(args.force_exit_time, "10:30:00")

    def test_cli_override_wins(self):
        args = self._args_seen(_CFG, ["--force-exit-time", "14:00:00"])
        self.assertEqual(args.force_exit_time, "14:00:00")

    def test_null_tp_sl_pass_through(self):
        args = self._args_seen(_CFG, [])
        self.assertIsNone(args.tp)
        self.assertIsNone(args.sl)


class TestPullbackUnaffected(unittest.TestCase):
    """부정 요구: 눌림목 청산 시각은 종가베팅 변경에 흔들리지 않는다."""

    def test_pullback_exit_default_still_1519(self):
        # main() 은 DB·브로커를 건드리므로 파서 기본값을 소스에서 직접 확인한다(회귀 가드).
        src = Path(run_pullback_exit.__file__).read_text(encoding="utf-8")
        self.assertIn('"--force-exit-time", default="15:19:00"', src)

    def test_pullback_ps1_still_passes_1519(self):
        ps1 = (Path(__file__).resolve().parents[2]
               / "ops" / "scheduled-tasks" / "run-trading-exit.ps1").read_text(encoding="utf-8")
        self.assertIn("15:19:00", ps1)


if __name__ == "__main__":
    unittest.main()
