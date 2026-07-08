"""close_bet.json config 배선 검증 (step2).

배치가 CLI 인자 없이 실행되면 close_bet.json 값을 쓰는지 확인:
  - run_close_bet: --score-threshold 미지정 시 default None (→ main 에서 config)
  - budget_for: 주입한 budget_map 을 사용 (config budget_by_count 반영 경로)
"""
import unittest

from scripts.run_close_bet import budget_for, build_arg_parser


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


if __name__ == "__main__":
    unittest.main()
