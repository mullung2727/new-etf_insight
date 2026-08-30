"""테마 사전을 개정한다 - 추가·분할·병합·폐기와 old->new 매핑을 함께 산출한다.

스코어링 경로 밖에서 주기적으로 돈다. 15:19 종가베팅 주문 창을 건드리지 않는다.

사람이 후보를 확인해 승격시키는 것은 불가능하다는 요구라, 승격 판단은 LLM이 한다.
다만 LLM에 올리기 전에 결정론 게이트를 먼저 건다. 1회성 고유명사(`청주 P&T7`,
`ALT-B4`)가 그대로 사전에 들어가면 사전이 무한 팽창해 자유문자열 시절로 되돌아간다.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "etl" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from new_etf_insight.llm import generate_json  # noqa: E402

from research.watchlist_expected_return.watchlist_probability_langgraph import (  # noqa: E402
    DEFAULT_WATCHLIST_DB,
    THEME_DICT_PATH,
    THEME_NOT_IN_DICT,
    load_theme_dictionary,
)

SEOUL = ZoneInfo("Asia/Seoul")
REVISION_SCHEMA_PATH = Path(__file__).resolve().with_name("theme_revision_schema.json")
REVISION_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "theme_revision.md"
DEFAULT_MIN_COUNT = 3
DEFAULT_MIN_TICKERS = 2
DEFAULT_UNUSED_DAYS = 90
# 폐기 판정 최소 표본. 이보다 적으면 "안 쓰였다"와 "아직 안 나왔다"를 구분할 수 없다.
# 하루 후보가 평균 5.2개라 20건은 약 4거래일이다.
DEFAULT_MIN_USAGE_SAMPLES = 20
ACTIONS = {"kept", "added", "merged", "split", "retired"}


def _has_theme_columns(con: sqlite3.Connection) -> bool:
    """v5 스코어링이 한 번도 안 돌았으면 테마 컬럼이 없다. 읽기 전용이라 ALTER하지 않는다."""
    columns = {row[1] for row in con.execute("PRAGMA table_info(llm_catalyst_assessments)")}
    return {"new_theme_candidate", "theme_scores_json"} <= columns


def collect_candidates(
    db_path: Path, min_count: int, min_tickers: int
) -> tuple[list[dict], list[dict]]:
    """승격 게이트를 통과한 새 테마 후보와 탈락 후보를 나눠 준다.

    반복성(min_count)과 일반성(min_tickers)을 둘 다 요구한다. 한 종목에서만
    여러 번 나온 이름은 그 종목 고유명사일 가능성이 높아 사전에 올리지 않는다.
    """
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    with closing(sqlite3.connect(
        f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True
    )) as con:
        if not _has_theme_columns(con):
            return [], []
        rows = con.execute("""
            SELECT new_theme_candidate, ticker, date
            FROM llm_catalyst_assessments
            WHERE new_theme_candidate IS NOT NULL AND TRIM(new_theme_candidate) <> ''
        """).fetchall()
    dates: dict[str, list[str]] = collections.defaultdict(list)
    for name, ticker, date in rows:
        counts[name.strip()][ticker] += 1
        dates[name.strip()].append(date)

    promoted, rejected = [], []
    for name, by_ticker in sorted(counts.items(), key=lambda kv: -sum(kv[1].values())):
        entry = {
            "name": name,
            "count": sum(by_ticker.values()),
            "ticker_count": len(by_ticker),
            "tickers": sorted(by_ticker),
            "dates": sorted(set(dates[name])),
        }
        target = promoted if (
            entry["count"] >= min_count and entry["ticker_count"] >= min_tickers
        ) else rejected
        target.append(entry)
    return promoted, rejected


def collect_usage(db_path: Path, unused_days: int) -> dict[str, dict[str, int]]:
    """축별 테마 사용 횟수. 최근 구간에서 0회면 폐기 후보다."""
    cutoff = (dt.datetime.now(SEOUL) - dt.timedelta(days=unused_days)).strftime("%Y%m%d")
    usage: dict[str, collections.Counter] = {
        "sector": collections.Counter(), "event": collections.Counter()
    }
    with closing(sqlite3.connect(
        f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True
    )) as con:
        if not _has_theme_columns(con):
            return {"sector": {}, "event": {}}
        rows = con.execute(
            "SELECT theme_scores_json FROM llm_catalyst_assessments"
            " WHERE theme_scores_json IS NOT NULL AND date >= ?",
            (cutoff,),
        ).fetchall()
    for (payload,) in rows:
        try:
            scores = json.loads(payload)
        except (TypeError, ValueError):
            continue
        for axis in ("sector", "event"):
            for item in scores.get(axis, []):
                if item.get("score", 0) > 0:
                    usage[axis][item["name"]] += 1
    return {axis: dict(counter) for axis, counter in usage.items()}


def build_revision_input(
    theme_dict: dict, promoted: list[dict], usage: dict[str, dict[str, int]], unused_days: int
) -> dict:
    return {
        "current_version": theme_dict["version"],
        "unused_window_days": unused_days,
        "axes": {
            axis: [
                {
                    "name": item["name"],
                    "members": item["members"],
                    "recent_use_count": usage.get(axis, {}).get(item["name"], 0),
                }
                for item in theme_dict[key]
            ]
            for axis, key in (("sector", "theme_sector"), ("event", "theme_event"))
        },
        "new_theme_candidates": promoted,
        "not_in_dict_use_count": {
            axis: usage.get(axis, {}).get(THEME_NOT_IN_DICT, 0) for axis in ("sector", "event")
        },
    }


def make_revision_prompt(revision_input: dict, next_version: str) -> str:
    template = REVISION_PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        next_version=next_version,
        input_json=json.dumps(revision_input, ensure_ascii=False, indent=2),
    )


def validate_revision(revision: dict, theme_dict: dict) -> None:
    """개정안이 과거 데이터를 고아로 만들지 않는지 본다.

    현행 사전의 모든 이름은 새 사전에 남아 있거나 매핑에 new_value를 가져야 한다.
    하나라도 빠지면 그 값으로 저장된 과거 행의 group by 가 조각난다.
    """
    if revision["version"] == theme_dict["version"]:
        raise ValueError("revision version must differ from current version")

    special = {item["name"] for item in theme_dict["special"]}
    migrations = revision["migrations"]
    for migration in migrations:
        if migration["axis"] not in {"sector", "event"}:
            raise ValueError(f"unknown axis: {migration['axis']}")
        if migration["action"] not in ACTIONS:
            raise ValueError(f"unknown action: {migration['action']}")
        if migration["action"] != "added" and not migration["new_value"]:
            raise ValueError(f"{migration['old_value']} must map to a new value")

    for axis, key in (("sector", "theme_sector"), ("event", "theme_event")):
        new_names = [item["name"] for item in revision[key]]
        if len(new_names) != len(set(new_names)):
            raise ValueError(f"{key} has duplicate names")
        if special & set(new_names):
            raise ValueError(f"{key} must not contain special values")
        mapped = {
            migration["old_value"]: migration["new_value"]
            for migration in migrations if migration["axis"] == axis
        }
        for item in theme_dict[key]:
            name = item["name"]
            if name in new_names:
                continue
            if name not in mapped:
                raise ValueError(f"{key} dropped '{name}' without a migration")
            if mapped[name] not in new_names:
                raise ValueError(f"{key} maps '{name}' to a value not in the new dictionary")
        for migration in migrations:
            if migration["axis"] == axis and migration["new_value"] not in new_names:
                raise ValueError(
                    f"{key} migration target '{migration['new_value']}' is not in the new dictionary"
                )


def apply_revision(theme_dict: dict, revision: dict) -> dict:
    """special 과 excluded_axes 는 개정 대상이 아니다. 그대로 물려준다."""
    return {
        "version": revision["version"],
        "note": theme_dict["note"],
        "special": theme_dict["special"],
        "theme_sector": revision["theme_sector"],
        "theme_event": revision["theme_event"],
        "excluded_axes": theme_dict["excluded_axes"],
    }


def write_migrations(
    db_path: Path, from_version: str, to_version: str, migrations: list[dict]
) -> int:
    created = dt.datetime.now(SEOUL).isoformat(timespec="seconds")
    rows = [
        (from_version, to_version, migration["axis"], migration["old_value"],
         migration["new_value"], migration["action"], created)
        for migration in migrations
    ]
    con = sqlite3.connect(db_path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS theme_dict_migrations (
                from_version TEXT NOT NULL,
                to_version TEXT NOT NULL,
                axis TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (from_version, to_version, axis, old_value)
            )
        """)
        con.executemany("""
            INSERT INTO theme_dict_migrations (
                from_version,to_version,axis,old_value,new_value,action,created_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(from_version,to_version,axis,old_value) DO UPDATE SET
                new_value=excluded.new_value,
                action=excluded.action,
                created_at=excluded.created_at
        """, rows)
        con.commit()
        return len(rows)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _next_version(current: str) -> str:
    """같은 날 두 번 개정해도 현행 버전과 겹치지 않게 한다."""
    today = dt.datetime.now(SEOUL).strftime("%Y-%m-%d")
    if not current.startswith(today):
        return today
    suffix = current[len(today):]
    return f"{today}.{int(suffix.lstrip('.') or 1) + 1}"


