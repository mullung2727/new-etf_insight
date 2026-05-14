from __future__ import annotations

import argparse
import json
from pathlib import Path

from new_etf_insight.batch_collect import collect_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DART 공시에서 상장 예정 주식 ETF 후보와 PDF 링크를 수집한다.")
    parser.add_argument("--begin", required=True, help="조회 시작일(YYYYMMDD)")
    parser.add_argument("--end", required=True, help="조회 종료일(YYYYMMDD)")
    parser.add_argument("--output", type=Path, help="결과 JSON 저장 경로")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--query", help="검증용 후보 검색어")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    candidates = collect_candidates(args.begin, args.end, max_pages=args.max_pages, query=args.query)
    payload = {"begin": args.begin, "end": args.end, "count": len(candidates), "candidates": candidates}

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
