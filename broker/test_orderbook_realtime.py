"""호가 수집기 2단계 — 0D 구독 제어·ACK 대기·재접속 복구 (SPEC §5, T04/T22/T23).

    $env:PYTHONPATH="broker"; .\\broker\\.venv\\Scripts\\python.exe -m unittest discover -s broker -p "test_orderbook_*.py"

실제 소켓 대신 send/close 만 기록하는 FakeWS 를 넣고, 수신 루프가 할 ACK 전달은
``_on_ack`` 를 직접 불러 흉내낸다.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kiwoom.ws import manager as mgr_mod
from kiwoom.ws.manager import ControlError, KiwoomWSManager
from routers import realtime

OK = {"trnm": "REG", "return_code": 0, "return_msg": ""}


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def close(self):
        self.closed = True


async def ack_nth(m: KiwoomWSManager, ws: FakeWS, n: int, ack: dict) -> None:
    """n 번째 프레임이 나간 뒤 수신 루프처럼 ACK 를 넣는다."""
    while len(ws.sent) < n:
        await asyncio.sleep(0)
    m._on_ack(ack)


def ready_manager(codes=()) -> tuple[KiwoomWSManager, FakeWS]:
    m, ws = KiwoomWSManager(), FakeWS()
    m._ws, m._ready = ws, True
    m._orderbook = set(codes)
    return m, ws


class TestFrames(unittest.IsolatedAsyncioTestCase):
    async def test_t04_reg_uses_group_2_refresh_1(self):
        m, ws = ready_manager()
        task = asyncio.create_task(m.add_orderbook(["005930", "000660"]))
        await ack_nth(m, ws, 1, OK)
        status = await task
        self.assertEqual(ws.sent, [{"trnm": "REG", "grp_no": "2", "refresh": "1", "data": [
            {"item": ["KRX:000660", "KRX:005930"], "type": ["0D"]}]}])
        self.assertEqual(status, {"connected": True, "venue": "KRX", "codes": ["000660", "005930"]})

    async def test_t04_post_is_additive_and_idempotent(self):
        m, ws = ready_manager(["005930"])
        self.assertEqual((await m.add_orderbook(["005930"]))["codes"], ["005930"])
        self.assertEqual(ws.sent, [])                          # 기존 코드만 → 재등록 안 함
        task = asyncio.create_task(m.add_orderbook(["005930", "0197V0"]))
        await ack_nth(m, ws, 1, OK)
        self.assertEqual((await task)["codes"], ["005930", "0197V0"])
        self.assertEqual(ws.sent[0]["data"][0]["item"], ["KRX:0197V0"])   # 신규만

    async def test_t04_remove_lists_all_codes_without_refresh(self):
        m, ws = ready_manager(["005930", "000660"])
        task = asyncio.create_task(m.remove_orderbook())
        await ack_nth(m, ws, 1, {"trnm": "REMOVE", "return_code": "0"})
        self.assertEqual((await task)["codes"], [])
        self.assertEqual(ws.sent, [{"trnm": "REMOVE", "grp_no": "2", "data": [
            {"item": ["KRX:000660", "KRX:005930"], "type": ["0D"]}]}])

    async def test_remove_with_empty_list_sends_nothing(self):
        m, ws = ready_manager()
        self.assertEqual((await m.remove_orderbook())["codes"], [])
        self.assertEqual(ws.sent, [])


class TestFailures(unittest.IsolatedAsyncioTestCase):
    async def test_not_connected_is_503(self):
        m = KiwoomWSManager()
        with self.assertRaises(ControlError) as ctx:
            await m.add_orderbook(["005930"])
        self.assertEqual(ctx.exception.status, 503)

    async def test_t22_rejected_ack_is_502_and_not_confirmed(self):
        m, ws = ready_manager()
        task = asyncio.create_task(m.add_orderbook(["005930"]))
        await ack_nth(m, ws, 1, {"trnm": "REG", "return_code": 1, "return_msg": "등록 한도 초과"})
        with self.assertRaises(ControlError) as ctx:
            await task
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("등록 한도 초과", ctx.exception.detail)
        self.assertEqual(m.orderbook_status()["codes"], [])

    async def test_t22_timeout_is_504_closes_session_and_ignores_late_ack(self):
        m, ws = ready_manager(["000660"])
        with patch.object(mgr_mod, "_ACK_TIMEOUT", 0.05):
            with self.assertRaises(ControlError) as ctx:
                await m.add_orderbook(["005930"])
        self.assertEqual(ctx.exception.status, 504)
        self.assertTrue(ws.closed)                             # 결과 불명 → 세션 종료
        self.assertEqual(m._orderbook, {"000660"})             # 확정 목록에 안 합침
        self.assertFalse(m.orderbook_status()["connected"])
        m._on_ack(OK)                                          # 늦은 ACK — 아무 요청도 성공시키지 않음
        self.assertIsNone(m._pending)

    async def test_session_end_fails_pending_request(self):
        m, ws = ready_manager()
        task = asyncio.create_task(m.add_orderbook(["005930"]))
        while not ws.sent:
            await asyncio.sleep(0)
        m._on_session_end()
        with self.assertRaises(ControlError) as ctx:
            await task
        self.assertEqual(ctx.exception.status, 503)

    async def test_delete_while_disconnected_clears_list(self):
        """소켓이 죽으면 서버 쪽 등록도 없다 — 목록만 비워 재접속 복구 대상에서 뺀다."""
        m = KiwoomWSManager()
        m._orderbook = {"005930"}
        self.assertEqual((await m.remove_orderbook())["codes"], [])


class TestReconnect(unittest.IsolatedAsyncioTestCase):
    async def test_t23_login_restores_00_then_confirmed_0d_before_connected(self):
        m, ws = KiwoomWSManager(), FakeWS()
        m._ws, m._orderbook = ws, {"005930", "000660"}
        published = []
        with patch.object(mgr_mod.bus, "publish", lambda ch, p: published.append((ch, p))):
            task = asyncio.create_task(m._after_login(ws))
            await ack_nth(m, ws, 1, OK)
            self.assertEqual(published, [])                    # 00 만 복구된 상태로 connected 금지
            await ack_nth(m, ws, 2, OK)
            await task
        self.assertEqual(ws.sent, [
            {"trnm": "REG", "grp_no": "1", "refresh": "1", "data": [{"item": [""], "type": ["00"]}]},
            {"trnm": "REG", "grp_no": "2", "refresh": "1", "data": [
                {"item": ["KRX:000660", "KRX:005930"], "type": ["0D"]}]},
        ])
        self.assertEqual(published, [("system", {"type": "connected"})])
        self.assertTrue(m.orderbook_status()["connected"])

    async def test_t23_rejected_0d_restore_keeps_fill_connection(self):
        """0D 복구 거부가 00 체결 연결을 끊으면 안 된다 — 목록만 버리고 connected. 재등록은 수집기 몫."""
        m, ws = KiwoomWSManager(), FakeWS()
        m._ws, m._orderbook = ws, {"005930"}
        published = []
        with patch.object(mgr_mod.bus, "publish", lambda ch, p: published.append((ch, p))):
            task = asyncio.create_task(m._after_login(ws))
            await ack_nth(m, ws, 1, OK)
            await ack_nth(m, ws, 2, {"trnm": "REG", "return_code": 1, "return_msg": "x"})
            await task
        self.assertFalse(ws.closed)
        self.assertEqual(published, [("system", {"type": "connected"})])
        self.assertEqual(m.orderbook_status(), {"connected": True, "venue": "KRX", "codes": []})

    async def test_t23_0d_restore_timeout_drops_list_so_next_session_is_00_only(self):
        m, ws = KiwoomWSManager(), FakeWS()
        m._ws, m._orderbook = ws, {"005930"}
        with patch.object(mgr_mod, "_ACK_TIMEOUT", 0.05):
            task = asyncio.create_task(m._after_login(ws))
            await ack_nth(m, ws, 1, OK)
            await task                                         # 0D ACK 없음 → 시간초과
        self.assertTrue(ws.closed)                             # 결과 불명이라 세션은 닫는다
        self.assertEqual(m._orderbook, set())                  # 다음 세션은 00 만 복구 → 반복 끊김 없음
        ws2 = FakeWS()
        m._ws = ws2
        task = asyncio.create_task(m._after_login(ws2))
        await ack_nth(m, ws2, 1, OK)
        await task
        self.assertEqual(len(ws2.sent), 1)
        self.assertTrue(m.orderbook_status()["connected"])

    async def test_t23_failed_00_registration_closes_without_connected(self):
        m, ws = KiwoomWSManager(), FakeWS()
        m._ws = ws
        published = []
        with patch.object(mgr_mod.bus, "publish", lambda ch, p: published.append((ch, p))):
            task = asyncio.create_task(m._after_login(ws))
            await ack_nth(m, ws, 1, {"trnm": "REG", "return_code": 1, "return_msg": "x"})
            await task
        self.assertEqual(published, [])
        self.assertTrue(ws.closed)
        self.assertFalse(m.orderbook_status()["connected"])


class FakeManager:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def orderbook_status(self):
        return {"connected": True, "venue": "KRX", "codes": []}

    async def add_orderbook(self, codes):
        self.calls.append(codes)
        if self.error:
            raise self.error
        return {"connected": True, "venue": "KRX", "codes": sorted(set(codes))}

    async def remove_orderbook(self):
        if self.error:
            raise self.error
        return {"connected": True, "venue": "KRX", "codes": []}


class TestRouter(unittest.TestCase):
    def client(self, fake):
        app = FastAPI()
        app.include_router(realtime.router)
        patcher = patch.object(realtime, "ws_manager", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app)

    def test_post_accepts_alphanumeric_six(self):
        fake = FakeManager()
        res = self.client(fake).post("/realtime/orderbook", json={"codes": ["005930", "0197V0"]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(fake.calls, [["005930", "0197V0"]])

    def test_post_bad_input_is_422(self):
        c = self.client(FakeManager())
        for body in ({"codes": []}, {"codes": ["A005930"]}, {"codes": ["KRX:005930"]},
                     {"codes": ["00593"]}, {"codes": ["0197v0"]}, {}):
            with self.subTest(body=body):
                self.assertEqual(c.post("/realtime/orderbook", json=body).status_code, 422)

    def test_control_errors_map_to_status(self):
        for status in (502, 503, 504):
            with self.subTest(status=status):
                c = self.client(FakeManager(ControlError(status, "REG: return_code=1")))
                res = c.post("/realtime/orderbook", json={"codes": ["005930"]})
                self.assertEqual(res.status_code, status)
                self.assertIn("return_code=1", res.json()["detail"])
                self.assertEqual(c.delete("/realtime/orderbook").status_code, status)

    def test_get_returns_status(self):
        res = self.client(FakeManager()).get("/realtime/orderbook")
        self.assertEqual(res.json(), {"connected": True, "venue": "KRX", "codes": []})


if __name__ == "__main__":
    unittest.main()
