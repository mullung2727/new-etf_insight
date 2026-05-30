"""Sync all ETF records from runs/ into DuckDB.

Usage (standalone):
    uv run python scripts/build_db.py [--runs-dir runs] [--db-path db/etf_insight.duckdb]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


DEFAULT_RUNS_DIR = Path(__file__).parent.parent / "runs"
DEFAULT_DB_PATH = Path(__file__).parent.parent / "db" / "etf_insight.duckdb"

_CREATE_ETF_RECORDS = """
CREATE TABLE IF NOT EXISTS etf_records (
    etf_key             VARCHAR PRIMARY KEY,
    route               VARCHAR,
    is_pre_listing_etf  BOOLEAN,
    fund_name           VARCHAR,
    asset_manager       VARCHAR,
    index_name          VARCHAR,
    index_provider      VARCHAR,
    index_description   VARCHAR,
    primary_country     VARCHAR,
    theme_status        VARCHAR,
    theme_bucket        VARCHAR,
    structure_tags      JSON,
    classification_confidence DOUBLE,
    classification_evidence VARCHAR,
    holdings_available_in_pdf BOOLEAN,
    holdings_summary    VARCHAR,
    keywords            JSON,
    trend_summary       VARCHAR,
    missing_info        JSON,
    rcept_no            VARCHAR,
    rcept_dt            VARCHAR,
    corp_code           VARCHAR,
    corp_name           VARCHAR,
    report_nm           VARCHAR,
    fund_code           VARCHAR,
    pdf_path            VARCHAR,
    first_rcept_dt      VARCHAR,
    revision_count      INTEGER,
    db_updated_at       TIMESTAMP
)
"""

_CREATE_ETF_HOLDINGS = """
CREATE TABLE IF NOT EXISTS etf_holdings (
    etf_key  VARCHAR,
    seq      INTEGER,
    name     VARCHAR,
    ticker   VARCHAR,
    exchange VARCHAR,
    weight   VARCHAR,
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


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_CREATE_ETF_RECORDS)
    con.execute(_CREATE_ETF_HOLDINGS)
    existing_columns = {
        row[1]
        for row in con.execute("PRAGMA table_info('etf_records')").fetchall()
    }
    migrations = {
        "theme_status": "ALTER TABLE etf_records ADD COLUMN theme_status VARCHAR",
        "theme_bucket": "ALTER TABLE etf_records ADD COLUMN theme_bucket VARCHAR",
        "structure_tags": "ALTER TABLE etf_records ADD COLUMN structure_tags JSON",
        "classification_confidence": "ALTER TABLE etf_records ADD COLUMN classification_confidence DOUBLE",
        "classification_evidence": "ALTER TABLE etf_records ADD COLUMN classification_evidence VARCHAR",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            con.execute(statement)


def _upsert_record(con: duckdb.DuckDBPyConnection, etf_key: str, record: dict) -> None:
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

    con = duckdb.connect(str(db_path))
    try:
        _ensure_schema(con)
        for etf_key, record in records.items():
            _upsert_record(con, etf_key, record)
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
