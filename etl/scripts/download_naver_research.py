"""네이버 리서치 '종목분석' 리포트 PDF 다운로더.

원본 소스 = 네이버 모바일 리서치 API(공식 JSON). stockinfo7(중간 껍데기, JS+애드블록)
경유 없이 원본에서 직접 받는다.

  목록:  m.stock.naver.com/api/research/company?page=N&pageSize=S
         → [{researchId, itemCode, itemName, brokerName, title, writeDate}, ...] (날짜 desc)
  상세:  m.stock.naver.com/api/research/company/{researchId}
         → researchContent.attachUrl (stock.pstatic.net 실제 PDF) + content/opinion/goalPrice

저장: exports/stock_reports/{종목명}_{종목코드}/{날짜}_{증권사}_{researchId}.pdf
멱등: 파일 존재 시 스킵. attachUrl 응답이 %PDF 아니면 저장 안 함(구 HTML 저장 버그 방지).

Usage (from etl/):
    uv run python scripts/download_naver_research.py --date 2026-07-03
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LIST_URL = "https://m.stock.naver.com/api/research/company?page={page}&pageSize={size}"
DETAIL_URL = "https://m.stock.naver.com/api/research/company/{rid}"
SECTOR_CATEGORIES = ("industry", "market", "invest", "economy")
CAT_LIST_URL = "https://m.stock.naver.com/api/research/{category}?page={page}&pageSize={size}"
CAT_DETAIL_URL = "https://m.stock.naver.com/api/research/{category}/{rid}"
# 종목별 과거 리포트(데스크톱 리서치, 종목코드 필터, EUC-KR 서버렌더 HTML, PDF 직링크)
STOCK_LIST_URL = (
    "https://finance.naver.com/research/company_list.naver"
    "?searchType=itemCode&itemName={name}&itemCode={code}&page={page}"
)
_STOCK_ROW_RE = re.compile(
    r'company_read\.naver\?nid=(\d+)[^"]*">([^<]+)</a>'                      # nid, title
    r'.*?<td>([^<]+)</td>'                                                   # broker
    r'\s*<td class="file">\s*<a href="(https://stock\.pstatic\.net/[^"]+\.pdf)"'  # pdf
    r'.*?<td class="date"[^>]*>(\d{2}\.\d{2}\.\d{2})</td>',                  # date YY.MM.DD
    re.S,
)
_UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"}
# 데스크톱 리서치(finance.naver.com)는 모바일 UA에 리포트 테이블을 안 준다 → 데스크톱 UA 필요.
_DESKTOP_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
FETCH_TIMEOUT = 30
REQUEST_SLEEP = 0.4  # 예의상 요청 간 간격
LOOKBACK_DAYS = 7  # 일일 배치 목록 재조회 창(오늘 포함 일수)
DEFAULT_EXPORT_BASE = Path(__file__).resolve().parents[1] / "exports" / "stock_reports"
SECTOR_EXPORT_BASE = Path(__file__).resolve().parents[1] / "exports" / "sector_reports"
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_CODE_RE = re.compile(r"^\d{6}$")


def _urlopen(url: str, ua: dict = _UA) -> bytes:
    req = urllib.request.Request(url, headers=ua)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read()


def _urlopen_text(url: str) -> str:
    # 데스크톱 리서치 페이지는 데스크톱 UA + EUC-KR
    return _urlopen(url, ua=_DESKTOP_UA).decode("euc-kr", "replace")


def _norm_date(yy_mm_dd: str) -> str:
    return "20" + yy_mm_dd.replace(".", "-")   # 26.07.03 → 2026-07-03


def since_from_months(months: int, today=None) -> str:
    """N개월 전 날짜(YYYY-MM-DD). UI가 '개월'로 받을 때 시작일 계산용."""
    from calendar import monthrange
    from datetime import date
    t = today or date.today()
    m, y = t.month - months, t.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(t.day, monthrange(y, m)[1])).isoformat()


def list_stock_reports(code, name, since=None, until=None, max_pages=20, fetch_fn=_urlopen_text) -> list[dict]:
    """한 종목의 과거 종목분석 리포트 메타(날짜 desc). 데스크톱 리서치 종목필터 파싱.

    since/until(YYYY-MM-DD)로 기간 필터. 날짜 desc라 writeDate < since 도달 시 중단.
    PDF 직링크가 목록에 있어 상세 fetch 불필요. 빈 페이지 만나면 중단.
    """
    enc_name = quote(name, encoding="euc-kr")
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        html = fetch_fn(STOCK_LIST_URL.format(name=enc_name, code=code, page=page))
        matched = _STOCK_ROW_RE.findall(html)
        if not matched:
            break
        stop = False
        for nid, title, broker, pdf, date in matched:
            wd = _norm_date(date)
            if until and wd > until:
                continue          # 종료일보다 최신 → 건너뜀
            if since and wd < since:
                stop = True        # 시작일 이전 → 이후는 더 과거뿐, 중단
                break
            if pdf in seen:
                continue          # 페이지 겹침 등 중복 pdf 제거(안정 식별자 기준)
            seen.add(pdf)
            out.append({
                "researchId": nid,
                "itemCode": code,
                "itemName": name,
                "brokerName": broker.strip(),
                "title": title.strip(),
                "writeDate": wd,
                "pdf_url": pdf,
            })
        if stop:
            break
    return out


def run_stock(code, name, out_dir=DEFAULT_EXPORT_BASE, since=None, until=None, max_pages=20, *,
              list_fetch=_urlopen_text, pdf_fetch=_urlopen, sleep_fn=time.sleep) -> dict:
    """한 종목 과거 리포트 PDF 일괄 다운로드. 기간 필터, 멱등(파일 존재 스킵)."""
    reports = list_stock_reports(code, name, since=since, until=until, max_pages=max_pages, fetch_fn=list_fetch)
    stats = {"listed": len(reports), "downloaded": 0, "skipped_exists": 0, "no_pdf": 0}
    for r in reports:
        dest = dest_path(out_dir, r["itemName"], r["itemCode"], r["writeDate"], r["brokerName"], pdf_key(r["pdf_url"]))
        if dest.exists():
            stats["skipped_exists"] += 1
            continue
        ok = download_pdf(r["pdf_url"], dest, fetch_fn=pdf_fetch)
        stats["downloaded" if ok else "no_pdf"] += 1
        sleep_fn(REQUEST_SLEEP)
    return stats


def sanitize(name: str) -> str:
    return _ILLEGAL_CHARS_RE.sub("_", (name or "").strip())


def _load_storage():
    """report_metrics.storage 지연 로드. facts_db 없을 땐 import조차 안 한다."""
    try:
        from scripts.report_metrics import storage
    except ImportError:
        from report_metrics import storage
    return storage


def _load_catalog():
    """report_metrics.catalog 지연 로드. facts_db 없을 땐 import조차 안 한다."""
    try:
        from scripts.report_metrics import catalog
    except ImportError:
        from report_metrics import catalog
    return catalog


def _load_sector_link():
    """report_metrics.sector_link 지연 로드. sector 수집에서만 쓴다."""
    try:
        from scripts.report_metrics import sector_link
    except ImportError:
        from report_metrics import sector_link
    return sector_link


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_reports(date_from, date_to, fetch_fn=_urlopen, page_size=100, max_pages=20,
                 category="company") -> tuple[list[dict], dict]:
    """기간([date_from, date_to], 양끝 포함) 리포트 메타 목록.

    날짜 desc 페이지네이션. date_from 이전을 만나면 중단(past_range), 빈 페이지를
    만나면 중단(empty_page), 끝까지 다 돌면 max_pages. 두 번째 반환값은 수집 메타.
    category='company'면 종목분석(LIST_URL, 6자리 코드 필터). 그 외면 카테고리
    목록(CAT_LIST_URL, 코드 필터 없이 기간 내 행 전부, itemCode/itemName="").
    """
    out: list[dict] = []
    rows_listed = 0
    pages_fetched = 0
    stop_reason = "max_pages"
    for page in range(1, max_pages + 1):
        if category == "company":
            url = LIST_URL.format(page=page, size=page_size)
        else:
            url = CAT_LIST_URL.format(category=category, page=page, size=page_size)
        rows = json.loads(fetch_fn(url))
        pages_fetched = page
        if not rows:
            stop_reason = "empty_page"
            break
        stop = False
        for r in rows:
            wd = str(r.get("writeDate", "") or "")
            if wd > date_to:
                continue          # 기간 이후(최신쪽) — 건너뛰고 계속
            if wd < date_from:
                stop = True        # 기간 이전 도달 → 이후는 더 과거뿐, 중단
                stop_reason = "past_range"
                break
            rows_listed += 1       # 기간 안 전체 행(코드 없는 행 포함)
            if category != "company":
                out.append({
                    "researchId": r["researchId"],
                    "itemCode": "",
                    "itemName": "",
                    "brokerName": str(r.get("brokerName", "") or "").strip(),
                    "title": str(r.get("title", "") or "").strip(),
                    "writeDate": wd,
                })
                continue
            code = str(r.get("itemCode", "") or "").strip()
            if not _CODE_RE.match(code):
                continue           # 종목코드 없는 행(비종목 혼입) 제외
            out.append({
                "researchId": r["researchId"],
                "itemCode": code,
                "itemName": str(r.get("itemName", "") or "").strip(),
                "brokerName": str(r.get("brokerName", "") or "").strip(),
                "title": str(r.get("title", "") or "").strip(),
                "writeDate": wd,
            })
        if stop:
            break
    meta = {"pages_fetched": pages_fetched, "rows_listed": rows_listed,
            "stop_reason": stop_reason, "last_page": pages_fetched}
    return out, meta


def fetch_detail(research_id, fetch_fn=_urlopen, category="company") -> dict:
    """상세 → researchContent (attachUrl/content/opinion/goalPrice 포함)."""
    if category == "company":
        url = DETAIL_URL.format(rid=research_id)
    else:
        url = CAT_DETAIL_URL.format(category=category, rid=research_id)
    data = json.loads(fetch_fn(url))
    return data.get("researchContent", {}) or {}


def pdf_key(pdf_url: str) -> str:
    """pstatic PDF의 안정 식별자(파일 stem). 모바일 API·데스크톱 목록이 부여하는
    researchId/nid 는 서로 다르지만 attachUrl/pdf_url 은 동일 pstatic 파일을 가리킨다
    → 이걸 파일명 키로 써야 두 경로 간 교차 중복제거가 맞는다.
    예: '.../20260702_company_957350000.pdf' → '20260702_company_957350000'
    """
    stem = pdf_url.rstrip("/").split("/")[-1]
    return stem[:-4] if stem.lower().endswith(".pdf") else stem


def dest_path(out_dir: Path, name: str, code: str, date_kst: str, broker: str, key) -> Path:
    """key = pdf_key(pdf_url) (안정 식별자). 파일명: {날짜}_{증권사}_{key}.pdf"""
    return out_dir / f"{sanitize(name)}_{code}" / f"{date_kst}_{sanitize(broker)}_{key}.pdf"


def download_pdf(url: str, dest: Path, fetch_fn=_urlopen) -> bool:
    """PDF 저장. 이미 있으면 False(스킵). 응답이 %PDF 아니면 저장 안 함(False)."""
    if dest.exists():
        return False
    data = fetch_fn(url)
    if data[:4] != b"%PDF":
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def _insert_run_row(con, category, date_from, date_to) -> int:
    cur = con.execute(
        "INSERT INTO report_collection_runs "
        "(source, category, date_from, date_to, started_at, status) "
        "VALUES ('naver_mobile', ?, ?, ?, ?, 'running')",
        (category, date_from, date_to, _utcnow()),
    )
    con.commit()
    return cur.lastrowid


def _fail_run_row(con, run_id, exc) -> None:
    con.execute(
        "UPDATE report_collection_runs SET finished_at = ?, status = 'error', "
        "stop_reason = ? WHERE run_id = ?",
        (_utcnow(), str(exc)[:200], run_id),
    )
    con.commit()


def _finish_run_row(con, run_id, listing, stats) -> None:
    con.execute(
        "UPDATE report_collection_runs SET finished_at = ?, status = ?, "
        "pages_fetched = ?, rows_listed = ?, new_docs = ?, dup_docs = ?, "
        "downloaded = ?, failed = ?, no_pdf = ?, stop_reason = ?, last_page = ? "
        "WHERE run_id = ?",
        (_utcnow(),
         "partial" if listing["stop_reason"] == "max_pages" else "completed",
         listing["pages_fetched"], listing["rows_listed"],
         stats["catalog_new"], stats["catalog_dup"], stats["downloaded"],
         stats["failed"], stats["no_pdf"], listing["stop_reason"],
         listing["last_page"], run_id),
    )
    con.commit()


def _skip_if_known_saved(con, catalog, sid, run_id, stats) -> bool:
    """이미 저장 완료된 문서면 last_seen 갱신·통계 후 True(호출부는 continue)."""
    known = con.execute(
        "SELECT document_id FROM report_sources "
        "WHERE source = 'naver_mobile' AND source_report_id = ?",
        (sid,),
    ).fetchone()
    if known is None:
        return False
    frow = con.execute(
        "SELECT file_status FROM report_documents WHERE document_id = ?",
        (known[0],),
    ).fetchone()
    if frow is None or frow[0] != "saved":
        return False
    catalog.upsert_source(
        con, source="naver_mobile", source_report_id=sid,
        document_id=known[0], run_id=run_id)
    stats["skipped_known"] += 1
    con.commit()
    return True


def _download_status(dest, url, pdf_fetch, sleep_fn, stats) -> str:
    """다운로드 시도 → file_status. stats 직접 갱신."""
    if dest.exists():
        stats["skipped_exists"] += 1
        return "saved"
    try:
        ok = download_pdf(url, dest, fetch_fn=pdf_fetch)
    except Exception:
        stats["failed"] += 1
        status = "failed"
    else:
        if ok:
            stats["downloaded"] += 1
            status = "saved"
        else:
            # download_pdf False = 응답이 %PDF 아님
            stats["not_pdf"] += 1
            status = "not_pdf"
    sleep_fn(REQUEST_SLEEP)
    return status


def _promote_saved(con, doc_id, pdf_rel, sha) -> None:
    """기존 문서가 not_pdf/failed였으면 upsert가 file_status를 안 바꾸므로 직접 승격."""
    try:
        con.execute(
            "UPDATE report_documents SET file_status = 'saved', "
            "pdf_path = ?, sha256 = ? "
            "WHERE document_id = ? AND file_status <> 'saved'",
            (pdf_rel, sha, doc_id),
        )
    except sqlite3.IntegrityError:
        print(f"[naver_research] sha256 충돌로 file_status 승격 생략: doc={doc_id}")


def _collect_loop(reports, category, out_dir, *, detail_fetch, pdf_fetch, sleep_fn,
                  con, storage, catalog, run_id, stats, is_company,
                  link_mod=None, names=None) -> None:
    """company/sector 공용 수집 루프. stats 직접 갱신.

    company 전용: upsert_api_fact, dest_path 규칙, document_stock primary.
    sector 전용: sector dest 규칙, sector_link 연결 판정(link_mod/names).
    con None이면 DB 없이 파일만(종목 일일 배치 facts_db 미지정 경로).
    """
    for r in reports:
        sid = f"{category}:{r['researchId']}"
        if con is not None and _skip_if_known_saved(con, catalog, sid, run_id, stats):
            continue
        # 안정 키(pdf_key)는 attachUrl 에서만 나오므로 상세를 먼저 받는다.
        detail = fetch_detail(r["researchId"], fetch_fn=detail_fetch, category=category)
        url = str(detail.get("attachUrl", "") or "")
        if not url:
            stats["no_pdf"] += 1
            continue
        pkey = pdf_key(url)
        if is_company and con is not None:
            # PDF 존재·다운로드 성공과 무관하게 목표가 저장 → dest 검사보다 먼저.
            storage.upsert_api_fact(con, {
                "pdf_key": pkey,
                "research_id": str(r["researchId"]),
                "stock_code": r["itemCode"],
                "stock_name": r["itemName"],
                "broker": sanitize(r["brokerName"]),
                "report_date": r["writeDate"],
                "title": r["title"],
                "opinion": detail.get("opinion"),
                "goal_price": storage.parse_price(detail.get("goalPrice")),
                "price_at_write": storage.parse_price(detail.get("priceAtWriteDate")),
                "content_html": detail.get("content"),
            })
            con.commit()  # 뒤 리포트의 상세·PDF 네트워크 오류가 앞서 저장한 목표가까지 롤백하지 않게
            stats["facts_saved"] += 1
        if is_company:
            dest = dest_path(out_dir, r["itemName"], r["itemCode"], r["writeDate"], r["brokerName"], pkey)
        else:
            dest = out_dir / category / f"{r['writeDate']}_{sanitize(r['brokerName'])}_{pkey}.pdf"
        file_status = _download_status(dest, url, pdf_fetch, sleep_fn, stats)
        if con is None:
            continue
        sha = catalog.sha256_file(dest) if file_status == "saved" else None
        if is_company:
            pdf_rel = f"stock_reports/{dest.parent.name}/{dest.name}" if file_status == "saved" else None
            list_url = LIST_URL.format(page=1, size=100)
            detail_url = DETAIL_URL.format(rid=r["researchId"])
        else:
            pdf_rel = f"sector_reports/{category}/{dest.name}" if file_status == "saved" else None
            list_url = CAT_LIST_URL.format(category=category, page=1, size=100)
            detail_url = CAT_DETAIL_URL.format(category=category, rid=r["researchId"])
        doc_id, created = catalog.upsert_document(
            con, document_type="company" if is_company else category, title=r["title"],
            broker=sanitize(r["brokerName"]), published_date=r["writeDate"],
            pdf_path=pdf_rel, sha256=sha, pdf_key=pkey,
            file_status=file_status)
        stats["catalog_new" if created else "catalog_dup"] += 1
        if file_status == "saved" and not created:
            _promote_saved(con, doc_id, pdf_rel, sha)
        catalog.upsert_source(
            con, source="naver_mobile", source_report_id=sid,
            document_id=doc_id,
            list_url=list_url,
            detail_url=detail_url,
            pdf_url=url, run_id=run_id)
        if is_company:
            catalog.upsert_document_stock(
                con, document_id=doc_id, stock_code=r["itemCode"],
                relation_type="primary", method="naver_itemcode")
        elif created and file_status == "saved" and link_mod is not None and names is not None:
            try:
                stats["linked"] += link_mod.link_document(
                    con, doc_id, dest, names[0], names[1], broker=r["brokerName"])
            except Exception as exc:
                print(f"[naver_research] link 실패 doc={doc_id}: {exc}")
        con.commit()


def run(date_kst, out_dir=DEFAULT_EXPORT_BASE, *, list_fetch=_urlopen, detail_fetch=_urlopen,
        pdf_fetch=_urlopen, sleep_fn=time.sleep, facts_db=None, lookback_days=LOOKBACK_DAYS) -> dict:
    date_to = date_kst
    date_from = (date.fromisoformat(date_kst) - timedelta(days=lookback_days - 1)).isoformat()
    reports, listing = list_reports(date_from, date_to, fetch_fn=list_fetch)
    stats = {"listed": len(reports), "downloaded": 0, "skipped_exists": 0, "no_pdf": 0,
             "facts_saved": 0, "skipped_known": 0, "not_pdf": 0, "failed": 0,
             "catalog_new": 0, "catalog_dup": 0, "stop_reason": listing["stop_reason"]}
    storage = _load_storage() if facts_db is not None else None
    catalog = _load_catalog() if facts_db is not None else None
    if storage is not None:
        storage.init_db(facts_db)
        con_cm = storage.connect_rw(facts_db)
    else:
        con_cm = nullcontext(None)
    with con_cm as con:
        run_id = None
        if con is not None:
            catalog.init_catalog(con)
            run_id = _insert_run_row(con, "company", date_from, date_to)
        try:
            _collect_loop(reports, "company", out_dir,
                          detail_fetch=detail_fetch, pdf_fetch=pdf_fetch,
                          sleep_fn=sleep_fn, con=con, storage=storage,
                          catalog=catalog, run_id=run_id, stats=stats,
                          is_company=True)
        except Exception as exc:
            if con is not None:
                _fail_run_row(con, run_id, exc)
            raise
        if con is not None:
            _finish_run_row(con, run_id, listing, stats)
    return stats


def run_sector(date_kst, category, out_dir=SECTOR_EXPORT_BASE, *, list_fetch=_urlopen,
               detail_fetch=_urlopen, pdf_fetch=_urlopen, sleep_fn=time.sleep,
               facts_db, lookback_days=LOOKBACK_DAYS, names=None) -> dict:
    """섹터/시황 카테고리(industry/market/invest/economy) 수집. run()과 같은 규칙.

    카탈로그(report_documents/report_sources/document_stocks rule_v1)가 기록처.
    분석 경계(설계 §4): report_api_facts/report_facts/report_estimates에는 절대 쓰지 않는다.
    names: (code_to_name, name_to_code) 주입(테스트용). None이면 load_names() 1회.
    """
    if category not in SECTOR_CATEGORIES:
        raise ValueError(f"sector category 아님: {category!r}")
    link_mod = _load_sector_link()
    if names is None:
        names = link_mod.load_names()
    date_to = date_kst
    date_from = (date.fromisoformat(date_kst) - timedelta(days=lookback_days - 1)).isoformat()
    reports, listing = list_reports(date_from, date_to, fetch_fn=list_fetch, category=category)
    stats = {"listed": len(reports), "downloaded": 0, "skipped_exists": 0, "no_pdf": 0,
             "facts_saved": 0, "skipped_known": 0, "not_pdf": 0, "failed": 0,
             "catalog_new": 0, "catalog_dup": 0, "stop_reason": listing["stop_reason"],
             "linked": 0}
    storage = _load_storage()
    catalog = _load_catalog()
    storage.init_db(facts_db)
    with storage.connect_rw(facts_db) as con:
        catalog.init_catalog(con)
        run_id = _insert_run_row(con, category, date_from, date_to)
        try:
            _collect_loop(reports, category, out_dir,
                          detail_fetch=detail_fetch, pdf_fetch=pdf_fetch,
                          sleep_fn=sleep_fn, con=con, storage=storage,
                          catalog=catalog, run_id=run_id, stats=stats,
                          is_company=False, link_mod=link_mod, names=names)
        except Exception as exc:
            _fail_run_row(con, run_id, exc)
            raise
        _finish_run_row(con, run_id, listing, stats)
    return stats


def _resolve_name(code: str) -> str:
    """종목코드 → 종목명 (stock_names 매핑). 없으면 코드 그대로."""
    try:
        import duckdb
        try:
            from scripts.build_krx_ohlcv import DEFAULT_DB_PATH
            from scripts.stock_names import load_code_to_name
        except ImportError:
            from build_krx_ohlcv import DEFAULT_DB_PATH
            from stock_names import load_code_to_name
        with duckdb.connect(str(DEFAULT_DB_PATH), read_only=True) as con:
            return load_code_to_name(con).get(code, code)
    except Exception:
        return code


def main() -> None:
    parser = argparse.ArgumentParser(description="네이버 리서치 종목 리포트 PDF 다운로드")
    parser.add_argument("--date", help="일자별 모드: 대상일 YYYY-MM-DD (KST)")
    parser.add_argument("--stock", help="종목별 모드: 종목코드 6자리(과거 리포트 일괄)")
    parser.add_argument("--name", help="종목명(미지정 시 stock_names 에서 조회)")
    parser.add_argument("--since", help="종목별 모드 시작일 YYYY-MM-DD(이후 리포트)")
    parser.add_argument("--until", help="종목별 모드 종료일 YYYY-MM-DD(이전 리포트)")
    parser.add_argument("--max-pages", type=int, default=20, help="종목별 모드 페이지 상한(≈30건/페이지)")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    out = args.out_dir or DEFAULT_EXPORT_BASE

    if args.stock:
        name = args.name or _resolve_name(args.stock)
        stats = run_stock(args.stock, name, out_dir=out, since=args.since, until=args.until,
                          max_pages=args.max_pages)
        print(f"[naver_research] stock={args.stock}({name}) since={args.since} until={args.until} "
              f"listed={stats['listed']} downloaded={stats['downloaded']} "
              f"skipped={stats['skipped_exists']} no_pdf={stats['no_pdf']}")
        if stats["listed"] == 0:
            print("[naver_research] 경고: 원천 목록 0건 — finance.naver.com 종목별 목록 주소 이동으로 원천 미지원일 수 있음", file=sys.stderr)
    elif args.date:
        storage = _load_storage()
        stats = run(args.date, out_dir=out, facts_db=storage.DEFAULT_DB)
        print(f"[naver_research] {args.date} listed={stats['listed']} "
              f"downloaded={stats['downloaded']} skipped={stats['skipped_exists']} no_pdf={stats['no_pdf']} "
              f"facts_saved={stats['facts_saved']} skipped_known={stats['skipped_known']} "
              f"not_pdf={stats['not_pdf']} failed={stats['failed']} stop_reason={stats['stop_reason']}")
        partials = ["company"] if stats["stop_reason"] == "max_pages" else []
        for cat in SECTOR_CATEGORIES:
            sstats = run_sector(args.date, cat, facts_db=storage.DEFAULT_DB)
            print(f"[naver_research:{cat}] {args.date} listed={sstats['listed']} "
                  f"downloaded={sstats['downloaded']} skipped_known={sstats['skipped_known']} "
                  f"skipped={sstats['skipped_exists']} no_pdf={sstats['no_pdf']} "
                  f"not_pdf={sstats['not_pdf']} failed={sstats['failed']} "
                  f"linked={sstats['linked']} stop_reason={sstats['stop_reason']}")
            if sstats["stop_reason"] == "max_pages":
                partials.append(cat)
        if partials:
            print(f"[naver_research] PARTIAL: 목록 페이지 상한 도달({','.join(partials)}) — 기간 앞부분 누락 가능",
                  file=sys.stderr)
            sys.exit(3)
    else:
        parser.error("--date 또는 --stock 중 하나 필요")


if __name__ == "__main__":
    main()
