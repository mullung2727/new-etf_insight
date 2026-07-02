"""notify 테스트 — send_discord/send_telegram best-effort + notify() 채널 디스패치."""
import unittest
from unittest.mock import MagicMock, patch

from scripts.notify import notify, send_discord, send_telegram


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


class TestSendTelegram(unittest.TestCase):
    def test_skips_when_creds_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(send_telegram("hi"))

    def test_posts_to_bot_api(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "42"}, clear=True), \
             patch("scripts.notify.requests.post", return_value=resp) as post:
            ok = send_telegram("hello")
        self.assertTrue(ok)
        self.assertIn("botT/sendMessage", post.call_args[0][0])
        self.assertEqual(post.call_args[1]["json"]["chat_id"], "42")


class TestNotifyDispatch(unittest.TestCase):
    def test_routes_by_env_channel(self):
        with patch.dict("os.environ", {"NOTIFY_CHANNEL": "telegram"}, clear=True), \
             patch("scripts.notify.send_telegram", return_value=True) as tg, \
             patch("scripts.notify.send_discord", return_value=True) as dc:
            notify("m")
        tg.assert_called_once()
        dc.assert_not_called()

    def test_arg_overrides_env(self):
        with patch.dict("os.environ", {"NOTIFY_CHANNEL": "telegram"}, clear=True), \
             patch("scripts.notify.send_discord", return_value=True) as dc:
            notify("m", channel="discord")
        dc.assert_called_once()

    def test_unknown_channel_falls_back_to_discord(self):
        with patch.dict("os.environ", {"NOTIFY_CHANNEL": "bogus"}, clear=True), \
             patch("scripts.notify.send_discord", return_value=True) as dc:
            notify("m")
        dc.assert_called_once()

    def test_default_is_discord(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch("scripts.notify.send_discord", return_value=True) as dc:
            notify("m")
        dc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
