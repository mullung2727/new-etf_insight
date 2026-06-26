"""종가배팅 매매·청산 현황 조회 (read-only).

watchlist.sqlite3의 close_bet_orders를 3분류로 노출한다. 매수/청산은 etl 배치가
기록하고, broker는 RO로 읽기만 한다(매매 데이터라 로컬전용 broker에 둔다).

  - today_buys: 오늘 매수분
  - watching:   어제 이전 매수 + 미청산(청산 워커 감시 대상과 동일 기준)
  - history:    청산 완료분

실시간 손익은 여기서 계산하지 않는다 — 프론트가 기존 /quotes(ka10095)를 폴링해
buy_bid/cntr_price로 표시한다.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/close-bet", tags=["close-bet"])

# KST는 DST가 없어 고정 +9 오프셋으로 충분(broker .venv에 tzdata 미설치).
_KST = timezone(timedelta(hours=9))

# 기본 경로 = repo_root/etl/db/watchlist.sqlite3 (broker/routers/close_bet.py 기준).
# 테스트·이설은 CLOSE_BET_DB_PATH로 override (루트 .env의 WATCHLIST_DB_PATH와 별개).
_DEFAULT = Path(__file__).resolve().parents[2] / "etl" / "db" / "watchlist.sqlite3"


def _db_path() -> Path:
    override = os.getenv("CLOSE_BET_DB_PATH")
    return Path(override) if override else _DEFAULT


def _connect() -> sqlite3.Connection:
    """RO 연결(다른 프로세스가 쓰는 동안 안전). row_factory=Row."""
    con = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _today() -> str:
    return datetime.now(_KST).strftime("%Y%m%d")


@router.get(
    "/positions",
    operation_id="get_close_bet_positions",
    summary="종가배팅 매매·청산 현황",
)
def get_close_bet_positions() -> dict[str, list[dict[str, Any]]]:
    """close_bet_orders를 오늘매수/감시중/청산이력 3분류로 반환한다."""
    today = _today()
    con = _connect()
    try:
        # 매수 현황: 전체 매수 원장(일자별, 청산여부 무관). created_at=주문시각(UTC).
        buys = [dict(r) for r in con.execute(
            "SELECT date, ticker, score, cntr_price, status, order_no, created_at "
            "FROM close_bet_orders "
            "ORDER BY date DESC, created_at DESC, ticker",
        )]
        watching = [dict(r) for r in con.execute(
            "SELECT date, ticker, score, cntr_price, qty "
            "FROM close_bet_orders "
            "WHERE date < ? AND status = 'confirmed' AND sell_status IS NULL "
            "ORDER BY date DESC, ticker",
            [today],
        )]
        # 청산 이력: 매수일(date) 표시, 사유는 손익 부호로 자명하므로 제거.
        history = [dict(r) for r in con.execute(
            "SELECT date, ticker, cntr_price, sell_price, sell_qty, "
            "pnl_pct, sold_at "
            "FROM close_bet_orders WHERE sell_status = 'filled' "
            "ORDER BY sold_at DESC",
        )]
    finally:
        con.close()
    return {"buys": buys, "watching": watching, "history": history}
