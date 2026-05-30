#  uv run python scripts/pdf_langgraph/pdf_analysis_langgraph.py

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any, TypedDict
import argparse

from langgraph.graph import END, StateGraph

from new_etf_insight.dart_viewer import fetch_dart_viewer_text
from new_etf_insight.holding_identifier import HoldingIdentifierResolver
from new_etf_insight.llm import generate_json
from new_etf_insight.pdf_text import extract_pdf_text


SUMMARY_SCHEMA_PATH = Path(__file__).with_name("summary_schema.json")
EXTERNAL_RESEARCH_SCHEMA_PATH = Path(__file__).with_name("external_research_schema.json")
CORRECTION_REVIEW_SCHEMA_PATH = Path(__file__).with_name("correction_review_schema.json")
CORRECTION_UPDATE_SCHEMA_PATH = Path(__file__).with_name("correction_update_schema.json")
PROMPTS_DIR = Path(__file__).with_name("prompts")

class State(TypedDict):
    pdf_path: str
    pdf_text: str
    classification_hints: dict
    prompt: str
    codex_output: str
    summary: dict
    validation_warnings: list[str]
    route: str
    research_prompt: str
    source: dict
    holding_identifier_resolver: Any

def load_pdf_text(state: State) -> State:
    pdf_path = Path(state["pdf_path"])
    pdf_content = pdf_path.read_bytes()
    pdf_text = extract_pdf_text(pdf_content)

    return {
        **state,
        "pdf_text": pdf_text,
    }

