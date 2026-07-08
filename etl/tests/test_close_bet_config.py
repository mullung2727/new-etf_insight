"""close_bet_config.py 로더 단위 테스트.

검증 항목:
  1. 파일 없으면 하드코딩 기본값 폴백(현재값 70/0.05/0.03/budget)
  2. 정상 파일 로드 → 값 반영, budget_by_count 는 int 키
  3. 키 일부 누락 → 누락 키만 기본값 폴백
  4. 범위 초과/타입 오류 → ValueError (배치 abort)
     - score_threshold 0~100 밖
     - tp/sl 0~1 밖
     - budget 0 이하
"""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.close_bet_config import DEFAULTS, load


def _write(cfg: dict) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(cfg, f)
    f.close()
    return Path(f.name)


class TestLoadFallback(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        cfg = load(Path(tempfile.gettempdir()) / "no_such_close_bet_cfg.json")
        self.assertEqual(cfg["score_threshold"], DEFAULTS["score_threshold"])
        self.assertEqual(cfg["tp"], DEFAULTS["tp"])
        self.assertEqual(cfg["sl"], DEFAULTS["sl"])
        self.assertEqual(cfg["budget_by_count"], DEFAULTS["budget_by_count"])

    def test_partial_keys_fallback(self):
        p = _write({"score_threshold": 60})
        cfg = load(p)
        self.assertEqual(cfg["score_threshold"], 60)  # 파일값
        self.assertEqual(cfg["tp"], DEFAULTS["tp"])    # 폴백
        self.assertEqual(cfg["budget_by_count"], DEFAULTS["budget_by_count"])


class TestLoadNormal(unittest.TestCase):
    def test_full_config_loaded(self):
        p = _write({
            "score_threshold": 50,
            "tp": 0.05,
            "sl": 0.03,
            "budget_by_count": {"1": 3000000, "2": 2000000, "3": 1666666},
        })
        cfg = load(p)
        self.assertEqual(cfg["score_threshold"], 50)
        self.assertEqual(cfg["tp"], 0.05)
        self.assertEqual(cfg["sl"], 0.03)
        # 문자열 키 → int 키 변환
        self.assertEqual(cfg["budget_by_count"], {1: 3000000, 2: 2000000, 3: 1666666})


class TestValidation(unittest.TestCase):
    def test_score_threshold_out_of_range(self):
        for bad in (-1, 101):
            with self.assertRaises(ValueError):
                load(_write({"score_threshold": bad}))

    def test_tp_out_of_range(self):
        with self.assertRaises(ValueError):
            load(_write({"tp": 1.5}))

    def test_sl_out_of_range(self):
        with self.assertRaises(ValueError):
            load(_write({"sl": -0.1}))

    def test_budget_non_positive(self):
        with self.assertRaises(ValueError):
            load(_write({"budget_by_count": {"1": 3000000, "2": 0, "3": 1666666}}))

    def test_budget_missing_count_key(self):
        with self.assertRaises(ValueError):
            load(_write({"budget_by_count": {"1": 3000000, "2": 2000000}}))


if __name__ == "__main__":
    unittest.main()
