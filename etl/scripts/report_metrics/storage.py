"""report_metrics.sqlite3 — 스키마·연결·upsert·직전 리포트 조회 (PLAN §2.2, §2.4).

wl_sqlite 는 watchlist 전용이라 재사용하지 않는다(그 모듈 docstring 이 명시).
연결은 WAL writer / query_only reader 두 가지로만 연다.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Sequence

from .models import STATUS_IMAGE_PDF, STATUS_PARSE_ERROR, ReportFacts, YearEstimate

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "db" / "report_metrics.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_facts (
  pdf_key TEXT PRIMARY KEY,
  pdf_path TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  broker TEXT NOT NULL,
  report_date TEXT NOT NULL,
  opinion TEXT,
  target_price INTEGER,
  price_at_report INTEGER,
  price_at_report_date TEXT,
  upside_printed REAL,
  prev_target_printed INTEGER,
  parse_status TEXT NOT NULL,
  parse_error TEXT,
  parser_version TEXT NOT NULL,
  parsed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_facts_stock_broker_date
  ON report_facts(stock_code, broker, report_date);

CREATE TABLE IF NOT EXISTS report_estimates (
  pdf_key TEXT NOT NULL REFERENCES report_facts(pdf_key) ON DELETE CASCADE,
  fiscal_year INTEGER NOT NULL,
  is_forecast INTEGER NOT NULL,
  revenue REAL,
  operating_profit REAL,
  net_profit REAL,
  eps REAL,
  per REAL,
  roe REAL,
  pbr REAL,
  div_yield REAL,
  PRIMARY KEY (pdf_key, fiscal_year)
);
"""

_FACT_COLUMNS = [f.name for f in fields(ReportFacts)]


@contextmanager
def connect_rw(db_path: str | Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextmanager
def connect_ro(db_path: str | Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()


def init_db(db_path: str | Path = DEFAULT_DB) -> None:
    """스키마 생성. 재실행 멱등."""
    with connect_rw(db_path) as con:
        con.executescript(_SCHEMA)


def upsert_facts(con: sqlite3.Connection, facts: ReportFacts) -> None:
    """pdf_key 기준 덮어쓰기. parsed_at 은 여기서 채운다."""
    facts.parsed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    values = [getattr(facts, name) for name in _FACT_COLUMNS]
    placeholders = ", ".join("?" for _ in _FACT_COLUMNS)
    con.execute(
        f"INSERT OR REPLACE INTO report_facts ({', '.join(_FACT_COLUMNS)}) "
        f"VALUES ({placeholders})",
        values,
    )


def replace_estimates(
    con: sqlite3.Connection, pdf_key: str, estimates: Sequence[YearEstimate]
) -> None:
    """한 리포트의 추정표를 통째로 교체(부분 갱신 시 유령 행이 남지 않게)."""
    con.execute("DELETE FROM report_estimates WHERE pdf_key = ?", (pdf_key,))
    con.executemany(
        "INSERT INTO report_estimates (pdf_key, fiscal_year, is_forecast, revenue, "
        "operating_profit, net_profit, eps, per, roe, pbr, div_yield) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                pdf_key,
                e.fiscal_year,
                1 if e.is_forecast else 0,
                e.revenue,
                e.operating_profit,
                e.net_profit,
                e.eps,
                e.per,
                e.roe,
                e.pbr,
                e.div_yield,
            )
            for e in estimates
        ],
    )


def find_previous_report(
    con: sqlite3.Connection, stock_code: str, broker: str, report_date: str
) -> Optional[sqlite3.Row]:
    """같은 종목·같은 증권사의 직전 리포트 1건.

    report_date 가 **엄격히 작은** 것만 본다 — 같은 날 같은 증권사 리포트는 상·하향 판단
    근거가 안 되므로 이전으로 잡지 않는다(PLAN §2.2).
    파싱 실패·이미지 PDF 는 목표가를 모를 뿐 목표가가 없는 게 아니므로 건너뛴다. 그걸 직전으로
    잡으면 앞의 정상 리포트 대비 상·하향이 new_target 으로 묻힌다. NOT RATED(no_target)는
    실제로 목표가가 없던 것이라 그대로 직전이 된다.
    """
    cur = con.execute(
        "SELECT * FROM report_facts "
        "WHERE stock_code = ? AND broker = ? AND report_date < ? "
        "AND parse_status NOT IN (?, ?) "
        "ORDER BY report_date DESC, pdf_key DESC LIMIT 1",
        (stock_code, broker, report_date, STATUS_PARSE_ERROR, STATUS_IMAGE_PDF),
    )
    return cur.fetchone()


def load_estimates(con: sqlite3.Connection, pdf_key: str) -> list[YearEstimate]:
    rows = con.execute(
        "SELECT * FROM report_estimates WHERE pdf_key = ? ORDER BY fiscal_year",
        (pdf_key,),
    ).fetchall()
    return [
        YearEstimate(
            fiscal_year=r["fiscal_year"],
            is_forecast=bool(r["is_forecast"]),
            revenue=r["revenue"],
            operating_profit=r["operating_profit"],
            net_profit=r["net_profit"],
            eps=r["eps"],
            per=r["per"],
            roe=r["roe"],
            pbr=r["pbr"],
            div_yield=r["div_yield"],
        )
        for r in rows
    ]
