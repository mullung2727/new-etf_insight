from __future__ import annotations

import argparse
import json
from pathlib import Path

from new_etf_insight.batch_collect import summarize_pdf_file, summarize_pdf_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PDF URL 하나를 상장 예정 주식 ETF 요약 JSON으로 변환한다.")
    parser.add_argument("pdf_url")
    parser.add_argument("--rcept-no", default="")
    parser.add_argument("--dart-url", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = {"rcept_no": args.rcept_no, "dart_url": args.dart_url, "pdf_url": args.pdf_url}
    pdf_path = Path(args.pdf_url)
    if pdf_path.exists():
        summary = summarize_pdf_file(pdf_path, source)
    else:
        summary = summarize_pdf_url(args.pdf_url, source)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
