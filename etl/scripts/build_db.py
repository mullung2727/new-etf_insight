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
    first_rcept_no      TEXT,
    first_collected_at  TEXT,
    db_updated_at       TEXT
)
"""

_CREATE_ETF_FILING_HISTORY = """
CREATE TABLE IF NOT EXISTS etf_filing_history (
    etf_key             TEXT,
    rcept_no            TEXT,
    rcept_dt            TEXT,
    first_collected_at  TEXT,
    action              TEXT,
    reason              TEXT,
    filing_json         TEXT,
    record_json         TEXT,
    PRIMARY KEY (etf_key, rcept_no)
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
    """Scan all runs/*/records/*.json; dedup by etf_key keeping latest (rcept_dt, rcept_no)."""
    best: dict[str, dict] = {}
    for json_path in runs_dir.glob("*/records/*.json"):
        try:
            record = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        etf_key = record.get("source", {}).get("etf_key") or json_path.stem
        source = record.get("source", {})
        current_key = (str(source.get("rcept_dt", "")), str(source.get("rcept_no", "")))
        existing_source = best.get(etf_key, {}).get("source", {})
        existing_key = (str(existing_source.get("rcept_dt", "")), str(existing_source.get("rcept_no", "")))
        if etf_key not in best or current_key >= existing_key:
            best[etf_key] = record
    return best


def _load_all_records(runs_dir: Path) -> list[tuple[str, dict]]:
    """Scan all runs/*/records/*.json without dedup; each file is one filing snapshot."""
    entries: list[tuple[str, dict]] = []
    for json_path in runs_dir.glob("*/records/*.json"):
        try:
            record = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        etf_key = record.get("source", {}).get("etf_key") or json_path.stem
        entries.append((etf_key, record))
    return entries


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_ETF_RECORDS)
    con.execute(_CREATE_ETF_HOLDINGS)
    con.execute(_CREATE_ETF_FILING_HISTORY)
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
        "first_rcept_no": "ALTER TABLE etf_records ADD COLUMN first_rcept_no TEXT",
        "first_collected_at": "ALTER TABLE etf_records ADD COLUMN first_collected_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            con.execute(statement)


def _history_filing_time(record: dict) -> str | None:
    """Per-filing first-observed time from a JSON snapshot. Never fabricate."""
    collected = record.get("collected_at")
    if collected is not None:
        return collected
    source = record.get("source", {})
    if source.get("rcept_no") and source.get("rcept_no") == record.get("first_rcept_no"):
        return record.get("first_collected_at")
    return None


def _upsert_history_preserve(
    con: sqlite3.Connection,
    etf_key: str,
    rcept_no: str,
    rcept_dt: str,
    first_collected_at: str | None,
    action: str,
    reason: str | None,
    filing_json: str,
    record_json: str | None,
) -> None:
    """Insert history row; preserve success snapshot and first-observed time.

    Success rows (record_json NOT NULL) never change. A snapshot-less row
    (failed or any skipped, including terminal) transitions to created/updated
    when a trusted success JSON arrives, usually via sync backfill. Failed to
    skipped and circumstantial skipped to terminal skipped also transition.
    Transitions update action/reason/record_json only, preserving
    first_collected_at, filing_json, rcept_dt (no fabrication of NULL times).
    """
    existing = con.execute(
        "SELECT action, reason, record_json FROM etf_filing_history WHERE etf_key = ? AND rcept_no = ?",
        [etf_key, rcept_no],
    ).fetchone()
    if existing is None:
        con.execute(
            "INSERT INTO etf_filing_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [etf_key, rcept_no, rcept_dt, first_collected_at, action, reason, filing_json, record_json],
        )
        return
    existing_action, existing_reason, existing_record_json = existing[0], existing[1], existing[2]
    if existing_record_json is not None:
        return
    is_circumstantial = existing_action == "skipped" and existing_reason == "correction_without_existing_record"
    if action in ("created", "updated") and record_json is not None:
        con.execute(
            "UPDATE etf_filing_history SET action = ?, reason = ?, record_json = ? WHERE etf_key = ? AND rcept_no = ?",
            [action, reason, record_json, etf_key, rcept_no],
        )
        return
    if existing_action == "failed" and action == "skipped":
        con.execute(
            "UPDATE etf_filing_history SET action = ?, reason = ? WHERE etf_key = ? AND rcept_no = ?",
            [action, reason, etf_key, rcept_no],
        )
        return
    if is_circumstantial and action == "skipped" and reason != "correction_without_existing_record":
        con.execute(
            "UPDATE etf_filing_history SET action = ?, reason = ? WHERE etf_key = ? AND rcept_no = ?",
            [action, reason, etf_key, rcept_no],
        )
        return


