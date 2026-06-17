from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from new_etf_insight.batch_collect import collect_candidates
from new_etf_insight.dart_pdf import download_representative_prospectus_pdf
from new_etf_insight.dart_viewer import build_etf_key, fetch_fund_code_from_dart_viewer
from new_etf_insight.holding_identifier import HoldingIdentifierResolver
from scripts.build_db import sync_to_db
from scripts.pdf_langgraph.pdf_analysis_langgraph import (
    analyze_pdf,
    is_correction_source,
    review_correction_filing,
    update_record_from_correction,
)


def run_daily_pipeline(
    begin: str,
    end: str,
    records_dir: Path,
    pdf_dir: Path,
    max_pages: int = 50,
    query: str | None = None,
) -> dict[str, Any]:
    candidates = collect_candidates(begin, end, max_pages=max_pages, query=query)
    holding_identifier_resolver = HoldingIdentifierResolver(bas_dd=end)
    results = []

    for candidate in candidates:
        rcept_no = str(candidate["rcept_no"])
        corp_code = str(candidate["corp_code"])
        fund_code = fetch_fund_code_from_dart_viewer(rcept_no)

        if not fund_code:
            results.append(
                {
                    "rcept_no": rcept_no,
                    "action": "failed",
                    "reason": "fund_code_not_found",
                }
            )
            continue

        etf_key = build_etf_key(corp_code, fund_code)
        record_path = records_dir / f"{etf_key}.json"
        filing = {
            **candidate,
            "fund_code": fund_code,
            "etf_key": etf_key,
        }

        previous_record = _read_json(record_path) if record_path.exists() else {}
        previous_rcept_no = str(previous_record.get("source", {}).get("rcept_no", ""))
        if previous_rcept_no == rcept_no:
            results.append(_skipped_result(rcept_no, etf_key, "existing_record"))
            continue

        if is_correction_source(filing) and not record_path.exists():
            results.append(_skipped_result(rcept_no, etf_key, "correction_without_existing_record"))
            continue

        if is_correction_source(filing) and record_path.exists():
            days_since_first_rcept = _days_between(
                str(previous_record.get("first_rcept_dt", "")),
                str(filing.get("rcept_dt", "")),
            )
            if days_since_first_rcept is not None and days_since_first_rcept >= 60:
                results.append(_skipped_result(rcept_no, etf_key, "correction_after_60_days"))
                continue

            review = review_correction_filing(filing)
            if not review["needs_update"]:
                results.append(
                    {
                        "rcept_no": rcept_no,
                        "etf_key": etf_key,
                        "action": "skipped",
                        "reason": review["reason"],
                    }
                )
                continue

            _save_correction_update(filing, record_path, review)
            results.append(
                {
                    "rcept_no": rcept_no,
                    "etf_key": etf_key,
                    "action": "updated",
                    "reason": review["reason"],
                }
            )
            continue

        if record_path.exists():
            results.append(_skipped_result(rcept_no, etf_key, "existing_record"))
            continue

        _save_pdf_analysis(filing, record_path, pdf_dir, holding_identifier_resolver=holding_identifier_resolver)
        results.append(
            {
                "rcept_no": rcept_no,
                "etf_key": etf_key,
                "action": "created",
                "reason": "new_record",
            }
        )

    runs_dir = records_dir.parent.parent
    db_path = runs_dir.parent / "db" / "etf_insight.sqlite3"
    synced = sync_to_db(runs_dir, db_path)

    return {
        "begin": begin,
        "end": end,
        "candidate_count": len(candidates),
        "results": results,
        "db_synced": synced,
        "db_path": str(db_path),
    }


def run_period_as_daily_runs(
    begin: str,
    end: str,
    runs_dir: Path = Path("runs"),
    max_pages: int = 50,
    query: str | None = None,
) -> dict[str, Any]:
    daily_results = []
    for target_date in _iter_dates(begin, end):
        date_text = target_date.strftime("%Y%m%d")
        daily_results.append(
            run_daily_pipeline(
                date_text,
                date_text,
                runs_dir / date_text / "records",
                runs_dir / date_text / "pdfs",
                max_pages=max_pages,
                query=query,
            )
        )

    return {
        "begin": begin,
        "end": end,
        "daily_results": daily_results,
    }


def _skipped_result(rcept_no: str, etf_key: str, reason: str) -> dict[str, str]:
    return {
        "rcept_no": rcept_no,
        "etf_key": etf_key,
        "action": "skipped",
        "reason": reason,
    }


def _days_between(begin: str, end: str) -> int | None:
    try:
        begin_dt = datetime.strptime(begin, "%Y%m%d")
        end_dt = datetime.strptime(end, "%Y%m%d")
    except ValueError:
        return None
    return (end_dt - begin_dt).days


def _iter_dates(begin: str, end: str) -> list[datetime]:
    begin_dt = datetime.strptime(begin, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")
    if begin_dt > end_dt:
        raise ValueError("begin must be before or equal to end")

    dates = []
    current = begin_dt
    while current <= end_dt:
        dates.append(current)
        current += timedelta(days=1)
    return dates


def _save_pdf_analysis(
    filing: dict[str, Any],
    record_path: Path,
    pdf_dir: Path,
    previous_record_path: Path | None = None,
    holding_identifier_resolver: HoldingIdentifierResolver | None = None,
) -> None:
    pdf_path = download_representative_prospectus_pdf(str(filing["rcept_no"]), pdf_dir)
    source = _build_source(filing, pdf_path)
    output = analyze_pdf(
        str(pdf_path),
        source=source,
        holding_identifier_resolver=holding_identifier_resolver,
    )

    previous_record = _read_json(previous_record_path) if previous_record_path else {}
    previous_rcept_no = str(previous_record.get("source", {}).get("rcept_no", ""))
    revision_count = int(previous_record.get("revision_count", 0))
    if previous_rcept_no and previous_rcept_no != str(filing["rcept_no"]):
        revision_count += 1

    record = {
        **output,
        "source": source,
        "first_rcept_dt": previous_record.get("first_rcept_dt", str(filing.get("rcept_dt", ""))),
        "revision_count": revision_count,
    }

    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_correction_update(
    filing: dict[str, Any],
    record_path: Path,
    review: dict[str, Any],
) -> None:
    previous_record = _read_json(record_path)
    output = update_record_from_correction(previous_record, filing, review)
    previous_rcept_no = str(previous_record.get("source", {}).get("rcept_no", ""))
    revision_count = int(previous_record.get("revision_count", 0))
    if previous_rcept_no and previous_rcept_no != str(filing["rcept_no"]):
        revision_count += 1

    record = {
        **output,
        "source": _build_source(filing, Path(str(previous_record.get("source", {}).get("pdf_path", "")))),
        "first_rcept_dt": previous_record.get("first_rcept_dt", str(filing.get("rcept_dt", ""))),
        "revision_count": revision_count,
    }

    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_source(filing: dict[str, Any], pdf_path: Path) -> dict[str, str]:
    return {
        "rcept_no": str(filing.get("rcept_no", "")),
        "rcept_dt": str(filing.get("rcept_dt", "")),
        "corp_code": str(filing.get("corp_code", "")),
        "corp_name": str(filing.get("corp_name", "")),
        "report_nm": str(filing.get("report_nm", "")),
        "fund_code": str(filing.get("fund_code", "")),
        "etf_key": str(filing.get("etf_key", "")),
        "pdf_path": pdf_path.as_posix(),
    }


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
