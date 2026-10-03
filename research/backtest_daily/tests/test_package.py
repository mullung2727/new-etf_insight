"""package 단위 테스트 — import·공개 저장소 무결성 (unittest)."""
import importlib
import unittest
from pathlib import Path

import research.backtest_daily

MODULES = ["data", "adjust", "universe", "guards", "bench",
           "exits", "paths", "stats", "validate",
           "portfolio", "perf"]


class TestPackage(unittest.TestCase):
    def test_all_modules_import(self):
        for m in MODULES:
            importlib.import_module(f"research.backtest_daily.{m}")

    def test_no_private_ref(self):
        forbidden = ".".join(["research", "private"])
        root = Path(research.backtest_daily.__file__).resolve().parent
        for p in sorted(root.rglob("*.py")):
            src = p.read_text(encoding="utf-8")
            self.assertNotIn(forbidden, src, f"forbidden ref in {p.name}")

    def test_docstring_daily_only(self):
        self.assertIn("일봉 전용", research.backtest_daily.__doc__)


if __name__ == "__main__":
    unittest.main()
