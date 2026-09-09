"""업무 DB — 연결·스키마·run lease·원문 버전 저장 (PLAN §14, §15).

기존 wl_sqlite 는 watchlist 전용이라 재사용하지 않는다(§15.2). 여기 연결 함수는
트랜잭션 시작 전에 FK 를 켜고 활성값을 검사한다. 체크포인터 DB 는 LangGraph 가
소유하며 이 경로로 쓰지 않는다.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "etl" / "db" / "early_signals.sqlite3"
# 추출은 "무엇을 뽑는가"이고 처리 한도는 "몇 건 처리하는가"다. 한도를 바꿨다고 이미 뽑아둔
# 변화가 달라지지 않으므로 추출 캐시 키에는 정책 버전을 넣지 않는다(§14 stage별 규칙).
EXTRACT_CACHE_POLICY = "policy_independent"
LEASE_HEARTBEAT_SEC = 60
LEASE_TAKEOVER_SEC = 300

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  cutoff_at TEXT NOT NULL,
  price_as_of TEXT,
  policy_version TEXT NOT NULL,
  prompt_version TEXT,
  model_identity TEXT,
  code_version TEXT,
  identity_version TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  manifest_id TEXT,
  manifest_hash TEXT,
  coverage_json TEXT,
  throughput_json TEXT,
  lease_owner TEXT,
  lease_heartbeat_at TEXT,
  run_status TEXT NOT NULL,
  result_json TEXT,
  error TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  artifact_path TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (mode, cutoff_at, policy_version, revision)
);

CREATE TABLE IF NOT EXISTS source_versions (
  source_version_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_key TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  document_key TEXT,
  origin_group_id TEXT NOT NULL,
  independence TEXT NOT NULL,
  published_at TEXT,
  published_precision TEXT,
  first_observed_at TEXT NOT NULL,
  available_at TEXT NOT NULL,
  extracted_text TEXT NOT NULL,
  raw_json TEXT,
  pdf_bytes_hash TEXT,
  pdf_path TEXT,
  entity_ids_json TEXT,
  quality_json TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (source_type, source_key, content_hash)
);

CREATE TABLE IF NOT EXISTS manifest_entries (
  manifest_id TEXT NOT NULL,
  source_version_id TEXT NOT NULL,
  PRIMARY KEY (manifest_id, source_version_id),
  FOREIGN KEY (source_version_id) REFERENCES source_versions(source_version_id)
);

CREATE TABLE IF NOT EXISTS processing_results (
  stage TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model_identity TEXT NOT NULL,
  code_version TEXT NOT NULL,
  status TEXT NOT NULL,
  output_json TEXT,
  raw_response TEXT,
  attempts INTEGER NOT NULL DEFAULT 1,
  elapsed_sec REAL,
  char_count INTEGER,
  error TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (stage, input_hash, policy_version, prompt_version, model_identity, code_version)
);

CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY,
  source_version_id TEXT NOT NULL,
  event_fingerprint TEXT NOT NULL,
  change_key TEXT NOT NULL,
  extract_version TEXT NOT NULL,
  entity_ids_json TEXT NOT NULL,
  anchor_locators_json TEXT NOT NULL,
  change_json TEXT NOT NULL,
  supersedes_event_id TEXT,
  first_detected_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (source_version_id, event_fingerprint, extract_version),
  FOREIGN KEY (source_version_id) REFERENCES source_versions(source_version_id)
);

CREATE TABLE IF NOT EXISTS event_evidence (
  event_id TEXT NOT NULL,
  source_version_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  locator_hash TEXT NOT NULL,
  locator_json TEXT NOT NULL,
  origin_group_id TEXT NOT NULL,
  independence TEXT NOT NULL,
  PRIMARY KEY (event_id, source_version_id, relation, locator_hash),
  FOREIGN KEY (event_id) REFERENCES events(event_id),
  FOREIGN KEY (source_version_id) REFERENCES source_versions(source_version_id)
);

CREATE TABLE IF NOT EXISTS assessments (
  assessment_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  episode_id TEXT,
  evidence_json TEXT,
  price_json TEXT,
  grades_json TEXT NOT NULL,
  action TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL,
  valid_until TEXT,
  previous_assessment_id TEXT,
  created_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (run_id, subject_type, subject_id),
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


@contextmanager
def connect_rw(db_path: Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    """업무 쓰기 연결 — WAL, FK ON(활성 검사), 정상 종료 commit / 예외 rollback / 항상 close."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        _enforce_foreign_keys(con)
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@contextmanager
def connect_ro(db_path: Path = DEFAULT_DB) -> Iterator[sqlite3.Connection]:
    """읽기 전용 연결 — query_only 와 FK ON. 쓰기 시도는 sqlite 가 거부한다."""
    con = sqlite3.connect(str(db_path))
    try:
        _enforce_foreign_keys(con)
        con.execute("PRAGMA query_only=ON")
        yield con
    finally:
        con.close()


