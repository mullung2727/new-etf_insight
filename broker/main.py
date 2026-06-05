import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP

from kiwoom.config import load_config
from notes import db as notes_db
from routers import account as account_router
from routers import conditions as conditions_router
from routers import notes as notes_router
from routers import orders as orders_router
from routers import quotes as quotes_router
from routers import settings as settings_router

from contextlib import asynccontextmanager
from kiwoom.ws.manager import KiwoomWSManager
from routers import events as events_router



load_dotenv()

_ws_manager = KiwoomWSManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _ws_manager.start()
    yield
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
app.include_router(orders_router.router)
app.include_router(conditions_router.router)
app.include_router(notes_router.router)
app.include_router(settings_router.router)
app.include_router(events_router.router)

mcp = FastApiMCP(
    app,
    name="kiwoom-broker",
    description="키움증권 매매 게이트웨이. 시세·잔고·주문·조건검색.",
    exclude_operations=["health_check"],
)
mcp.mount()


@app.get("/health", operation_id="health_check")
def health() -> dict:
    cfg = load_config()
    return {"status": "ok", "env": cfg.env, "account": cfg.account_no or "<unset>"}
