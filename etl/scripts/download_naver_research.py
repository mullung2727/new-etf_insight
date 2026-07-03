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
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LIST_URL = "https://m.stock.naver.com/api/research/company?page={page}&pageSize={size}"
DETAIL_URL = "https://m.stock.naver.com/api/research/company/{rid}"
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
DEFAULT_EXPORT_BASE = Path(__file__).resolve().parents[1] / "exports" / "stock_reports"
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


def list_reports(date_kst, fetch_fn=_urlopen, page_size=100, max_pages=20) -> list[dict]:
    """대상일(YYYY-MM-DD) 종목분석 리포트 메타 목록. 날짜 desc 페이지네이션, 이전날 만나면 중단."""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        rows = json.loads(fetch_fn(LIST_URL.format(page=page, size=page_size)))
        if not rows:
            break
        stop = False
        for r in rows:
            wd = str(r.get("writeDate", "") or "")
            if wd > date_kst:
                continue          # 대상일 이후(과거일 조회 시) — 건너뛰고 계속
            if wd < date_kst:
                stop = True        # 이전날 도달 → 대상일 끝
                break
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
    return out


def fetch_detail(research_id, fetch_fn=_urlopen) -> dict:
    """상세 → researchContent (attachUrl/content/opinion/goalPrice 포함)."""
    data = json.loads(fetch_fn(DETAIL_URL.format(rid=research_id)))
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


def run(date_kst, out_dir=DEFAULT_EXPORT_BASE, *, list_fetch=_urlopen, detail_fetch=_urlopen,
        pdf_fetch=_urlopen, sleep_fn=time.sleep) -> dict:
    reports = list_reports(date_kst, fetch_fn=list_fetch)
    stats = {"listed": len(reports), "downloaded": 0, "skipped_exists": 0, "no_pdf": 0}
    for r in reports:
        # 안정 키(pdf_key)는 attachUrl 에서만 나오므로 상세를 먼저 받는다.
        detail = fetch_detail(r["researchId"], fetch_fn=detail_fetch)
        url = str(detail.get("attachUrl", "") or "")
        if not url:
            stats["no_pdf"] += 1
            continue
        dest = dest_path(out_dir, r["itemName"], r["itemCode"], date_kst, r["brokerName"], pdf_key(url))
        if dest.exists():
            stats["skipped_exists"] += 1
            continue
        ok = download_pdf(url, dest, fetch_fn=pdf_fetch)
        stats["downloaded" if ok else "no_pdf"] += 1
        sleep_fn(REQUEST_SLEEP)
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
    elif args.date:
        stats = run(args.date, out_dir=out)
        print(f"[naver_research] {args.date} listed={stats['listed']} "
              f"downloaded={stats['downloaded']} skipped={stats['skipped_exists']} no_pdf={stats['no_pdf']}")
    else:
        parser.error("--date 또는 --stock 중 하나 필요")


if __name__ == "__main__":
    main()
