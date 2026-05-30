import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_mcp import FastApiMCP

from duck import DUCKDB_PATH, init_connection
from routers import etfs as etfs_router
from routers import stats as stats_router

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_connection()
    yield


app = FastAPI(
    title="ETF Insight API",
    description="DuckDB-backed ETF data gateway (REST + MCP).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


app.include_router(etfs_router.router)
app.include_router(stats_router.router)

mcp = FastApiMCP(
    app,
    name="etf-insight",
    description="ETF Insight data gateway. Tools read from DuckDB and return JSON.",
    exclude_operations=["health_check"],
)
mcp.mount()


@app.get("/health", operation_id="health_check")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "db_path": str(DUCKDB_PATH),
        "db_exists": DUCKDB_PATH.exists(),
    }
