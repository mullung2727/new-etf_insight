"""리포트 문서 카탈로그 — docs/2026-09-29_095528-broker-reports-full-coverage-sector-design.md (§4, §6-2).

기존 report_facts/report_api_facts/report_estimates는 이 모듈이 절대 쓰지 않는다(document_type 기준 분석 경계, 설계 §4).
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_documents (
  document_id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_type TEXT NOT NULL CHECK (document_type IN ('company','industry','market','invest','economy','unknown')),
  title TEXT,
  broker TEXT NOT NULL,
  published_date TEXT NOT NULL,
  pdf_path TEXT,
  sha256 TEXT UNIQUE,
  pdf_key TEXT UNIQUE,
  file_status TEXT NOT NULL CHECK (file_status IN ('saved','no_pdf','failed','not_pdf')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_docs_date ON report_documents(published_date);

CREATE TABLE IF NOT EXISTS report_sources (
  source TEXT NOT NULL,
  source_report_id TEXT NOT NULL,
  document_id INTEGER NOT NULL REFERENCES report_documents(document_id),
  list_url TEXT, detail_url TEXT, pdf_url TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  run_id INTEGER REFERENCES report_collection_runs(run_id),
  PRIMARY KEY (source, source_report_id)
);
CREATE INDEX IF NOT EXISTS idx_sources_doc ON report_sources(document_id);

CREATE TABLE IF NOT EXISTS report_document_stocks (
  document_id INTEGER NOT NULL REFERENCES report_documents(document_id),
  stock_code TEXT NOT NULL CHECK (length(stock_code) = 6),
  relation_type TEXT NOT NULL CHECK (relation_type IN ('primary','mention','unknown')),
  page_from INTEGER, page_to INTEGER,
  method TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'auto' CHECK (review_status IN ('auto','confirmed','rejected')),
  PRIMARY KEY (document_id, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_docstocks_code ON report_document_stocks(stock_code);

CREATE TABLE IF NOT EXISTS report_collection_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  category TEXT,
  date_from TEXT, date_to TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','completed','partial','error')),
  pages_fetched INTEGER NOT NULL DEFAULT 0,
  rows_listed INTEGER NOT NULL DEFAULT 0,
  new_docs INTEGER NOT NULL DEFAULT 0,
  dup_docs INTEGER NOT NULL DEFAULT 0,
  downloaded INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  no_pdf INTEGER NOT NULL DEFAULT 0,
  stop_reason TEXT,
  last_page INTEGER
);
"""

_STOCK_RE = re.compile(r"^\d{6}$")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_catalog(con: sqlite3.Connection) -> None:
    """카탈로그 4개 테이블 생성. 재실행 멱등."""
    con.executescript(_CATALOG_SCHEMA)


def sha256_file(path: Path) -> str:
    """파일 SHA-256 hex. 1MB 청크 스트리밍."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upsert_document(
    con: sqlite3.Connection,
    *,
    document_type: str,
    title: str | None = None,
    broker: str,
    published_date: str,
    pdf_path: str | None = None,
    sha256: str | None = None,
    pdf_key: str | None = None,
    file_status: str,
    now: str | None = None,
) -> tuple[int, bool]:
    """문서 upsert → (document_id, created).

    찾기 순서: sha256(None 아니면) → pdf_key(None 아니면).
    있으면 updated_at만 갱신(다른 컬럼 유지). 단 기존 title이 NULL/빈값이고
    새 title이 있으면 title만 채운다.
    """
    now = now or _utcnow()
    row = None
    if sha256 is not None:
        row = con.execute(
            "SELECT document_id, title FROM report_documents WHERE sha256 = ?",
            (sha256,),
        ).fetchone()
    if row is None and pdf_key is not None:
        row = con.execute(
            "SELECT document_id, title FROM report_documents WHERE pdf_key = ?",
            (pdf_key,),
        ).fetchone()
    if row is not None:
        doc_id, existing_title = row[0], row[1]
        if (existing_title is None or existing_title == "") and title:
            con.execute(
                "UPDATE report_documents SET title = ?, updated_at = ? WHERE document_id = ?",
                (title, now, doc_id),
            )
        else:
            con.execute(
                "UPDATE report_documents SET updated_at = ? WHERE document_id = ?",
                (now, doc_id),
            )
        return doc_id, False
    cur = con.execute(
        "INSERT INTO report_documents (document_type, title, broker, published_date, "
        "pdf_path, sha256, pdf_key, file_status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (document_type, title, broker, published_date, pdf_path, sha256,
         pdf_key, file_status, now, now),
    )
    return cur.lastrowid, True


def upsert_source(
    con: sqlite3.Connection,
    *,
    source: str,
    source_report_id: str,
    document_id: int,
    list_url: str | None = None,
    detail_url: str | None = None,
    pdf_url: str | None = None,
    run_id: int | None = None,
    now: str | None = None,
) -> bool:
    """없으면 INSERT → True. 있으면 last_seen_at만 갱신 → False(document_id 유지)."""
    now = now or _utcnow()
    row = con.execute(
        "SELECT 1 FROM report_sources WHERE source = ? AND source_report_id = ?",
        (source, source_report_id),
    ).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO report_sources (source, source_report_id, document_id, list_url, "
            "detail_url, pdf_url, first_seen_at, last_seen_at, run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (source, source_report_id, document_id, list_url, detail_url,
             pdf_url, now, now, run_id),
        )
        return True
    con.execute(
        "UPDATE report_sources SET last_seen_at = ? WHERE source = ? AND source_report_id = ?",
        (now, source, source_report_id),
    )
    return False


def upsert_document_stock(
    con: sqlite3.Connection,
    *,
    document_id: int,
    stock_code: str,
    relation_type: str,
    method: str,
    page_from: int | None = None,
    page_to: int | None = None,
) -> None:
    """문서-종목 연결. stock_code는 6자리 숫자 문자열(선행 0 유지, int 금지).

    이미 있고 review_status가 'auto'일 때만 relation/method/page 갱신.
    confirmed/rejected면 건드리지 않는다.
    """
    if not isinstance(stock_code, str) or not _STOCK_RE.match(stock_code):
        raise ValueError(f"stock_code 6자리 숫자 문자열: {stock_code!r}")
    row = con.execute(
        "SELECT review_status FROM report_document_stocks "
        "WHERE document_id = ? AND stock_code = ?",
        (document_id, stock_code),
    ).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO report_document_stocks "
            "(document_id, stock_code, relation_type, page_from, page_to, method) "
            "VALUES (?,?,?,?,?,?)",
            (document_id, stock_code, relation_type, page_from, page_to, method),
        )
    elif row[0] == "auto":
        con.execute(
            "UPDATE report_document_stocks SET relation_type = ?, page_from = ?, "
            "page_to = ?, method = ? WHERE document_id = ? AND stock_code = ?",
            (relation_type, page_from, page_to, method, document_id, stock_code),
        )
