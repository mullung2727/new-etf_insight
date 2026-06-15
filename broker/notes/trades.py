"""거래 원장 기록 — kiwoom_trade_history.

broker를 통과한 모든 주문을 기록한다. notes와 같은 SQLite 연결(notes.db)을
공유하지만 별개 테이블이다. order_no가 PK이므로 중복 기록은 무시(INSERT OR IGNORE).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import get_conn

# 한국은 DST가 없으므로 고정 오프셋. tzdata 미설치/Windows zoneinfo 부재 환경에서도 안전.
_KST = timezone(timedelta(hours=9))


def _seoul_today() -> str:
    return datetime.now(_KST).strftime("%Y%m%d")


def _now_iso() -> str:
    return datetime.now(_KST).isoformat()


def record_trade(
    *,
    order_no: str,
    ticker: str,
    side: str,
    order_type: str,
    qty: int,
    price: int,
    status: str,
    source: str,
    raw: str,
    date: str | None = None,
) -> None:
    """거래 1건 기록. order_no 중복 시 기존 행 유지(무시)."""
    conn = get_conn()
    conn.execute(
        """
        INSERT OR IGNORE INTO kiwoom_trade_history
            (order_no, date, ticker, side, order_type, qty, price,
             status, source, raw, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_no,
            date or _seoul_today(),
            ticker,
            side,
            order_type,
            qty,
            price,
            status,
            source,
            raw,
            _now_iso(),
        ),
    )
    conn.commit()
