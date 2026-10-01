"""package 단위 테스트 — import·공개 저장소 무결성 (unittest)."""
import importlib
import unittest
from pathlib import Path

import research.backtest_minute

MODULES = ["data", "prevday", "ticks", "fills", "exits", "bench", "validate"]


class TestPackage(unittest.TestCase):
    def test_all_modules_import(self):
        for m in MODULES:
            importlib.import_module(f"research.backtest_minute.{m}")

    def test_no_private_ref(self):
        forbidden = ".".join(["research", "private"])
        root = Path(research.backtest_minute.__file__).resolve().parent
        for p in sorted(root.rglob("*.py")):
            src = p.read_text(encoding="utf-8")
            self.assertNotIn(forbidden, src, f"forbidden ref in {p.name}")

    def test_docstring_minute_only(self):
        self.assertIn("분봉 전용", research.backtest_minute.__doc__)


if __name__ == "__main__":
    unittest.main()
