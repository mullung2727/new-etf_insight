from __future__ import annotations

import io
import subprocess

import requests
from pypdf import PdfReader


def download_pdf(url: str) -> bytes:
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        result = subprocess.run(
            ["curl", "-fsSL", "-A", "Mozilla/5.0", url],
            check=True,
            capture_output=True,
        )
        return result.stdout


def extract_pdf_text(pdf_content: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
