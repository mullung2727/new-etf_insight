# 설계 결정: 현재 "*" 전체 구독 → 프론트에서 channel 보고 분기.
# 향후 실시간 시세(10종목 틱) 추가 시 데이터 양이 커지므로 채널 분리 고려:
#   GET /events?ch=system,00  → 체결+연결상태 (layout)
#   GET /events?ch=0B         → 시세 틱 (trading 페이지)
# 그 시점에 쿼리 파라미터로 bus.subscribe 채널을 좁히는 방식으로 확장.

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from kiwoom.ws.event_bus import bus

router = APIRouter(tags=['events'])

@router.get("/events")
async def stream_events(request: Request):
    """Stream all broker realtime events to one browser client via SSE."""
    queue: asyncio.Queue = asyncio.Queue()
    bus.subscribe("*", queue)

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
            bus.unsubscribe("*", queue)
    
    return EventSourceResponse(gen(), ping=15)
