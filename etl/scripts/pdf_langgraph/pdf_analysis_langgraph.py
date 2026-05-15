## uv run python scripts/pdf_langgraph/pdf_analysis_langgraph.py
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph


class State(TypedDict):
    topic: str
    prompt: str
    codex_output: str
    result: dict


def call_codex(prompt: str) -> str:
    """
    Codex 구독형 CLI를 호출하는 단일 어댑터.

    중요:
    - LangGraph 노드에서 subprocess를 직접 흩뿌리지 말고 이 함수만 호출한다.
    - 나중에 OpenAI API나 다른 LLM으로 바꾸고 싶으면 이 함수만 교체한다.
    - Phase -2에서는 최종 답변만 필요하므로 --output-last-message를 사용한다.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "codex_last_message.txt"

        subprocess.run(
            [
                "codex",
                "-a",
                "never",
                "exec",
                "--sandbox",
                "read-only",
                "--output-last-message",
                str(output_path),
                prompt,
            ],
            check=True,
            text=True,
        )

        return output_path.read_text(encoding="utf-8").strip()


def make_prompt(state: State) -> State:
    """
    LLM에 보낼 프롬프트를 만든다.

    지금은 hello world 수준이지만,
    나중에는 여기서 PDF 관련 페이지 텍스트를 넣는 식으로 확장한다.
    """
    topic = state["topic"]

    prompt = f"""
너는 JSON만 출력해야 해.
마크다운 코드블럭을 쓰지 마.
설명 문장도 쓰지 마.

아래 주제에 대해 한 문장으로 답해.

주제:
{topic}

출력 형식:
{{
  "topic": "{topic}",
  "message": "한 문장 답변"
}}
""".strip()

    return {
        **state,
        "prompt": prompt,
    }


def call_codex_node(state: State) -> State:
    """
    LLM이 필요한 지점에서만 call_codex를 호출한다.

    나중에 PDF 파이프라인에서도:
    - 정보 구조화 추출
    - 외부 리서치 요약
    - 애매한 판단
    같은 지점에서만 이 노드를 쓰면 된다.
    """
    codex_output = call_codex(state["prompt"])

    return {
        **state,
        "codex_output": codex_output,
    }


def parse_json_result(state: State) -> State:
    """
    Codex 출력이 JSON이라는 계약을 검증한다.
    Phase -2에서는 단순 json.loads만 사용한다.
    나중에는 JSON Schema나 Pydantic 검증으로 강화한다.
    """
    result = json.loads(state["codex_output"])

    return {
        **state,
        "result": result,
    }


def build_graph():
    graph = StateGraph(State)

    graph.add_node("make_prompt", make_prompt)
    graph.add_node("call_codex", call_codex_node)
    graph.add_node("parse_json_result", parse_json_result)

    graph.set_entry_point("make_prompt")
    graph.add_edge("make_prompt", "call_codex")
    graph.add_edge("call_codex", "parse_json_result")
    graph.add_edge("parse_json_result", END)

    return graph.compile()


def main() -> None:
    app = build_graph()

    result = app.invoke(
        {
            "topic": "15+ 155는 얼마인가?",
            "prompt": "",
            "codex_output": "",
            "result": {},
        }
    )

    print(json.dumps(result["result"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
