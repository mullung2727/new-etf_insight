"""스윙 후보 결과 저장: etl/db/swing_pick.sqlite3.

설계: docs/done/PLAN_SWING_PICK.md §2-3 스키마 그대로 + 컬럼 2개
(swing_candidates.excluded_reason, swing_runs.prev_date·warnings — warnings는
JSON 배열).
연결 규약·커밋 방식은 scripts/report_metrics/storage.py와 같다
(WAL writer / query_only reader).

왜 판정 전부를 저장하나:
- 탈락 종목(1차 컷·리스크·오류)도 행으로 남긴다. 나중에 항목별 예측력을
  검증하려면 (어느 항목이 1주~1달 수익을 가르나) 통과 종목만으론 부족해서다.
- 같은 날 재실행은 그날 행을 통째로 교체한다 (멱등). 19:00 배치가 중간에
  죽고 다시 돌 때 반쪽 날짜가 두 벌 남으면 검증을 망가뜨리기 때문이다.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "db" / "swing_pick.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS swing_candidates (
    date_kst      TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    name          TEXT NOT NULL,
    sources       TEXT NOT NULL,     -- JSON 배열: telegram / youtube / report / high52
    trading_value INTEGER,           -- 1차 컷 근거
    jev_sustain   REAL,              -- 1번 Jev score
    jev_risk      REAL,              -- 2번 Jev noul('예' 확률)
    op_profit     REAL,              -- 3번 최근 분기 영업이익
    op_profit_yoy REAL,              -- 3번 전년 동기 영업이익
    frgn_net_5d   INTEGER,           -- 4번 외인 5일 순매수(금액)
    orgn_net_5d   INTEGER,           -- 4번 기관 5일 순매수(금액)
    ma20_gap      REAL,              -- 5번 종가/20일선 - 1
    ret_5d        REAL,              -- 5번 5일 상승률
    per           REAL,              -- 6번 참고 (적자면 NULL + per_note)
    per_note      TEXT,
    themes        TEXT,              -- 7번 참고: JSON [{name, ret, up, down}]
    s1 INTEGER, s3 INTEGER, s4 INTEGER, s5 INTEGER,   -- 0/1/2
    risk_out      INTEGER NOT NULL,  -- 2번 탈락 1/0 (Jev 실패도 0, jev_error로 구분)
    total         INTEGER,           -- s1+s3+s4+s5 (탈락도 계산해 둠)
    rank          INTEGER,           -- 최종 순위 (상위 3만 1~3, 나머지 NULL)
    errors        TEXT,              -- JSON {항목: 오류명}
    jev_input_hash TEXT,             -- Jev 입력 묶음 sha1 (재현·중복 확인)
    excluded_reason TEXT,            -- 순위 제외 사유 (cut:<사유>/risk/jev_error/status:<사유>), 순위 대상이면 NULL
    created_at    TEXT NOT NULL,
    PRIMARY KEY (date_kst, ticker)
);

CREATE TABLE IF NOT EXISTS swing_runs (
    date_kst      TEXT PRIMARY KEY,
    n_candidates  INTEGER NOT NULL,
    n_risk_out    INTEGER NOT NULL,
    n_errors      INTEGER NOT NULL,
    jev_input_tokens INTEGER,
    summary       TEXT,              -- GPT 요약문 JSON (실패 시 NULL)
    summary_model TEXT,
    notified      INTEGER NOT NULL,  -- 전송 성공 1/0
    prev_date     TEXT,              -- 시세 기준일 YYYYMMDD (KRX DB max date)
    warnings      TEXT,              -- JSON 배열
    created_at    TEXT NOT NULL
);
"""

_CANDIDATE_COLUMNS = [
    "date_kst", "ticker", "name", "sources", "trading_value",
    "jev_sustain", "jev_risk", "op_profit", "op_profit_yoy",
    "frgn_net_5d", "orgn_net_5d", "ma20_gap", "ret_5d",
    "per", "per_note", "themes",
    "s1", "s3", "s4", "s5", "risk_out", "total", "rank",
    "errors", "jev_input_hash", "excluded_reason", "created_at",
]

_RUN_COLUMNS = [
    "date_kst", "n_candidates", "n_risk_out", "n_errors",
    "jev_input_tokens", "summary", "summary_model", "notified",
    "prev_date", "warnings", "created_at",
]

# list/dict를 받으면 JSON 문자열로 직렬화하는 컬럼. 문자열이 들어오면 그대로 둔다
# (summary는 호출자가 미리 JSON 문자열로 만든다).
_JSON_COLUMNS = frozenset({"sources", "themes", "errors", "warnings"})


@contextmanager
def connect_rw(db_path: str | Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextmanager
def connect_ro(db_path: str | Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()


def init_db(db_path: str | Path = DEFAULT_DB) -> None:
    """스키마 생성. 재실행 멱등."""
    with connect_rw(db_path) as con:
        con.executescript(_SCHEMA)


def _jsonable(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_day(
    db_path: str | Path,
    date_kst: str,
    candidates: list[dict],
    run: dict,
) -> None:
    """하루치 판정 저장. 한 트랜잭션으로 그날 후보 전부 교체 + run upsert.

    dict 키 = 컬럼명. 모르는 키는 무시하고, 빠진 컬럼은 NULL로 둔다
    (호출자가 그래프 내부 키(input_text 등)를 섞어 넘겨도 되게).
    risk_out None(Jev 실패)은 0으로 저장한다 — NOT NULL 컬럼이라 NULL을 못
    넣고, 실패 여부는 excluded_reason='jev_error' + errors.jev로 구분돼서다.
    """
    with connect_rw(db_path) as con:
        con.executescript(_SCHEMA)
        con.execute("DELETE FROM swing_candidates WHERE date_kst = ?", (date_kst,))
        now = _utc_now()
        for row in candidates:
            values = []
            for col in _CANDIDATE_COLUMNS:
                if col == "date_kst":
                    values.append(date_kst)
                elif col == "created_at":
                    values.append(now)
                elif col == "risk_out":
                    values.append(0 if row.get("risk_out") is None else row[col])
                elif col in _JSON_COLUMNS:
                    values.append(_jsonable(row.get(col)))
                else:
                    values.append(row.get(col))
            holders = ", ".join("?" for _ in _CANDIDATE_COLUMNS)
            con.execute(
                f"INSERT INTO swing_candidates ({', '.join(_CANDIDATE_COLUMNS)}) "
                f"VALUES ({holders})",
                values,
            )
        run_values = []
        for col in _RUN_COLUMNS:
            if col == "date_kst":
                run_values.append(date_kst)
            elif col == "created_at":
                run_values.append(now)
            elif col in _JSON_COLUMNS:
                run_values.append(_jsonable(run.get(col)))
            else:
                run_values.append(run.get(col))
        holders = ", ".join("?" for _ in _RUN_COLUMNS)
        con.execute(
            f"INSERT OR REPLACE INTO swing_runs ({', '.join(_RUN_COLUMNS)}) "
            f"VALUES ({holders})",
            run_values,
        )


def mark_notified(db_path: str | Path, date_kst: str) -> None:
    """전송 성공 후 notified만 1로. save_day 때 notify 결과를 알 수 없어 분리."""
    with connect_rw(db_path) as con:
        con.execute(
            "UPDATE swing_runs SET notified = 1 WHERE date_kst = ?",
            (date_kst,),
        )
