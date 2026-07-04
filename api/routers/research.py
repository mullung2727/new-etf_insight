"""증권사 종목 리포트 — 자동완성 + 목록(다운로드 여부) + 백그라운드 다운로드.

다운로드 코어는 etl `download_naver_research`(stdlib-only) 재사용. 저장소는 일자별
배치와 동일한 exports/stock_reports/ 공유(파일명 researchId 고유키 → 교차 중복제거).
백그라운드 = 인메모리 job + 스레드(로컬 단일사용자, 동시 최대 3). 재시작 시 진행상태
소실 허용(파일은 남고, 목록의 downloaded 플래그로 확인 가능).
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from duck_watchlist import krx_cursor

# etl 다운로드 함수 재사용 (urllib/re 만 씀 → api venv 에서 import 가능)
_ETL_SCRIPTS = Path(__file__).resolve().parents[2] / "etl" / "scripts"
if str(_ETL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_ETL_SCRIPTS))
import download_naver_research as dnr  # noqa: E402

router = APIRouter(prefix="/research", tags=["research"])

_MAX_ACTIVE = 3
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


class StockCandidate(BaseModel):
    code: str
    name: str


class ReportItem(BaseModel):
    researchId: str
    brokerName: str
    title: str
    writeDate: str
    downloaded: bool
    pdfKey: str


class ReportsResponse(BaseModel):
    code: str
    name: str
    total: int
    already: int
    reports: list[ReportItem]


class DownloadRequest(BaseModel):
    name: str | None = None
    since: str | None = None
    until: str | None = None
    researchIds: list[str] | None = None  # 지정 시 해당 리포트만; None=기간 전체(기존 동작)


class JobStatus(BaseModel):
    job_id: str
    status: str  # running | done | error
    code: str
    name: str
    total: int
    downloaded: int
    skipped: int
    failed: int
    error: str | None = None


def _resolve_name(code: str) -> str:
    try:
        with krx_cursor() as con:
            row = con.execute("SELECT name FROM stock_names WHERE code=?", [code]).fetchone()
        return row[0] if row else code
    except Exception:
        return code


def _dest_for(report: dict) -> Path:
    return dnr.dest_path(
        dnr.DEFAULT_EXPORT_BASE, report["itemName"], report["itemCode"],
        report["writeDate"], report["brokerName"], dnr.pdf_key(report["pdf_url"]),
    )


@router.get("/search", response_model=list[StockCandidate], operation_id="research_search")
def search(q: str = Query(min_length=1), limit: int = 20) -> list[StockCandidate]:
    """종목코드/종목명 부분일치 자동완성 후보(stock_names)."""
    try:
        with krx_cursor() as con:
            rows = con.execute(
                "SELECT code, name FROM stock_names "
                "WHERE code LIKE ? OR name LIKE ? "
                "ORDER BY (code LIKE ?) DESC, name LIMIT ?",
                [f"{q}%", f"%{q}%", f"{q}%", limit],
            ).fetchall()
        return [StockCandidate(code=c, name=n) for c, n in rows]
    except Exception:
        return []


@router.get("/stock/{code}/reports", response_model=ReportsResponse, operation_id="research_stock_reports")
def stock_reports(code: str, since: str | None = None, until: str | None = None,
                  name: str | None = None) -> ReportsResponse:
    """한 종목 리포트 목록(기간 필터) + 각 건 다운로드 여부(디스크 대조)."""
    name = name or _resolve_name(code)
    reports = dnr.list_stock_reports(code, name, since=since, until=until)
    items: list[ReportItem] = []
    already = 0
    for r in reports:
        dl = _dest_for(r).exists()
        already += int(dl)
        items.append(ReportItem(
            researchId=r["researchId"], brokerName=r["brokerName"],
            title=r["title"], writeDate=r["writeDate"], downloaded=dl,
            pdfKey=dnr.pdf_key(r["pdf_url"]),
        ))
    return ReportsResponse(code=code, name=name, total=len(items), already=already, reports=items)


@router.get("/stock/{code}/reports/{research_id}/pdf", operation_id="research_stock_report_pdf")
def stock_report_pdf(code: str, research_id: str,
                     write_date: str = Query(alias="writeDate"),
                     broker_name: str = Query(alias="brokerName"),
                     pdf_key: str = Query(alias="pdfKey"),
                     name: str | None = None) -> FileResponse:
    """이미 받은 PDF 원본을 그대로 서빙. 네이버 재조회 없음 — reports 응답이 이미 준 경로정보로 재조립.
    research_id는 라우팅/가독성용일 뿐 경로 조립엔 안 쓰임(파일 위치는 write_date/broker_name/pdf_key로만 결정)."""
    name = name or _resolve_name(code)
    dest = dnr.dest_path(dnr.DEFAULT_EXPORT_BASE, name, code, write_date, broker_name, pdf_key)
    base = dnr.DEFAULT_EXPORT_BASE.resolve()
    resolved = dest.resolve()
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="파일 없음")
    return FileResponse(resolved, media_type="application/pdf")


def _run_job(job: dict, since: str | None, until: str | None,
             research_ids: list[str] | None = None) -> None:
    try:
        reports = dnr.list_stock_reports(job["code"], job["name"], since=since, until=until)
        if research_ids is not None:
            wanted = set(research_ids)
            reports = [r for r in reports if r["researchId"] in wanted]
        job["total"] = len(reports)
        for r in reports:
            dest = _dest_for(r)
            if dest.exists():
                job["skipped"] += 1
                continue
            try:
                ok = dnr.download_pdf(r["pdf_url"], dest)
                job["downloaded" if ok else "failed"] += 1
            except Exception:
                job["failed"] += 1
            time.sleep(dnr.REQUEST_SLEEP)
        job["status"] = "done"
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(exc)


@router.post("/stock/{code}/download", response_model=JobStatus, operation_id="research_download")
def start_download(code: str, req: DownloadRequest) -> JobStatus:
    """백그라운드 다운로드 시작 → job_id. 동시 최대 3개(초과 시 429). 이미 받은 건 스킵."""
    with _LOCK:
        active = sum(1 for j in _JOBS.values() if j["status"] == "running")
        if active >= _MAX_ACTIVE:
            raise HTTPException(status_code=429, detail="동시 다운로드 최대 3개")
        name = req.name or _resolve_name(code)
        job_id = uuid.uuid4().hex[:12]
        job = {"job_id": job_id, "status": "running", "code": code, "name": name,
               "total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "error": None}
        _JOBS[job_id] = job
    threading.Thread(target=_run_job, args=(job, req.since, req.until, req.researchIds),
                     daemon=True).start()
    return JobStatus(**job)


@router.get("/jobs/{job_id}", response_model=JobStatus, operation_id="research_job_status")
def job_status(job_id: str) -> JobStatus:
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job 없음")
    return JobStatus(**job)
