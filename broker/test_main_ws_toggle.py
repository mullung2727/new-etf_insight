"""broker lifespan 웹소켓 토글 테스트 (네트워크 없이).

Usage:
    .venv/Scripts/python.exe -m unittest test_main_ws_toggle
"""
import asyncio
import os
import unittest
from unittest import mock

import main


async def _noop():
    return None


class WsToggleCase(unittest.TestCase):
    def _enter_exit(self):
        mgr = mock.Mock()
        mgr.start = mock.AsyncMock()
        mgr.stop = mock.AsyncMock()

        async def _run():
            async with main.lifespan(mock.Mock()):
                pass

        with mock.patch.object(main, "_ws_manager", mgr), \
                mock.patch.object(main, "_fill_sync_loop", _noop), \
                mock.patch.object(main, "_idea_alert_loop", _noop):
            asyncio.run(_run())
        return mgr

    def _env_without_ws_flag(self):
        env = dict(os.environ)
        env.pop("KIWOOM_WS_ENABLED", None)
        return env

    def test_default_starts_ws(self):
        with mock.patch.dict(os.environ, self._env_without_ws_flag(), clear=True):
            mgr = self._enter_exit()
        mgr.start.assert_called_once()
        mgr.stop.assert_called_once()

    def test_ws_disabled(self):
        with mock.patch.dict(os.environ, {"KIWOOM_WS_ENABLED": "0"}):
            mgr = self._enter_exit()
        mgr.start.assert_not_called()
        mgr.stop.assert_not_called()

    def test_ws_enabled_explicit(self):
        with mock.patch.dict(os.environ, {"KIWOOM_WS_ENABLED": "1"}):
            mgr = self._enter_exit()
        mgr.start.assert_called_once()
        mgr.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
