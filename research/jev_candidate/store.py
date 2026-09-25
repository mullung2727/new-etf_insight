"""Jev 후보 판단 P2 저장 — etl/db/jev_candidate.sqlite3 (§8 subset).

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest research.jev_candidate.tests.test_store
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).resolve().parents[2] / "etl" / "db" / "jev_candidate.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, mode TEXT, track TEXT, date TEXT, as_of TEXT,
    jev_model TEXT, gpt_model TEXT, question_set_ver TEXT, prompt_ver TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    run_id TEXT, date TEXT, ticker TEXT, channel TEXT, post_id TEXT,
    posted_at_kst TEXT, section TEXT, text_hash TEXT, about_noul REAL, passed INTEGER,
    PRIMARY KEY (run_id, ticker, channel, post_id)
);
CREATE TABLE IF NOT EXISTS states (
    run_id TEXT, date TEXT, ticker TEXT, name TEXT, price_pct REAL,
    state_text TEXT, state_hash TEXT, anon_map_json TEXT,
    n_candidates INTEGER, n_passed INTEGER, n_ambiguous INTEGER, n_included INTEGER,
    truncated INTEGER,
    PRIMARY KEY (run_id, ticker)
);
CREATE TABLE IF NOT EXISTS jev_answers (
    run_id TEXT, date TEXT, ticker TEXT, qid TEXT, type TEXT,
    value TEXT, confidence REAL, probs_json TEXT,
    PRIMARY KEY (run_id, ticker, qid)
);
CREATE TABLE IF NOT EXISTS filter_cache (
    key TEXT PRIMARY KEY, about_noul REAL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS answer_cache (
    key TEXT PRIMARY KEY, model TEXT, rows_json TEXT, usage_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id TEXT, date TEXT, ticker TEXT, arm TEXT,
    score REAL, rank INTEGER, decision TEXT,
    PRIMARY KEY (run_id, ticker, arm)
);
CREATE TABLE IF NOT EXISTS outcomes (
    date TEXT, ticker TEXT, d0_close INTEGER, d1_open INTEGER, d1_0930 INTEGER,
    d1_close INTEGER, d1_high INTEGER, d1_low INTEGER,
    ret_open_0930 REAL, ret_open_close REAL, max_ret_o REAL, min_ret_o REAL,
    excluded_open TEXT,
    PRIMARY KEY (date, ticker)
);
"""

_RUN_COLS = (
    "run_id",
    "mode",
    "track",
    "date",
    "as_of",
    "jev_model",
    "gpt_model",
    "question_set_ver",
    "prompt_ver",
    "created_at",
)


