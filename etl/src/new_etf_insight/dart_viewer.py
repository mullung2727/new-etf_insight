from __future__ import annotations

import html
import re
from dataclasses import dataclass

import requests

from new_etf_insight.dart_pdf import fetch_dart_main_html


VIEWER_URL = "https://dart.fss.or.kr/report/viewer.do"


@dataclass(frozen=True)
class ViewerSection:
    rcp_no: str
    dcm_no: str
    ele_id: str
    offset: str
    length: str
    dtd: str


def extract_viewer_sections(main_html: str) -> list[ViewerSection]:
    pattern = re.compile(
        r"node1\['rcpNo'\]\s*=\s*\"(?P<rcp_no>[^\"]+)\";.*?"
        r"node1\['dcmNo'\]\s*=\s*\"(?P<dcm_no>[^\"]+)\";.*?"
        r"node1\['eleId'\]\s*=\s*\"(?P<ele_id>[^\"]+)\";.*?"
        r"node1\['offset'\]\s*=\s*\"(?P<offset>[^\"]+)\";.*?"
        r"node1\['length'\]\s*=\s*\"(?P<length>[^\"]+)\";.*?"
        r"node1\['dtd'\]\s*=\s*\"(?P<dtd>[^\"]+)\";",
        re.S,
    )

    return [
        ViewerSection(
            rcp_no=match.group("rcp_no"),
            dcm_no=match.group("dcm_no"),
            ele_id=match.group("ele_id"),
            offset=match.group("offset"),
            length=match.group("length"),
            dtd=match.group("dtd"),
        )
        for match in pattern.finditer(main_html)
    ]


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<(br|BR)\s*/?>", "\n", raw_html)
    text = re.sub(r"</(p|tr|td|th|div|table|h\d)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def fetch_viewer_section_text(section: ViewerSection) -> str:
    response = requests.get(
        VIEWER_URL,
        params={
            "rcpNo": section.rcp_no,
            "dcmNo": section.dcm_no,
            "eleId": section.ele_id,
            "offset": section.offset,
            "length": section.length,
            "dtd": section.dtd,
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    return html_to_text(response.text)


def extract_fund_code(text: str) -> str | None:
    match = re.search(r"펀드\s*코드\s*[:：]\s*([A-Z0-9-]+)", text, flags=re.I)
    if match:
        return match.group(1).upper()

    table_match = re.search(
        r"펀드\s*코드.{0,200}?\b([A-Z]{1,4}\d{3,5})\b",
        text,
        flags=re.I | re.S,
    )
    if table_match:
        return table_match.group(1).upper()

    return None


def fetch_fund_code_from_dart_viewer(rcept_no: str) -> str | None:
    main_html = fetch_dart_main_html(rcept_no)

    for section in extract_viewer_sections(main_html):
        fund_code = extract_fund_code(fetch_viewer_section_text(section))
        if fund_code:
            return fund_code

    return None


def fetch_dart_viewer_text(rcept_no: str) -> str:
    main_html = fetch_dart_main_html(rcept_no)
    section_texts = [
        fetch_viewer_section_text(section)
        for section in extract_viewer_sections(main_html)
    ]
    return "\n".join(text for text in section_texts if text)


def build_etf_key(corp_code: str, fund_code: str) -> str:
    return f"{corp_code}_{fund_code.upper()}"
