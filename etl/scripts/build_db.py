"""Sync all ETF records from runs/ into SQLite (etf_insight.sqlite3).

Usage (standalone):
    uv run python scripts/build_db.py [--runs-dir runs] [--db-path db/etf_insight.sqlite3]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


DEFAULT_RUNS_DIR = Path(__file__).parent.parent / "runs"
DEFAULT_DB_PATH = Path(__file__).parent.parent / "db" / "etf_insight.sqlite3"

# SQLite 타입 매핑: VARCHAR→TEXT, JSON→TEXT(직렬화 문자열), DOUBLE→REAL,
# BOOLEAN→INTEGER(0/1), TIMESTAMP→TEXT(CURRENT_TIMESTAMP ISO 문자열).
_CREATE_ETF_RECORDS = """
CREATE TABLE IF NOT EXISTS etf_records (
    etf_key             TEXT PRIMARY KEY,
    route               TEXT,
    is_pre_listing_etf  INTEGER,
    fund_name           TEXT,
    asset_manager       TEXT,
    index_name          TEXT,
    index_provider      TEXT,
    index_description   TEXT,
    primary_country     TEXT,
    theme_status        TEXT,
    theme_bucket        TEXT,
    structure_tags      TEXT,
    classification_confidence REAL,
    classification_evidence TEXT,
    holdings_available_in_pdf INTEGER,
    holdings_summary    TEXT,
    keywords            TEXT,
    trend_summary       TEXT,
    missing_info        TEXT,
    rcept_no            TEXT,
    rcept_dt            TEXT,
    corp_code           TEXT,
    corp_name           TEXT,
    report_nm           TEXT,
    fund_code           TEXT,
    pdf_path            TEXT,
    first_rcept_dt      TEXT,
    revision_count      INTEGER,
    db_updated_at       TEXT
)
"""

_CREATE_ETF_HOLDINGS = """
CREATE TABLE IF NOT EXISTS etf_holdings (
    etf_key  TEXT,
    seq      INTEGER,
    name     TEXT,
    ticker   TEXT,
    exchange TEXT,
    weight   TEXT,
    PRIMARY KEY (etf_key, seq)
)
"""


def _load_records(runs_dir: Path) -> dict[str, dict]:
    """Scan all runs/*/records/*.json; dedup by etf_key keeping latest rcept_dt."""
    best: dict[str, dict] = {}
    for json_path in runs_dir.glob("*/records/*.json"):
        try:
            record = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        etf_key = record.get("source", {}).get("etf_key") or json_path.stem
        current_dt = record.get("source", {}).get("rcept_dt", "")
        existing_dt = best.get(etf_key, {}).get("source", {}).get("rcept_dt", "")
        if current_dt >= existing_dt:
            best[etf_key] = record
    return best


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_ETF_RECORDS)
    con.execute(_CREATE_ETF_HOLDINGS)
    # PRAGMA table_info: row[1] = 컬럼명 (DuckDB/SQLite 동일).
    existing_columns = {
        row[1]
        for row in con.execute("PRAGMA table_info('etf_records')").fetchall()
    }
    migrations = {
        "theme_status": "ALTER TABLE etf_records ADD COLUMN theme_status TEXT",
        "theme_bucket": "ALTER TABLE etf_records ADD COLUMN theme_bucket TEXT",
        "structure_tags": "ALTER TABLE etf_records ADD COLUMN structure_tags TEXT",
        "classification_confidence": "ALTER TABLE etf_records ADD COLUMN classification_confidence REAL",
        "classification_evidence": "ALTER TABLE etf_records ADD COLUMN classification_evidence TEXT",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            con.execute(statement)


def _upsert_record(con: sqlite3.Connection, etf_key: str, record: dict) -> None:
    summary = record.get("summary", {})
    source = record.get("source", {})
    index = summary.get("index", {})
    holdings = summary.get("holdings", {})
    market_exposure = summary.get("market_exposure") or {}
    theme_classification = summary.get("theme_classification") or {}

    con.execute(
        """
        INSERT OR REPLACE INTO etf_records (
            etf_key,
            route,
            is_pre_listing_etf,
            fund_name,
            asset_manager,
            index_name,
            index_provider,
            index_description,
            primary_country,
            theme_status,
            theme_bucket,
            structure_tags,
            classification_confidence,
            classification_evidence,
            holdings_available_in_pdf,
            holdings_summary,
            keywords,
            trend_summary,
            missing_info,
            rcept_no,
            rcept_dt,
            corp_code,
            corp_name,
            report_nm,
            fund_code,
            pdf_path,
            first_rcept_dt,
            revision_count,
            db_updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
        )
        """,
        [
            etf_key,
            record.get("route"),
            summary.get("is_pre_listing_etf"),
            summary.get("fund_name"),
            summary.get("asset_manager"),
            index.get("name"),
            index.get("provider"),
            index.get("description"),
            market_exposure.get("primary_country"),
            theme_classification.get("theme_status"),
            theme_classification.get("theme_bucket"),
            json.dumps(theme_classification.get("structure_tags") or [], ensure_ascii=False),
            theme_classification.get("confidence"),
            theme_classification.get("evidence"),
            holdings.get("available_in_pdf"),
            holdings.get("summary"),
            json.dumps(summary.get("keywords") or [], ensure_ascii=False),
            summary.get("trend_summary"),
            json.dumps(summary.get("missing_info") or [], ensure_ascii=False),
            source.get("rcept_no"),
            source.get("rcept_dt"),
            source.get("corp_code"),
            source.get("corp_name"),
            source.get("report_nm"),
            source.get("fund_code"),
            source.get("pdf_path"),
            record.get("first_rcept_dt"),
            record.get("revision_count", 0),
        ],
    )

    con.execute("DELETE FROM etf_holdings WHERE etf_key = ?", [etf_key])
    for seq, item in enumerate(holdings.get("items") or []):
        con.execute(
            "INSERT INTO etf_holdings VALUES (?, ?, ?, ?, ?, ?)",
            [
                etf_key,
                seq,
                item.get("name"),
                item.get("ticker"),
                item.get("exchange"),
                item.get("weight"),
            ],
        )


def sync_to_db(runs_dir: Path, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Sync all ETF records from runs_dir into DuckDB. Returns upserted count."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    records = _load_records(runs_dir)

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA journal_mode=WAL")  # reader-writer 동시성 (reader는 query_only)
        _ensure_schema(con)
        for etf_key, record in records.items():
            _upsert_record(con, etf_key, record)
        con.commit()  # SQLite는 명시 커밋 필요 (DuckDB와 달리 자동 커밋 아님)
    finally:
        con.close()

    return len(records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync ETF records into DuckDB")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    count = sync_to_db(args.runs_dir, args.db_path)
    print(f"Synced {count} ETF records → {args.db_path}")
