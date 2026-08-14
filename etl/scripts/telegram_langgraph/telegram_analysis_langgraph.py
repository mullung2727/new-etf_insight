"""Telegram 증분 종목 분석 LangGraph.

하루 3회(morning/close/evening) 배치. 각 run은 워터마크(post_id) 이후 새 글만 보고,
discovery_source 채널에서 종목 언급을 뽑아 과거 7일 이력과 비교(LLM)한 뒤
`telegram_stock_insights`에 세션 단위로 저장한다.

설계: docs/telegram_langgraph_analysis_plan.md

Usage (from etl/):
    uv run python scripts/telegram_langgraph/telegram_analysis_langgraph.py \
        --date 2026-07-03 --session close
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import TypedDict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from langgraph.graph import END, StateGraph

from new_etf_insight.llm import generate_json

# 순수 로직 재사용 — 종목 추출/취합은 기존 discover 파이프라인 것을 그대로 쓴다.
try:
    from scripts.build_krx_ohlcv import DEFAULT_DB_PATH as STOCK_DB
    from scripts.discover_telegram_stock_candidates import aggregate_candidates
    from scripts.stock_names import load_name_to_code
    from scripts.telegram_analysis_watermark import (
        advance_watermarks,
        ensure_schema as ensure_watermark_schema,
        read_watermarks,
    )
    from scripts.telegram_channels import load_all_channels, load_discovery_channels
    from scripts.telegram_session_highlights import (
        ensure_schema as ensure_highlights_schema,
        replace_session_highlights,
    )
    from scripts.telegram_stock_insights import (
        ensure_schema as ensure_insights_schema,
        update_analysis,
        upsert_candidate,
    )
except ImportError:  # 스크립트 디렉터리에서 직접 실행 fallback
    _SCRIPTS = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_SCRIPTS))
    from build_krx_ohlcv import DEFAULT_DB_PATH as STOCK_DB
    from discover_telegram_stock_candidates import aggregate_candidates
    from stock_names import load_name_to_code
    from telegram_analysis_watermark import (
        advance_watermarks,
        ensure_schema as ensure_watermark_schema,
        read_watermarks,
    )
    from telegram_channels import load_all_channels, load_discovery_channels
    from telegram_session_highlights import (
        ensure_schema as ensure_highlights_schema,
        replace_session_highlights,
    )
    from telegram_stock_insights import (
        ensure_schema as ensure_insights_schema,
        update_analysis,
        upsert_candidate,
    )


_HERE = Path(__file__).resolve().parent
PROMPTS_DIR = _HERE / "prompts"
SESSION_OVERVIEW_SCHEMA = _HERE / "session_overview_schema.json"
STOCK_EXTRACT_SCHEMA = _HERE / "stock_extract_schema.json"
STOCK_INSIGHT_SCHEMA = _HERE / "stock_insight_schema.json"

_MAX_SAMPLES_PER_STOCK = 5
_MAX_POST_CHARS = 500  # 추출 프롬프트에 넣을 글당 최대 길이(토큰 예산)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


class State(TypedDict):
    date_kst: str
    start_date_kst: str
    session: str                     # morning | close | evening (run 라벨)
    db_path: str
    stock_db_path: str
    history_days: int
    min_text_length: int

    watermark_in: dict               # {channel: last_post_id} run 시작 시점 워터마크
    rows: list[dict]                 # 이번 run 증분 글(post_id > 워터마크, discovery 채널)
    channel_post_counts: dict        # {channel: 증분 글 수} 디버그 집계

    overview_prompt: str
    overview_llm_output: str
    session_highlights: list[dict]
    overview_warnings: list[str]

    extract_prompt: str
    extract_llm_output: str
    stock_mentions: list[dict]       # LLM이 뽑은 중요 종목(마스터 이름→코드 확정 + 집계)
    stock_history: dict              # {ticker: [과거 7일 insight rows]}

    stock_prompt: str
    stock_llm_output: str
    stock_insights: list[dict]       # 종목별 변화 판단(신규/지속+변화)

    final_report: dict
    persisted_count: int
    warnings: list[str]


_POSTS_QUERY = """
SELECT channel, post_id, post_ref, posted_at_utc, date_kst, text,
       links_json, created_at, updated_at
