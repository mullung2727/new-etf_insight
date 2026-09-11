"""호가 수집기 대상 종목 (SPEC_ORDERBOOK_SNAPSHOT_RECORDER §4).

오전: 미청산 → 직전 후보 → static. 전날까지의 사실만 쓰므로 바로 ready/empty.
오후: 15:00 점수 배치의 완료 산출물(recent_3day_probability_scores.json)을 7개 조건으로
      검증한 뒤에만 ready. 파일을 쓰는 중이거나 전날 결과면 wait — 호출부가 1초마다 다시 부른다.
입력 DB 는 읽기 전용이다. 조회 전에 파일 존재를 확인한다(없는 DB 를 새로 만들지 않게).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from orderbook_recorder_config import CODE_RE  # noqa: E402
from wl_sqlite import connect_ro  # noqa: E402

KST = timezone(timedelta(hours=9))
WATCHLIST_DB = pathlib.Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"
_READ_TRIES = 3


def _result(status: str, source_date: str | None, symbols: list[str],
            excluded: list[dict], meta: dict) -> dict[str, Any]:
    return {"status": status, "source_date": source_date, "symbols": symbols,
            "excluded": excluded, "source_meta": meta}


def _union(groups: list[list[str]], max_n: int) -> tuple[list[str], list[dict]]:
    """최초 출현 순서로 중복 제거 후 max 개. 넘친 종목은 symbol_limit 으로 남긴다."""
    ordered = list(dict.fromkeys(t for group in groups for t in group))
    return ordered[:max_n], [{"ticker": t, "reason": "symbol_limit"} for t in ordered[max_n:]]


def plan_additions(current: list[str], symbols: list[str], max_n: int) -> tuple[list[str], list[dict]]:
    """구간 도중 새 완료 산출물 → 추가할 신규 종목. 기존 등록 수를 포함해 max 를 적용한다."""
    new = [t for t in symbols if t not in current]
    room = max(max_n - len(current), 0)
    return new[:room], [{"ticker": t, "reason": "symbol_limit"} for t in new[room:]]


def _days_between(later: str, earlier: str) -> int:
    return (datetime.strptime(later, "%Y%m%d") - datetime.strptime(earlier, "%Y%m%d")).days


def _morning(cfg: dict, date: str, db: pathlib.Path) -> dict[str, Any]:
    notes: list[str] = []
    unsold: list[str] = []
    candidates: list[str] = []
    source_date = None
    if not db.exists():
        notes.append("watchlist_db_missing")
    else:
        with connect_ro(db) as con:
            # run_close_bet_exit.load_unsold_positions 와 같은 조건. 그 함수는 스키마를 바꿔서 직접 부르지 않는다.
            unsold = [r[0] for r in con.execute(
                "SELECT DISTINCT ticker FROM close_bet_orders"
                " WHERE status = 'confirmed' AND sell_status IS NULL AND date < ? ORDER BY ticker",
                [date])]
            prev = con.execute("SELECT MAX(date) FROM llm_scores WHERE date < ?", [date]).fetchone()[0]
            if prev is None:
                notes.append("no_prior_candidates")
            elif _days_between(date, prev) > cfg["symbols"]["max_lookback_days"]:
                notes.append("candidates_stale")
            else:
                candidates = [r[0] for r in con.execute(
                    "SELECT DISTINCT ticker FROM llm_scores WHERE date = ? ORDER BY ticker", [prev])]
                source_date = prev
    symbols, excluded = _union([unsold, candidates, cfg["symbols"]["static"]], cfg["symbols"]["max"])
    meta = {"generated_at": None, "sha256": None, "notes": notes,
            "unsold": unsold, "candidates": candidates}
    return _result("ready" if symbols else "empty", source_date, symbols, excluded, meta)


def _read_stable(path: pathlib.Path) -> tuple[bytes | None, str | None]:
    """읽기 전후 크기·mtime 이 같을 때의 바이트. 생산자가 쓰는 중이면 다시 읽는다."""
    for _ in range(_READ_TRIES):
        try:
            before = path.stat()
            raw = path.read_bytes()
            after = path.stat()
        except FileNotFoundError:
            return None, "file_missing"
        if ((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
                and len(raw) == after.st_size):
            return raw, None
    return None, "file_changing"


def _afternoon(cfg: dict, date: str, db: pathlib.Path, now: datetime) -> dict[str, Any]:
    def wait(reason: str) -> dict[str, Any]:
        return _result("wait", None, [], [], {"reason": reason})

    raw, reason = _read_stable(pathlib.Path(cfg["scoring_result_path"]))
    if reason:
        return wait(reason)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return wait("invalid_json")
    if not isinstance(doc, dict):
        return wait("invalid_json")
    if doc.get("db_write") is not True:
        return wait("db_write_false")

    try:
        generated = datetime.fromisoformat(doc["generated_at"])
    except (KeyError, TypeError, ValueError):
        return wait("generated_at_invalid")
    if generated.tzinfo is None:
        return wait("generated_at_invalid")
    start = datetime.strptime(date + cfg["windows"]["afternoon"]["start"], "%Y%m%d%H:%M:%S")
    generated = generated.astimezone(KST)
    if generated < start.replace(tzinfo=KST):     # 전날 결과 또는 당일 15:00 이전 결과
        return wait("generated_at_stale")
    if generated > now:
        return wait("generated_at_future")

    results = [r for r in doc.get("results") or [] if isinstance(r, dict) and r.get("date") == date]
    if len(results) != 1:
        return wait("no_result_for_date" if not results else "results_not_unique")
    scores = results[0].get("scores")
    if not isinstance(scores, list) or not (
            results[0].get("candidate_count") == results[0].get("scored_count") == len(scores)):
        return wait("count_mismatch")
    tickers = [s.get("ticker") for s in scores if isinstance(s, dict)]
    if (len(tickers) != len(scores) or len(set(tickers)) != len(tickers)
            or not all(isinstance(t, str) and CODE_RE.match(t) for t in tickers)
            or any(s.get("date") != date for s in scores)):
        return wait("scores_invalid")

    if tickers:
        if not db.exists():
            return wait("watchlist_db_missing")
        marks = ",".join("?" * len(tickers))
        with connect_ro(db) as con:   # 한 SELECT = 한 읽기 트랜잭션
            stored = dict(con.execute(
                f"SELECT ticker, score FROM llm_scores WHERE date = ? AND ticker IN ({marks})",
                [date, *tickers]).fetchall())
        if any(t not in stored or stored[t] != s.get("probability_score")
               for t, s in zip(tickers, scores)):
            return wait("db_score_mismatch")

    symbols, excluded = _union([sorted(tickers), cfg["symbols"]["static"]], cfg["symbols"]["max"])
    meta = {"generated_at": doc["generated_at"], "sha256": hashlib.sha256(raw).hexdigest(),
            "candidates": sorted(tickers)}
    return _result("ready" if symbols else "empty", date, symbols, excluded, meta)


def resolve_symbols(cfg: dict, date: str, window: str, *, watchlist_db: pathlib.Path = WATCHLIST_DB,
                    now: datetime | None = None) -> dict[str, Any]:
    """date(KST YYYYMMDD)·window(morning/afternoon) → status(ready/wait/empty) 와 대상 종목."""
    if window == "morning":
        return _morning(cfg, date, pathlib.Path(watchlist_db))
    if window == "afternoon":
        return _afternoon(cfg, date, pathlib.Path(watchlist_db), now or datetime.now(KST))
    raise ValueError(f"unknown window {window!r}")
