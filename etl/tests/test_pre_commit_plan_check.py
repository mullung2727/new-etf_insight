import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "pre_commit_checks",
    Path(__file__).resolve().parents[2] / "scripts" / "pre_commit_checks.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
plan_progress_done = _mod.plan_progress_done


class TestPlanProgressDone(unittest.TestCase):
    def test_single_done(self):
        self.assertTrue(plan_progress_done("## 진행\n- [x] a\n- [X] b\n"))

    def test_single_unchecked(self):
        self.assertFalse(plan_progress_done("## 진행\n- [x] a\n- [ ] b\n"))

    def test_two_sections_second_unchecked(self):
        text = "## 진행 (P1)\n- [x] a\n## 진행 (P2)\n- [ ] b\n"
        self.assertFalse(plan_progress_done(text))

    def test_two_sections_both_done(self):
        text = "## 진행 (P1)\n- [x] a\n## 진행 (P2)\n- [x] b\n"
        self.assertTrue(plan_progress_done(text))

    def test_no_section(self):
        self.assertFalse(plan_progress_done("# PLAN\n## 개요\n- [x] a\n"))

    def test_outside_checkbox_ignored(self):
        text = "## 진행\n- [x] a\n## P1 진행 메모\n- [ ] note\n"
        self.assertTrue(plan_progress_done(text))


if __name__ == "__main__":
    unittest.main()
