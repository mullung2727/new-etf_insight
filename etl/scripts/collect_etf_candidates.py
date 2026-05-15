from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from new_etf_insight.batch_collect import collect_candidates
from new_etf_insight.dart_pdf import download_representative_prospectus_pdf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DART 공시에서 상장 예정 주식 ETF 후보와 PDF 링크를 수집한다.")
    parser.add_argument("--begin", required=True, help="조회 시작일(YYYYMMDD)")
    parser.add_argument("--end", required=True, help="조회 종료일(YYYYMMDD)")
    parser.add_argument("--output", type=Path, help="결과 JSON 저장 경로")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--query", help="검증용 후보 검색어")
    parser.add_argument("--download-pdfs", action="store_true", help='report_nm에 "주식"이 있는 후보의 대표 투자설명서 PDF를 저장한다.')
    parser.add_argument("--pdf-dir", type=Path, default=Path("../downloads/pdfs"), help="PDF 저장 폴더")
    return parser.parse_args()


def should_download_pdf(candidate: dict[str, Any]) -> bool:
    return "주식" in str(candidate.get("report_nm", ""))


def download_candidate_pdfs(candidates: list[dict[str, Any]], pdf_dir: Path) -> list[dict[str, str]]:
    downloads = []
    for candidate in candidates:
        if not should_download_pdf(candidate):
            continue

        pdf_path = download_representative_prospectus_pdf(str(candidate["rcept_no"]), pdf_dir)
        downloads.append(
            {
                "rcept_no": str(candidate.get("rcept_no", "")),
                "corp_name": str(candidate.get("corp_name", "")),
                "report_nm": str(candidate.get("report_nm", "")),
                "pdf_path": str(pdf_path),
            }
        )
    return downloads


def main() -> None:
    args = parse_args()
    candidates = collect_candidates(args.begin, args.end, max_pages=args.max_pages, query=args.query)
    payload = {"begin": args.begin, "end": args.end, "count": len(candidates), "candidates": candidates}

    if args.download_pdfs:
        downloads = download_candidate_pdfs(candidates, args.pdf_dir)
        payload["pdf_download_count"] = len(downloads)
        payload["pdf_downloads"] = downloads

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