def _backfill_history_from_json(con: sqlite3.Connection, etf_key: str, record: dict) -> None:
    source = record.get("source", {})
    rcept_no = source.get("rcept_no")
    if not rcept_no:
        return
    try:
        revision = int(record.get("revision_count", 0))
    except (TypeError, ValueError):
        revision = 0
    _upsert_history_preserve(
        con,
        etf_key,
        str(rcept_no),
        str(source.get("rcept_dt", "")),
        _history_filing_time(record),
        "created" if revision == 0 else "updated",
        "backfilled_from_json",
        json.dumps(source, ensure_ascii=False),
        json.dumps(record, ensure_ascii=False),
    )


def _first_identity_matches(
    rec_no: str | None,
    rec_dt: str | None,
    resolved_no: str | None,
    resolved_dt: str | None,
) -> bool:
    if rec_no is None and rec_dt is None:
        return False
    if resolved_no is None and resolved_dt is None:
        return False
    if rec_no and resolved_no and rec_no != resolved_no:
        return False
    if rec_dt and resolved_dt and rec_dt != resolved_dt:
        return False
    return True


def _first_collected_from_evidence(
    all_records: list[dict],
    latest_first_no: str | None,
    latest_first_dt: str | None,
    latest_first_collected: str | None,
    resolved_no: str | None,
    resolved_dt: str | None,
) -> str | None:
    if resolved_no is None and resolved_dt is None:
        return None
    if latest_first_collected is not None and _first_identity_matches(
        latest_first_no, latest_first_dt, resolved_no, resolved_dt
    ):
        return latest_first_collected
    for record in all_records:
        f_col = record.get("first_collected_at") or None
        if f_col is None:
            continue
        f_no = record.get("first_rcept_no") or None
        f_dt = record.get("first_rcept_dt") or None
        if _first_identity_matches(f_no, f_dt, resolved_no, resolved_dt):
            return f_col
    best = None
    best_key = None
    for record in all_records:
        source = record.get("source", {}) or {}
        src_no = str(source.get("rcept_no") or "") or None
        src_dt = str(source.get("rcept_dt") or "") or None
        if resolved_no and resolved_dt:
            if src_no != resolved_no or src_dt != resolved_dt:
                continue
        elif resolved_no and not resolved_dt:
            if src_no != resolved_no:
                continue
        elif resolved_dt and not resolved_no:
            if src_dt != resolved_dt:
                continue
        else:
            continue
        f_no = record.get("first_rcept_no") or None
        f_dt = record.get("first_rcept_dt") or None
        if (f_no is not None or f_dt is not None) and not _first_identity_matches(
            f_no, f_dt, resolved_no, resolved_dt
        ):
            continue
        key = (src_dt or "", src_no or "")
        if best is None or key < best_key:
            best = record
            best_key = key
    if best is None:
        return None
    return best.get("first_collected_at") or best.get("collected_at") or None