def _enforce_foreign_keys(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA foreign_keys=ON")
    if con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("foreign_keys 를 켤 수 없다 — 업무 DB 연결을 중단한다")


def ensure_schema(db_path: Path = DEFAULT_DB) -> None:
    with connect_rw(db_path) as con:
        con.executescript(_SCHEMA)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def acquire_run_lease(
    con: sqlite3.Connection, run_id: str, owner: str | None = None, *, now: str | None = None
) -> bool:
    """실행 lease 는 하나만 허용한다(§14).

    heartbeat 가 LEASE_TAKEOVER_SEC 이상 갱신되지 않았고 기존 프로세스가 살아 있지
    않을 때만 인수한다. 생존을 확인할 수 없으면 중복 시작을 거부한다.
    """
    owner = owner or f"{os.getpid()}@{os.environ.get('COMPUTERNAME', 'local')}"
    stamp = now or utc_now()
    row = con.execute(
        "SELECT lease_owner, lease_heartbeat_at FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        return False
    holder, heartbeat = row
    if holder and holder != owner:
        if not heartbeat:
            return False
        age = (datetime.fromisoformat(stamp) - datetime.fromisoformat(heartbeat)).total_seconds()
        if age < LEASE_TAKEOVER_SEC or _process_alive(holder):
            return False
    con.execute(
        "UPDATE runs SET lease_owner=?, lease_heartbeat_at=? WHERE run_id=?",
        (owner, stamp, run_id),
    )
    return True


def _process_alive(owner: str) -> bool:
    """같은 PC 의 PID 만 확인 가능하다. 확인 불가면 살아 있다고 본다(fail-closed)."""
    pid_text, _, host = owner.partition("@")
    if host != os.environ.get("COMPUTERNAME", "local"):
        return True
    try:
        pid = int(pid_text)
    except ValueError:
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return True
    return True


def start_run(
    con: sqlite3.Connection, *, run_id: str, mode: str, cutoff_at: str, policy_version: str,
    identity_version: str, code_version: str, prompt_version: str = "", model_identity: str = "",
) -> int:
    """새 run 행을 만든다. 같은 (mode, cutoff, policy) 재실행은 revision 을 올린다.

    unique(mode, cutoff_at, policy_version, revision) 때문에 revision 고정으로는 재실행이
    조용히 무시되고, 행이 없어 lease 획득까지 실패한다(§15.2 오류 정정은 새 revision).
    """
    row = con.execute(
        "SELECT MAX(revision) FROM runs WHERE mode=? AND cutoff_at=? AND policy_version=?",
        (mode, cutoff_at, policy_version),
    ).fetchone()
    revision = (row[0] or 0) + 1
    con.execute(
        "INSERT INTO runs (run_id, mode, cutoff_at, policy_version, identity_version,"
        " code_version, prompt_version, model_identity, revision, run_status, started_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,'running',?)",
        (run_id, mode, cutoff_at, policy_version, identity_version, code_version,
         prompt_version, model_identity, revision, utc_now()),
    )
    return revision


def persist_source_version(con: sqlite3.Connection, record: dict[str, Any]) -> str:
    """원문 버전 저장. 같은 (type, key, content_hash) 는 덮어쓰지 않고 기존 id 를 돌려준다."""
    existing = con.execute(
        "SELECT source_version_id FROM source_versions WHERE source_type=? AND source_key=?"
        " AND content_hash=?",
        (record["source_type"], record["source_key"], record["content_hash"]),
    ).fetchone()
    if existing:
        return existing[0]
    con.execute(
        "INSERT INTO source_versions (source_version_id, source_type, source_key, content_hash,"
        " document_key, origin_group_id, independence, published_at, published_precision,"
        " first_observed_at, available_at, extracted_text, raw_json, pdf_bytes_hash, pdf_path,"
        " entity_ids_json, quality_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            record["source_version_id"], record["source_type"], record["source_key"],
            record["content_hash"], record.get("document_key"), record["origin_group_id"],
            record["independence"], record.get("published_at"), record.get("published_precision"),
            record["first_observed_at"], record["available_at"], record["extracted_text"],
            json.dumps(record.get("raw"), ensure_ascii=False) if record.get("raw") else None,
            record.get("pdf_bytes_hash"), record.get("pdf_path"),
            json.dumps(record.get("entity_ids", []), ensure_ascii=False),
            json.dumps(record.get("quality", {}), ensure_ascii=False),
        ),
    )
    return record["source_version_id"]


def freeze_manifest(con: sqlite3.Connection, manifest_id: str, source_version_ids: list[str]) -> str:
    """입력 목록을 동결한다. 같은 manifest_id 재호출은 멱등이다(§4.2)."""
    con.executemany(
        "INSERT OR IGNORE INTO manifest_entries (manifest_id, source_version_id) VALUES (?,?)",
        [(manifest_id, sid) for sid in source_version_ids],
    )
    from .identity import canonical_hash

    return canonical_hash({"manifest_id": manifest_id, "sources": sorted(set(source_version_ids))})


def load_manifest(con: sqlite3.Connection, manifest_id: str) -> list[str]:
    return [row[0] for row in con.execute(
        "SELECT source_version_id FROM manifest_entries WHERE manifest_id=? ORDER BY source_version_id",
        (manifest_id,),
    )]


def finish_run(
    con: sqlite3.Connection, run_id: str, *, status: str, result: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None, throughput: dict[str, Any] | None = None,
    manifest_id: str | None = None, manifest_hash: str | None = None,
    artifact_path: str | None = None, error: str | None = None,
) -> None:
    # 뒤 단계(assess)가 앞 단계(extract)의 실측치를 NULL 로 덮지 않도록 전부 COALESCE 한다.
    con.execute(
        "UPDATE runs SET run_status=?, result_json=COALESCE(?, result_json),"
        " coverage_json=COALESCE(?, coverage_json),"
        " throughput_json=COALESCE(?, throughput_json),"
        " manifest_id=COALESCE(?, manifest_id), manifest_hash=COALESCE(?, manifest_hash),"
        " artifact_path=COALESCE(?, artifact_path), error=COALESCE(?, error),"
        " finished_at=? WHERE run_id=?",
        (
            status,
            json.dumps(result, ensure_ascii=False) if result is not None else None,
            json.dumps(coverage, ensure_ascii=False) if coverage is not None else None,
            json.dumps(throughput, ensure_ascii=False) if throughput is not None else None,
            manifest_id, manifest_hash, artifact_path, error, utc_now(), run_id,
        ),
    )


def persist_events(con: sqlite3.Connection, events: list[dict[str, Any]]) -> int:
    """이벤트·근거를 멱등 저장한다. 같은 (source_version, fingerprint, extract_version) 은 무시."""
    inserted = 0
    for event in events:
        cursor = con.execute(
            "INSERT OR IGNORE INTO events (event_id, source_version_id, event_fingerprint,"
            " change_key, extract_version, entity_ids_json, anchor_locators_json, change_json,"
            " first_detected_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event["event_id"], event["source_version_id"], event["event_fingerprint"],
                event["change_key"], event["extract_version"],
                json.dumps(event["entity_ids"], ensure_ascii=False),
                json.dumps(event["anchor_locators"], ensure_ascii=False),
                json.dumps(event["change"], ensure_ascii=False), utc_now(),
            ),
        )
        inserted += cursor.rowcount
        for anchor in event["anchor_locators"]:
            con.execute(
                "INSERT OR IGNORE INTO event_evidence (event_id, source_version_id, relation,"
                " locator_hash, locator_json, origin_group_id, independence)"
                " VALUES (?,?,'support',?,?,?,?)",
                (event["event_id"], event["source_version_id"], anchor["locator_hash"],
                 json.dumps(anchor, ensure_ascii=False),
                 event.get("origin_group_id", ""), event.get("independence", "unknown")),
            )
    return inserted


