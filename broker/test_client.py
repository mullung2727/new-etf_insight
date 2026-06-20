"""client.request — 429 재시도 백오프 + 전역 throttle 검증.

broker는 pytest 없음 → stdlib unittest.
    .venv/Scripts/python.exe -m unittest test_client

httpx.post / get_token / _config / time을 mock해 HTTP·실대기 없이 제어흐름만 본다.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import kiwoom.client as client
from kiwoom.client import KiwoomError


class _Cfg:
    rest_host = "https://api.test"


class FakeResp:
    def __init__(self, status: int, json_data=None, headers=None, text: str = ""):
        self.status_code = status
        self._json = json_data if json_data is not None else {"return_code": 0}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        client._last_call = 0.0  # 전역 throttle 상태 격리
        self.sleeps: list[float] = []
        self._p = [
            patch.object(client, "_config", return_value=_Cfg()),
            patch.object(client, "get_token", return_value="tok"),
            patch.object(client.time, "sleep", side_effect=self.sleeps.append),
        ]
        for p in self._p:
            p.start()

    def tearDown(self) -> None:
        for p in self._p:
            p.stop()


class TestRetryOn429(_Base):
    def test_retries_then_succeeds(self):
        seq = [FakeResp(429), FakeResp(429), FakeResp(200, {"return_code": 0, "ok": 1})]
        calls = {"n": 0}

        def post(*a, **k):
            r = seq[calls["n"]]
            calls["n"] += 1
            return r

        with patch.object(client.httpx, "post", side_effect=post):
            res = client.request("ka10001", "/ep", {})
        self.assertEqual(res.data["ok"], 1)
        self.assertEqual(calls["n"], 3)                      # 2회 재시도 후 성공
        self.assertIn(0.2, self.sleeps)                      # 1차 백오프
        self.assertIn(0.4, self.sleeps)                      # 2차 백오프(지수)

    def test_honors_retry_after_header(self):
        seq = [FakeResp(429, headers={"retry-after": "1.5"}),
               FakeResp(200, {"return_code": 0})]
        calls = {"n": 0}

        def post(*a, **k):
            r = seq[calls["n"]]
            calls["n"] += 1
            return r

        with patch.object(client.httpx, "post", side_effect=post):
            client.request("ka10001", "/ep", {})
        self.assertIn(1.5, self.sleeps)                      # 헤더값 우선

    def test_exhausts_retries_then_raises(self):
        calls = {"n": 0}

        def post(*a, **k):
            calls["n"] += 1
            return FakeResp(429, text="rate limit")

        with patch.object(client.httpx, "post", side_effect=post):
            with self.assertRaises(KiwoomError) as ctx:
                client.request("ka10001", "/ep", {})
        self.assertIn("HTTP 429", str(ctx.exception))
        self.assertEqual(calls["n"], client._MAX_RETRIES + 1)  # 최초1 + 재시도N


class TestThrottle(_Base):
    def test_enforces_min_interval(self):
        # monotonic 고정 → 2번째 호출은 간격 0 → MIN_INTERVAL 만큼 sleep
        with patch.object(client.time, "monotonic", return_value=1000.0), \
             patch.object(client.httpx, "post", return_value=FakeResp(200)):
            client.request("ka10001", "/ep", {})   # 1st: 간격 무한대 → sleep 없음
            client.request("ka10001", "/ep", {})   # 2nd: 간격 0 → throttle
        self.assertIn(client._MIN_INTERVAL, self.sleeps)

    def test_first_call_no_throttle(self):
        with patch.object(client.time, "monotonic", return_value=5000.0), \
             patch.object(client.httpx, "post", return_value=FakeResp(200)):
            client.request("ka10001", "/ep", {})
        self.assertEqual(self.sleeps, [])          # 첫 콜은 대기 없음


if __name__ == "__main__":
    unittest.main()