def load_prompt_template(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")

CLASSIFICATION_TERMS = {
    "active_terms": ["액티브", "Active", "ACTIVE", "초과성과", "알파"],
    "strategy_terms": ["커버드콜", "타겟프리미엄", "프리미엄", "버퍼", "채권혼합", "월배당", "환헤지"],
    "income_terms": ["배당", "인컴", "리츠", "REIT", "월분배", "월배당"],
    "leverage_terms": ["레버리지", "인버스", "곱버스", "2X", "2배", "선물"],
    "index_provider_terms": [
        "KOSPI",
        "KRX",
        "FnGuide",
        "에프앤가이드",
        "MSCI",
        "S&P",
        "NASDAQ",
        "나스닥",
        "Bloomberg",
        "블룸버그",
        "Solactive",
        "iSelect",
        "WISE",
        "Dow Jones",
        "KEDI",
        "코스콤",
    ],
    "theme_terms": [
        "AI",
        "인공지능",
        "반도체",
        "로봇",
        "클라우드",
        "사이버보안",
        "2차전지",
        "배터리",
        "원전",
        "수소",
        "태양광",
        "바이오",
        "헬스케어",
        "비만",
        "방산",
        "조선",
        "우주항공",
        "인프라",
        "K뷰티",
        "엔터",
        "고령화",
        "비트코인",
        "이더리움",
        "블록체인",
    ],
}


def _unique_matches(text: str, terms: list[str]) -> list[str]:
    seen: set[str] = set()
    matches: list[str] = []
    for term in terms:
        if term in text and term not in seen:
            seen.add(term)
            matches.append(term)
    return matches


def extract_classification_hints(pdf_text: str) -> dict:
    """Extract non-authoritative hints for the LLM theme classifier."""
    fund_name_match = re.search(r"(?:집합투자기구\s*명칭|펀드명|상품명)\s*[:：]?\s*(.+)", pdf_text)
    index_name_match = re.search(r"(?:기초지수|비교지수|추종지수)\s*[:：]?\s*(.+)", pdf_text)

    compact_text = pdf_text[:20000]
    return {
        "extracted_fund_name_line": fund_name_match.group(1).strip()[:200] if fund_name_match else None,
        "extracted_index_line": index_name_match.group(1).strip()[:200] if index_name_match else None,
        "detected_keywords": {
            key: _unique_matches(compact_text, terms)
            for key, terms in CLASSIFICATION_TERMS.items()
        },
        "note": "전처리 결과는 최종 분류가 아니라 LLM 판단을 돕는 힌트다.",
    }


def preprocess_classification_hints(state: State) -> State:
    return {
        **state,
        "classification_hints": extract_classification_hints(state["pdf_text"]),
    }

def review_correction_filing(filing: dict) -> dict:
    correction_text = fetch_dart_viewer_text(str(filing["rcept_no"]))
    template = load_prompt_template("correction_review.md")
    prompt = template.format(
        filing=json.dumps(filing, ensure_ascii=False, indent=2),
        correction_text=correction_text,
    )

    return json.loads(
        call_llm(
            prompt,
            output_schema_path=CORRECTION_REVIEW_SCHEMA_PATH,
        )
    )

def update_record_from_correction(existing_record: dict, filing: dict, review: dict) -> dict:
    correction_text = fetch_dart_viewer_text(str(filing["rcept_no"]))
    template = load_prompt_template("correction_update.md")
    prompt = template.format(
        existing_record=json.dumps(existing_record, ensure_ascii=False, indent=2),
        filing=json.dumps(filing, ensure_ascii=False, indent=2),
        review=json.dumps(review, ensure_ascii=False, indent=2),
        correction_text=correction_text,
    )

    output = json.loads(
        call_llm(
            prompt,
            output_schema_path=CORRECTION_UPDATE_SCHEMA_PATH,
        )
    )
    summary, warnings = normalize_summary(output.get("summary", {}))
    return {
        **output,
        "summary": summary,
        "validation_warnings": [*output.get("validation_warnings", []), *warnings],
    }

def call_llm(
    prompt: str,
    *,
    search: bool = False,
    output_schema_path: Path = SUMMARY_SCHEMA_PATH,
) -> str:
    return generate_json(prompt, search=search, output_schema_path=output_schema_path)


def call_codex(
    prompt: str,
    *,
    search: bool = False,
    output_schema_path: Path = SUMMARY_SCHEMA_PATH,
) -> str:
    return call_llm(prompt, search=search, output_schema_path=output_schema_path)


def make_summary_prompt(state: State) -> State:
    template = load_prompt_template("pdf_summary.md")
    prompt = template.format(
        classification_hints=json.dumps(state["classification_hints"], ensure_ascii=False, indent=2),
        pdf_text=state["pdf_text"],
    ).strip()

    return {
        **state,
        "prompt": prompt,
    }

def call_llm_node(state: State) -> State:
    """
    LLM이 필요한 지점에서만 provider adapter를 호출한다.
    """
    codex_output = call_llm(state["prompt"])

    return {
        **state,
        "codex_output": codex_output,
    }


def parse_summary_json(state: State) -> State:
    summary = json.loads(state["codex_output"])
    summary, warnings = normalize_summary(summary)
    return {
        **state,
        "summary": summary,
        "validation_warnings": [*state.get("validation_warnings", []), *warnings],
    }


def normalize_summary(summary: dict) -> tuple[dict, list[str]]:
    warnings = []
    normalized = {**summary}

    for key in ("keywords", "missing_info"):
        if normalized.get(key) is None:
            normalized[key] = []
            warnings.append(f"{key}_null_normalized_to_empty_list")

    theme = normalized.get("theme_classification")
    if isinstance(theme, dict):
        theme = {**theme}
        confidence = theme.get("confidence")
        if isinstance(confidence, (int, float)):
            if confidence < 0:
                theme["confidence"] = 0
                warnings.append("theme_confidence_clamped_to_0")
            elif confidence > 1:
                theme["confidence"] = 1
                warnings.append("theme_confidence_clamped_to_1")

        status = theme.get("theme_status")
        bucket = theme.get("theme_bucket")
        if status == "non_theme" and bucket not in {None, "none", "unknown"}:
            theme["theme_bucket"] = "none"
            warnings.append("non_theme_bucket_normalized_to_none")
        elif status in {"theme", "mixed"} and bucket == "none":
            theme["theme_bucket"] = "unknown"
            warnings.append("theme_bucket_none_normalized_to_unknown")

        if theme.get("structure_tags") is None:
            theme["structure_tags"] = []
            warnings.append("structure_tags_null_normalized_to_empty_list")
        normalized["theme_classification"] = theme

    holdings = normalized.get("holdings")
    if isinstance(holdings, dict):
        holdings = {**holdings}
        for key in ("items", "where_to_find_more"):
            if holdings.get(key) is None:
                holdings[key] = []
                warnings.append(f"holdings_{key}_null_normalized_to_empty_list")

        items = []
        for item in holdings.get("items") or []:
            if not isinstance(item, dict):
                items.append(item)
                continue

            normalized_item = {**item}
            for key in ("ticker", "exchange"):
                value = normalized_item.get(key)
                if isinstance(value, str) and not value.strip():
                    normalized_item[key] = None
                    warnings.append(f"holding_{key}_blank_normalized_to_null")
            items.append(normalized_item)
        holdings["items"] = items
        normalized["holdings"] = holdings

    return normalized, warnings

def route_holdings(state: State) -> str:
    available = state["summary"]["holdings"]["available_in_pdf"]

    if available:
        return "finalize_pdf_result"

    return "prepare_external_research"

def finalize_pdf_result(state: State) -> State:
    return {
        **state,
        "route": "pdf_holdings_available",
    }

def prepare_external_research(state: State) -> State:
    summary = state["summary"]
    holdings = summary["holdings"]
    where_to_find_more = holdings["where_to_find_more"]
    if not where_to_find_more:
        summary = {
            **summary,
            "missing_info": [
                *summary.get("missing_info", []),
                "구성종목/비중 외부 검색 단서 부족",
            ],
        }

        return {
            **state,
            "summary": summary,
            "route": "external_research_required",
            "research_prompt": "",
        }
    
    template = load_prompt_template("external_research.md")
    index = summary.get('index', {})

    research_prompt = template.format(
        fund_name=summary.get("fund_name"),
        asset_manager=summary.get("asset_manager"),
        index_name=index.get("name"),
        index_provider=index.get("provider"),
        where_to_find_more="\n".join(
            f"- {item}" for item in where_to_find_more
        ),
    ).strip()

    return {
        **state,
        "summary": summary,
        "route": "external_research_required",
        "research_prompt": research_prompt,
    }

def research_external_holdings(state: State) -> State:
    if not state["research_prompt"]:
        return state

    research_result = json.loads(
        call_llm(
            state["research_prompt"],
            search=True,
            output_schema_path=EXTERNAL_RESEARCH_SCHEMA_PATH,
        )
    )

    summary = state["summary"]
    holdings = summary["holdings"]
    missing_info = [
        item
        for item in summary.get("missing_info", [])
        if item not in {"개별 구성종목명", "개별 구성종목별 비중"}
    ]
    holdings_summary_parts = [
        f"외부 리서치 출처: {research_result['source_name']}",
        f"URL: {research_result['source_url']}",
        f"기준일: {research_result['as_of_date']}",
    ]

    summary, warnings = normalize_summary(
        {
            **summary,
            "missing_info": missing_info,
            "holdings": {
                **holdings,
                "items": research_result["items"],
                "summary": ", ".join(
                    part for part in holdings_summary_parts if not part.endswith(": None")
                ),
            },
        }
    )

    return {
        **state,
        "summary": summary,
        "validation_warnings": [*state.get("validation_warnings", []), *warnings],
        "route": "external_research_completed",
    }


def enrich_missing_holding_identifiers(state: State) -> State:
    summary = state["summary"]
    holdings = summary.get("holdings") or {}
    items = holdings.get("items") or []
    if not items:
        return state

    resolver = state.get("holding_identifier_resolver") or HoldingIdentifierResolver()
    primary_country = (summary.get("market_exposure") or {}).get("primary_country")
    enriched_items = resolver.enrich_items(items, primary_country=primary_country)

    return {
        **state,
        "holding_identifier_resolver": resolver,
        "summary": {
            **summary,
            "holdings": {
                **holdings,
                "items": enriched_items,
            },
        },
    }

def build_graph():
    graph = StateGraph(State)

    graph.add_node("load_pdf_text", load_pdf_text)
    graph.add_node("preprocess_classification_hints", preprocess_classification_hints)
    graph.add_node("make_summary_prompt", make_summary_prompt)
    graph.add_node("call_llm", call_llm_node)
    graph.add_node("parse_summary_json", parse_summary_json)

    graph.set_entry_point("load_pdf_text")
    graph.add_edge("load_pdf_text", "preprocess_classification_hints")
    graph.add_edge("preprocess_classification_hints", "make_summary_prompt")
    graph.add_edge("make_summary_prompt", "call_llm")
    graph.add_edge("call_llm", "parse_summary_json")
    graph.add_node("finalize_pdf_result", finalize_pdf_result)
    graph.add_node("prepare_external_research", prepare_external_research)
    graph.add_node("research_external_holdings", research_external_holdings)
    graph.add_node("enrich_missing_holding_identifiers", enrich_missing_holding_identifiers)

    graph.add_conditional_edges(
        "parse_summary_json",
        route_holdings,
        {
            "finalize_pdf_result": "finalize_pdf_result",
            "prepare_external_research": "prepare_external_research",
        },
    )

    graph.add_edge("finalize_pdf_result", "enrich_missing_holding_identifiers")
    graph.add_edge("prepare_external_research", "research_external_holdings")
    graph.add_edge("research_external_holdings", "enrich_missing_holding_identifiers")
    graph.add_edge("enrich_missing_holding_identifiers", END)

    return graph.compile()

def analyze_pdf(
    pdf_path: str,
    output_path: Path | None = None,
    source: dict | None = None,
    holding_identifier_resolver: HoldingIdentifierResolver | None = None,
) -> dict:

    app = build_graph()

    result = app.invoke(
        {
            "pdf_path": pdf_path,
            "pdf_text": "",
            "classification_hints": {},
            "prompt": "",
            "codex_output": "",
            "summary": {},
            "validation_warnings": [],
            "route": "",
            "research_prompt": "",
            "source": source or {},
            "holding_identifier_resolver": holding_identifier_resolver,
        }
    )



    output = {
        "route": result["route"],
        "summary": result["summary"],
        "validation_warnings": result["validation_warnings"],
        "research_prompt": result["research_prompt"],
    }

    if result["source"]:
        output["source"] = result["source"]

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return output

def is_correction_source(source: dict) -> bool:
    return "[기재정정]" in str(source.get("report_nm", ""))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETF 투자설명서 PDF를 분석해 JSON으로 출력한다.")
    parser.add_argument("pdf_path", help="분석할 PDF 파일 경로")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    summary = analyze_pdf(args.pdf_path)

    print(json.dumps(summary, ensure_ascii=False, indent=2))

def save_graph_png(path: str) -> None:
    app = build_graph()
    png = app.get_graph().draw_mermaid_png()
    Path(path).write_bytes(png)


if __name__ == "__main__":
    save_graph_png('./current_graph.png')
    main()
