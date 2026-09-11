# 채널 필터: ch 생략 = system,00 (브라우저 layout). 호가 수집기는 ?ch=0D,system.
# 0D 는 초당 수십 건이라 기본 스트림에 섞지 않는다(SPEC_ORDERBOOK_SNAPSHOT_RECORDER §6).

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from kiwoom.ws.event_bus import bus

router = APIRouter(tags=['events'])

ALLOWED_CHANNELS = ("system", "00", "0D")
DEFAULT_CHANNELS = ("system", "00")
QUEUE_MAX = 2000  # SSE 연결당 큐 상한. 넘치면 bus 가 버리고 채널별로 센다.


def parse_channels(ch: str | None) -> tuple[str, ...]:
    """``ch`` 쿼리 → 중복 제거한 채널 튜플. 빈 값·미허용·``*`` 은 422."""
    if ch is None:
        return DEFAULT_CHANNELS
    parts = [p.strip() for p in ch.split(",")]
    bad = [p for p in parts if p not in ALLOWED_CHANNELS]
    if bad:
        raise HTTPException(422, f"unknown channel(s) {bad}; allowed: {list(ALLOWED_CHANNELS)}")
    return tuple(dict.fromkeys(parts))


@router.get("/events")
async def stream_events(request: Request, ch: str | None = Query(None)):
    """Stream broker realtime events for the requested channels to one client via SSE."""
    chans = parse_channels(ch)
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    for c in chans:
        bus.subscribe(c, queue)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    continue # let EventSourceResponse send its keep-alive ping
                yield json.dumps(event)
        finally:
            for c in chans:
                bus.unsubscribe(c, queue)

    return EventSourceResponse(gen(), ping=15)
