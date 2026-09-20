"""Persistent Kiwoom realtime WS client.

Holds one always-on WebSocket to Kiwoom, logs in, subscribes the configured
realtime channels, and republishes incoming REAL messages onto the EventBus.
Reconnects with a fixed backoff when the socket drops. Mirrors the LOGIN ->
PING-echo handshake proven in ``kiwoom.conditions``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import websockets

from . import channels
from .event_bus import bus
from .. import tr
from ..auth import get_token
from ..config import Config, load_config

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 5.0  # seconds between reconnect attempts
_ACK_TIMEOUT = 5.0      # REG/REMOVE 응답 대기 (SPEC reg_timeout_sec)
_KST = timezone(timedelta(hours=9))

# Channels to subscribe on every (re)connect.
_SUBSCRIBE_TYPES = [tr.RT_FILL]
_ORDERBOOK_GROUP = "2"  # 호가 수집기 소유. 00 은 group 1 이라 서로 해제하지 않는다.


class ControlError(Exception):
    """구독 제어 실패. status 는 HTTP 코드(502 거부, 503 미연결, 504 ACK 시간초과)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


def _orderbook_frame(trnm: str, codes: list[str]) -> dict:
    frame = {"trnm": trnm, "grp_no": _ORDERBOOK_GROUP}
    if trnm == tr.WS_REG:
        frame["refresh"] = "1"  # 1 = 기존 등록 유지. REMOVE 에는 넣지 않는다.
    frame["data"] = [{"item": sorted(codes), "type": [tr.RT_ORDERBOOK]}]
    return frame