def record_processing(
    con: sqlite3.Connection, *, stage: str, input_hash: str, policy_version: str,
    prompt_version: str, model_identity: str, code_version: str, status: str,
    output: Any = None, attempts: int = 1, elapsed_sec: float | None = None,
    char_count: int | None = None, error: str | None = None,
) -> None:
    """완료 결과 재사용을 위한 캐시(§14). 같은 입력·버전이면 다시 호출하지 않는다."""
    con.execute(
        "INSERT OR REPLACE INTO processing_results (stage, input_hash, policy_version,"
        " prompt_version, model_identity, code_version, status, output_json, attempts,"
        " elapsed_sec, char_count, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (stage, input_hash, policy_version, prompt_version, model_identity, code_version,
         status, json.dumps(output, ensure_ascii=False) if output is not None else None,
         attempts, elapsed_sec, char_count, error, utc_now()),
    )


def load_processing(
    con: sqlite3.Connection, *, stage: str, input_hash: str, policy_version: str,
    prompt_version: str, model_identity: str, code_version: str,
) -> dict[str, Any] | None:
    row = con.execute(
        "SELECT status, output_json FROM processing_results WHERE stage=? AND input_hash=?"
        " AND policy_version=? AND prompt_version=? AND model_identity=? AND code_version=?",
        (stage, input_hash, policy_version, prompt_version, model_identity, code_version),
    ).fetchone()
    if not row or row[0] != "done":
        return None
    return json.loads(row[1]) if row[1] else None


