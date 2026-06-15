"""notify.send_discord 테스트 — best-effort 동작 검증."""
import unittest
from unittest.mock import MagicMock, patch

from scripts.notify import send_discord


class TestSendDiscord(unittest.TestCase):
    def test_skips_when_no_url(self):
        # 명시적 빈 문자열 → 미설정 취급
        self.assertFalse(send_discord("hi", webhook_url=""))

    def test_skips_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(send_discord("hi"))

    def test_posts_when_url_given(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        with patch("scripts.notify.requests.post", return_value=resp) as post:
            ok = send_discord("hello", webhook_url="https://discord/webhook")
        self.assertTrue(ok)
        post.assert_called_once()
        _, kwargs = post.call_args
        self.assertEqual(kwargs["json"]["content"], "hello")

    def test_uses_env_url(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://env/hook"}, clear=True), \
             patch("scripts.notify.requests.post", return_value=resp) as post:
            send_discord("x")
        self.assertEqual(post.call_args[0][0], "https://env/hook")

    def test_truncates_long_message(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        with patch("scripts.notify.requests.post", return_value=resp) as post:
            send_discord("A" * 5000, webhook_url="https://h")
        self.assertLessEqual(len(post.call_args[1]["json"]["content"]), 1900)

    def test_swallows_post_exception(self):
        with patch("scripts.notify.requests.post", side_effect=RuntimeError("boom")):
            self.assertFalse(send_discord("x", webhook_url="https://h"))


if __name__ == "__main__":
    unittest.main()
