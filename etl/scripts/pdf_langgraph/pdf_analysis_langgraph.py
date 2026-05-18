#  uv run python scripts/pdf_langgraph/pdf_analysis_langgraph.py

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict
import argparse

from langgraph.graph import END, StateGraph

from new_etf_insight.dart_viewer import fetch_dart_viewer_text
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
    prompt: str
    codex_output: str
    summary: dict
    route: str
    research_prompt: str
    source: dict

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

    return json.loads(
        call_llm(
            prompt,
            output_schema_path=CORRECTION_UPDATE_SCHEMA_PATH,
        )
    )

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
    prompt = template.format(pdf_text=state["pdf_text"]).strip()

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
    return {
        **state,
        "summary": summary,
    }

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

    return {
        **state,
        "summary": {
            **summary,
            "missing_info": missing_info,
            "holdings": {
                **holdings,
                "items": research_result["items"],
                "summary": ", ".join(
                    part for part in holdings_summary_parts if not part.endswith(": None")
                ),
            },
        },
        "route": "external_research_completed",
    }

def build_graph():
    graph = StateGraph(State)

    graph.add_node("load_pdf_text", load_pdf_text)
    graph.add_node("make_summary_prompt", make_summary_prompt)
    graph.add_node("call_llm", call_llm_node)
    graph.add_node("parse_summary_json", parse_summary_json)

    graph.set_entry_point("load_pdf_text")
    graph.add_edge("load_pdf_text", "make_summary_prompt")
    graph.add_edge("make_summary_prompt", "call_llm")
    graph.add_edge("call_llm", "parse_summary_json")
    graph.add_node("finalize_pdf_result", finalize_pdf_result)
    graph.add_node("prepare_external_research", prepare_external_research)
    graph.add_node("research_external_holdings", research_external_holdings)

    graph.add_conditional_edges(
        "parse_summary_json",
        route_holdings,
        {
            "finalize_pdf_result": "finalize_pdf_result",
            "prepare_external_research": "prepare_external_research",
        },
    )

    graph.add_edge("finalize_pdf_result", END)
    graph.add_edge("prepare_external_research", "research_external_holdings")
    graph.add_edge("research_external_holdings", END)

    return graph.compile()

def analyze_pdf(
    pdf_path: str,
    output_path: Path | None = None,
    source: dict | None = None,
) -> dict:

    app = build_graph()

    result = app.invoke(
        {
            "pdf_path": pdf_path,
            "pdf_text": "",
            "prompt": "",
            "codex_output": "",
            "summary": {},
            "route": "",
            "research_prompt": "",
            "source": source or {},
        }
    )



    output = {
        "route": result["route"],
        "summary": result["summary"],
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
