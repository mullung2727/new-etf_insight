from __future__ import annotations

import argparse
import re
from pathlib import Path

from pypdf import PdfReader


DEFAULT_KEYWORDS = (
    "현대차고정피지컬AI",
    "기초지수",
    "투자목적",
    "투자대상",
    "구성종목",
    "포트폴리오",
    "총보수",
    "보수",
    "상장",
    "설정",
    "지정참가",
    "유동성공급",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PDF 텍스트에서 키워드 주변 문맥을 확인한다.")
    parser.add_argument("path", type=Path, help="PDF 파일 경로")
    parser.add_argument("--keyword", action="append", default=[], help="추가 검색 키워드. 여러 번 지정 가능")
    parser.add_argument("--context", type=int, default=180, help="키워드 앞뒤 문맥 길이")
    return parser.parse_args()


def extract_text(path: Path) -> tuple[int, str]:
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return len(reader.pages), text


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def print_keyword_contexts(text: str, keyword: str, context: int) -> None:
    matches = list(re.finditer(re.escape(keyword), text, flags=re.IGNORECASE))
    print(f"\n- {keyword}: {len(matches)}건")
    for index, match in enumerate(matches[:5], start=1):
        start = max(match.start() - context, 0)
        end = min(match.end() + context, len(text))
        print(f"  {index}. ...{compact(text[start:end])}...")


def main() -> None:
    args = parse_args()
    page_count, text = extract_text(args.path)
    keywords = tuple(dict.fromkeys((*DEFAULT_KEYWORDS, *args.keyword)))

    print(f"PDF: {args.path}")
    print(f"pages={page_count}, chars={len(text)}")
    print("\n첫 텍스트 샘플:")
    print(compact(text[:2000]))

    print("\n키워드 문맥:")
    for keyword in keywords:
        print_keyword_contexts(text, keyword, args.context)


if __name__ == "__main__":
    main()
