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

import websockets

from . import channels
from .event_bus import bus
from .. import tr
from ..auth import get_token
from ..config import Config, load_config

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 5.0  # seconds between reconnect attempts

# Channels to subscribe on every (re)connect.
_SUBSCRIBE_TYPES = [tr.RT_FILL]

class KiwoomWSManager:
    """Owns the background task that keeps the Kiwoom realtime feed flowing."""

    def __init__(self) -> None:
        self._cfg: Config | None = None
        self._running = False
        self._task: asyncio.Task | None = None

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
        """One connection: LOGIN -> REG -> receive loop"""
        cfg = self._config()
        async with websockets.connect(cfg.ws_host, open_timeout=10) as ws:
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
                    await self._subscribe(ws)
                    bus.publish("system", {"type":"connected"})
                    logger.info("WS connected and subscribed")
                    continue
                if trnm == "REAL":
                    for channel, values in channels.parse_message(msg):
                        bus.publish(channel, values)

    async def _subscribe(self, ws) -> None:
        """Reginster realtime channels after a successful login."""
        await ws.send(json.dumps({
            "trnm": tr.WS_REG,
            "grp_no": "1",
            "refresh": "1",
            "data": [{"item": [""], "type": _SUBSCRIBE_TYPES}]
        }))