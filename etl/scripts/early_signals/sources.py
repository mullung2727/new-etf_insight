"""원문 확보 — 텔레그램 글과 증권사 리포트 PDF 를 버전으로 스냅샷한다 (PLAN §3, §4).

available_at = max(published_at, first_observed_at). 날짜만 있는 리포트는 그날 23:59:59 KST.
시점 게이트는 모드에 따라 다르다(§4.1) — live/replay_observed 는 available_at, 
historical_exploration 은 published_at 기준이다. 지금 처음 관측하는 과거 자료는
first_observed_at 이 늘 cutoff 뒤라 available_at 게이트로는 전건이 탈락한다.
초기 적재에서 과거 게시일을 first_observed_at 으로 복사하지 않는다.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .identity import content_hash, normalize_origin_group, split_units

ROOT = Path(__file__).resolve().parents[3]
TELEGRAM_DB = ROOT / "etl" / "db" / "telegram_public.sqlite3"
REPORT_DIR = ROOT / "etl" / "exports" / "stock_reports"
KST = timezone(timedelta(hours=9))

# 리포트 파일명: {date}_{broker}_{yyyymmdd}_{kind}_{key}.pdf  (download_naver_research.dest_path)
_PDF_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+?)_(\d{8})_(\w+)_(.+)\.pdf$")
_DIR_NAME = re.compile(r"^(.+)_(\w{6})$")


def to_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


HISTORICAL = "historical_exploration"


def gate_time(record: dict[str, Any], mode: str) -> str | None:
    """모드별 시점 게이트에 쓸 시각. historical_exploration 은 발행 시각을 본다(§4.1)."""
    if mode == HISTORICAL:
        return record.get("published_at")
    return record.get("available_at")


def passes_cutoff(record: dict[str, Any], cutoff_at: str, mode: str) -> bool:
    """게이트 시각이 없으면 사용하지 않는다(historical 에서 발행 시각 없는 자료 배제)."""
    moment = gate_time(record, mode)
    return bool(moment) and moment <= cutoff_at


def available_at(published: str | None, first_observed: str) -> str:
    """발행 시각과 최초 관측 시각 중 늦은 값. 발행 시각이 없으면 관측 시각(§4.1)."""
    if not published:
        return first_observed
    return max(published, first_observed)


def _date_to_utc_end_of_day(date_kst: str) -> str:
    """날짜만 있는 자료는 그날 23:59:59 KST 를 보수적 발행 시각으로 본다."""
    day = datetime.strptime(date_kst[:10], "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=KST)
    return to_utc(day)


def capture_telegram(
    cutoff_at: str, *, since_days: int = 90, observed_at: str, db_path: Path = TELEGRAM_DB,
    channels_excluded: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """cutoff 이전 최근 since_days 텔레그램 글을 source_version 후보로 만든다."""
    cutoff_dt = datetime.fromisoformat(cutoff_at)
    start = (cutoff_dt - timedelta(days=since_days)).astimezone(KST).strftime("%Y-%m-%d")
    end = cutoff_dt.astimezone(KST).strftime("%Y-%m-%d")
    excluded = set(channels_excluded)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT channel, post_id, date_kst, text FROM telegram_posts"
            " WHERE date_kst >= ? AND date_kst <= ? AND text IS NOT NULL AND length(text) > 0"
            " ORDER BY date_kst, channel, post_id",
            (start, end + " 23:59:59"),
        ).fetchall()
    finally:
        con.close()

    out = []
    for channel, post_id, date_kst, text in rows:
        if channel in excluded:
            continue
        published = _parse_kst(date_kst)
        if published and published > cutoff_at:
            continue      # cutoff 이후 발행 — 모든 모드에서 사용 거부(§4.1)
        digest = content_hash(text)
        source_key = f"{channel}/{post_id}"
        group_id, independence = normalize_origin_group(
            url=f"https://t.me/{channel}/{post_id}", source_version_id=digest)
        out.append({
            "source_type": "telegram",
            "source_key": source_key,
            "content_hash": digest,
            "source_version_id": hashlib.sha256(f"telegram|{source_key}|{digest}".encode()).hexdigest(),
            "document_key": None,
            "origin_group_id": group_id,
            "independence": independence,
            "published_at": published,
            "published_precision": "timestamp" if published and len(date_kst) > 10 else "date",
            "first_observed_at": observed_at,
            "available_at": available_at(published, observed_at),
            "extracted_text": text,
            "raw": {"channel": channel, "post_id": post_id, "date_kst": date_kst},
            "entity_ids": [],
            "quality": {"chars": len(text)},
        })
    return out


def _parse_kst(date_kst: str) -> str | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(date_kst[:19], fmt).replace(tzinfo=KST)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            return _date_to_utc_end_of_day(date_kst)
        return to_utc(parsed)
    return None


def capture_reports(
    cutoff_at: str, *, since_days: int = 90, observed_at: str, report_dir: Path = REPORT_DIR,
    extract_text=None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """로컬 PDF 를 훑어 리포트 source_version 후보를 만든다.

    네이버 목록 조회는 신규 발견용이고, 과거 재현(replay)에는 이미 받아둔 PDF 만 쓴다.
    페이지 경계를 보존해 텍스트를 추출하고 페이지별 근거 단위를 만든다.
    """
    extract_text = extract_text or extract_pdf_pages
    cutoff_dt = datetime.fromisoformat(cutoff_at)
    start = (cutoff_dt - timedelta(days=since_days)).astimezone(KST).date()
    end = cutoff_dt.astimezone(KST).date()
    stats = {"scanned": 0, "in_window": 0, "parsed": 0, "unreadable": 0, "after_cutoff": 0}

    out = []
    for pdf in sorted(report_dir.glob("*/*.pdf")):
        stats["scanned"] += 1
        matched = _PDF_NAME.match(pdf.name)
        folder = _DIR_NAME.match(pdf.parent.name)
        if not matched or not folder:
            continue
        date_kst, broker, _, _, key = matched.groups()
        name, code = folder.groups()
        published_date = datetime.strptime(date_kst, "%Y-%m-%d").date()
        if published_date < start or published_date > end:
            continue
        stats["in_window"] += 1
        published = _date_to_utc_end_of_day(date_kst)
        if published > cutoff_at:
            stats["after_cutoff"] += 1
            continue
        pages = extract_text(pdf)
        if not pages or not any(page.strip() for page in pages):
            stats["unreadable"] += 1
            continue
        stats["parsed"] += 1
        text = "\f".join(pages)
        digest = content_hash(text)
        source_key = f"{code}/{key}"
        group_id, independence = normalize_origin_group(
            pdf_bytes_hash=_file_hash(pdf), source_version_id=digest)
        out.append({
            "source_type": "report",
            "source_key": source_key,
            "content_hash": digest,
            "source_version_id": hashlib.sha256(f"report|{source_key}|{digest}".encode()).hexdigest(),
            "document_key": key,
            "origin_group_id": group_id,
            "independence": independence,
            "published_at": published,
            "published_precision": "date",
            "first_observed_at": observed_at,
            "available_at": available_at(published, observed_at),
            "extracted_text": text,
            "raw": {"broker": broker, "name": name, "code": code, "date_kst": date_kst},
            "pdf_bytes_hash": _file_hash(pdf),
            "pdf_path": _repo_relative(pdf),
            "entity_ids": [code],
            "quality": {"pages": len(pages), "chars": len(text)},
        })
    return out, stats


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    """페이지 경계를 보존한 텍스트. 실패한 페이지는 빈 문자열로 두고 계속한다."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return []
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def _repo_relative(path: Path) -> str:
    """저장소 안이면 상대경로, 밖(테스트 임시 디렉터리 등)이면 절대경로 그대로."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_document(record: dict[str, Any]) -> list[dict[str, Any]]:
    """근거 단위(문장/표 셀)를 확정한다. 페이지 경계는 \\f 로 구분돼 있다."""
    units: list[dict[str, Any]] = []
    offset = 0
    pages = record["extracted_text"].split("\f")
    single = record["source_type"] != "report"
    for index, page_text in enumerate(pages):
        page_no = None if single else index + 1
        for unit in split_units(page_text, page_no):
            units.append({**unit,
                          "start_char": unit["start_char"] + offset,
                          "end_char": unit["end_char"] + offset})
        offset += len(page_text) + 1
    return units


def inventory_sources(records: list[dict[str, Any]]) -> dict[str, Any]:
    """§12.3 처리량 게이트 입력 — 확보 규모를 먼저 센다."""
    by_type: dict[str, int] = {}
    units = 0
    chars = 0
    for record in records:
        by_type[record["source_type"]] = by_type.get(record["source_type"], 0) + 1
        units += len(snapshot_document(record))
        chars += len(record["extracted_text"])
    return {
        "sources": len(records),
        "by_type": by_type,
        "evidence_units": units,
        "chars": chars,
        "origin_groups": len({r["origin_group_id"] for r in records}),
        "independence_unknown": sum(1 for r in records if r["independence"] == "unknown"),
    }


def check_coverage(
    records: list[dict[str, Any]], cutoff_at: str, stats: dict[str, int], mode: str = HISTORICAL
) -> dict[str, Any]:
    """누락·한도·cutoff 위반을 기록한다. 완전 결과인지 판단하는 근거다(§3.2)."""
    violations = [r["source_version_id"] for r in records
                  if not passes_cutoff(r, cutoff_at, mode)]
    return {
        "cutoff_at": cutoff_at,
        "mode": mode,
        "gate_field": "published_at" if mode == HISTORICAL else "available_at",
        "telegram_sources": sum(1 for r in records if r["source_type"] == "telegram"),
        "report_sources": sum(1 for r in records if r["source_type"] == "report"),
        "report_scan": stats,
        "cutoff_violations": len(violations),
        "coverage_incomplete": bool(stats.get("unreadable")),
    }
