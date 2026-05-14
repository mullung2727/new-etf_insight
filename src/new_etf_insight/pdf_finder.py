from __future__ import annotations

import html
import re
from urllib.parse import urljoin


PDF_LINK_RE = re.compile(r"""href=["'](?P<href>/pdf/download/(?:file|pdf)\.do\?[^"']+)["']""")
OPEN_PDF_RE = re.compile(r"""openPdfDownload\(['"]\d+['"],\s*['"](?P<dcm_no>\d+)['"]\)""")
NODE_DCM_RE = re.compile(r"""node\d+\['dcmNo'\]\s*=\s*["'](?P<dcm_no>\d+)["']""")


def extract_pdf_links(page_html: str, base_url: str = "https://dart.fss.or.kr") -> list[str]:
    links = []
    for match in PDF_LINK_RE.finditer(page_html):
        href = html.unescape(match.group("href"))
        links.append(urljoin(base_url, href))
    return links


def pick_primary_pdf_link(links: list[str]) -> str | None:
    for link in links:
        if "/pdf/download/file.do" in link:
            return link
    return links[0] if links else None


def extract_dcm_no(page_html: str) -> str | None:
    match = OPEN_PDF_RE.search(page_html)
    if match:
        return match.group("dcm_no")

    match = NODE_DCM_RE.search(page_html)
    return match.group("dcm_no") if match else None
