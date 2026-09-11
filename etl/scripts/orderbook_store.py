"""호가 스냅샷 저장소 — FID 정규화·스키마·회차 저장·실행 기록 (SPEC §9·§10).

DB: etl/db/orderbook.sqlite3 (WAL). 이 모듈은 orderbook DB 에만 스키마를 만든다.
한 격자 회차(모든 종목 행 + 실행기록 갱신)는 한 트랜잭션이다. 실패하면 회차 전체 rollback.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any

DB_PATH = pathlib.Path(__file__).resolve().parents[1] / "db" / "orderbook.sqlite3"

# (FID, 컬럼, 가격여부). 가격은 부호(전일대비 표시)를 떼고 절댓값.
_FIDS: list[tuple[str, str, bool]] = (
    [(str(40 + i), f"ask{i}_px", True) for i in range(1, 11)]
    + [(str(60 + i), f"ask{i}_qty", False) for i in range(1, 11)]
    + [(str(50 + i), f"bid{i}_px", True) for i in range(1, 11)]
    + [(str(70 + i), f"bid{i}_qty", False) for i in range(1, 11)]
    + [("121", "ask_total_qty", False), ("125", "bid_total_qty", False),
       ("23", "exp_px", True), ("24", "exp_qty", False),
       ("291", "exp_px_ca", True), ("292", "exp_qty_ca", False)]
)
BOOK_COLUMNS = ([f"ask{i}_px" for i in range(1, 11)] + [f"ask{i}_qty" for i in range(1, 11)]
                + [f"bid{i}_px" for i in range(1, 11)] + [f"bid{i}_qty" for i in range(1, 11)]
                + ["ask_total_qty", "bid_total_qty", "exp_px", "exp_qty", "exp_px_ca", "exp_qty_ca"])
SNAPSHOT_COLUMNS = ["date", "ticker", "venue", "ts", "recv_ts", "quote_tm", *BOOK_COLUMNS]

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS orderbook_snapshot (\n"
    "  date TEXT NOT NULL,\n  ticker TEXT NOT NULL,\n  venue TEXT NOT NULL,\n"
    "  ts TEXT NOT NULL,\n  recv_ts TEXT NOT NULL,\n  quote_tm TEXT,\n"
    + "".join(f"  {c} INTEGER,\n" for c in BOOK_COLUMNS)
    + "  PRIMARY KEY (date, ticker, venue, ts)\n);\n"
    "CREATE TABLE IF NOT EXISTS orderbook_run (\n"
    "  run_id INTEGER PRIMARY KEY AUTOINCREMENT,\n  date TEXT NOT NULL,\n"
    "  started_at TEXT NOT NULL,\n  ended_at TEXT,\n  mode TEXT NOT NULL,\n"
    "  source_date TEXT,\n  venue TEXT,\n  symbols TEXT NOT NULL,\n"
    "  rows_written INTEGER,\n  note TEXT\n);\n"
    "CREATE INDEX IF NOT EXISTS ix_orderbook_run_date ON orderbook_run(date);\n"
)
_INSERT = (f"INSERT OR REPLACE INTO orderbook_snapshot ({', '.join(SNAPSHOT_COLUMNS)})"
           f" VALUES ({', '.join(':' + c for c in SNAPSHOT_COLUMNS)})")
_RUN_FIELDS = {"ended_at", "source_date", "venue", "symbols", "rows_written", "note"}


def normalize(values: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """0D values → (호가 컬럼 dict + quote_tm, 숫자 파싱 실패 수).

    누락·빈 문자열은 NULL, 정상 0 은 0. 파싱 실패는 그 필드만 NULL 로 두고 센다.
    """
    book: dict[str, Any] = {"quote_tm": values.get("21") or None}
    invalid = 0
    for fid, column, is_price in _FIDS:
        raw = values.get(fid)
        if raw is None or str(raw).strip() == "":
            book[column] = None
            continue
        try:
            number = int(str(raw).strip())
        except ValueError:
            book[column] = None
            invalid += 1
            continue
        book[column] = abs(number) if is_price else number
    return book, invalid


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)


def start_run(con: sqlite3.Connection, *, date: str, started_at: str, mode: str,
              note: dict) -> int:
    """실행기록 1행을 만들고 커밋한다. 실행마다 새 행(덮어쓰지 않음)."""
    cur = con.execute(
        "INSERT INTO orderbook_run (date, started_at, mode, symbols, rows_written, note)"
        " VALUES (?, ?, ?, '', 0, ?)",
        [date, started_at, mode, json.dumps(note, ensure_ascii=False)])
    con.commit()
    return cur.lastrowid


def _run_update(con: sqlite3.Connection, run_id: int, fields: dict[str, Any]) -> None:
    unknown = set(fields) - _RUN_FIELDS
    if unknown:
        raise ValueError(f"unknown orderbook_run fields: {sorted(unknown)}")
    if "note" in fields:
        fields = {**fields, "note": json.dumps(fields["note"], ensure_ascii=False)}
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE orderbook_run SET {sets} WHERE run_id = ?", [*fields.values(), run_id])


def update_run(con: sqlite3.Connection, run_id: int, **fields: Any) -> None:
    """준비 완료·구독 변경·종료 때 실행기록을 갱신하고 커밋한다."""
    _run_update(con, run_id, fields)
    con.commit()


def write_round(con: sqlite3.Connection, run_id: int, rows: list[dict[str, Any]], *,
                rows_written: int, note: dict | None = None) -> int:
    """한 격자 회차를 한 트랜잭션으로 저장한다. 반환: 이번 실행의 누적 rows_written.

    note 는 바뀐 회차에만 넘긴다(first_recv_ts 반영 등). 실패하면 회차 전체 rollback 후 예외.
    """
    total = rows_written + len(rows)
    fields: dict[str, Any] = {"rows_written": total}
    if note is not None:
        fields["note"] = note
    try:
        con.executemany(_INSERT, rows)
        _run_update(con, run_id, fields)
        con.commit()
    except sqlite3.Error:
        con.rollback()
        raise
    return total
