"""notify 테스트 — send_discord/send_telegram best-effort + notify() 채널 디스패치."""
import unittest
from unittest.mock import MagicMock, patch

import requests

from scripts.notify import notify, send_discord, send_telegram, send_telegram_report


def _http_error(status: int) -> requests.exceptions.HTTPError:
    """status_code 실린 HTTPError (raise_for_status가 던지는 형태)."""
    resp = MagicMock()
    resp.status_code = status
    return requests.exceptions.HTTPError(response=resp)


def _ok_resp() -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    return resp


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

    def test_retries_on_5xx_then_succeeds(self):
        resp_5xx = MagicMock()
        resp_5xx.raise_for_status.side_effect = _http_error(503)
        sleeps: list[float] = []
        with patch("scripts.notify.requests.post", side_effect=[resp_5xx, _ok_resp()]) as post:
            ok = send_discord("x", webhook_url="https://h", _sleep=sleeps.append)
        self.assertTrue(ok)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleeps, [2.0])

    def test_retries_on_network_error_then_gives_up(self):
        sleeps: list[float] = []
        with patch(
            "scripts.notify.requests.post",
            side_effect=requests.exceptions.ConnectionError("down"),
        ) as post:
            ok = send_discord("x", webhook_url="https://h", _sleep=sleeps.append)
        self.assertFalse(ok)
        self.assertEqual(post.call_count, 3)  # 총 3회 후 포기
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_does_not_retry_on_read_timeout(self):
        # ReadTimeout = 서버가 이미 처리했을 수 있음 → 재시도 시 중복 전송 → 제외
        sleeps: list[float] = []
        with patch(
            "scripts.notify.requests.post",
            side_effect=requests.exceptions.ReadTimeout("slow"),
        ) as post:
            ok = send_discord("x", webhook_url="https://h", _sleep=sleeps.append)
        self.assertFalse(ok)
        self.assertEqual(post.call_count, 1)  # 재시도 없음 (중복 방지)
        self.assertEqual(sleeps, [])

    def test_does_not_retry_on_4xx(self):
        resp_4xx = MagicMock()
        resp_4xx.raise_for_status.side_effect = _http_error(404)  # 잘못된 웹훅 = 영구
        sleeps: list[float] = []
        with patch("scripts.notify.requests.post", return_value=resp_4xx) as post:
            ok = send_discord("x", webhook_url="https://h", _sleep=sleeps.append)
        self.assertFalse(ok)
        self.assertEqual(post.call_count, 1)  # 재시도 없음
        self.assertEqual(sleeps, [])


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


class TestSendTelegramReport(unittest.TestCase):
    """텔레그램 배치 전용 Discord 채널(별도 웹훅). 기존 4종 discord 웹훅과 분리."""

    def test_skips_when_webhook_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(send_telegram_report("hi"))

    def test_posts_to_report_webhook(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        with patch.dict(
            "os.environ",
            {"TELEGRAM_REPORT_TO_DISCORD_WEBHOOK_URL": "https://report/hook"},
            clear=True,
        ), patch("scripts.notify.requests.post", return_value=resp) as post:
            ok = send_telegram_report("hello")
        self.assertTrue(ok)
        self.assertEqual(post.call_args[0][0], "https://report/hook")

    def test_ignores_default_discord_webhook(self):
        # DISCORD_WEBHOOK_URL(기존 4종용)만 있고 리포트 웹훅 없으면 skip
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://main/hook"}, clear=True):
            self.assertFalse(send_telegram_report("hi"))


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

    def test_routes_telegram_report_channel(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch("scripts.notify.send_telegram_report", return_value=True) as tr, \
             patch("scripts.notify.send_discord", return_value=True) as dc:
            notify("m", channel="telegram_report")
        tr.assert_called_once()
        dc.assert_not_called()

    def test_default_is_discord(self):
        with patch.dict("os.environ", {}, clear=True), \
             patch("scripts.notify.send_discord", return_value=True) as dc:
            notify("m")
        dc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
