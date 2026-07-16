"""telegram_channels.py (채널 config 로더) 단위 테스트.

검증 항목:
  1. load_all_channels: json 전체를 dict로 로드
  2. load_channel_config: 채널 하나 반환, 없는 채널은 KeyError
  3. 실제 telegram_channels.json: 채널은 순수 텍스트 수집(attachments 없음 — 리포트는 네이버 배치)
  4. load_discovery_channels: feed_role=discovery_source 채널만 필터
"""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.telegram_channels import (
    DEFAULT_CONFIG,
    load_all_channels,
    load_channel_config,
    load_discovery_channels,
)


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "channels.json"
        self.cfg.write_text(json.dumps({
            "chanA": {"source_url": "https://t.me/s/chanA", "attachments": {"link_pattern": "x"}},
            "chanB": {"source_url": "https://t.me/s/chanB"},
            "chanC": {"source_url": "https://t.me/s/chanC", "feed_role": "discovery_source"},
        }), encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_all_channels(self):
        data = load_all_channels(self.cfg)
        self.assertEqual(set(data), {"chanA", "chanB", "chanC"})

    def test_load_channel_config_found(self):
        cfg = load_channel_config("chanA", self.cfg)
        self.assertEqual(cfg["source_url"], "https://t.me/s/chanA")
        self.assertIn("attachments", cfg)

    def test_load_channel_config_missing_raises(self):
        with self.assertRaises(KeyError):
            load_channel_config("nope", self.cfg)

    def test_load_discovery_channels(self):
        data = load_discovery_channels(self.cfg)
        self.assertEqual(set(data), {"chanC"})


class RealConfigTest(unittest.TestCase):
    def test_channels_are_collect_only(self):
        # 리포트 PDF는 네이버 별도 배치 → 채널 config엔 attachments 없음(순수 수집)
        for ch in ("companyreport", "butler_works"):
            cfg = load_channel_config(ch)
            self.assertTrue(cfg["source_url"].startswith("https://t.me/s/"))
            self.assertNotIn("attachments", cfg)

    def test_default_config_path_points_to_scripts(self):
        self.assertTrue(DEFAULT_CONFIG.name == "telegram_channels.json")
        self.assertTrue(DEFAULT_CONFIG.exists())

    def test_real_discovery_channels(self):
        # 채널명 하드코딩 금지(웹UI 추가마다 깨짐). 불변식만 검사:
        # discovery 채널은 비어있지 않고, 전부 config에 feed_role=discovery_source 로 존재.
        discovery = load_discovery_channels()
        all_channels = load_all_channels()
        self.assertTrue(discovery, "discovery 채널이 하나도 없음 — config 확인")
        for ch in discovery:
            self.assertEqual(all_channels[ch].get("feed_role"), "discovery_source")


if __name__ == "__main__":
    unittest.main()
