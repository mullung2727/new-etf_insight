"""조건검색 — list saved conditions and run one (single-shot).

Kiwoom condition search is WebSocket-only (no REST). Protocol requires
LOGIN -> CNSRLST -> CNSRREQ in the same session; the server ignores CNSRREQ
without a prior CNSRLST. Realtime subscription (ka10173) is deferred to
the v2 standalone worker.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

from . import tr
from .auth import get_token
from .config import Config, load_config
from .models import ConditionItem

_cfg: Config | None = None


def _config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


async def _roundtrip(send_msg: dict[str, Any], want_trnm: str) -> dict[str, Any]:
    """LOGIN, send one request, return the first reply whose trnm == want_trnm."""
    cfg = _config()
    async with websockets.connect(cfg.ws_host, open_timeout=10) as ws:
        await ws.send(json.dumps({"trnm": tr.WS_LOGIN, "token": get_token()}))
        async with asyncio.timeout(15):
            while True:
                msg = json.loads(await ws.recv())
                trnm = msg.get("trnm")
                if trnm == "PING":
                    await ws.send(json.dumps(msg))
                    continue
                if trnm == tr.WS_LOGIN:
                    if msg.get("return_code") not in (0, "0"):
                        raise RuntimeError(f"WS login failed: {msg.get('return_msg')}")
                    await ws.send(json.dumps(send_msg))
                    continue
                if trnm == want_trnm:
                    return msg


def list_conditions() -> list[ConditionItem]:
    """Return saved condition formulas as (seq, name) pairs (ka10171)."""
    msg = asyncio.run(_roundtrip({"trnm": tr.WS_CONDITION_LIST}, tr.WS_CONDITION_LIST))
    items: list[ConditionItem] = []
    for entry in msg.get("data", []):
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            items.append(ConditionItem(seq=str(entry[0]), name=str(entry[1])))
        elif isinstance(entry, dict):
            items.append(
                ConditionItem(
                    seq=str(entry.get("seq", "")), name=str(entry.get("cnsr_nm", ""))
                )
            )
    return items


async def _run_condition_async(seq: str) -> dict[str, Any]:
    """LOGIN -> CNSRLST -> CNSRREQ, return first non-PING reply after CNSRREQ."""
    cfg = _config()
    cnsrreq = {
        "trnm": tr.WS_CONDITION_REQ,
        "seq": str(seq),
        "search_type": "0",
        "stex_tp": "K",
        "cont_yn": "N",
        "next_key": "",
    }
    state = "login"
    async with websockets.connect(cfg.ws_host, open_timeout=10) as ws:
        await ws.send(json.dumps({"trnm": tr.WS_LOGIN, "token": get_token()}))
        async with asyncio.timeout(20):
            while True:
                msg = json.loads(await ws.recv())
                trnm = msg.get("trnm")
                if trnm == "PING":
                    await ws.send(json.dumps(msg))
                    continue
                if trnm == tr.WS_LOGIN:
                    if msg.get("return_code") not in (0, "0"):
                        raise RuntimeError(f"WS login failed: {msg.get('return_msg')}")
                    await ws.send(json.dumps({"trnm": tr.WS_CONDITION_LIST}))
                    state = "cnsrlst"
                    continue
                if trnm == tr.WS_CONDITION_LIST and state == "cnsrlst":
                    await ws.send(json.dumps(cnsrreq))
                    state = "cnsrreq"
                    continue
                if state == "cnsrreq":
                    return msg


def run_condition(seq: str) -> dict[str, Any]:
    """Run a saved condition once and return matching stocks (ka10172)."""
    return asyncio.run(_run_condition_async(seq))
