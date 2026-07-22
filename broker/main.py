import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP

from kiwoom.config import load_config
from notes import db as notes_db
from routers import account as account_router
from routers import close_bet as close_bet_router
from routers import conditions as conditions_router
from routers import notes as notes_router
from routers import orders as orders_router
from routers import quotes as quotes_router
from routers import settings as settings_router

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from kiwoom.ws.event_bus import bus
from kiwoom.ws.manager import KiwoomWSManager
from notes import alert, autolink
from routers import events as events_router

logger = logging.getLogger(__name__)

load_dotenv()

_ws_manager = KiwoomWSManager()

# 한국은 DST가 없으므로 고정 오프셋(notes/trades.py와 동일 패턴).
_KST = timezone(timedelta(hours=9))
_FILL_DEBOUNCE = 3.0  # 연속 체결을 모아 한 번만 재동기화
_IDEA_POLL = 300  # idea 진입가 감시 폴링 주기(초). 5분 = 하루 78콜, 1콜/tick.


def _in_market_hours(now: datetime) -> bool:
    """장중(평일 09:00~15:30 KST)인가. idea 진입가 감시 창."""
    if now.weekday() >= 5:  # 토·일
        return False
    return (now.hour, now.minute) >= (9, 0) and (now.hour, now.minute) <= (15, 30)


def _seoul_today() -> str:
    return datetime.now(_KST).strftime("%Y%m%d")


async def _fill_sync_loop() -> None:
    """WS 체결통보("00", 913=="체결") 수신 시 노트를 재동기화한다.

    실시간 경로는 증분을 직접 쓰지 않고 sync_trades(=kt00007 재조회) 트리거
    역할만 한다. 체결 한 건이 오면 3초 디바운스 후 그 창에 모인 체결을 한 번에
    반영한다. kt00007 호출은 sync requests라 to_thread로 오프로드한다.
    """
    queue: asyncio.Queue = asyncio.Queue()
    bus.subscribe("00", queue)
    try:
        while True:
            ev = await queue.get()
            if (ev.get("payload") or {}).get("913") != "체결":
                continue
            await asyncio.sleep(_FILL_DEBOUNCE)
            while not queue.empty():  # 창에 모인 후속 통보 흡수
                queue.get_nowait()
            try:
                await asyncio.to_thread(autolink.sync_trades, _seoul_today())
            except Exception:  # noqa: BLE001 — 동기화 실패가 루프를 죽이지 않도록
                logger.exception("실시간 체결 동기화 실패")
    finally:
        bus.unsubscribe("00", queue)


async def _idea_alert_loop() -> None:
    """장중 5분마다 idea 노트의 진입가 도달을 감시해 Discord로 알린다.

    판정·발송은 alert.run_idea_alert_check(동기, kt/ka 시세 콜)에 위임하고 여기선
    시간대 게이팅과 주기만 담당한다. 키움 콜은 to_thread로 오프로드. 한 tick의
    실패가 루프를 죽이지 않도록 감싼다(_fill_sync_loop와 동일 패턴).
    """
    while True:
        try:
            if _in_market_hours(datetime.now(_KST)):
                today = _seoul_today()
                await asyncio.to_thread(alert.run_idea_alert_check, today)
        except Exception:  # noqa: BLE001
            logger.exception("idea 진입가 감시 실패")
        await asyncio.sleep(_IDEA_POLL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _ws_manager.start()
    fill_task = asyncio.create_task(_fill_sync_loop())
    idea_task = asyncio.create_task(_idea_alert_loop())
    yield
    for task in (fill_task, idea_task):
        task.cancel()
    for task in (fill_task, idea_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await _ws_manager.stop()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:3001").split(",")
    if o.strip()
]

app = FastAPI(
    title="Kiwoom Broker API",
    description="키움증권 REST API gateway — REST + MCP (SSE).",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

notes_db.init()  # 노트 SQLite 테이블 보장

app.include_router(quotes_router.router)
app.include_router(account_router.router)
app.include_router(close_bet_router.router)
app.include_router(orders_router.router)
app.include_router(conditions_router.router)
app.include_router(notes_router.router)
app.include_router(settings_router.router)
app.include_router(events_router.router)

mcp = FastApiMCP(
    app,
    name="kiwoom-broker",
    description="키움증권 매매 게이트웨이. 시세·잔고·주문·조건검색.",
)
mcp.mount()


@app.get("/health", operation_id="health_check", summary="broker 상태 확인")
def health() -> dict:
    """broker 생존·환경(paper/real)·계좌번호 확인. 응답이 오면 주문 가능 상태.

    주문 전 상태 점검은 이 도구 하나로 끝낸다(포트·프로세스 탐색 불요).
    """
    cfg = load_config()
    return {"status": "ok", "env": cfg.env, "account": cfg.account_no or "<unset>"}