def _resolve_first_meta(
    con: sqlite3.Connection, etf_key: str, latest: dict, all_records: list[dict]
) -> dict:
    """Resolve first_rcept_no/first_rcept_dt/first_collected_at as a pair.

    Priority: existing DB pair -> earliest JSON evidence -> latest memory of
    older filing. Never forge a correction number/time as the original when
    the first date is earlier but the original is missing; keep NULL. Only a
    matching original JSON collected_at or an explicitly preserved
    first_collected_at counts as first-time evidence. A DB-known first time
    never gets overwritten by other JSON times.
    """
    existing = None
    try:
        existing = con.execute(
            "SELECT first_rcept_no, first_rcept_dt, first_collected_at FROM etf_records WHERE etf_key = ?",
            [etf_key],
        ).fetchone()
    except sqlite3.OperationalError:
        existing = None
    db_no = (existing[0] or None) if existing else None
    db_dt = (existing[1] or None) if existing else None
    db_collected = (existing[2] or None) if existing else None

    earliest_no: str | None = None
    earliest_dt: str | None = None
    for record in all_records:
        source = record.get("source", {}) or {}
        no = str(source.get("rcept_no") or "") or None
        dt = str(source.get("rcept_dt") or "") or None
        if no is None and dt is None:
            continue
        key = (dt or "", no or "")
        if earliest_no is None and earliest_dt is None:
            earliest_no, earliest_dt = no, dt
        elif key < ((earliest_dt or ""), (earliest_no or "")):
            earliest_no, earliest_dt = no, dt

    latest_first_no = latest.get("first_rcept_no") or None
    latest_first_dt = latest.get("first_rcept_dt") or None
    latest_first_collected = latest.get("first_collected_at") or None

    resolved_no: str | None = None
    resolved_dt: str | None = None

    if db_no and db_dt:
        resolved_no, resolved_dt = db_no, db_dt
    elif db_dt and not db_no:
        resolved_dt = db_dt
        match_no = None
        for record in all_records:
            source = record.get("source", {}) or {}
            if str(source.get("rcept_dt") or "") == db_dt:
                candidate = str(source.get("rcept_no") or "") or None
                if candidate and (match_no is None or candidate < match_no):
                    match_no = candidate
        resolved_no = match_no
    elif db_no and not db_dt:
        resolved_no = db_no
        match_dt = None
        for record in all_records:
            source = record.get("source", {}) or {}
            if str(source.get("rcept_no") or "") == db_no:
                dt = str(source.get("rcept_dt") or "") or None
                if dt:
                    match_dt = dt
                    break
        resolved_dt = match_dt
    else:
        if latest_first_dt and (earliest_dt is None or latest_first_dt < earliest_dt):
            resolved_no, resolved_dt = latest_first_no, latest_first_dt
        elif latest_first_no and not latest_first_dt:
            match_dt = None
            for record in all_records:
                source = record.get("source", {}) or {}
                if str(source.get("rcept_no") or "") == latest_first_no:
                    dt = str(source.get("rcept_dt") or "") or None
                    if dt:
                        match_dt = dt
                        break
            if match_dt is not None:
                resolved_no, resolved_dt = latest_first_no, match_dt
            else:
                resolved_no, resolved_dt = latest_first_no, None
        else:
            resolved_no, resolved_dt = earliest_no, earliest_dt

    if db_collected is not None:
        resolved_collected = db_collected
    else:
        resolved_collected = _first_collected_from_evidence(
            all_records,
            latest_first_no,
            latest_first_dt,
            latest_first_collected,
            resolved_no,
            resolved_dt,
        )

    resolved = dict(latest)
    resolved["first_rcept_no"] = resolved_no
    resolved["first_rcept_dt"] = resolved_dt
    resolved["first_collected_at"] = resolved_collected
    return resolved


def _upsert_record(con: sqlite3.Connection, etf_key: str, record: dict) -> None:
    source_key = record.get("source", {})
    new_key = (str(source_key.get("rcept_dt", "")), str(source_key.get("rcept_no", "")))
    try:
        existing_source = con.execute(
            "SELECT rcept_dt, rcept_no FROM etf_records WHERE etf_key = ?",
            [etf_key],
        ).fetchone()
    except sqlite3.OperationalError:
        existing_source = None
    if existing_source is not None:
        existing_key = (str(existing_source[0] or ""), str(existing_source[1] or ""))
        if existing_key > new_key:
            return
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
            first_rcept_no,
            first_collected_at,
            db_updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP
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
            record.get("first_rcept_no"),
            record.get("first_collected_at"),
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


