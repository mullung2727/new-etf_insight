"""증권사 종목 리포트 — 자동완성 + 로컬 저장 PDF 목록 + 백그라운드 다운로드.

다운로드 코어는 etl `download_naver_research`(stdlib-only) 재사용. 저장소는 일자별
배치와 동일한 exports/stock_reports/ 공유(파일명 researchId 고유키 → 교차 중복제거).
백그라운드 = 인메모리 job + 스레드(로컬 단일사용자, 동시 최대 3). 재시작 시 진행상태
소실 허용(파일은 남고, 목록의 downloaded 플래그로 확인 가능).
"""
from __future__ import annotations

import re
import sqlite3
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

_FACTS_DB = Path(__file__).resolve().parents[2] / "etl" / "db" / "report_metrics.sqlite3"
_CODE_RE = re.compile(r"^\d{6}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOC_TYPES = ("company", "industry", "market", "invest", "economy", "unknown")
_DEFAULT_SECTOR_TYPES = ("industry", "market", "invest", "economy")

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
    documentId: int
    documentType: str
    relationType: str
    pageFrom: int | None
    pageTo: int | None
    subcategory: str | None = None


class ReportsResponse(BaseModel):
    code: str
    name: str
    total: int
    already: int
    reports: list[ReportItem]


class PrimaryStockItem(BaseModel):
    code: str
    name: str
    pageFrom: int | None
    pageTo: int | None


class DocumentItem(BaseModel):
    documentId: int
    documentType: str
    subcategory: str | None = None
    title: str
    brokerName: str
    publishedDate: str
    primaryStocks: list[PrimaryStockItem]


class DocumentsResponse(BaseModel):
    total: int
    items: list[DocumentItem]


class DocumentsFiltersResponse(BaseModel):
    brokers: list[str]
    subcategories: list[str]
    types: list[str]


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


def _resolve_names(codes) -> dict[str, str]:
    """여러 종목코드 → 종목명 한 번에 조회. 실패 시 코드 그대로."""
    uniq = list(dict.fromkeys(codes))
    if not uniq:
        return {}
    try:
        with krx_cursor() as con:
            rows = con.execute(
                f"SELECT code, name FROM stock_names WHERE code IN "
                f"({','.join('?' for _ in uniq)})",
                uniq,
            ).fetchall()
        mapping = {c: n for c, n in rows}
        return {c: mapping.get(c, c) for c in uniq}
    except Exception:
        return {c: c for c in uniq}


def _resolve_name(code: str) -> str:
    return _resolve_names([code])[code]


def _exports_base() -> Path:
    # 테스트가 dnr.DEFAULT_EXPORT_BASE를 monkeypatch하므로 매번 동적 계산.
    return dnr.DEFAULT_EXPORT_BASE.parent


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


_KEY_TAIL_RE = re.compile(r"_(\d{8}_[A-Za-z]+_\d+)$")  # pstatic 키 예: 20260702_company_957350000


def _split_broker_key(rest: str, known_keys) -> tuple[str, str] | None:
    """파일명 '{증권사}_{키}' → (증권사, 키).

    증권사(sanitize 로 '/'→'_')와 키 모두 '_'를 품을 수 있어 첫 '_' 분할은 틀린다.
    1) 이 종목 facts 키 중 끝이 맞는 것(긴 것 우선) 2) pstatic 키 패턴 3) 구 규칙(첫 '_').
    """
    for k in sorted(known_keys, key=len, reverse=True):
        if len(rest) > len(k) + 1 and rest.endswith("_" + k):
            return rest[: -len(k) - 1], k
    m = _KEY_TAIL_RE.search(rest)
    if m and m.start() > 0:
        return rest[: m.start()], m.group(1)
    parts = rest.split("_", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _scan_local_reports(code: str, since: str | None, until: str | None,
                        name: str | None) -> ReportsResponse:
    """카탈로그 없을 때 폴백: 로컬 저장 PDF 목록(원천 조회 없음)."""
    facts: dict[str, tuple[str | None, str | None]] = {}
    if _FACTS_DB.exists():
        try:
            con = sqlite3.connect(f"file:{_FACTS_DB}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT pdf_key, research_id, title FROM report_api_facts WHERE stock_code=?",
                    (code,),
                ).fetchall()
            finally:
                con.close()
            facts = {k: (rid, title) for k, rid, title in rows}
        except Exception:
            facts = {}
    items: list[ReportItem] = []
    for d in dnr.DEFAULT_EXPORT_BASE.glob(f"*_{code}"):
        if not d.is_dir():
            continue
        for pdf in d.glob("*.pdf"):
            stem = pdf.stem
            if len(stem) <= 11 or stem[10] != "_" or not _DATE_RE.match(stem[:10]):
                continue
            parts = _split_broker_key(stem[11:], facts)
            if parts is None:
                continue
            broker, key = parts
            write_date = stem[:10]
            if since and write_date < since:
                continue
            if until and write_date > until:
                continue
            rid, title = facts.get(key, (None, None))
            items.append(ReportItem(
                researchId=rid or key, brokerName=broker,
                title=title or "", writeDate=write_date, downloaded=True,
                pdfKey=key, documentId=0, documentType="company",
                relationType="primary", pageFrom=None, pageTo=None,
                subcategory=None,
            ))
    items.sort(key=lambda i: i.brokerName)
    items.sort(key=lambda i: i.writeDate, reverse=True)
    name = name or _resolve_name(code)
    return ReportsResponse(code=code, name=name, total=len(items), already=len(items),
                           reports=items)


def _catalog_stock_reports(code: str, since: str | None, until: str | None,
                           name: str | None, include_mentions: bool) -> ReportsResponse:
    """카탈로그 기반 종목 탭. 카탈로그 테이블 없으면 OperationalError(폴백용)."""
    con = sqlite3.connect(f"file:{_FACTS_DB}?mode=ro", uri=True)
    try:
        q = ("SELECT d.document_id, d.document_type, d.subcategory, d.title, "
             "d.broker, d.published_date, d.pdf_key, "
             "s.relation_type, s.page_from, s.page_to "
             "FROM report_document_stocks s "
             "JOIN report_documents d USING(document_id) "
             "WHERE s.stock_code=? AND d.file_status='saved'")
        args: list[str] = [code]
        if not include_mentions:
            q += " AND s.relation_type='primary'"
        if since:
            q += " AND d.published_date >= ?"
            args.append(since)
        if until:
            q += " AND d.published_date <= ?"
            args.append(until)
        rows = con.execute(q, args).fetchall()
        try:
            fact_rows = con.execute(
                "SELECT pdf_key, research_id, title FROM report_api_facts "
                "WHERE stock_code=?",
                (code,),
            ).fetchall()
            facts = {k: (rid, t) for k, rid, t in fact_rows}
        except sqlite3.OperationalError:
            facts = {}
    finally:
        con.close()
    items: list[ReportItem] = []
    for doc_id, dtype, sub, title, broker, pub, pkey, rel, pf, pt in rows:
        if dtype == "company":
            rid, ftitle = facts.get(pkey, (None, None)) if pkey else (None, None)
            research_id = rid or pkey or f"doc-{doc_id}"
            disp_title = title or ftitle or ""
        else:
            research_id = f"doc-{doc_id}"
            disp_title = title or ""
        items.append(ReportItem(
            researchId=research_id, brokerName=broker, title=disp_title,
            writeDate=pub, downloaded=True, pdfKey=pkey or "",
            documentId=doc_id, documentType=dtype, relationType=rel,
            pageFrom=pf, pageTo=pt, subcategory=sub,
        ))
    items.sort(key=lambda i: i.brokerName)
    items.sort(key=lambda i: 0 if i.documentType == "company" else 1)
    items.sort(key=lambda i: i.writeDate, reverse=True)
    name = name or _resolve_name(code)
    return ReportsResponse(code=code, name=name, total=len(items), already=len(items),
                           reports=items)


@router.get("/stock/{code}/reports", response_model=ReportsResponse, operation_id="research_stock_reports")
def stock_reports(code: str, since: str | None = None, until: str | None = None,
                  name: str | None = None,
                  include_mentions: bool = False) -> ReportsResponse:
    """로컬 카탈로그가 권위 소스. 카탈로그 없으면 로컬 파일 스캔으로 폴백."""
    if not _CODE_RE.match(code):
        raise HTTPException(status_code=422, detail="종목코드 6자리")
    if _FACTS_DB.exists():
        try:
            return _catalog_stock_reports(code, since, until, name, include_mentions)
        except sqlite3.OperationalError:
            pass
    return _scan_local_reports(code, since, until, name)


@router.get("/documents/filters", response_model=DocumentsFiltersResponse,
               operation_id="research_documents_filters")
def documents_filters() -> DocumentsFiltersResponse:
    """섹터 화면용 필터 값. 비-company saved 기준."""
    if not _FACTS_DB.exists():
        return DocumentsFiltersResponse(brokers=[], subcategories=[], types=[])
    try:
        con = sqlite3.connect(f"file:{_FACTS_DB}?mode=ro", uri=True)
        try:
            brokers = [r[0] for r in con.execute(
                "SELECT DISTINCT broker FROM report_documents "
                "WHERE document_type != 'company' AND file_status='saved' "
                "ORDER BY broker").fetchall()]
            subs = [r[0] for r in con.execute(
                "SELECT DISTINCT subcategory FROM report_documents "
                "WHERE document_type != 'company' AND file_status='saved' "
                "AND subcategory IS NOT NULL AND subcategory != '' "
                "ORDER BY subcategory").fetchall()]
            types = [r[0] for r in con.execute(
                "SELECT DISTINCT document_type FROM report_documents "
                "WHERE document_type != 'company' AND file_status='saved' "
                "ORDER BY document_type").fetchall()]
        finally:
            con.close()
    except sqlite3.OperationalError:
        return DocumentsFiltersResponse(brokers=[], subcategories=[], types=[])
    return DocumentsFiltersResponse(brokers=brokers, subcategories=subs, types=types)


@router.get("/documents", response_model=DocumentsResponse, operation_id="research_documents")
def documents(types: str | None = None, broker: str | None = None,
              subcategory: str | None = None, since: str | None = None,
              until: str | None = None, q: str | None = None,
              limit: int = Query(default=50, le=200, ge=1),
              offset: int = Query(default=0, ge=0)) -> DocumentsResponse:
    """섹터 전용 화면용 목록. 기본 company 제외."""
    if types is None:
        wanted = list(_DEFAULT_SECTOR_TYPES)
    else:
        wanted = [t.strip() for t in types.split(",") if t.strip()]
        if not wanted or any(t not in _DOC_TYPES for t in wanted):
            raise HTTPException(status_code=422, detail="types 값 오류")
    if not _FACTS_DB.exists():
        return DocumentsResponse(total=0, items=[])
    where = "file_status='saved' AND document_type IN (%s)" % ",".join("?" for _ in wanted)
    args: list[str] = list(wanted)
    if broker:
        where += " AND broker=?"
        args.append(broker)
    if subcategory:
        where += " AND subcategory=?"
        args.append(subcategory)
    if since:
        where += " AND published_date >= ?"
        args.append(since)
    if until:
        where += " AND published_date <= ?"
        args.append(until)
    if q:
        where += " AND title LIKE ?"
        args.append(f"%{q}%")
    try:
        con = sqlite3.connect(f"file:{_FACTS_DB}?mode=ro", uri=True)
        try:
            total = con.execute(
                f"SELECT COUNT(*) FROM report_documents WHERE {where}", args).fetchone()[0]
            rows = con.execute(
                "SELECT document_id, document_type, subcategory, title, broker, "
                f"published_date FROM report_documents WHERE {where} "
                "ORDER BY published_date DESC, document_id DESC LIMIT ? OFFSET ?",
                [*args, limit, offset]).fetchall()
            stocks_by_doc: dict[int, list[tuple]] = {}
            if rows:
                ids = [r[0] for r in rows]
                srows = con.execute(
                    "SELECT document_id, stock_code, page_from, page_to "
                    "FROM report_document_stocks WHERE document_id IN (%s) "
                    "AND relation_type='primary' "
                    "ORDER BY document_id, page_from" % ",".join(
                        "?" for _ in ids), ids).fetchall()
                for did, sc, pf, pt in srows:
                    stocks_by_doc.setdefault(did, []).append((sc, pf, pt))
        finally:
            con.close()
    except sqlite3.OperationalError:
        return DocumentsResponse(total=0, items=[])
    all_codes = [sc for lst in stocks_by_doc.values() for sc, _, _ in lst]
    names = _resolve_names(all_codes)
    items = [DocumentItem(
        documentId=did, documentType=dt, subcategory=sub, title=t or "",
        brokerName=b, publishedDate=pd,
        primaryStocks=[PrimaryStockItem(code=sc, name=names.get(sc, sc),
                                        pageFrom=pf, pageTo=pt)
                       for sc, pf, pt in stocks_by_doc.get(did, [])],
    ) for did, dt, sub, t, b, pd in rows]
    return DocumentsResponse(total=total, items=items)


@router.get("/documents/{document_id}/pdf", operation_id="research_document_pdf")
def document_pdf(document_id: int) -> FileResponse:
    """카탈로그 pdf_path 기준 PDF 서빙. 경로탈출·없음 → 404."""
    if not _FACTS_DB.exists():
        raise HTTPException(status_code=404, detail="파일 없음")
    try:
        con = sqlite3.connect(f"file:{_FACTS_DB}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT pdf_path FROM report_documents WHERE document_id=?",
                (document_id,)).fetchone()
        finally:
            con.close()
    except sqlite3.OperationalError:
        raise HTTPException(status_code=404, detail="파일 없음")
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="파일 없음")
    base = _exports_base().resolve()
    target = (base / row[0]).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="파일 없음")
    return FileResponse(target, media_type="application/pdf")


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
    # 폴더명(저장 당시 네이버 종목명) ≠ 현재 종목명이면 이름 조립 경로가 빗나간다 → 코드로 폴더 탐색
    candidates = [dest] + ([d / dest.name for d in dnr.DEFAULT_EXPORT_BASE.glob(f"*_{code}")]
                           if _CODE_RE.match(code) else [])
    for c in candidates:
        resolved = c.resolve()
        if resolved.is_relative_to(base) and resolved.is_file():
            return FileResponse(resolved, media_type="application/pdf")
    raise HTTPException(status_code=404, detail="파일 없음")


def _run_job(job: dict, since: str | None, until: str | None,
             research_ids: list[str] | None = None) -> None:
    try:
        reports = dnr.list_stock_reports(job["code"], job["name"], since=since, until=until)
        # 기간 필터로 0건인 정상 조회를 원천 오류로 오판하지 않게 — 필터 없이 첫 페이지만 확인
        if not reports and (since or until) and dnr.list_stock_reports(job["code"], job["name"], max_pages=1):
            job["total"] = 0
            job["status"] = "done"
            return
        if not reports:
            job["status"] = "error"
            job["error"] = "원천 목록 0건 — 네이버 종목별 리포트 주소 이동으로 현재 원천 미지원"
            return
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
