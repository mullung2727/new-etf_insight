"""youtube_channels.py (채널 config 로더·parse/resolve) 단위 테스트.

스펙: docs/youtube_tech.md §3.7 A1~A8
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from scripts import run_youtube_channels as ryc
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


RY_CH_A = "UC" + "A" * 22
RY_CH_B = "UC" + "B" * 22


class RunAllRetryTest(unittest.TestCase):
    """run_all 끝-1회 재시도. process_channel만 mock이라 네트워크·DB 없이 돈다."""

    def test_failed_then_success_is_recovered(self):
        attempts: dict[str, int] = {}

        def fake(con, ch, date_kst, **kwargs):
            attempts[ch] = attempts.get(ch, 0) + 1
            if ch == RY_CH_A and attempts[ch] == 1:
                raise RuntimeError("HTTP Error 500: Internal Server Error")
            return {"channel_id": ch}

        sleep = MagicMock()
        with patch("scripts.run_youtube_channels.process_channel", side_effect=fake):
            results, errors = ryc.run_all(
                None, {RY_CH_A: {}, RY_CH_B: {}}, "2026-10-18", _sleep=sleep
            )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        sleep.assert_called_once_with(60)

    def test_second_failure_keeps_the_new_message(self):
        attempts: dict[str, int] = {}

        def fake(con, ch, date_kst, **kwargs):
            attempts[ch] = attempts.get(ch, 0) + 1
            raise RuntimeError(f"boom attempt {attempts[ch]}")

        sleep = MagicMock()
        with patch("scripts.run_youtube_channels.process_channel", side_effect=fake):
            results, errors = ryc.run_all(
                None, {RY_CH_A: {}}, "2026-10-18", _sleep=sleep
            )
        self.assertEqual(results, [])
        self.assertEqual(errors, [(RY_CH_A, "boom attempt 2")])
        sleep.assert_called_once_with(60)

    def test_all_success_sleeps_never(self):
        sleep = MagicMock()
        with patch(
            "scripts.run_youtube_channels.process_channel",
            side_effect=lambda con, ch, date_kst, **kwargs: {"channel_id": ch},
        ):
            results, errors = ryc.run_all(
                None, {RY_CH_A: {}, RY_CH_B: {}}, "2026-10-18", _sleep=sleep
            )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        sleep.assert_not_called()

    def test_channel_not_in_config_is_not_retried(self):
        missing = "UC" + "Z" * 22
        sleep = MagicMock()
        with patch(
            "scripts.run_youtube_channels.process_channel",
            side_effect=AssertionError("must not be called"),
        ) as mocked:
            results, errors = ryc.run_all(
                None, {RY_CH_A: {}}, "2026-10-18", only=missing, _sleep=sleep
            )
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("channel not in config", errors[0][1])
        mocked.assert_not_called()
        sleep.assert_not_called()

    def test_permanent_http_error_is_not_retried(self):
        sleep = MagicMock()
        with patch(
            "scripts.run_youtube_channels.process_channel",
            side_effect=RuntimeError("HTTP Error 403: Forbidden"),
        ) as mocked:
            results, errors = ryc.run_all(
                None, {RY_CH_A: {}}, "2026-10-18", _sleep=sleep
            )
        self.assertEqual(results, [])
        self.assertEqual(errors, [(RY_CH_A, "HTTP Error 403: Forbidden")])
        self.assertEqual(mocked.call_count, 1)
        sleep.assert_not_called()

    def test_http_404_is_retried(self):
        attempts: dict[str, int] = {}

        def fake(con, ch, date_kst, **kwargs):
            attempts[ch] = attempts.get(ch, 0) + 1
            if attempts[ch] == 1:
                raise RuntimeError("HTTP Error 404: Not Found")
            return {"channel_id": ch}

        sleep = MagicMock()
        with patch("scripts.run_youtube_channels.process_channel", side_effect=fake):
            results, errors = ryc.run_all(
                None, {RY_CH_A: {}}, "2026-10-18", _sleep=sleep
            )
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        sleep.assert_called_once_with(60)


if __name__ == "__main__":
    unittest.main()