class KiwoomWSManager:
    """Owns the background task that keeps the Kiwoom realtime feed flowing."""

    def __init__(self) -> None:
        self._cfg: Config | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._ws = None                      # 현재 세션 소켓. 세션 밖이면 None
        self._ready = False                  # 로그인 + 00/0D 복구 완료
        self._ctl_lock = asyncio.Lock()      # REG/REMOVE 는 ACK 에 요청 식별자가 없어 한 번에 하나
        self._pending: tuple[str, asyncio.Future] | None = None
        self._orderbook: set[str] = set()    # ACK 받은 0D 종목 (재접속 때 복구 대상)
        self._login_task: asyncio.Task | None = None

    def _config(self) -> Config:
        if self._cfg is None:
            self._cfg = load_config()
        return self._cfg
    
    async def start(self) -> None:
        """Launch the background reconnect loop (idempotent)."""

        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("KiwoomWSManager started")

    async def stop(self) -> None:
        """Stop the loop and cancel the background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("KiwoomWSManager stopped")

    async def _run_loop(self) -> None:
        """Reconnect forever until stopped."""
        while self._running:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — log and retry any failure
                logger.warning(f"WS session ended {exc}")
            bus.publish("system", {"type": "disconnected"})
            if self._running:
                await asyncio.sleep(_RECONNECT_DELAY)
    
    async def _session(self) -> None:
        """One connection: LOGIN -> REG(별도 태스크) -> receive loop.

        REG ACK 를 기다리는 동안에도 수신 루프가 PING·ACK 를 처리해야 하므로
        등록·복구는 ``_after_login`` 태스크로 돌린다.
        """
        cfg = self._config()
        # close_timeout: ACK 시간초과로 세션을 끊을 때 HTTP 504 가 늦지 않게 짧게 둔다.
        async with websockets.connect(cfg.ws_host, open_timeout=10, close_timeout=2) as ws:
            self._ws = ws
            try:
                await ws.send(json.dumps({"trnm": tr.WS_LOGIN, "token": get_token()}))
                async for raw in ws:
                    msg = json.loads(raw)
                    trnm = msg.get("trnm")

                    if trnm == "PING":
                        await ws.send(raw) # echo back verbatim
                        continue
                    if trnm == tr.WS_LOGIN:
                        if msg.get("return_code") not in (0, "0"):
                            raise RuntimeError(f"WS login failed: {msg.get("return_msg")}")
                        self._login_task = asyncio.create_task(self._after_login(ws))
                        continue
                    if trnm in (tr.WS_REG, tr.WS_REMOVE):
                        self._on_ack(msg)
                        continue
                    if trnm == "REAL":
                        self._on_real(msg)
            finally:
                self._on_session_end()

    async def _after_login(self, ws) -> None:
        """00 과 확정 0D 목록을 복구한 뒤에 connected 를 알린다.

        00 등록 실패는 세션을 끊는다. 0D 복구 실패는 체결 연결을 끊지 않도록 0D 목록만 버린다 —
        그대로 두면 재접속마다 같은 거부가 반복돼 00 까지 계속 끊긴다. 재등록은 connected 를 받은
        수집기가 POST 로 한다.
        """
        try:
            async with self._ctl_lock:
                await self._request({"trnm": tr.WS_REG, "grp_no": "1", "refresh": "1",
                                     "data": [{"item": [""], "type": _SUBSCRIBE_TYPES}]}, "REG 00")
                if self._orderbook:
                    try:
                        await self._request(_orderbook_frame(tr.WS_REG, list(self._orderbook)),
                                            "REG 0D restore")
                    except ControlError as exc:
                        logger.warning(f"0D restore failed, dropping {sorted(self._orderbook)}: {exc.detail}")
                        self._orderbook.clear()
                        if exc.status != 502:     # 504/503 은 세션이 이미 닫혔다 → 다음 세션은 00 만
                            return
                self._ready = True
            bus.publish("system", {"type": "connected"})
            logger.info(f"WS connected and subscribed (0D {len(self._orderbook)})")
        except ControlError as exc:
            logger.warning(f"WS subscribe failed, reconnecting: {exc.detail}")
            await ws.close()

    def _on_session_end(self) -> None:
        """소켓 참조를 버리고 대기 중인 제어 요청을 실패시킨다."""
        self._ws, self._ready = None, False
        if self._pending and not self._pending[1].done():
            self._pending[1].set_exception(ControlError(503, "websocket closed"))
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()

    def _on_ack(self, msg: dict) -> None:
        """수신 루프에서 온 REG/REMOVE 응답을 대기 중인 요청에 넘긴다. 대기 없으면 버린다."""
        if self._pending and self._pending[0] == msg.get("trnm") and not self._pending[1].done():
            self._pending[1].set_result(msg)

    async def _request(self, frame: dict, stage: str) -> dict:
        """프레임을 보내고 ACK 를 기다린다. 호출자가 ``_ctl_lock`` 을 잡고 있어야 한다."""
        ws = self._ws
        if ws is None:
            raise ControlError(503, f"{stage}: websocket not connected")
        fut = asyncio.get_running_loop().create_future()
        self._pending = (frame["trnm"], fut)
        try:
            await ws.send(json.dumps(frame))
            ack = await asyncio.wait_for(fut, _ACK_TIMEOUT)
        except asyncio.TimeoutError:
            # 결과 불명. 늦은 ACK 가 다음 요청의 성공으로 읽히지 않게 세션을 끊는다 →
            # 재접속 때 확정 목록만 복구된다.
            self._ws, self._ready = None, False
            await ws.close()
            raise ControlError(504, f"{stage}: no ACK within {_ACK_TIMEOUT:g}s") from None
        except websockets.ConnectionClosed:
            raise ControlError(503, f"{stage}: websocket closed") from None
        finally:
            self._pending = None
        if ack.get("return_code") not in (0, "0"):
            raise ControlError(502, f"{stage}: return_code={ack.get('return_code')}"
                                    f" return_msg={ack.get('return_msg')}")
        return ack

    def orderbook_status(self) -> dict:
        return {"connected": self._ready, "venue": "KRX", "codes": sorted(self._orderbook)}

    async def add_orderbook(self, codes: list[str]) -> dict:
        """0D 추가 등록(멱등). ACK 성공한 신규 종목만 확정 목록에 합친다."""
        async with self._ctl_lock:
            if not self._ready:
                raise ControlError(503, "REG 0D: websocket not connected")
            new = sorted(set(codes) - self._orderbook)
            if new:
                await self._request(_orderbook_frame(tr.WS_REG, new), "REG 0D")
                self._orderbook.update(new)
            return self.orderbook_status()

    async def remove_orderbook(self) -> dict:
        """group 2 의 0D 전부 해제. 00(group 1)은 건드리지 않는다."""
        async with self._ctl_lock:
            if self._orderbook and self._ready:
                try:
                    await self._request(_orderbook_frame(tr.WS_REMOVE, list(self._orderbook)),
                                        "REMOVE 0D")
                except ControlError as exc:
                    if exc.status != 502:     # 세션이 끊겼으면 서버 등록도 사라졌다. 502 = 아직 등록됨
                        self._orderbook.clear()
                    raise
            # ponytail: 미연결이면 서버 등록이 이미 없으니 목록만 비운다(재접속 때 되살리지 않게).
            self._orderbook.clear()
            return self.orderbook_status()

    def _on_real(self, msg: dict) -> None:
        """REAL 프레임 수신 즉시 KST ms 시각을 찍어 각 payload 에 넣고 발행한다.

        호가 수집기는 이 시각으로 신선도를 판정한다(SSE 전달 지연과 무관하게).
        """
        recv_ts = datetime.now(_KST).isoformat(timespec="milliseconds")
        for channel, values in channels.parse_message(msg):
            bus.publish(channel, {**values, "_recv_ts": recv_ts})


ws_manager = KiwoomWSManager()  # broker 전역 단일 연결 (main lifespan 이 start/stop)