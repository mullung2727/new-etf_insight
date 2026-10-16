from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from new_etf_insight.batch_collect import collect_candidates
from new_etf_insight.dart_pdf import download_representative_prospectus_pdf
from new_etf_insight.dart_viewer import build_etf_key, fetch_fund_code_from_dart_viewer
from new_etf_insight.holding_identifier import HoldingIdentifierResolver
from scripts.build_db import (
    ensure_previous_snapshot,
    get_db_latest_snapshot,
    get_history_entry,
    sync_to_db,
    write_history_observation,
)
from scripts.pdf_langgraph.pdf_analysis_langgraph import (
    analyze_pdf,
    is_correction_source,
    review_correction_filing,
    update_record_from_correction,
)

_AUTO_COLLECTED_AT: Any = object()


def run_daily_pipeline(
    begin: str,
    end: str,
    records_dir: Path,
    pdf_dir: Path,
    max_pages: int = 50,
    query: str | None = None,
) -> dict[str, Any]:
    candidates = collect_candidates(begin, end, max_pages=max_pages, query=query)
    candidates.sort(key=lambda item: (str(item.get("rcept_dt", "")), str(item.get("rcept_no", ""))))
    holding_identifier_resolver = HoldingIdentifierResolver(bas_dd=end)
    runs_dir = records_dir.parent.parent
    db_path = runs_dir.parent / "db" / "etf_insight.sqlite3"
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
        rcept_dt = str(filing.get("rcept_dt", ""))

        json_records = _scan_etf_records(etf_key, records_dir, runs_dir)
        json_latest = max(json_records, key=_record_sort_key) if json_records else {}
        earliest_record = min(json_records, key=_record_sort_key) if json_records else {}
        if any(str(item.get("source", {}).get("rcept_no", "")) == rcept_no for item in json_records):
            results.append(_skipped_result(rcept_no, etf_key, "existing_record"))
            continue

        db_record = get_db_latest_snapshot(db_path, etf_key) or {}
        if json_latest and db_record:
            json_no = str(json_latest.get("source", {}).get("rcept_no", ""))
            db_no = str(db_record.get("source", {}).get("rcept_no", ""))
            if json_no and json_no == db_no:
                latest_record = dict(json_latest)
                use_db_first = True
            elif _record_sort_key(json_latest) >= _record_sort_key(db_record):
                latest_record = dict(json_latest)
                use_db_first = True
            else:
                latest_record = dict(db_record)
                use_db_first = False
            if use_db_first:
                if db_record.get("first_rcept_no"):
                    latest_record["first_rcept_no"] = db_record["first_rcept_no"]
                if db_record.get("first_rcept_dt"):
                    latest_record["first_rcept_dt"] = db_record["first_rcept_dt"]
                if db_record.get("first_collected_at") is not None:
                    latest_record["first_collected_at"] = db_record["first_collected_at"]
        elif json_latest:
            latest_record = json_latest
        elif db_record:
            latest_record = dict(db_record)
        else:
            latest_record = {}

        history = get_history_entry(db_path, etf_key, rcept_no)
        if history is not None and history["action"] != "failed":
            if history["action"] in ("created", "updated") and not json_records:
                snapshot = history.get("record")
                if snapshot is not None and isinstance(snapshot, dict):
                    snapshot_key = _record_sort_key(snapshot)
                    db_key = _record_sort_key(db_record) if db_record else None
                    if db_key is not None and db_key > snapshot_key:
                        results.append(_skipped_result(rcept_no, etf_key, "existing_record"))
                        continue
                    try:
                        record_path.parent.mkdir(parents=True, exist_ok=True)
                        record_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception as exc:
                        results.append(
                            {
                                "rcept_no": rcept_no,
                                "etf_key": etf_key,
                                "action": "failed",
                                "reason": "snapshot_restore_failed",
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                        )
                        continue
                    results.append(_skipped_result(rcept_no, etf_key, "existing_record"))
                    continue
                pass  # No snapshot to reuse; allow rebuild below.
            elif history["reason"] == "correction_without_existing_record" and latest_record:
                pass  # Record arrived since the circumstantial skip; re-process.
            else:
                reason = history["reason"] if history["action"] == "skipped" else "existing_record"
                results.append(_skipped_result(rcept_no, etf_key, str(reason or "existing_record")))
                continue

        if history is None:
            collected_at: str | None = _utc_now_iso()
        else:
            observed = history.get("first_collected_at")
            collected_at = observed if observed not in (None, "") else None

        if is_correction_source(filing) and not latest_record:
            write_history_observation(
                db_path, etf_key, rcept_no, rcept_dt, collected_at,
                "skipped", "correction_without_existing_record", filing, None,
            )
            results.append(_skipped_result(rcept_no, etf_key, "correction_without_existing_record"))
            continue

        if is_correction_source(filing) and latest_record:
            days_since_first_rcept = _days_between(
                str(latest_record.get("first_rcept_dt", "")),
                rcept_dt,
            )
            if days_since_first_rcept is not None and days_since_first_rcept >= 60:
                write_history_observation(
                    db_path, etf_key, rcept_no, rcept_dt, collected_at,
                    "skipped", "correction_after_60_days", filing, None,
                )
                results.append(_skipped_result(rcept_no, etf_key, "correction_after_60_days"))
                continue

            if _is_stale_filing(filing, latest_record):
                write_history_observation(
                    db_path, etf_key, rcept_no, rcept_dt, collected_at,
                    "skipped", "stale_filing", filing, None,
                )
                results.append(_skipped_result(rcept_no, etf_key, "stale_filing"))
                continue

            try:
                review = review_correction_filing(filing)
            except Exception as exc:
                write_history_observation(
                    db_path, etf_key, rcept_no, rcept_dt, collected_at,
                    "failed", "correction_review_failed", filing, None,
                )
                results.append(
                    {
                        "rcept_no": rcept_no,
                        "etf_key": etf_key,
                        "action": "failed",
                        "reason": "correction_review_failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            if not review["needs_update"]:
                write_history_observation(
                    db_path, etf_key, rcept_no, rcept_dt, collected_at,
                    "skipped", review["reason"], filing, None,
                )
                results.append(
                    {
                        "rcept_no": rcept_no,
                        "etf_key": etf_key,
                        "action": "skipped",
                        "reason": review["reason"],
                    }
                )
                continue

            ensure_previous_snapshot(db_path, etf_key, latest_record)
            try:
                record = _save_correction_update(
                    filing, record_path, review,
                    previous_record=latest_record,
                    earliest_record=earliest_record,
                    collected_at=collected_at,
                )
            except Exception as exc:
                write_history_observation(
                    db_path, etf_key, rcept_no, rcept_dt, collected_at,
                    "failed", "correction_update_failed", filing, None,
                )
                results.append(
                    {
                        "rcept_no": rcept_no,
                        "etf_key": etf_key,
                        "action": "failed",
                        "reason": "correction_update_failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            write_history_observation(
                db_path, etf_key, rcept_no, rcept_dt, collected_at,
                "updated", review["reason"], filing, record,
            )
            results.append(
                {
                    "rcept_no": rcept_no,
                    "etf_key": etf_key,
                    "action": "updated",
                    "reason": review["reason"],
                }
            )
            continue

        if record_path.exists() or latest_record:
            write_history_observation(
                db_path, etf_key, rcept_no, rcept_dt, collected_at,
                "skipped", "existing_record", filing, None,
            )
            results.append(_skipped_result(rcept_no, etf_key, "existing_record"))
            continue

        try:
            record = _save_pdf_analysis(
                filing, record_path, pdf_dir,
                holding_identifier_resolver=holding_identifier_resolver,
                collected_at=collected_at,
            )
        except Exception as exc:
            write_history_observation(
                db_path, etf_key, rcept_no, rcept_dt, collected_at,
                "failed", "pdf_analysis_failed", filing, None,
            )
            results.append(
                {
                    "rcept_no": rcept_no,
                    "etf_key": etf_key,
                    "action": "failed",
                    "reason": "pdf_analysis_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            continue

        write_history_observation(
            db_path, etf_key, rcept_no, rcept_dt, collected_at,
            "created", "new_record", filing, record,
        )
        results.append(
            {
                "rcept_no": rcept_no,
                "etf_key": etf_key,
                "action": "created",
                "reason": "new_record",
            }
        )

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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    source = record.get("source", {})
    return (str(source.get("rcept_dt", "")), str(source.get("rcept_no", "")))


def _scan_etf_records(etf_key: str, records_dir: Path, runs_dir: Path) -> list[dict[str, Any]]:
    paths = [records_dir / f"{etf_key}.json"]
    try:
        if runs_dir.is_dir():
            paths.extend(sorted(runs_dir.glob(f"*/records/{etf_key}.json")))
    except OSError:
        pass
    records = []
    seen = set()
    for path in paths:
        marker = str(path)
        if marker in seen:
            continue
        seen.add(marker)
        if not path.is_file():
            continue
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return records


def _is_stale_filing(filing: dict[str, Any], latest_record: dict[str, Any]) -> bool:
    latest_source = latest_record.get("source", {})
    return (str(filing.get("rcept_dt", "")), str(filing.get("rcept_no", ""))) < (
        str(latest_source.get("rcept_dt", "")),
        str(latest_source.get("rcept_no", "")),
    )


def _pipeline_first_collected(
    previous_record: dict[str, Any],
    earliest_record: dict[str, Any],
    resolved_no: str | None,
    resolved_dt: str | None,
) -> str | None:
    if resolved_no is None and resolved_dt is None:
        return None
    for rec in (previous_record, earliest_record):
        if not rec:
            continue
        f_col = rec.get("first_collected_at") or None
        if f_col is None:
            continue
        f_no = rec.get("first_rcept_no") or None
        f_dt = rec.get("first_rcept_dt") or None
        if f_no is None and f_dt is None:
            continue
        if f_no and resolved_no and f_no != resolved_no:
            continue
        if f_dt and resolved_dt and f_dt != resolved_dt:
            continue
        return f_col
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for rec in (earliest_record, previous_record):
        if not rec:
            continue
        src = rec.get("source", {}) or {}
        src_no = str(src.get("rcept_no") or "") or None
        src_dt = str(src.get("rcept_dt") or "") or None
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
        f_no = rec.get("first_rcept_no") or None
        f_dt = rec.get("first_rcept_dt") or None
        if f_no is not None or f_dt is not None:
            if f_no and resolved_no and f_no != resolved_no:
                continue
            if f_dt and resolved_dt and f_dt != resolved_dt:
                continue
        candidates.append((src_dt or "", src_no or "", rec))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    best = candidates[0][2]
    return best.get("first_collected_at") or best.get("collected_at") or None


def _first_meta(
    previous_record: dict[str, Any],
    earliest_record: dict[str, Any],
    filing: dict[str, Any],
    collected_at: str | None,
) -> dict[str, Any]:
    if not previous_record:
        return {
            "first_rcept_no": str(filing.get("rcept_no", "")),
            "first_rcept_dt": str(filing.get("rcept_dt", "")),
            "first_collected_at": collected_at,
        }
    prev_source = previous_record.get("source", {}) or {}
    early_source = (earliest_record or {}).get("source", {}) or {}
    explicit_dt = previous_record.get("first_rcept_dt") or (earliest_record or {}).get("first_rcept_dt") or None
    early_dt = str(early_source.get("rcept_dt") or "") or None
    early_no = str(early_source.get("rcept_no") or "") or None
    prev_dt = str(prev_source.get("rcept_dt") or "") or None
    prev_no = str(prev_source.get("rcept_no") or "") or None
    if explicit_dt and early_dt and explicit_dt > early_dt:
        first_dt = early_dt
        explicit_no = None
    else:
        first_dt = explicit_dt or early_dt or prev_dt
        explicit_no = previous_record.get("first_rcept_no") or (earliest_record or {}).get("first_rcept_no") or None
    if explicit_no:
        first_no = explicit_no
    else:
        if early_dt and early_no and early_dt == first_dt:
            first_no = early_no
        elif prev_dt and prev_no and prev_dt == first_dt:
            first_no = prev_no
        else:
            first_no = None
    return {
        "first_rcept_no": first_no,
        "first_rcept_dt": first_dt,
        "first_collected_at": _pipeline_first_collected(
            previous_record, earliest_record or {}, first_no, first_dt
        ),
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
    previous_record: dict[str, Any] | None = None,
    earliest_record: dict[str, Any] | None = None,
    collected_at: Any = _AUTO_COLLECTED_AT,
) -> dict[str, Any]:
    pdf_path = download_representative_prospectus_pdf(str(filing["rcept_no"]), pdf_dir)
    source = _build_source(filing, pdf_path)
    output = analyze_pdf(
        str(pdf_path),
        source=source,
        holding_identifier_resolver=holding_identifier_resolver,
    )

    if previous_record is None:
        previous_record = _read_json(previous_record_path) if previous_record_path else {}
    previous_rcept_no = str(previous_record.get("source", {}).get("rcept_no", ""))
    revision_count = int(previous_record.get("revision_count", 0))
    if previous_rcept_no and previous_rcept_no != str(filing["rcept_no"]):
        revision_count += 1
    if collected_at is _AUTO_COLLECTED_AT:
        collected_at = _utc_now_iso()
    elif collected_at == "":
        collected_at = None

    record = {
        **output,
        "source": source,
        **_first_meta(previous_record, earliest_record or {}, filing, collected_at),
        "collected_at": collected_at,
        "revision_count": revision_count,
    }

    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def _save_correction_update(
    filing: dict[str, Any],
    record_path: Path,
    review: dict[str, Any],
    previous_record: dict[str, Any] | None = None,
    earliest_record: dict[str, Any] | None = None,
    collected_at: Any = _AUTO_COLLECTED_AT,
) -> dict[str, Any]:
    if previous_record is None:
        previous_record = _read_json(record_path)
    output = update_record_from_correction(previous_record, filing, review)
    previous_rcept_no = str(previous_record.get("source", {}).get("rcept_no", ""))
    revision_count = int(previous_record.get("revision_count", 0))
    if previous_rcept_no and previous_rcept_no != str(filing["rcept_no"]):
        revision_count += 1
    if collected_at is _AUTO_COLLECTED_AT:
        collected_at = _utc_now_iso()
    elif collected_at == "":
        collected_at = None

    record = {
        **output,
        "source": _build_source(filing, Path(str(previous_record.get("source", {}).get("pdf_path", "")))),
        **_first_meta(previous_record, earliest_record or {}, filing, collected_at),
        "collected_at": collected_at,
        "revision_count": revision_count,
    }

    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


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
