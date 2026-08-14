import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_telegram_pipeline import run_pipeline  # noqa: E402


def _ok(cmd, **kw):
    return SimpleNamespace(returncode=0)


class RunPipelineTest(unittest.TestCase):
    def test_stages_run_in_order_with_date_session(self):
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            return SimpleNamespace(returncode=0)

        run_pipeline("2026-07-06", "close", runner=runner)

        self.assertEqual(len(calls), 3)
        joined = [" ".join(c) for c in calls]
        # discover → analyze → digest 순서
        self.assertIn("discover", joined[0])
        self.assertIn("analysis", joined[1])
        self.assertIn("digest", joined[2])
        # 모든 단계 --date/--session 전달
        for c in calls:
            self.assertIn("--date", c)
            self.assertIn("2026-07-06", c)
            self.assertIn("--session", c)
            self.assertIn("close", c)

    def test_stops_and_raises_on_stage_failure(self):
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            # 첫 단계(discover) 실패
            return SimpleNamespace(returncode=2)

        with self.assertRaises(RuntimeError):
            run_pipeline("2026-07-06", "close", runner=runner)
        # 실패 후 다음 단계 실행 안 됨
        self.assertEqual(len(calls), 1)

    def test_digest_flags_only_on_digest_stage(self):
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            return SimpleNamespace(returncode=0)

        run_pipeline("2026-07-06", "close", digest_channel="telegram_report",
                     dry_run=True, runner=runner)
        digest_cmd = calls[-1]
        self.assertIn("--dry-run", digest_cmd)
        self.assertIn("--channel", digest_cmd)
        self.assertIn("telegram_report", digest_cmd)
        # discover/analyze 엔 digest 전용 플래그 없음
        for c in calls[:-1]:
            self.assertNotIn("--dry-run", c)
            self.assertNotIn("--channel", c)

    def test_start_date_only_on_discover_and_analyze(self):
        calls = []

        def runner(cmd, **kw):
            calls.append(cmd)
            return SimpleNamespace(returncode=0)

        run_pipeline("2026-07-07", "morning", start_date="2026-07-06", runner=runner)
        for c in calls[:2]:
            self.assertIn("--start-date", c)
            self.assertIn("2026-07-06", c)
        self.assertNotIn("--start-date", calls[2])


if __name__ == "__main__":
    unittest.main()