def revise(
    db_path: Path,
    dict_path: Path,
    *,
    min_count: int = DEFAULT_MIN_COUNT,
    min_tickers: int = DEFAULT_MIN_TICKERS,
    unused_days: int = DEFAULT_UNUSED_DAYS,
    min_usage_samples: int = DEFAULT_MIN_USAGE_SAMPLES,
    model: str | None = None,
    revise_fn=None,
    next_version: str | None = None,
) -> dict:
    theme_dict = load_theme_dictionary(dict_path)
    promoted, rejected = collect_candidates(db_path, min_count, min_tickers)
    usage = collect_usage(db_path, unused_days)
    samples = sum(sum(counts.values()) for counts in usage.values())

    if not promoted:
        if samples < min_usage_samples:
            # 표본이 적으면 "안 쓰였다"와 "아직 안 나왔다"를 구분할 수 없다.
            return {
                "changed": False,
                "reason": f"not enough usage samples ({samples} < {min_usage_samples})",
                "promoted": promoted, "rejected": rejected, "usage": usage,
            }
        if all(
            usage.get(axis, {}).get(item["name"], 0) > 0
            for axis, key in (("sector", "theme_sector"), ("event", "theme_event"))
            for item in theme_dict[key]
        ):
            return {
                "changed": False, "reason": "no promotable candidate and no unused theme",
                "promoted": promoted, "rejected": rejected, "usage": usage,
            }

    version = next_version or _next_version(theme_dict["version"])
    revision_input = build_revision_input(theme_dict, promoted, usage, unused_days)
    prompt = make_revision_prompt(revision_input, version)

    if revise_fn is not None:
        revision = json.loads(revise_fn(prompt))
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            schema_path = Path(tmpdir) / "theme_revision_schema.json"
            schema_path.write_text(
                REVISION_SCHEMA_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
            revision = json.loads(generate_json(
                prompt, output_schema_path=schema_path, search=False, model=model
            ))
    validate_revision(revision, theme_dict)
    return {
        "changed": True,
        "from_version": theme_dict["version"],
        "to_version": revision["version"],
        "dictionary": apply_revision(theme_dict, revision),
        "migrations": revision["migrations"],
        "promoted": promoted,
        "rejected": rejected,
        "usage": usage,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="테마 사전 개정")
    parser.add_argument("--db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--dict", dest="dict_path", type=Path, default=THEME_DICT_PATH)
    parser.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT)
    parser.add_argument("--min-tickers", type=int, default=DEFAULT_MIN_TICKERS)
    parser.add_argument("--unused-days", type=int, default=DEFAULT_UNUSED_DAYS)
    parser.add_argument("--min-usage-samples", type=int, default=DEFAULT_MIN_USAGE_SAMPLES)
    parser.add_argument("--model", default=None, help="개정 판단에 쓸 모델. 미지정이면 codex 기본값")
    parser.add_argument("--write", action="store_true", help="사전 파일과 매핑을 실제로 갱신")
    args = parser.parse_args(argv)

    result = revise(
        args.db, args.dict_path,
        min_count=args.min_count, min_tickers=args.min_tickers,
        unused_days=args.unused_days, min_usage_samples=args.min_usage_samples,
        model=args.model,
    )
    if not result["changed"]:
        print(f"[theme-revision] no change - {result['reason']}")
        print(f"[theme-revision] rejected candidates: {len(result['rejected'])}")
        return 0

    print(f"[theme-revision] {result['from_version']} -> {result['to_version']}")
    print(f"[theme-revision] promoted={len(result['promoted'])} "
          f"rejected={len(result['rejected'])} migrations={len(result['migrations'])}")
    for migration in result["migrations"]:
        if migration["action"] != "kept":
            print(f"  {migration['axis']:6} {migration['action']:8} "
                  f"{migration['old_value']} -> {migration['new_value']}")
    if not args.write:
        print("[theme-revision] dry-run - rerun with --write to apply")
        return 0

    args.dict_path.write_text(
        json.dumps(result["dictionary"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written = write_migrations(
        args.db, result["from_version"], result["to_version"], result["migrations"]
    )
    print(f"[theme-revision] applied - dictionary updated, {written} migrations saved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
