"""YouTube 영상 1건 LangGraph: 청크 요약 → 이슈 통합 → 종목 추출.

스펙: docs/youtube_tech.md §5
Usage (from etl/):
  uv run python scripts/youtube_langgraph/youtube_analysis_langgraph.py \\
    --channel-id UCxxx --video-id xxxxxxxxxxx [--force]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, TypedDict

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from langgraph.graph import END, StateGraph

from new_etf_insight.llm import generate_json

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
PROMPTS_DIR = _HERE / "prompts"
CHUNK_SCHEMA = _HERE / "chunk_summary_schema.json"
REDUCE_SCHEMA = _HERE / "reduce_issues_schema.json"
STOCK_SCHEMA = _HERE / "stock_extract_schema.json"

WINDOW_SEC = 600
CHUNK_CHARS = 4000

try:
    from scripts.build_krx_ohlcv import DEFAULT_DB_PATH as STOCK_DB
    from scripts.collect_youtube import DEFAULT_DB, ensure_schema as ensure_videos_schema
    from scripts.stock_names import load_name_to_code
    from scripts.youtube_channels import load_discovery_channels
    from scripts.youtube_stock_insights import (
        ensure_schema as ensure_insights_schema,
        upsert_stock_from_video,
    )
except ImportError:
    sys.path.insert(0, str(_SCRIPTS))
    from build_krx_ohlcv import DEFAULT_DB_PATH as STOCK_DB
    from collect_youtube import DEFAULT_DB, ensure_schema as ensure_videos_schema
    from stock_names import load_name_to_code
    from youtube_channels import load_discovery_channels
    from youtube_stock_insights import (
        ensure_schema as ensure_insights_schema,
        upsert_stock_from_video,
    )


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _model_label() -> str:
    return (os.getenv("ETF_LLM_PROVIDER") or "codex").strip().lower()


# --- summaries table ---

_CREATE_SUMMARIES = """
CREATE TABLE IF NOT EXISTS youtube_video_summaries (
  channel_id   TEXT NOT NULL,
  video_id     TEXT NOT NULL,
  date_kst     TEXT NOT NULL,
  model        TEXT,
  summary_json TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE(channel_id, video_id)
)
"""


def ensure_summary_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_SUMMARIES)


def has_summary(con: sqlite3.Connection, channel_id: str, video_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM youtube_video_summaries WHERE channel_id=? AND video_id=?",
        (channel_id, video_id),
    ).fetchone()
    return row is not None


def upsert_summary(
    con: sqlite3.Connection,
    *,
    channel_id: str,
    video_id: str,
    date_kst: str,
    model: str,
    summary_obj: dict,
) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute(
        """
        INSERT INTO youtube_video_summaries(
            channel_id, video_id, date_kst, model, summary_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, video_id) DO UPDATE SET
            date_kst=excluded.date_kst,
            model=excluded.model,
            summary_json=excluded.summary_json,
            updated_at=excluded.updated_at
        """,
        (
            channel_id,
            video_id,
            date_kst,
            model,
            json.dumps(summary_obj, ensure_ascii=False),
            now,
            now,
        ),
    )


# --- chunking ---

def chunk_by_timecode(
    segments: list[dict],
    *,
    window_sec: int = WINDOW_SEC,
) -> list[dict]:
    """segments: [{start: float seconds, text: str}, ...] → chunks."""
    if not segments:
        return []
    chunks: list[dict] = []
    bucket: list[str] = []
    start0 = float(segments[0].get("start") or 0)
    win_start = start0
    win_end = win_start + window_sec
    last_end = win_start

    def flush(end_sec: float) -> None:
        nonlocal bucket, win_start, win_end
        text = "\n".join(bucket).strip()
        if text:
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "start_sec": win_start,
                    "end_sec": end_sec,
                    "text": text,
                }
            )
        bucket = []

    for seg in segments:
        st = float(seg.get("start") or 0)
        tx = (seg.get("text") or "").strip()
        if not tx:
            continue
        # advance windows until segment fits
        while st >= win_end and bucket:
            flush(win_end)
            win_start = win_end
            win_end = win_start + window_sec
        if st >= win_end and not bucket:
            win_start = st
            win_end = win_start + window_sec
        bucket.append(tx)
        last_end = max(last_end, st)

    if bucket:
        flush(max(last_end, win_start))
    return chunks


def chunk_by_chars(text: str, *, chunk_chars: int = CHUNK_CHARS) -> list[dict]:
    s = (text or "").strip()
    if not s:
        return []
    chunks: list[dict] = []
    i = 0
    while i < len(s):
        part = s[i : i + chunk_chars]
        chunks.append(
            {
                "chunk_index": len(chunks),
                "start_sec": None,
                "end_sec": None,
                "text": part,
            }
        )
        i += chunk_chars
    return chunks


def build_chunks(
    transcript: str,
    segments: list[dict] | None = None,
) -> list[dict]:
    if segments:
        out = chunk_by_timecode(segments)
        if out:
            return out
    return chunk_by_chars(transcript)


# --- state ---

class State(TypedDict, total=False):
    channel_id: str
    video_id: str
    date_kst: str
    title: str
    transcript: str
    segments: list  # optional [{start, text}]
    db_path: str
    stock_db_path: str
    force: bool
    skip: bool
    is_discovery: bool

    chunks: list
    page_summaries: list
    summary_obj: dict
    stock_mentions: list
    warnings: list
    llm_calls: int
    persisted: bool
    model: str


GenerateFn = Callable[..., str]


def validate_reduce(obj: Any) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("reduce output must be object")
    for k in ("headline", "issues", "bullets", "risk_or_caveat"):
        if k not in obj:
            raise ValueError(f"reduce missing key: {k}")
    if not isinstance(obj["issues"], list) or not isinstance(obj["bullets"], list):
        raise ValueError("issues/bullets must be arrays")
    return obj


def summary_block_text(summary: dict) -> str:
    lines = [f"headline: {summary.get('headline', '')}"]
    for iss in summary.get("issues") or []:
        if isinstance(iss, dict):
            lines.append(
                f"- [{iss.get('title', '')}] {iss.get('summary', '')}"
                f" ({iss.get('time_hint') or ''})"
            )
    for b in summary.get("bullets") or []:
        lines.append(f"* {b}")
    if summary.get("risk_or_caveat"):
        lines.append(f"risk: {summary['risk_or_caveat']}")
    return "\n".join(lines)


def load_video_row(
    con: sqlite3.Connection, channel_id: str, video_id: str
) -> dict | None:
    row = con.execute(
        """
        SELECT channel_id, video_id, title, date_kst, transcript
        FROM youtube_videos
        WHERE channel_id=? AND video_id=?
        """,
        (channel_id, video_id),
    ).fetchone()
    if not row:
        return None
    return {
        "channel_id": row[0],
        "video_id": row[1],
        "title": row[2] or "",
        "date_kst": row[3],
        "transcript": row[4],
    }


def node_load(state: State) -> State:
    warnings = list(state.get("warnings") or [])
    channel_id = state["channel_id"]
    video_id = state["video_id"]
    force = bool(state.get("force"))
    con = sqlite3.connect(state["db_path"])
    try:
        ensure_videos_schema(con)
        ensure_summary_schema(con)
        ensure_insights_schema(con)
        if not force and has_summary(con, channel_id, video_id):
            return {
                **state,
                "skip": True,
                "llm_calls": 0,
                "warnings": warnings,
                "page_summaries": [],
                "stock_mentions": [],
            }
        row = load_video_row(con, channel_id, video_id)
    finally:
        con.close()

    if not row:
        warnings.append("video_not_found")
        return {**state, "skip": True, "llm_calls": 0, "warnings": warnings}

    tr = row.get("transcript")
    if tr is None or not str(tr).strip():
        warnings.append("no_transcript")
        return {
            **state,
            "skip": True,
            "llm_calls": 0,
            "title": row["title"],
            "date_kst": row["date_kst"],
            "transcript": "",
            "warnings": warnings,
        }

    discovery_ids = set(load_discovery_channels())
    return {
        **state,
        "skip": False,
        "title": row["title"],
        "date_kst": row["date_kst"],
        "transcript": str(tr),
        "is_discovery": channel_id in discovery_ids,
        "llm_calls": 0,
        "warnings": warnings,
        "model": _model_label(),
    }


def node_chunk(state: State) -> State:
    if state.get("skip"):
        return {**state, "chunks": []}
    chunks = build_chunks(state.get("transcript") or "", state.get("segments"))
    if not chunks:
        return {
            **state,
            "skip": True,
            "chunks": [],
            "warnings": list(state.get("warnings") or []) + ["empty_chunks"],
        }
    return {**state, "chunks": chunks}


def node_map_summarize(
    state: State,
    *,
    generate_fn: GenerateFn | None = None,
) -> State:
    if state.get("skip"):
        return {**state, "page_summaries": []}
    gen = generate_fn or generate_json
    pages: list[dict] = []
    calls = int(state.get("llm_calls") or 0)
    title = state.get("title") or ""
    for ch in state.get("chunks") or []:
        tr = ""
        if ch.get("start_sec") is not None:
            tr = f"{ch['start_sec']:.0f}s~{ch.get('end_sec') or ''}"
        prompt = _load_prompt("chunk_summary.md").format(
            chunk_index=ch["chunk_index"],
            time_range=tr or "n/a",
            title=title,
            chunk_text=ch["text"],
        )
        raw = gen(prompt, output_schema_path=CHUNK_SCHEMA, search=False)
        calls += 1
        obj = json.loads(raw)
        obj["chunk_index"] = ch["chunk_index"]
        pages.append(obj)
    return {**state, "page_summaries": pages, "llm_calls": calls}


def node_reduce(
    state: State,
    *,
    generate_fn: GenerateFn | None = None,
) -> State:
    if state.get("skip"):
        return state
    gen = generate_fn or generate_json
    pages = state.get("page_summaries") or []
    blocks = []
    for p in pages:
        blocks.append(
            f"[chunk {p.get('chunk_index')}] {p.get('headline')}\n"
            + "\n".join(f"  - {b}" for b in (p.get("bullets") or []))
        )
    prompt = _load_prompt("reduce_issues.md").format(
        title=state.get("title") or "",
        pages_block="\n\n".join(blocks),
    )
    raw = gen(prompt, output_schema_path=REDUCE_SCHEMA, search=False)
    calls = int(state.get("llm_calls") or 0) + 1
    obj = validate_reduce(json.loads(raw))
    return {**state, "summary_obj": obj, "llm_calls": calls}


def node_extract_stocks(
    state: State,
    *,
    generate_fn: GenerateFn | None = None,
    name_to_code: dict[str, str] | None = None,
) -> State:
    if state.get("skip"):
        return {**state, "stock_mentions": []}
    if not state.get("is_discovery"):
        return {**state, "stock_mentions": []}

    gen = generate_fn or generate_json
    summary = state.get("summary_obj") or {}
    prompt = _load_prompt("stock_extract.md").format(
        title=state.get("title") or "",
        channel_id=state["channel_id"],
        video_id=state["video_id"],
        date_kst=state.get("date_kst") or "",
        summary_block=summary_block_text(summary),
    )
    raw = gen(prompt, output_schema_path=STOCK_SCHEMA, search=False)
    calls = int(state.get("llm_calls") or 0) + 1
    stocks = json.loads(raw).get("stocks") or []

    if name_to_code is None:
        import duckdb

        stock_con = duckdb.connect(
            state.get("stock_db_path") or str(STOCK_DB), read_only=True
        )
        try:
            name_to_code = load_name_to_code(stock_con)
        finally:
            stock_con.close()

    warnings = list(state.get("warnings") or [])
    mentions: list[dict] = []
    for s in stocks:
        name = (s.get("name") or "").strip()
        note = s.get("note") or ""
        if not name:
            continue
        code = name_to_code.get(name)
        if not code:
            warnings.append(f"llm_name_not_in_master:{name}")
            continue
        mentions.append(
            {
                "ticker": code,
                "name": name,
                "note": note,
                "discovery_reason": note or f"유튜브 이슈 요약 언급 ({state['video_id']})",
            }
        )
    return {
        **state,
        "stock_mentions": mentions,
        "llm_calls": calls,
        "warnings": warnings,
    }


def node_persist(state: State) -> State:
    if state.get("skip"):
        return {**state, "persisted": False}
    summary = state.get("summary_obj")
    if not summary:
        return {**state, "persisted": False, "warnings": list(state.get("warnings") or []) + ["no_summary"]}

    con = sqlite3.connect(state["db_path"])
    try:
        ensure_summary_schema(con)
        ensure_insights_schema(con)
        upsert_summary(
            con,
            channel_id=state["channel_id"],
            video_id=state["video_id"],
            date_kst=state.get("date_kst") or "",
            model=state.get("model") or _model_label(),
            summary_obj=summary,
        )
        for m in state.get("stock_mentions") or []:
            upsert_stock_from_video(
                con,
                date_kst=state.get("date_kst") or "",
                ticker=m["ticker"],
                name=m["name"],
                channel_id=state["channel_id"],
                video_id=state["video_id"],
                discovery_reason=m["discovery_reason"],
                analysis=m.get("note") or None,
            )
        con.commit()
    finally:
        con.close()
    return {**state, "persisted": True}


def build_graph(*, generate_fn: GenerateFn | None = None):
    """generate_fn 주입 시 테스트 mock."""

    def map_n(s: State) -> State:
        return node_map_summarize(s, generate_fn=generate_fn)

    def red_n(s: State) -> State:
        return node_reduce(s, generate_fn=generate_fn)

    def stock_n(s: State) -> State:
        return node_extract_stocks(s, generate_fn=generate_fn)

    g = StateGraph(State)
    g.add_node("load", node_load)
    g.add_node("chunk", node_chunk)
    g.add_node("map_summarize", map_n)
    g.add_node("reduce", red_n)
    g.add_node("extract_stocks", stock_n)
    g.add_node("persist", node_persist)
    g.set_entry_point("load")
    g.add_edge("load", "chunk")
    g.add_edge("chunk", "map_summarize")
    g.add_edge("map_summarize", "reduce")
    g.add_edge("reduce", "extract_stocks")
    g.add_edge("extract_stocks", "persist")
    g.add_edge("persist", END)
    return g.compile()


def run_video(
    *,
    channel_id: str,
    video_id: str,
    db_path: str,
    force: bool = False,
    segments: list[dict] | None = None,
    stock_db_path: str | None = None,
    generate_fn: GenerateFn | None = None,
    name_to_code: dict[str, str] | None = None,
) -> State:
    """영상 1건 분석. 테스트에서 generate_fn / name_to_code / segments 주입."""
    graph = build_graph(generate_fn=generate_fn)

    # stock extract needs injectable name_to_code without rebuilding whole graph:
    # wrap extract by running pipeline functions when name_to_code provided
    if name_to_code is not None or generate_fn is not None:
        # manual pipeline for injectability (same order as graph)
        state: State = {
            "channel_id": channel_id,
            "video_id": video_id,
            "db_path": db_path,
            "stock_db_path": stock_db_path or str(STOCK_DB),
            "force": force,
            "segments": segments or [],
            "warnings": [],
            "llm_calls": 0,
        }
        state = node_load(state)
        state = node_chunk(state)
        state = node_map_summarize(state, generate_fn=generate_fn)
        state = node_reduce(state, generate_fn=generate_fn)
        state = node_extract_stocks(
            state, generate_fn=generate_fn, name_to_code=name_to_code
        )
        state = node_persist(state)
        return state

    init: State = {
        "channel_id": channel_id,
        "video_id": video_id,
        "db_path": db_path,
        "stock_db_path": stock_db_path or str(STOCK_DB),
        "force": force,
        "segments": segments or [],
        "warnings": [],
        "llm_calls": 0,
    }
    return graph.invoke(init)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel-id", required=True)
    ap.add_argument("--video-id", required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--stock-db", default=str(STOCK_DB))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    result = run_video(
        channel_id=args.channel_id,
        video_id=args.video_id,
        db_path=args.db,
        force=args.force,
        stock_db_path=args.stock_db,
    )
    print(
        f"[youtube_analysis] channel={args.channel_id} video={args.video_id} "
        f"skip={result.get('skip')} llm_calls={result.get('llm_calls')} "
        f"persisted={result.get('persisted')} "
        f"stocks={len(result.get('stock_mentions') or [])} "
        f"warnings={len(result.get('warnings') or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
