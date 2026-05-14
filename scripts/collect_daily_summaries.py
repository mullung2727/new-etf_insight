from __future__ import annotations

import argparse
from pathlib import Path

from new_etf_insight.batch_collect import collect_daily_summaries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="날짜 범위별 상장 예정 주식 ETF 후보를 요약해 JSONL로 저장한다.")
    parser.add_argument("--begin", required=True, help="조회 시작일(YYYYMMDD)")
    parser.add_argument("--end", required=True, help="조회 종료일(YYYYMMDD)")
    parser.add_argument("--output", type=Path, help="결과 JSONL 저장 경로")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--query", help="검증용 후보 검색어")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or Path(f"artifacts/etf_summaries/{args.begin}.jsonl")
    summaries = collect_daily_summaries(args.begin, args.end, output, max_pages=args.max_pages, query=args.query)
    matched = [summary for summary in summaries if summary.get("is_pre_listing_equity_etf")]

    print(f"저장 완료: {output}")
    print(f"전체 결과: {len(summaries)}건")
    print(f"상장 예정 주식 ETF 판정: {len(matched)}건")


if __name__ == "__main__":
    main()
