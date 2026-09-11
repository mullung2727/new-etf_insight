"""호가 수집기 설정 로더 (SPEC §3)."""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.orderbook_recorder_config import DEFAULTS, PROJECT_ROOT, ConfigError, load


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "orderbook_recorder.json"

    def tearDown(self):
        self.tmp.cleanup()

    def load_with(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return load(self.path)

    def assert_rejects(self, data, key):
        with self.assertRaises(ConfigError) as ctx:
            self.load_with(data)
        self.assertEqual(ctx.exception.key, key)

    def test_missing_file_uses_defaults(self):
        cfg = load(self.path)
        self.assertEqual(cfg["windows"], DEFAULTS["windows"])
        self.assertEqual(cfg["symbols"], DEFAULTS["symbols"])
        self.assertEqual(cfg["max_stale_sec"], 30)
        self.assertEqual(cfg["scoring_result_path"],
                         (PROJECT_ROOT / "../../reports/recent_3day_probability_scores.json").resolve())

    def test_nested_window_keys_fall_back(self):
        cfg = self.load_with({"windows": {"morning": {"end": "09:10:00"}}})
        self.assertEqual(cfg["windows"]["morning"], {"start": "08:45:00", "end": "09:10:00"})
        self.assertEqual(cfg["windows"]["afternoon"], {"start": "15:00:00", "end": "15:30:00"})

    def test_unknown_keys_are_errors(self):
        self.assert_rejects({"window_start": "08:45:00"}, "window_start")
        self.assert_rejects({"windows": {"evening": {}}}, "windows.evening")
        self.assert_rejects({"windows": {"morning": {"begin": "08:45:00"}}}, "windows.morning.begin")
        self.assert_rejects({"symbols": {"mode": "union"}}, "symbols.mode")

    def test_fixed_values(self):
        self.assert_rejects({"snapshot_sec": 3}, "snapshot_sec")
        self.assert_rejects({"snapshot_sec": True}, "snapshot_sec")
        self.assert_rejects({"reg_timeout_sec": 4}, "reg_timeout_sec")
        self.assert_rejects({"venue": "NXT"}, "venue")

    def test_numeric_ranges(self):
        self.assert_rejects({"reg_retry": 0}, "reg_retry")
        self.assert_rejects({"max_stale_sec": 0}, "max_stale_sec")
        self.assert_rejects({"symbols": {"max": 0}}, "symbols.max")
        self.assert_rejects({"symbols": {"max_lookback_days": -1}}, "symbols.max_lookback_days")
        self.assert_rejects({"enabled": "yes"}, "enabled")
        self.assertEqual(self.load_with({"symbols": {"max_lookback_days": 0}})["symbols"]["max_lookback_days"], 0)

    def test_window_times(self):
        self.assert_rejects({"windows": {"morning": {"start": "8:45:00"}}}, "windows.morning.start")
        self.assert_rejects({"windows": {"morning": {"start": "10:00:00"}}}, "windows.morning")
        self.assert_rejects({"windows": {"morning": {"end": "15:10:00"}}}, "windows")

    def test_static_codes_are_alphanumeric_six_and_deduped(self):
        cfg = self.load_with({"symbols": {"static": ["005930", "0197V0", "005930"]}})
        self.assertEqual(cfg["symbols"]["static"], ["005930", "0197V0"])
        self.assert_rejects({"symbols": {"static": ["A005930"]}}, "symbols.static")
        self.assert_rejects({"symbols": {"static": "005930"}}, "symbols.static")

    def test_broker_url_must_be_local(self):
        self.assert_rejects({"broker_url": "http://192.168.0.5:8001"}, "broker_url")
        self.assertEqual(self.load_with({"broker_url": "http://localhost:8001"})["broker_url"],
                         "http://localhost:8001")

    def test_scoring_path_absolute_is_kept(self):
        target = Path(self.tmp.name) / "scores.json"
        self.assertEqual(self.load_with({"scoring_result_path": str(target)})["scoring_result_path"], target)

    def test_repo_config_file_is_valid(self):
        load()   # etl/scripts/orderbook_recorder.json


if __name__ == "__main__":
    unittest.main()
