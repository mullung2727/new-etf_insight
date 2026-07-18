"""프로젝트 DB 스키마 카탈로그 생성기.

`etl/db/`의 모든 *.sqlite3 / *.duckdb 를 스캔해 테이블 스키마를 하나의 마크다운
(`etl/docs/DB_SCHEMA.md`)으로 덤프한다. 스키마의 진짜 소스는 각 스크립트의
`CREATE TABLE` — 이 문서는 그 결과를 한눈에 보기 위한 스냅샷일 뿐이다.
스키마를 바꾼 뒤 이 스크립트를 재실행해 문서를 갱신한다(손으로 고치지 말 것).

Usage (from etl/):
    uv run python scripts/dump_db_schema.py            # docs/DB_SCHEMA.md 갱신
    uv run python scripts/dump_db_schema.py --stdout   # 파일 안 쓰고 출력만
    uv run python scripts/dump_db_schema.py --self-check
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_DIR = Path(__file__).resolve().parents[1] / "db"
OUT_PATH = Path(__file__).resolve().parents[1] / "docs" / "DB_SCHEMA.md"


def dump_sqlite(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """(테이블명, CREATE SQL) 목록. 인덱스·뷰·내부테이블 제외."""
    rows = con.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "AND name NOT IN ('__write_probe__', '_openclaw_lock_probe') "  # openclaw 인프라 프로브, 앱 스키마 아님
        "ORDER BY name"
    ).fetchall()
    return [(name, (sql or "").strip()) for name, sql in rows]


def dump_duckdb(path: Path) -> list[tuple[str, str]]:
    """(테이블명, 컬럼 나열) 목록. DuckDB는 원본 DDL 대신 컬럼·타입으로 재구성."""
    import duckdb

    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()]
        out: list[tuple[str, str]] = []
        for t in tables:
            cols = con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name=? ORDER BY ordinal_position", (t,)
            ).fetchall()
            body = "\n".join(f"    {c} {d}" for c, d in cols)
            out.append((t, f"{t} (\n{body}\n)"))
        return out
    finally:
        con.close()


def render(catalog: list[tuple[str, list[tuple[str, str]]]]) -> str:
    """{db파일: [(테이블, 스키마텍스트)]} → 마크다운. 타임스탬프 없음(git이 이력 담당)."""
    lines = [
        "# DB 스키마 카탈로그",
        "",
        "> 자동 생성: `uv run python scripts/dump_db_schema.py`. **직접 수정 금지.**",
        "> 스키마 변경(테이블/컬럼 추가·변경) 후 재실행해 갱신할 것.",
        "> 실제 DDL 소스는 각 스크립트의 `CREATE TABLE` 문.",
        "",
    ]
    for db_name, tables in catalog:
        lines.append(f"## `{db_name}`")
        lines.append("")
        if not tables:
            lines.append("_(테이블 없음)_")
            lines.append("")
            continue
        lines.append(f"테이블 {len(tables)}개: " + ", ".join(f"`{t}`" for t, _ in tables))
        lines.append("")
        for _, body in tables:
            lines.append("```sql")
            lines.append(body)
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_catalog(db_dir: Path = DB_DIR) -> list[tuple[str, list[tuple[str, str]]]]:
    catalog: list[tuple[str, list[tuple[str, str]]]] = []
    for path in sorted(db_dir.glob("*.sqlite3")):
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            catalog.append((path.name, dump_sqlite(con)))
        finally:
            con.close()
    for path in sorted(db_dir.glob("*.duckdb")):
        catalog.append((path.name, dump_duckdb(path)))
    return catalog


def _self_check() -> None:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE foo (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    con.execute("CREATE TABLE bar (k TEXT, UNIQUE(k))")
    tables = dump_sqlite(con)
    assert [t[0] for t in tables] == ["bar", "foo"], tables  # 이름순
    assert "CREATE TABLE foo" in tables[1][1] and "name TEXT NOT NULL" in tables[1][1]
    md = render([("mem.sqlite3", tables)])
    assert "## `mem.sqlite3`" in md
    assert "테이블 2개: `bar`, `foo`" in md
    assert "```sql" in md and "CREATE TABLE foo" in md
    # 빈 DB
    assert "_(테이블 없음)_" in render([("empty.sqlite3", [])])
    print("self-check PASS")


def main() -> int:
    p = argparse.ArgumentParser(description="DB 스키마 카탈로그 생성 (etl/db/*.sqlite3,*.duckdb)")
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--stdout", action="store_true", help="파일 대신 표준출력")
    p.add_argument("--db-dir", type=Path, default=DB_DIR)
    p.add_argument("--out", type=Path, default=OUT_PATH)
    args = p.parse_args()

    if args.self_check:
        _self_check()
        return 0

    md = render(build_catalog(args.db_dir))
    if args.stdout:
        sys.stdout.write(md)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    print(f"[schema] {args.out} 갱신 ({md.count('```sql')} 테이블)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
