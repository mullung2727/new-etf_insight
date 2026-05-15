from __future__ import annotations

import argparse
import html
import io
import os
import re
import zipfile
from typing import Any

import requests
from dotenv import load_dotenv


API_URL = "https://opendart.fss.or.kr/api/document.xml"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

DEFAULT_KEYWORDS = (
    "현대차고정피지컬AI",
    "상장지수투자신탁",
    "상장",
    "기초지수",
    "투자목적",
    "투자대상",
    "포트폴리오",
    "구성종목",
    "총보수",
    "보수",
    "지정참가회사",
    "유동성공급자",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DART 원문 XML ZIP에서 키워드 주변 문맥을 확인한다.")
    parser.add_argument("rcept_no", help="DART 접수번호")
    parser.add_argument("--keyword", action="append", default=[], help="추가 검색 키워드. 여러 번 지정 가능")
    parser.add_argument("--context", type=int, default=90, help="키워드 앞뒤 문맥 길이")
    return parser.parse_args()


def get_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("DART_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env에 DART_API_KEY가 없어")
    return api_key


def download_document(api_key: str, rcept_no: str) -> bytes:
    response = requests.get(
        API_URL,
        params={"crtfc_key": api_key, "rcept_no": rcept_no},
        timeout=30,
    )
    response.raise_for_status()
    return response.content


def decode_bytes(content: bytes) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def xml_to_text(xml: str) -> str:
    text = re.sub(r"<[^>]+>", "\n", xml)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def document_files(content: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        files = []
        for info in archive.infolist():
            raw = archive.read(info.filename)
            files.append(
                {
                    "name": info.filename,
                    "size": info.file_size,
                    "text": xml_to_text(decode_bytes(raw)),
                }
            )
        return files


def keyword_contexts(text: str, keyword: str, context: int) -> list[str]:
    normalized_keyword = "".join(keyword.split())
    normalized_text = re.sub(r"\s+", "", text)
    contexts = []

    for match in re.finditer(re.escape(normalized_keyword), normalized_text, flags=re.IGNORECASE):
        start = max(match.start() - context, 0)
        end = min(match.end() + context, len(normalized_text))
        contexts.append(normalized_text[start:end])
        if len(contexts) >= 5:
            break

    if contexts:
        return contexts

    for match in re.finditer(re.escape(keyword), text, flags=re.IGNORECASE):
        start = max(match.start() - context, 0)
        end = min(match.end() + context, len(text))
        contexts.append(re.sub(r"\s+", " ", text[start:end]).strip())
        if len(contexts) >= 5:
            break
    return contexts


def main() -> None:
    args = parse_args()
    api_key = get_api_key()
    keywords = tuple(dict.fromkeys((*DEFAULT_KEYWORDS, *args.keyword)))

    content = download_document(api_key, args.rcept_no)
    files = document_files(content)

    print(f"DART 원문 다운로드 완료: {args.rcept_no}")
    print(f"원문: {DART_VIEW_URL.format(rcept_no=args.rcept_no)}")
    print(f"ZIP 크기: {len(content):,} bytes")
    print(f"파일 수: {len(files)}")

    for index, file in enumerate(files, start=1):
        print(f"\n[{index}] {file['name']} ({file['size']:,} bytes, text={len(file['text']):,} chars)")

    merged_text = "\n".join(file["text"] for file in files)
    print("\n키워드 문맥:")
    for keyword in keywords:
        contexts = keyword_contexts(merged_text, keyword, args.context)
        print(f"\n- {keyword}: {len(contexts)}건")
        for index, context in enumerate(contexts, start=1):
            print(f"  {index}. ...{context}...")


if __name__ == "__main__":
    main()