def get_history_entry(db_path: Path, etf_key: str, rcept_no: str) -> dict | None:
    """History row for one filing: {action, reason, first_collected_at, rcept_dt, record} or None."""
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return None
    try:
        try:
            row = con.execute(
                "SELECT action, reason, first_collected_at, rcept_dt, record_json FROM etf_filing_history WHERE etf_key = ? AND rcept_no = ?",
                [etf_key, rcept_no],
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        record = None
        if row[4] is not None:
            try:
                record = json.loads(row[4])
            except Exception:
                record = None
        return {"action": row[0], "reason": row[1], "first_collected_at": row[2], "rcept_dt": row[3], "record": record}
    finally:
        con.close()


def get_db_latest_snapshot(db_path: Path, etf_key: str) -> dict | None:
    """Latest success snapshot for ETF from history, with DB first meta applied."""
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return None
    try:
        try:
            rows = con.execute(
                "SELECT rcept_no, rcept_dt, record_json FROM etf_filing_history WHERE etf_key = ? AND record_json IS NOT NULL",
                [etf_key],
            ).fetchall()
        except sqlite3.OperationalError:
            return None
        best = None
        best_key = None
        for rcept_no, rcept_dt, record_json in rows:
            try:
                record = json.loads(record_json)
            except Exception:
                continue
            if not isinstance(record, dict):
                continue
            source = record.get("source", {}) if isinstance(record.get("source", {}), dict) else {}
            key = (str(source.get("rcept_dt") or rcept_dt or ""), str(source.get("rcept_no") or rcept_no or ""))
            if best is None or key > best_key:
                best = record
                best_key = key
        if best is None:
            return None
        try:
            first_row = con.execute(
                "SELECT first_rcept_no, first_rcept_dt, first_collected_at FROM etf_records WHERE etf_key = ?",
                [etf_key],
            ).fetchone()
        except sqlite3.OperationalError:
            first_row = None
        if first_row is not None:
            db_no, db_dt, db_collected = first_row[0], first_row[1], first_row[2]
            best["first_rcept_no"] = db_no or None
            best["first_rcept_dt"] = db_dt or None
            best["first_collected_at"] = db_collected or None
        return best
    finally:
        con.close()


def write_history_observation(
    db_path: Path,
    etf_key: str,
    rcept_no: str,
    rcept_dt: str,
    first_collected_at: str | None,
    action: str,
    reason: str | None,
    filing: dict,
    record: dict | None,
) -> None:
    """Persist one filing observation; existing rows keep snapshot/first-time."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(con)
        _upsert_history_preserve(
            con,
            etf_key,
            rcept_no,
            rcept_dt,
            first_collected_at,
            action,
            reason,
            json.dumps(filing, ensure_ascii=False),
            json.dumps(record, ensure_ascii=False) if record is not None else None,
        )
        con.commit()
    finally:
        con.close()


def ensure_previous_snapshot(db_path: Path, etf_key: str, previous_record: dict) -> None:
    """Preserve the about-to-be-overwritten snapshot if its history row is missing."""
    if not previous_record:
        return
    source = previous_record.get("source", {})
    rcept_no = source.get("rcept_no")
    if not rcept_no:
        return
    try:
        revision = int(previous_record.get("revision_count", 0))
    except (TypeError, ValueError):
        revision = 0
    write_history_observation(
        db_path,
        etf_key,
        str(rcept_no),
        str(source.get("rcept_dt", "")),
        _history_filing_time(previous_record),
        "created" if revision == 0 else "updated",
        "backfilled_from_json",
        dict(source),
        previous_record,
    )


def sync_to_db(runs_dir: Path, db_path: Path = DEFAULT_DB_PATH) -> int:
    """Sync all ETF records from runs_dir into DuckDB. Returns upserted count."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    records = _load_records(runs_dir)
    all_entries = _load_all_records(runs_dir)
    grouped: dict[str, list[dict]] = {}
    for _etf_key, _record in all_entries:
        grouped.setdefault(_etf_key, []).append(_record)

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA journal_mode=WAL")  # reader-writer 동시성 (reader는 query_only)
        _ensure_schema(con)
        for etf_key, record in all_entries:
            _backfill_history_from_json(con, etf_key, record)
        for etf_key, record in records.items():
            resolved = _resolve_first_meta(con, etf_key, record, grouped.get(etf_key, [record]))
            _upsert_record(con, etf_key, resolved)
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