_SQL_VARS = 900      # SQLite 기본 한도(32,766)보다 넉넉히 아래로 끊는다


def load_sources(con: sqlite3.Connection, source_version_ids: list[str]) -> list[dict[str, Any]]:
    keys = ("source_version_id", "source_type", "source_key", "published_at",
            "extracted_text", "entity_ids_json", "origin_group_id", "independence")
    out = []
    for start in range(0, len(source_version_ids), _SQL_VARS):
        batch = source_version_ids[start:start + _SQL_VARS]
        marks = ",".join("?" * len(batch))
        for row in con.execute(
            f"SELECT source_version_id, source_type, source_key, published_at, extracted_text,"
            f" entity_ids_json, origin_group_id, independence FROM source_versions"
            f" WHERE source_version_id IN ({marks})", batch,
        ).fetchall():
            record = dict(zip(keys, row))
            record["entity_ids"] = json.loads(record.pop("entity_ids_json") or "[]")
            out.append(record)
    return out


def persist_assessment(con: sqlite3.Connection, run_id: str, item: dict[str, Any]) -> str:
    """종목 판단을 저장한다. 같은 run 의 같은 대상은 한 번만(§15.2 unique).

    episode_id 는 성과를 묶는 단위다. 같은 가설이 매주 다시 뽑혀도 독립 표본으로
    세지 않으려면 여기서 이어 붙여야 한다(§17.2, T26).
    """
    from .identity import canonical_hash

    assessment_id = canonical_hash({
        "run_id": run_id, "subject_type": item["subject_type"], "subject_id": item["subject_id"]})
    con.execute(
        "INSERT OR IGNORE INTO assessments (assessment_id, run_id, subject_type, subject_id,"
        " episode_id, evidence_json, price_json, grades_json, action, reason_codes_json,"
        " valid_until, previous_assessment_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            assessment_id, run_id, item["subject_type"], item["subject_id"],
            item.get("episode_id"),
            json.dumps({"event_count": item.get("event_count"),
                        "origin_groups": item.get("origin_groups"),
                        "event_ids": [e.get("event_id") for e in item.get("events", [])]},
                       ensure_ascii=False),
            json.dumps({**item.get("price", {}), "scenario": item.get("scenario")},
                       ensure_ascii=False),
            json.dumps(item["grades"], ensure_ascii=False), item["action"],
            json.dumps(item["reason_codes"], ensure_ascii=False),
            item.get("valid_until"), item.get("previous_assessment_id"), utc_now(),
        ),
    )
    return assessment_id


def find_open_episode(
    con: sqlite3.Connection, subject_id: str, cutoff_at: str, max_age_days: int = 84
) -> tuple[str, str] | None:
    """진행 중인 episode 를 찾는다. (episode_id, 시작 run 의 cutoff).

    같은 종목이 매주 다시 뽑혀도 새 episode 를 만들지 않는다 — 84일이 지나야 새로 연다.
    """
    row = con.execute(
        "SELECT a.episode_id, r.cutoff_at FROM assessments a JOIN runs r ON r.run_id = a.run_id"
        " WHERE a.subject_id = ? AND a.episode_id IS NOT NULL AND r.cutoff_at <= ?"
        " ORDER BY r.cutoff_at DESC LIMIT 1", (subject_id, cutoff_at)).fetchone()
    if not row:
        return None
    started = datetime.fromisoformat(row[1])
    if (datetime.fromisoformat(cutoff_at) - started).days > max_age_days:
        return None
    return row[0], row[1]


def load_assessments(
    con: sqlite3.Connection, run_id: str, actions: tuple[str, ...] | None = None
) -> list[dict[str, Any]]:
    query = ("SELECT assessment_id, subject_id, episode_id, grades_json, action,"
             " reason_codes_json, price_json FROM assessments WHERE run_id=?")
    params: list[Any] = [run_id]
    if actions:
        query += f" AND action IN ({','.join('?' * len(actions))})"
        params.extend(actions)
    keys = ("assessment_id", "subject_id", "episode_id", "grades", "action",
            "reason_codes", "price")
    out = []
    for row in con.execute(query, params).fetchall():
        item = dict(zip(keys, row))
        for field in ("grades", "reason_codes", "price"):
            item[field] = json.loads(item[field]) if item[field] else None
        out.append(item)
    return out