def connect(path: Any = DEFAULT_DB) -> sqlite3.Connection:
    """sqlite 연결 — 부모 폴더 자동 생성. ":memory:" 는 그대로."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path))


def ensure_schema(con: sqlite3.Connection) -> None:
    """7 테이블 생성 (없으면)."""
    con.executescript(_SCHEMA)
    con.commit()


def save_run(con: sqlite3.Connection, **fields: Any) -> None:
    """runs 1행 upsert — 컬럼 키만 쓴다."""
    cols = [c for c in _RUN_COLS if c in fields]
    con.execute(
        f"INSERT OR REPLACE INTO runs ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [fields[c] for c in cols],
    )
    con.commit()


def _posted_str(v: Any) -> Any:
    """datetime → isoformat, 나머지는 그대로."""
    return v.isoformat() if hasattr(v, "isoformat") else v


def save_posts(con: sqlite3.Connection, rows: list[dict]) -> None:
    """posts 여러 행 upsert."""
    con.executemany(
        "INSERT OR REPLACE INTO posts (run_id, date, ticker, channel, post_id,"
        " posted_at_kst, section, text_hash, about_noul, passed)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r.get("run_id"),
                r.get("date"),
                r.get("ticker"),
                r.get("channel"),
                str(r.get("post_id")),
                _posted_str(r.get("posted_at_kst")),
                r.get("section"),
                r.get("text_hash"),
                r.get("about_noul"),
                None if r.get("passed") is None else int(bool(r.get("passed"))),
            )
            for r in rows
        ],
    )
    con.commit()


def save_state(con: sqlite3.Connection, row: dict) -> None:
    """states 1행 upsert."""
    con.execute(
        "INSERT OR REPLACE INTO states (run_id, date, ticker, name, price_pct,"
        " state_text, state_hash, anon_map_json,"
        " n_candidates, n_passed, n_ambiguous, n_included, truncated)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row.get("run_id"),
            row.get("date"),
            row.get("ticker"),
            row.get("name"),
            row.get("price_pct"),
            row.get("state_text"),
            row.get("state_hash"),
            row.get("anon_map_json"),
            row.get("n_candidates"),
            row.get("n_passed"),
            row.get("n_ambiguous"),
            row.get("n_included"),
            None if row.get("truncated") is None else int(bool(row.get("truncated"))),
        ),
    )
    con.commit()


def save_answers(
    con: sqlite3.Connection, run_id: str, date: str, ticker: str, rows: list[dict]
) -> None:
    """jev_answers 여러 행 upsert — ask() 행 그대로 받는다."""
    con.executemany(
        "INSERT OR REPLACE INTO jev_answers"
        " (run_id, date, ticker, qid, type, value, confidence, probs_json)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                run_id,
                date,
                ticker,
                r.get("qid"),
                r.get("type"),
                None if r.get("value") is None else str(r.get("value")),
                r.get("confidence"),
                json.dumps(r.get("probs"), ensure_ascii=False),
            )
            for r in rows
        ],
    )
    con.commit()


def has_state(con: sqlite3.Connection, run_id: str, ticker: str) -> bool:
    """멱등 가드 — states 행 존재 여부."""
    row = con.execute(
        "SELECT 1 FROM states WHERE run_id = ? AND ticker = ? LIMIT 1", (run_id, ticker)
    ).fetchone()
    return row is not None


def save_decisions(con: sqlite3.Connection, rows: list[dict]) -> None:
    """decisions 여러 행 upsert — decide_day 행 그대로 받는다."""
    con.executemany(
        "INSERT OR REPLACE INTO decisions"
        " (run_id, date, ticker, arm, score, rank, decision)"
        " VALUES (?,?,?,?,?,?,?)",
        [
            (
                r.get("run_id"),
                r.get("date"),
                r.get("ticker"),
                r.get("arm"),
                r.get("score"),
                r.get("rank"),
                r.get("decision"),
            )
            for r in rows
        ],
    )
    con.commit()


def save_outcomes(con: sqlite3.Connection, rows: list[dict]) -> None:
    """outcomes 여러 행 upsert — 시가 트랙만 (§8)."""
    con.executemany(
        "INSERT OR REPLACE INTO outcomes (date, ticker, d0_close, d1_open, d1_0930,"
        " d1_close, d1_high, d1_low,"
        " ret_open_0930, ret_open_close, max_ret_o, min_ret_o, excluded_open)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r.get("date"),
                r.get("ticker"),
                r.get("d0_close"),
                r.get("d1_open"),
                r.get("d1_0930"),
                r.get("d1_close"),
                r.get("d1_high"),
                r.get("d1_low"),
                r.get("ret_open_0930"),
                r.get("ret_open_close"),
                r.get("max_ret_o"),
                r.get("min_ret_o"),
                r.get("excluded_open"),
            )
            for r in rows
        ],
    )
    con.commit()


def load_outcomes(con: sqlite3.Connection, dates: list[str]) -> dict[tuple[str, str], dict]:
    """지정 일자 outcomes — (date, ticker) → 행. 멱등 건너뜀용."""
    if not dates:
        return {}
    placeholders = ", ".join("?" * len(dates))
    rows = con.execute(
        "SELECT date, ticker, d0_close, d1_open, d1_0930, d1_close, d1_high, d1_low,"
        " ret_open_0930, ret_open_close, max_ret_o, min_ret_o, excluded_open"
        f" FROM outcomes WHERE date IN ({placeholders}) ORDER BY date, ticker",
        dates,
    ).fetchall()
    cols = (
        "date", "ticker", "d0_close", "d1_open", "d1_0930", "d1_close", "d1_high",
        "d1_low", "ret_open_0930", "ret_open_close", "max_ret_o", "min_ret_o",
        "excluded_open",
    )
    return {(r[0], r[1]): dict(zip(cols, r)) for r in rows}


def load_states(con: sqlite3.Connection, run_id: str) -> list[dict]:
    """한 run_id 의 states 요약 — ticker 순."""
    rows = con.execute(
        "SELECT ticker, name, price_pct, n_passed, n_included"
        " FROM states WHERE run_id = ? ORDER BY ticker",
        (run_id,),
    ).fetchall()
    return [
        {"ticker": t, "name": n, "price_pct": p, "n_passed": np_, "n_included": ni}
        for t, n, p, np_, ni in rows
    ]


def load_answers(con: sqlite3.Connection, run_id: str) -> dict[str, list[dict]]:
    """한 run_id 의 Jev 답 — ticker → 행 목록. score·noul 은 float, choice 는 str."""
    rows = con.execute(
        "SELECT ticker, qid, type, value, confidence FROM jev_answers"
        " WHERE run_id = ? ORDER BY ticker, qid",
        (run_id,),
    ).fetchall()
    out: dict[str, list[dict]] = {}
    for ticker, qid, qtype, value, confidence in rows:
        v: object = float(value) if qtype in ("score", "noul") else str(value)
        out.setdefault(ticker, []).append(
            {"qid": qid, "type": qtype, "value": v, "confidence": confidence}
        )
    return out


def get_filter(con: sqlite3.Connection, key: str) -> float | None:
    """filter_cache 조회 — 없으면 None."""
    row = con.execute("SELECT about_noul FROM filter_cache WHERE key = ?", (key,)).fetchone()
    return None if row is None else float(row[0])


def put_filter(con: sqlite3.Connection, key: str, noul: float) -> None:
    """filter_cache upsert."""
    con.execute(
        "INSERT OR REPLACE INTO filter_cache (key, about_noul, created_at) VALUES (?,?,?)",
        (key, float(noul), datetime.now(timezone.utc).isoformat()),
    )
    con.commit()


def get_answers(con: sqlite3.Connection, key: str) -> list[dict] | None:
    """answer_cache 조회 — 없으면 None."""
    row = con.execute("SELECT rows_json FROM answer_cache WHERE key = ?", (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def put_answers(
    con: sqlite3.Connection, key: str, model: str, rows: list[dict], usage: dict
) -> None:
    """answer_cache upsert."""
    con.execute(
        "INSERT OR REPLACE INTO answer_cache (key, model, rows_json, usage_json, created_at)"
        " VALUES (?,?,?,?,?)",
        (
            key,
            model,
            json.dumps(rows, ensure_ascii=False),
            json.dumps(usage, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    con.commit()
