"""알림 차단 안전장치 회귀 테스트 — 테스트 실행이 실제 웹훅을 때리면 안 된다.

과거 차단이 `conftest.py`(pytest 전용)에만 있었고 이 저장소는 `unittest` 로 돌려서
실행 1회당 실제 Discord 전송이 69건 나갔다. 차단은 `tests/__init__.py` 가 담당한다.
"""
from __future__ import annotations

import os
import unittest

import requests

import tests as tests_pkg


class TestNotifyGuard(unittest.TestCase):
    def test_notify_env_is_blank_at_package_import(self):
        """1차 방어: 알림 env 를 빈 값으로 고정하면 sender 가 전송을 스킵한다.

        단독 실행 시점만 검증한다. 전체 스위트에서는 다른 테스트의
        `patch.dict(os.environ, ...)` 복원이 실제 URL 을 되살려 이 값이 뒤집힌다 —
        그래서 env 는 보장이 아니라 최선노력이고, 보장은 아래 HTTP 백스톱이 한다.
        """
        tests_pkg.block_real_notifications()
        for key in tests_pkg.NOTIFY_ENV:
            self.assertEqual(os.environ.get(key, ""), "", f"{key} 가 비어있지 않음")

    def test_discord_request_never_leaves_the_machine(self):
        """env 가 뚫려도 HTTP 백스톱이 막는다 — 네트워크 없이 204 를 돌려준다."""
        before = tests_pkg.blocked_count
        resp = requests.post(
            "https://discord.com/api/webhooks/should-never-be-sent",
            json={"content": "guard test"},
            timeout=5,
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(tests_pkg.blocked_count, before + 1)

    def test_other_hosts_are_not_blocked(self):
        """알림 차단은 알림 호스트 한정 — broker·KRX 조회까지 막으면 안 된다."""
        self.assertNotIn("localhost", tests_pkg.BLOCKED_HOSTS)
        before = tests_pkg.blocked_count
        with self.assertRaises(requests.exceptions.RequestException):
            # 아무도 안 듣는 포트 — 백스톱이 가로챘다면 예외 없이 204 가 온다.
            requests.get("http://127.0.0.1:9/never", timeout=2)
        self.assertEqual(tests_pkg.blocked_count, before)

    def test_local_writes_are_blocked(self):
        """로컬 서버 쓰기는 즉시 실패시킨다 — 운영 노트 DB 오염 재발 방지."""
        for method in ("post", "patch", "put", "delete"):
            with self.subTest(method=method):
                with self.assertRaises(AssertionError):
                    getattr(requests, method)("http://localhost:8001/notes", timeout=2)

    def test_external_writes_still_pass(self):
        """외부 호스트 쓰기는 통과 — KRX OpenAPI 는 조회를 POST 로 한다."""
        with self.assertRaises(requests.exceptions.RequestException):
            requests.post("http://198.51.100.1:9/never", timeout=2)


if __name__ == "__main__":
    unittest.main()
