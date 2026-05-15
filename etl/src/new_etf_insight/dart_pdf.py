from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests

from new_etf_insight.models import DART_BASE_URL, DART_VIEW_URL


PDF_DOWNLOAD_MAIN_URL = f"{DART_BASE_URL}/pdf/download/main.do?rcp_no={{rcept_no}}&dcm_no={{dcm_no}}"


def extract_pdf_download_dcm_no(html: str, rcept_no: str) -> str:
    pattern = rf"openPdfDownload\(\s*['\"]{re.escape(rcept_no)}['\"]\s*,\s*['\"](\d+)['\"]\s*\)"
    match = re.search(pattern, html)
    if not match:
        raise ValueError(f"PDF 다운로드 dcmNo를 찾지 못했어: rcept_no={rcept_no}")
    return match.group(1)


def build_pdf_download_main_url(rcept_no: str, dcm_no: str) -> str:
    return PDF_DOWNLOAD_MAIN_URL.format(rcept_no=rcept_no, dcm_no=dcm_no)


def extract_prospectus_file_url(html: str) -> str:
    for href in re.findall(r"""href=["']([^"']*?/pdf/download/file\.do\?[^"']+)["']""", html):
        url = unescape(href)
        if "투자설명서" in url or "%ED%88%AC%EC%9E%90%EC%84%A4%EB%AA%85%EC%84%9C" in url:
            return urljoin(DART_BASE_URL, url)
    raise ValueError("투자설명서 원문 PDF 다운로드 링크를 찾지 못했어")


def fetch_dart_main_html(rcept_no: str) -> str:
    response = requests.get(
        DART_VIEW_URL.format(rcept_no=rcept_no),
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def download_representative_prospectus_pdf(rcept_no: str, output_dir: Path) -> Path:
    html = fetch_dart_main_html(rcept_no)
    dcm_no = extract_pdf_download_dcm_no(html, rcept_no)
    download_main_url = build_pdf_download_main_url(rcept_no, dcm_no)

    response = requests.get(
        download_main_url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": DART_VIEW_URL.format(rcept_no=rcept_no)},
        timeout=30,
    )
    response.raise_for_status()
    pdf_url = extract_prospectus_file_url(response.text)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{rcept_no}_{dcm_no}.pdf"

    response = requests.get(
        pdf_url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": download_main_url},
        timeout=60,
    )
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path
