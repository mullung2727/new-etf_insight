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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LIST_URL = "https://m.stock.naver.com/api/research/company?page={page}&pageSize={size}"
DETAIL_URL = "https://m.stock.naver.com/api/research/company/{rid}"
_UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"}
FETCH_TIMEOUT = 30
REQUEST_SLEEP = 0.4  # 예의상 요청 간 간격
DEFAULT_EXPORT_BASE = Path(__file__).resolve().parents[1] / "exports" / "stock_reports"
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_CODE_RE = re.compile(r"^\d{6}$")


def _urlopen(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read()


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


def dest_path(out_dir: Path, name: str, code: str, date_kst: str, broker: str, research_id) -> Path:
    return out_dir / f"{sanitize(name)}_{code}" / f"{date_kst}_{sanitize(broker)}_{research_id}.pdf"


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
        # dest 는 목록 데이터만으로 계산 가능 → 이미 있으면 상세 API도 건너뜀(멱등 + 요청 최소).
        dest = dest_path(out_dir, r["itemName"], r["itemCode"], date_kst, r["brokerName"], r["researchId"])
        if dest.exists():
            stats["skipped_exists"] += 1
            continue
        detail = fetch_detail(r["researchId"], fetch_fn=detail_fetch)
        url = str(detail.get("attachUrl", "") or "")
        if not url:
            stats["no_pdf"] += 1
            continue
        ok = download_pdf(url, dest, fetch_fn=pdf_fetch)
        stats["downloaded" if ok else "no_pdf"] += 1
        sleep_fn(REQUEST_SLEEP)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="네이버 리서치 종목 리포트 PDF 다운로드")
    parser.add_argument("--date", required=True, help="대상일 YYYY-MM-DD (KST)")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    out = args.out_dir or DEFAULT_EXPORT_BASE
    stats = run(args.date, out_dir=out)
    print(f"[naver_research] {args.date} listed={stats['listed']} "
          f"downloaded={stats['downloaded']} skipped={stats['skipped_exists']} no_pdf={stats['no_pdf']}")


if __name__ == "__main__":
    main()