FROM telegram_posts
WHERE date_kst BETWEEN ? AND ?
ORDER BY channel, post_id
"""


def ensure_schema(db_path: str) -> None:
    """워터마크·insights 테이블을 쓰기 커넥션에서 미리 만든다(읽기 노드는 DDL 금지)."""
    con = sqlite3.connect(db_path)
    try:
        ensure_watermark_schema(con)
        ensure_insights_schema(con)
        ensure_highlights_schema(con)
        con.commit()
    finally:
        con.close()


def load_posts(state: State) -> State:
    """워터마크 이후 증분 글만(discovery 채널). 읽기 전용 커넥션."""
    con = sqlite3.connect(state["db_path"])
    try:
        con.execute("PRAGMA query_only=ON")
        watermark_in = read_watermarks(con)
        cur = con.execute(
            _POSTS_QUERY,
            (state.get("start_date_kst") or state["date_kst"], state["date_kst"]),
        )
        cols = [c[0] for c in cur.description]
        all_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()

    discovery = set(load_discovery_channels())
    rows = [
        r for r in all_rows
        if r["channel"] in discovery
        and r["post_id"] > watermark_in.get(r["channel"], 0)
    ]
    channel_post_counts = dict(Counter(r["channel"] for r in rows))

    return {
        **state,
        "watermark_in": watermark_in,
        "rows": rows,
        "channel_post_counts": channel_post_counts,
    }


_SCORE_LIMITS = {
    "market_impact": 25,
    "evidence_quality": 25,
    "novelty": 20,
    "investment_relevance": 20,
    "cross_channel": 10,
}


def make_session_overview_prompt(state: State) -> State:
    """증분 원문 전체에서 투자 가치가 있는 흐름을 뽑기 위한 프롬프트를 만든다."""
    rows = state["rows"]
    if not rows:
        return {**state, "overview_prompt": ""}
    sig = {ch: cfg.get("signal_type", "") for ch, cfg in load_all_channels().items()}
    lines = [
        f"[{r['post_ref']} · {r['channel']} · {sig.get(r['channel'], '')}] "
        f"{' '.join(r['text'].split())[:_MAX_POST_CHARS]}"
        for r in rows
    ]
    prompt = _load_prompt("session_overview.md").format(
        date_kst=state["date_kst"],
        session=state["session"],
        posts_block="\n".join(lines),
    )
    return {**state, "overview_prompt": prompt}


def call_session_overview_llm(state: State) -> State:
    if not state["overview_prompt"]:
        return {**state, "overview_llm_output": ""}
    output = generate_json(
        state["overview_prompt"], output_schema_path=SESSION_OVERVIEW_SCHEMA, search=False
    )
    return {**state, "overview_llm_output": output}


def parse_and_validate_overview(state: State) -> State:
    """점수를 코드에서 재계산하고 실제 입력에 없는 출처를 제거한다."""
    output = state["overview_llm_output"]
    if not output:
        return {**state, "session_highlights": [], "overview_warnings": []}

    warnings = list(state.get("overview_warnings", []))
    ref_to_channel = {r["post_ref"]: r["channel"] for r in state["rows"]}
    valid_refs = set(ref_to_channel)
    highlights: list[dict] = []
    for item in json.loads(output).get("highlights", []):
        breakdown = item.get("score_breakdown") or {}
        if set(breakdown) != set(_SCORE_LIMITS) or any(
            not isinstance(breakdown.get(key), int)
            or breakdown[key] < 0
            or breakdown[key] > limit
            for key, limit in _SCORE_LIMITS.items()
        ):
            warnings.append(f"score_out_of_range:{item.get('title', '')}")
            continue

        requested_refs = item.get("source_post_refs") or []
        refs = list(dict.fromkeys(ref for ref in requested_refs if ref in valid_refs))
        for bad_ref in requested_refs:
            if bad_ref not in valid_refs:
                warnings.append(f"unknown_source_ref:{bad_ref}")
        if not refs:
            warnings.append(f"highlight_without_valid_source:{item.get('title', '')}")
            continue

        source_channels = list(dict.fromkeys(ref_to_channel[ref] for ref in refs))
        cleaned = dict(item)
        cleaned["score_breakdown"] = {key: breakdown[key] for key in _SCORE_LIMITS}
        cleaned["score_total"] = sum(cleaned["score_breakdown"].values())
        cleaned["source_post_refs"] = refs
        cleaned["source_channels"] = source_channels
        highlights.append(cleaned)

    highlights.sort(key=lambda item: item["score_total"], reverse=True)
    return {
        **state,
        "session_highlights": highlights[:5],
        "overview_warnings": warnings,
    }


def make_extract_prompt(state: State) -> State:
    """증분 원문을 [채널] 본문 블록으로 조립. 새 글 0이면 빈 프롬프트."""
    rows = state["rows"]
    if not rows:
        return {**state, "extract_prompt": ""}

    sig = {ch: cfg.get("signal_type", "") for ch, cfg in load_all_channels().items()}
    lines = [
        f"[{r['channel']} · {sig.get(r['channel'], '')}] {' '.join(r['text'].split())[:_MAX_POST_CHARS]}"
        for r in rows
    ]
    prompt = _load_prompt("stock_extract.md").format(
        date_kst=state["date_kst"],
        session=state["session"],
        posts_block="\n".join(lines),
    )
    return {**state, "extract_prompt": prompt}


def call_extract_llm(state: State) -> State:
    """LLM이 원문서 중요 종목만 추출(정밀). 빈 프롬프트면 codex 안 부름."""
    if not state["extract_prompt"]:
        return {**state, "extract_llm_output": ""}
    output = generate_json(
        state["extract_prompt"], output_schema_path=STOCK_EXTRACT_SCHEMA, search=False
    )
    return {**state, "extract_llm_output": output}


def parse_extract(state: State) -> State:
    """LLM 추출 종목명 → 마스터로 코드 확정(환각 방어) + 파이썬으로 채널/근거 집계.

    LLM은 '어떤 종목'만 정하고(정밀), 언급 채널·원문·수는 파이썬이 결정론적으로 센다."""
    output = state["extract_llm_output"]
    if not output:
        return {**state, "stock_mentions": []}

    extracted = json.loads(output).get("stocks", [])
    if not extracted:
        return {**state, "stock_mentions": []}
    notes = {s["name"]: s.get("note", "") for s in extracted}

    import duckdb

    con = duckdb.connect(state.get("stock_db_path") or str(STOCK_DB), read_only=True)
    try:
        name_to_code = load_name_to_code(con)
    finally:
        con.close()

    warnings = list(state["warnings"])
    confirmed_n2c: dict[str, str] = {}
    for name in notes:
        code = name_to_code.get(name)
        if code:
            confirmed_n2c[name] = code
        else:
            warnings.append(f"llm_name_not_in_master:{name}")  # 이름해석 실패 → 버림

    # 확정 종목에 한해서만 원문 집계(노이즈 후보가 애초에 없으니 substring도 안전)
    confirmed_c2n = {code: name for name, code in confirmed_n2c.items()}
    posts = [
        {"channel": r["channel"], "post_ref": r["post_ref"], "text": r["text"]}
        for r in state["rows"]
    ]
    candidates = aggregate_candidates(posts, confirmed_n2c, confirmed_c2n)

    stock_mentions = [
        {
            "ticker": code,
            "name": entry["name"],
            "mention_channels": entry["mention_channels"],
            "source_post_refs": entry["source_post_refs"],
            "mention_count": len(entry["source_post_refs"]),
            "discovery_reason": notes.get(entry["name"]) or entry["discovery_reason"],
        }
        for code, entry in candidates.items()
    ]

    return {**state, "stock_mentions": stock_mentions, "warnings": warnings}


_HISTORY_QUERY = """
SELECT date_kst, session, name, discovery_reason, analysis
FROM telegram_stock_insights
WHERE ticker = ? AND date_kst >= ? AND date_kst < ?
"""
_HISTORY_COLS = ["date_kst", "session", "name", "discovery_reason", "analysis"]
# 세션은 시간순(알파벳순 아님: close<evening<morning 이면 뒤섞임)
_SESSION_RANK = {"morning": 0, "close": 1, "evening": 2}


def load_stock_history(state: State) -> State:
    """후보 ticker별 과거 history_days일 insight 이력 조회(당일 이전만). 읽기 전용.

    이력 없으면 그 종목은 신규 등장(빈 리스트)."""
    mentions = state["stock_mentions"]
    if not mentions:
        return {**state, "stock_history": {}}

    today = state["date_kst"]
    lower = (dt.date.fromisoformat(today) - dt.timedelta(days=state["history_days"])).isoformat()

    con = sqlite3.connect(state["db_path"])
    history: dict[str, list[dict]] = {}
    try:
        con.execute("PRAGMA query_only=ON")
        for m in mentions:
            rows = con.execute(_HISTORY_QUERY, (m["ticker"], lower, today)).fetchall()
            recs = [dict(zip(_HISTORY_COLS, r)) for r in rows]
            recs.sort(key=lambda x: (x["date_kst"], _SESSION_RANK.get(x["session"], 9)))
            history[m["ticker"]] = recs
    finally:
        con.close()

    return {**state, "stock_history": history}


def make_stock_insight_prompt(state: State) -> State:
    """종목별 [이번 언급(소스+본문)] + [과거 이력]을 프롬프트로 조립.

    후보 0이면 빈 프롬프트 → 다음 LLM 노드 스킵."""
    mentions = state["stock_mentions"]
    if not mentions:
        return {**state, "stock_prompt": ""}

    ref_to_text = {r["post_ref"]: r["text"] for r in state["rows"]}
    min_len = state["min_text_length"]
    history = state["stock_history"]

    stocks = []
    for m in mentions:
        samples = [
            {"post_ref": ref, "text": ref_to_text[ref]}
            for ref in m["source_post_refs"]
            if len(ref_to_text.get(ref, "")) >= min_len
        ][:_MAX_SAMPLES_PER_STOCK]
        stocks.append({
            "code": m["ticker"],
            "name": m["name"],
            "이번_언급": {
                "mention_channels": m["mention_channels"],
                "mention_count": m["mention_count"],
                "samples": samples,
            },
            "과거_이력": history.get(m["ticker"], []),
        })

    prompt = _load_prompt("stock_insight.md").format(
        date_kst=state["date_kst"],
        session=state["session"],
        history_days=state["history_days"],
        stocks_json=json.dumps(stocks, ensure_ascii=False, indent=2),
    )
    return {**state, "stock_prompt": prompt}


def call_stock_insight_llm(state: State) -> State:
    """빈 프롬프트면 codex 안 부른다(비용 가드)."""
    if not state["stock_prompt"]:
        return {**state, "stock_llm_output": ""}
    output = generate_json(
        state["stock_prompt"], output_schema_path=STOCK_INSIGHT_SCHEMA, search=False
    )
    return {**state, "stock_llm_output": output}


def parse_stock_insight(state: State) -> State:
    output = state["stock_llm_output"]
    if not output:
        return {**state, "stock_insights": []}
    data = json.loads(output)
    return {**state, "stock_insights": data.get("stocks", [])}


def _insight_analysis_json(ins: dict) -> str:
    return json.dumps(
        {
            "change_type": ins.get("change_type"),
            "change_summary": ins.get("change_summary"),
            "themes": ins.get("themes", []),
            "evidence_summary": ins.get("evidence_summary"),
        },
        ensure_ascii=False,
    )


def build_final_report(state: State) -> State:
    """파이썬 집계값 + LLM 서술을 code로 조인. 정량은 파이썬, 서술만 LLM.

    LLM이 후보에 없는 종목을 지어내면(code 불일치) 버리고 warning."""
    mentions_by_code = {m["ticker"]: m for m in state["stock_mentions"]}
    warnings = list(state["warnings"]) + list(state.get("overview_warnings", []))

    notable = []
    for ins in state["stock_insights"]:
        code = ins.get("code")
        m = mentions_by_code.get(code)
        if not m:
            warnings.append(f"llm_stock_not_in_candidates:{code}")
            continue
        notable.append({
            "name": m["name"],           # 마스터 정식명(파이썬)
            "code": code,
            "change_type": ins.get("change_type"),
            "mention_count": m["mention_count"],   # 파이썬 집계
            "channels": m["mention_channels"],     # 파이썬 집계
            "themes": ins.get("themes", []),
            "change_summary": ins.get("change_summary"),
            "evidence_summary": ins.get("evidence_summary"),
        })

    report = {
        "date_kst": state["date_kst"],
        "session": state["session"],
        "post_count": len(state["rows"]),
        "channel_post_counts": state["channel_post_counts"],
        "session_highlights": state.get("session_highlights", []),
        "notable_stocks": notable,
        "warnings": warnings,
    }
    return {**state, "final_report": report, "warnings": warnings}


def persist_and_advance(state: State) -> State:
    """insights upsert + 워터마크 전진을 1 트랜잭션으로. (persist만 되고 워터마크 안 밀리는
    어긋남 차단.)

    - 후보는 전부 candidate로 upsert(analysis는 LLM 결과 있을 때만 채움).
    - 후보 0이어도 rows 있으면 워터마크만 전진(글은 봤으므로).
    - rows 0이면 아무것도 안 함."""
    rows = state["rows"]
    if not rows:
        return {**state, "persisted_count": 0}

    date_kst, session = state["date_kst"], state["session"]
    mentions = state["stock_mentions"]
    insight_by_code = {i["code"]: i for i in state["stock_insights"] if i.get("code")}

    con = sqlite3.connect(state["db_path"])
    try:
        replace_session_highlights(
            con, date_kst, session, state.get("session_highlights", [])
        )
        for m in mentions:
            upsert_candidate(
                con, date_kst, session, m["ticker"], m["name"],
                mention_channels=m["mention_channels"],
                source_post_refs=m["source_post_refs"],
                discovery_reason=m["discovery_reason"],
            )
            ins = insight_by_code.get(m["ticker"])
            if ins:
                update_analysis(con, date_kst, session, m["ticker"], _insight_analysis_json(ins))

        channel_max: dict[str, int] = {}
        for r in rows:
            ch = r["channel"]
            channel_max[ch] = max(channel_max.get(ch, 0), r["post_id"])
        advance_watermarks(con, channel_max)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    return {**state, "persisted_count": len(mentions)}


def build_graph():
    graph = StateGraph(State)

    graph.add_node("load_posts", load_posts)
    graph.add_node("make_session_overview_prompt", make_session_overview_prompt)
    graph.add_node("call_session_overview_llm", call_session_overview_llm)
    graph.add_node("parse_and_validate_overview", parse_and_validate_overview)
    graph.add_node("make_extract_prompt", make_extract_prompt)
    graph.add_node("call_extract_llm", call_extract_llm)
    graph.add_node("parse_extract", parse_extract)
    graph.add_node("load_stock_history", load_stock_history)
    graph.add_node("make_stock_insight_prompt", make_stock_insight_prompt)
    graph.add_node("call_stock_insight_llm", call_stock_insight_llm)
    graph.add_node("parse_stock_insight", parse_stock_insight)
    graph.add_node("build_final_report", build_final_report)
    graph.add_node("persist_and_advance", persist_and_advance)

    graph.set_entry_point("load_posts")
    graph.add_edge("load_posts", "make_session_overview_prompt")
    graph.add_edge("make_session_overview_prompt", "call_session_overview_llm")
    graph.add_edge("call_session_overview_llm", "parse_and_validate_overview")
    graph.add_edge("parse_and_validate_overview", "make_extract_prompt")
    graph.add_edge("make_extract_prompt", "call_extract_llm")
    graph.add_edge("call_extract_llm", "parse_extract")
    graph.add_edge("parse_extract", "load_stock_history")
    graph.add_edge("load_stock_history", "make_stock_insight_prompt")
    graph.add_edge("make_stock_insight_prompt", "call_stock_insight_llm")
    graph.add_edge("call_stock_insight_llm", "parse_stock_insight")
    graph.add_edge("parse_stock_insight", "build_final_report")
    graph.add_edge("build_final_report", "persist_and_advance")
    graph.add_edge("persist_and_advance", END)

    return graph.compile()


def analyze_telegram_session(
    date_kst: str,
    session: str,
    start_date_kst: str | None = None,
    db_path: Path = Path("db/telegram_public.sqlite3"),
    output_path: Path | None = None,
    history_days: int = 7,
    min_text_length: int = 30,
    stock_db_path: str = "",
) -> dict:
    ensure_schema(str(db_path))  # 워터마크/insights 테이블 선생성(읽기 노드 DDL 금지)
    app = build_graph()
    result = app.invoke({
        "date_kst": date_kst,
        "start_date_kst": start_date_kst or date_kst,
        "session": session,
        "db_path": str(db_path),
        "stock_db_path": stock_db_path,
        "history_days": history_days,
        "min_text_length": min_text_length,
        "watermark_in": {},
        "rows": [],
        "channel_post_counts": {},
        "overview_prompt": "",
        "overview_llm_output": "",
        "session_highlights": [],
        "overview_warnings": [],
        "extract_prompt": "",
        "extract_llm_output": "",
        "stock_mentions": [],
        "stock_history": {},
        "stock_prompt": "",
        "stock_llm_output": "",
        "stock_insights": [],
        "final_report": {},
        "persisted_count": 0,
        "warnings": [],
    })

    report = result["final_report"]
    if output_path is not None:  # 디버그용 JSON 덤프. 운영 저장은 persist_and_advance가 DB로.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="텔레그램 증분 종목 분석 LangGraph")
    ap.add_argument("--date", required=True, help="분석 대상 KST 일자 YYYY-MM-DD")
    ap.add_argument("--session", required=True, choices=["morning", "close", "evening"])
    ap.add_argument("--start-date", help="증분 조회 시작 KST 일자(기본: --date)")
    ap.add_argument("--db", default="db/telegram_public.sqlite3")
    ap.add_argument("--output", help="디버그 JSON 덤프 경로(선택)")
    ap.add_argument("--history-days", type=int, default=7)
    ap.add_argument("--min-text-length", type=int, default=30)
    args = ap.parse_args()

    report = analyze_telegram_session(
        args.date,
        args.session,
        start_date_kst=args.start_date,
        db_path=Path(args.db),
        output_path=Path(args.output) if args.output else None,
        history_days=args.history_days,
        min_text_length=args.min_text_length,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
