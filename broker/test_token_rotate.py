"""US 토큰 회전(auth.rotate_token + /us/token/rotate) 검증.

broker는 pytest가 없으므로 stdlib unittest + FastAPI TestClient로 돌린다.
    .venv/Scripts/python.exe -m unittest test_token_rotate

키움 호출은 auth.httpx.post를 mock해 HTTP 없이 폐기→발급 순서·캐시만 검증한다.
라우터는 최소 앱에 us_router만 포함한다(test_us_orders.py와 같은 패턴).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiwoom import auth
from routers import us as us_router

_KST = timezone(timedelta(hours=9))
_NEW_EXP = datetime(2099, 12, 31, 23, 59, 59, tzinfo=_KST).timestamp()


def _cfg(tmp: Path) -> SimpleNamespace:
    return SimpleNamespace(env="real", rest_host="https://x", appkey="k",
                           secretkey="s", token_cache_path=tmp / "c.json")


def _resp(payload: dict) -> Mock:
    r = Mock()
    r.json.return_value = payload
    return r


class _RotateBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        orig_token, orig_exp = auth._token, auth._expires_at
        self.addCleanup(setattr, auth, "_token", orig_token)
        self.addCleanup(setattr, auth, "_expires_at", orig_exp)

    def _rotate(self, mem_token, mem_exp, cache, post_fn):
        auth._token, auth._expires_at = mem_token, mem_exp
        if cache is not None:
            (self.tmp / "c.json").write_text(json.dumps(cache), encoding="utf-8")
        calls: list = []

        def _post(url, **kw):
            calls.append((url, kw.get("json")))
            return post_fn(url)

        with patch.object(auth, "_config", return_value=_cfg(self.tmp)), \
                patch.object(auth.httpx, "post", side_effect=_post):
            got = auth.rotate_token()
        return got, calls


class TestRotateToken(_RotateBase):
    """auth.rotate_token — 폐기 후 재발급, 순서·캐시·폐기 실패 계속."""

    def _ok_post(self, url):
        if url.endswith("/oauth2/revoke"):
            return _resp({"return_code": 0})
        return _resp({"token": "NEW", "expires_dt": "20991231235959"})

    def test_revoke_then_issue(self):
        got, calls = self._rotate("OLD", 10**12, None, self._ok_post)
        self.assertEqual([u for u, _ in calls],
                         ["https://x/oauth2/revoke", "https://x/oauth2/token"])
        self.assertEqual(calls[0][1]["token"], "OLD")
        self.assertEqual(got, _NEW_EXP)
        saved = json.loads((self.tmp / "c.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["token"], "NEW")
        self.assertEqual(auth._token, "NEW")

    def test_revoke_error_still_issues(self):
        def _post(url):
            if url.endswith("/oauth2/revoke"):
                raise RuntimeError("revoke down")
            return _resp({"token": "NEW", "expires_dt": "20991231235959"})

        _, calls = self._rotate("OLD", 10**12, None, _post)
        self.assertEqual([u for u, _ in calls],
                         ["https://x/oauth2/revoke", "https://x/oauth2/token"])
        self.assertEqual(auth._token, "NEW")

    def test_cache_token_when_memory_empty(self):
        cache = {"env": "real", "token": "CACHED", "expires_at": 1000}  # 만료됐어도 폐기 대상
        _, calls = self._rotate(None, 0.0, cache, self._ok_post)
        self.assertEqual(calls[0][0], "https://x/oauth2/revoke")
        self.assertEqual(calls[0][1]["token"], "CACHED")
        self.assertEqual(auth._token, "NEW")


class TestRotateRoute(unittest.TestCase):
    """POST /us/token/rotate — US 프로파일 게이트·502 매핑."""

    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(us_router.router)
        self.client = TestClient(app)

    def test_us_profile_rotates(self):
        with patch("routers.us.load_config",
                   return_value=SimpleNamespace(profile="US")), \
                patch("kiwoom.auth.rotate_token", return_value=123.0) as rt:
            r = self.client.post("/us/token/rotate")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"expires_at": 123.0})
        rt.assert_called_once_with()

    def test_non_us_rejected(self):
        with patch("routers.us.load_config",
                   return_value=SimpleNamespace(profile="")), \
                patch("kiwoom.auth.rotate_token", return_value=123.0) as rt:
            r = self.client.post("/us/token/rotate")
        self.assertEqual(r.status_code, 422)
        rt.assert_not_called()

    def test_rotate_error_502(self):
        with patch("routers.us.load_config",
                   return_value=SimpleNamespace(profile="US")), \
                patch("kiwoom.auth.rotate_token", side_effect=RuntimeError("boom")):
            r = self.client.post("/us/token/rotate")
        self.assertEqual(r.status_code, 502)


class TestMcpExcluded(unittest.TestCase):
    def test_excluded_in_main(self):
        text = (Path(__file__).resolve().parent / "main.py").read_text(encoding="utf-8")
        line = next(line for line in text.splitlines() if "exclude_operations" in line)
        self.assertIn('"rotate_us_token"', line)


if __name__ == "__main__":
    unittest.main()
