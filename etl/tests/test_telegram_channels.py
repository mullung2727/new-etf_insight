"""telegram_channels.py (채널 config 로더) 단위 테스트.

검증 항목:
  1. load_all_channels: json 전체를 dict로 로드
  2. load_channel_config: 채널 하나 반환, 없는 채널은 KeyError
  3. 실제 telegram_channels.json: companyreport에 attachments, butler_works엔 없음
"""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.telegram_channels import (
    DEFAULT_CONFIG,
    load_all_channels,
    load_channel_config,
)


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "channels.json"
        self.cfg.write_text(json.dumps({
            "chanA": {"source_url": "https://t.me/s/chanA", "attachments": {"link_pattern": "x"}},
            "chanB": {"source_url": "https://t.me/s/chanB"},
        }), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_all_channels(self):
        data = load_all_channels(self.cfg)
        self.assertEqual(set(data), {"chanA", "chanB"})

    def test_load_channel_config_found(self):
        cfg = load_channel_config("chanA", self.cfg)
        self.assertEqual(cfg["source_url"], "https://t.me/s/chanA")
        self.assertIn("attachments", cfg)

    def test_load_channel_config_missing_raises(self):
        with self.assertRaises(KeyError):
            load_channel_config("nope", self.cfg)


class RealConfigTest(unittest.TestCase):
    def test_companyreport_has_attachments(self):
        cfg = load_channel_config("companyreport")
        att = cfg["attachments"]
        self.assertIn("link_pattern", att)
        self.assertIn("ticker_pattern", att)
        self.assertEqual(att["out_subdir"], "stock_reports")

    def test_butler_works_has_no_attachments(self):
        cfg = load_channel_config("butler_works")
        self.assertNotIn("attachments", cfg)

    def test_default_config_path_points_to_scripts(self):
        self.assertTrue(DEFAULT_CONFIG.name == "telegram_channels.json")
        self.assertTrue(DEFAULT_CONFIG.exists())


if __name__ == "__main__":
    unittest.main()
