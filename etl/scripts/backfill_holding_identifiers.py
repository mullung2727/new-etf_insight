from __future__ import annotations

import argparse
import json
from pathlib import Path

from new_etf_insight.holding_backfill import backfill_weighted_missing_identifiers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing ticker/exchange values in existing ETF record JSON files."
    )
    parser.add_argument(
        "records_root",
        nargs="?",
        default="runs",
        help="A runs directory, one run directory, a records directory, or one record JSON file.",
    )
    parser.add_argument("--bas-dd", help="KRX base date in YYYYMMDD format.")
    parser.add_argument(
        "--report-path",
        default=None,
        help="Report JSON path. Defaults to <records_root>/holding_identifier_backfill_report.json.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Create a report without updating record JSON files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records_root = Path(args.records_root)
    report_path = Path(args.report_path) if args.report_path else _default_report_path(records_root)
    report = backfill_weighted_missing_identifiers(
        records_root,
        bas_dd=args.bas_dd,
        report_path=report_path,
        dry_run=args.dry_run,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _default_report_path(records_root: Path) -> Path:
    if records_root.is_file():
        return records_root.with_name(f"{records_root.stem}_holding_identifier_backfill_report.json")
    if records_root.name == "records":
        return records_root.parent / "holding_identifier_backfill_report.json"
    return records_root / "holding_identifier_backfill_report.json"


if __name__ == "__main__":
    main()
