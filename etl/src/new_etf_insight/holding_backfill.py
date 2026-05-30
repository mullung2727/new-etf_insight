from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from new_etf_insight.holding_identifier import HoldingIdentifierResolver, SecurityMasterItem


def backfill_weighted_missing_identifiers(
    records_root: Path,
    *,
    bas_dd: str | None = None,
    report_path: Path | None = None,
    dry_run: bool = False,
    resolver: HoldingIdentifierResolver | None = None,
) -> dict[str, Any]:
    resolver = resolver or HoldingIdentifierResolver(bas_dd=bas_dd)
    record_paths = list(_iter_record_paths(records_root))
    report: dict[str, Any] = {
        "records_root": str(records_root),
        "record_count": len(record_paths),
        "candidate_count": 0,
        "updated_count": 0,
        "unresolved_count": 0,
        "updated": [],
        "unresolved": [],
        "dry_run": dry_run,
    }

    for record_path in record_paths:
        record = _read_json(record_path)
        summary = record.get("summary") or {}
        holdings = summary.get("holdings") or {}
        items = holdings.get("items") or []
        if not isinstance(items, list):
            continue

        primary_country = (summary.get("market_exposure") or {}).get("primary_country")
        changed = False
        for index, item in enumerate(items):
            if not _is_weighted_identifier_candidate(item):
                continue

            report["candidate_count"] += 1
            match = resolver.resolve(str(item.get("name", "")), primary_country=primary_country)
            if match is None:
                report["unresolved_count"] += 1
                report["unresolved"].append(_report_item(record_path, record, index, item))
                continue

            _apply_identifier_match(item, match)
            changed = True
            report["updated_count"] += 1
            report["updated"].append(
                {
                    **_report_item(record_path, record, index, item),
                    "ticker": match.ticker,
                    "exchange": match.exchange,
                    "matched_name": match.name,
                    "identifier_source": match.source,
                }
            )

        if changed and not dry_run:
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def _iter_record_paths(records_root: Path) -> list[Path]:
    if records_root.is_file():
        return [records_root]
    if (records_root / "records").is_dir():
        return sorted((records_root / "records").glob("*.json"))
    direct_records = sorted(records_root.glob("*.json"))
    if direct_records:
        return direct_records
    return sorted(records_root.glob("*/records/*.json"))


def _is_weighted_identifier_candidate(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if not str(item.get("name") or "").strip():
        return False
    if not _has_weight(item.get("weight")):
        return False
    return not (item.get("ticker") and item.get("exchange"))


def _has_weight(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _apply_identifier_match(item: dict[str, Any], match: SecurityMasterItem) -> None:
    item["ticker"] = item.get("ticker") or match.ticker
    item["exchange"] = item.get("exchange") or match.exchange
    item["identifier_source"] = match.source
    item["identifier_confidence"] = "high"


def _report_item(record_path: Path, record: dict[str, Any], index: int, item: dict[str, Any]) -> dict[str, Any]:
    summary = record.get("summary") or {}
    source = record.get("source") or {}
    return {
        "record_path": str(record_path),
        "etf_key": source.get("etf_key"),
        "fund_name": summary.get("fund_name"),
        "holding_index": index,
        "name": item.get("name"),
        "weight": item.get("weight"),
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
