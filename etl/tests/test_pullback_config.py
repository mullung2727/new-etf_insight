"""눌림목 전략 JSON 설정 로더 TDD 테스트."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.pullback_config import load


VALID = {
    "budget_per_stock": 300_000,
    "max_new_positions": 3,
    "tp": 0.03,
    "sl": 0.03,
    "max_wait_days": 5,
    "max_hold_days": 3,
}


def write_config(value: dict) -> Path:
    path = Path(tempfile.mktemp(suffix=".json"))
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class PullbackConfigTest(unittest.TestCase):
    def test_loads_complete_config(self):
        self.assertEqual(load(write_config(VALID)), VALID)

    def test_missing_file_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            load(Path(tempfile.gettempdir()) / "missing_pullback_config.json")

    def test_missing_required_key_fails_closed(self):
        invalid = {key: value for key, value in VALID.items() if key != "budget_per_stock"}
        with self.assertRaises(ValueError):
            load(write_config(invalid))

    def test_rejects_invalid_ranges_and_types(self):
        invalid_values = (
            {"budget_per_stock": 0}, {"budget_per_stock": 1.5},
            {"max_new_positions": 0}, {"max_new_positions": 4},
            {"tp": 0}, {"tp": 1.1}, {"sl": 0},
            {"max_wait_days": 6}, {"max_hold_days": 0},
        )
        for change in invalid_values:
            with self.subTest(change=change), self.assertRaises(ValueError):
                load(write_config({**VALID, **change}))


if __name__ == "__main__":
    unittest.main()
