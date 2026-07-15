"""youtube_channels.py (채널 config 로더·parse/resolve) 단위 테스트.

스펙: docs/youtube_tech.md §3.7 A1~A8
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

from scripts.youtube_channels import (
    DEFAULT_CONFIG,
    load_all_channels,
    load_auto_summary_channels,
    load_channel_config,
    load_discovery_channels,
    parse_channel_id,
    resolve_channel_id,
)

# tech §3.2 공용 test vector
REAL_UC = "UCeN2YeJcBCRJoXgzF_OU3qw"


class ParseChannelIdTest(unittest.TestCase):
    """A1~A4: 네트워크 없이 UC /channel/UC 만."""

    def test_a1_bare_uc24(self):
        self.assertEqual(parse_channel_id(REAL_UC), REAL_UC)

    def test_a2_channel_url(self):
        url = f"https://www.youtube.com/channel/{REAL_UC}"
        self.assertEqual(parse_channel_id(url), REAL_UC)

    def test_a3_handle_url_local_raises(self):
        with self.assertRaises(ValueError):
            parse_channel_id("https://www.youtube.com/@unrealtech")

    def test_a4_watch_url_raises(self):
        with self.assertRaises(ValueError):
            parse_channel_id("https://www.youtube.com/watch?v=F-UgZE6QZiQ")

    def test_a4_shorts_url_raises(self):
        with self.assertRaises(ValueError):
            parse_channel_id("https://www.youtube.com/shorts/F-UgZE6QZiQ")

    def test_a4_youtu_be_raises(self):
        with self.assertRaises(ValueError):
            parse_channel_id("https://youtu.be/F-UgZE6QZiQ")

    def test_a4_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_channel_id("")


class ResolveChannelIdTest(unittest.TestCase):
    """A5/A5b/A5c: fixture HTML + mock opener (live 네트워크 없음)."""

    def test_a5_canonical_uc(self):
        html = f"""<!doctype html><html><head>
<link rel="canonical" href="https://www.youtube.com/channel/{REAL_UC}">
</head><body></body></html>"""

        def opener(req, timeout=40):
            return io.BytesIO(html.encode("utf-8"))

        self.assertEqual(
            resolve_channel_id("https://www.youtube.com/@unrealtech", opener=opener),
            REAL_UC,
        )

    def test_a5b_canonical_over_decoy_channel_id(self):
        decoy = "UCdecoyChannelId_zzzzzzz"  # UC + 22, 형식만 맞음
        html = f"""<!doctype html><html><head>
<link rel="canonical" href="https://www.youtube.com/channel/{REAL_UC}">
</head><body>
<script>"channelId":"{decoy}"</script>
</body></html>"""

        def opener(req, timeout=40):
            return io.BytesIO(html.encode("utf-8"))

        self.assertEqual(
            resolve_channel_id("@unrealtech", opener=opener),
            REAL_UC,
        )
        self.assertNotEqual(
            resolve_channel_id("@unrealtech", opener=opener),
            decoy,
        )

    def test_a5c_no_channel_signals_raises(self):
        html = "<!doctype html><html><head><title>nope</title></head><body></body></html>"

        def opener(req, timeout=40):
            return io.BytesIO(html.encode("utf-8"))

        with self.assertRaises(ValueError):
            resolve_channel_id("https://www.youtube.com/@nobody", opener=opener)

    def test_resolve_parse_first_no_network(self):
        """parse 성공이면 opener 호출 없이 그대로."""
        calls = []

        def opener(req, timeout=40):
            calls.append(req)
            raise URLError("should not be called")

        self.assertEqual(resolve_channel_id(REAL_UC, opener=opener), REAL_UC)
        self.assertEqual(calls, [])


class LoaderTest(unittest.TestCase):
    """A6~A7: tempfile json."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = self.tmp / "youtube_channels.json"
        self.cfg.write_text(
            json.dumps(
                {
                    REAL_UC: {
                        "source_url": f"https://www.youtube.com/channel/{REAL_UC}",
                        "handle": "@unrealtech",
                        "label": "unreal",
                        "feed_role": "discovery_source",
                        "summary_mode": "auto",
                    },
                    "UCcollectOnlyChannel_zz1": {
                        "source_url": "https://www.youtube.com/channel/UCcollectOnlyChannel_zz1",
                        "label": "collect-only",
                        "summary_mode": "manual",
                    },
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a6_load_all(self):
        data = load_all_channels(self.cfg)
        self.assertEqual(set(data), {REAL_UC, "UCcollectOnlyChannel_zz1"})

    def test_a6_load_channel_found(self):
        cfg = load_channel_config(REAL_UC, self.cfg)
        self.assertEqual(cfg["handle"], "@unrealtech")

    def test_a6_load_channel_missing_raises(self):
        with self.assertRaises(KeyError):
            load_channel_config("UCnotexistChannelIdzzzzz", self.cfg)

    def test_a6_missing_file_returns_empty(self):
        missing = self.tmp / "nope.json"
        self.assertEqual(load_all_channels(missing), {})

    def test_a7_load_discovery_only(self):
        data = load_discovery_channels(self.cfg)
        self.assertEqual(set(data), {REAL_UC})

    def test_load_auto_summary_only(self):
        data = load_auto_summary_channels(self.cfg)
        self.assertEqual(set(data), {REAL_UC})
        # 미기입 = manual
        self.cfg.write_text(
            json.dumps(
                {
                    REAL_UC: {
                        "source_url": f"https://www.youtube.com/channel/{REAL_UC}",
                    }
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(load_auto_summary_channels(self.cfg), {})


class DefaultConfigTest(unittest.TestCase):
    """A8: DEFAULT_CONFIG 파일명·존재."""

    def test_a8_default_config_name_and_exists(self):
        self.assertEqual(DEFAULT_CONFIG.name, "youtube_channels.json")
        self.assertTrue(DEFAULT_CONFIG.exists())
        # 실채널이 들어 있어도 dict 이면 OK (빈 객체 강제 아님)
        data = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)


if __name__ == "__main__":
    unittest.main()
