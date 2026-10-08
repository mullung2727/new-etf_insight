"""호가 리서치 일 파일 아카이브 — C: sqlite → D: parquet 변환·검증 후 C: 삭제.

장중 C:(SSD)에 쌓인 일 단위 원본(`orderbook_raw_YYYYMMDD.sqlite3`)을
테이블별(code·rt 정렬) parquet zstd 로 D: 외장하드(`D:/orderbook/YYYY/MM/`)에 쓰고,
sqlite 와 parquet 을 다시 읽어 비교한 뒤 전부 같을 때만 C: 원본을 지운다.
검증 통과 시에만 C: 삭제 — D: 미연결·공간 부족·불일치면 C: 에 두고 batch 채널로 알림.

설계: docs/PLAN_ORDERBOOK_RESEARCH_RECORDING.md §5 "마감 후 변환·이동·삭제".

Usage (from etl/):
    uv run python scripts/orderbook_archive.py [--src DIR] [--dst DIR] [--dry-run]
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  (cp949 가드 + path)

import argparse
import os
import re
import shutil
import sqlite3
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
from dotenv import load_dotenv

from notify import notify

REPO_ROOT = Path(__file__).resolve().parents[2]  # new-etf_insight/
ETL_ROOT = Path(__file__).resolve().parents[1]  # etl/
DEFAULT_SRC = ETL_ROOT / "db" / "orderbook_research"
DEFAULT_DST = Path("D:/orderbook")

KST = ZoneInfo("Asia/Seoul")
RECORDING_CUTOFF = time(15, 40)  # 오늘 파일은 이 시각 전이면 녹화 중이라 건너뜀
SPACE_FACTOR = 3  # D: 여유 공간이 sqlite 크기의 3배 미만이면 보류
BACKLOG_WARN = 3  # C: 잔류 파일이 이 개수 이상이면 경고
READ_CHUNK = 200_000  # sqlite fetchmany 행 수

_FILE_RE = re.compile(r"^orderbook_raw_(\d{8})\.sqlite3$")
TABLES = ("trade", "book", "gaps")


def _file_date(name: str) -> str | None:
    """`orderbook_raw_YYYYMMDD.sqlite3` → "YYYYMMDD". 아니면(None) 무시."""
    m = _FILE_RE.match(name)
    if not m:
        return None
    try:
        datetime.strptime(m.group(1), "%Y%m%d")
    except ValueError:
        return None
    return m.group(1)


def _iter_day_files(src: Path) -> list[tuple[str, Path]]:
    """src 의 일 파일들을 (날짜, 경로) 날짜 순으로. 이름 규칙 밖은 제외."""
    try:
        names = os.listdir(src)
    except OSError:
        return []
    out = []
    for name in names:
        ymd = _file_date(name)
        if ymd is not None and (src / name).is_file():
            out.append((ymd, src / name))
    return sorted(out)


def _order_by(cols: list[str]) -> str:
    if "code" in cols:
        return ' ORDER BY "code", "rt"'
    if "rt" in cols:
        return ' ORDER BY "rt"'
    return ""


def _table_exists(sqlite_path: Path, table: str) -> bool:
    con = sqlite3.connect(sqlite_path)
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    finally:
        con.close()


def _sqlite_columns(sqlite_path: Path, table: str) -> list[str]:
    con = sqlite3.connect(sqlite_path)
    try:
        return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    finally:
        con.close()


def _sqlite_stats(sqlite_path: Path, table: str, cols: list[str]) -> dict:
    """검증용 집계: count, distinct code, rt min/max, trade 종목별 (code, sum, count)."""
    con = sqlite3.connect(sqlite_path)
    try:
        stats = {
            "count": con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
            "distinct_code": None,
            "min_rt": None,
            "max_rt": None,
            "per_code": None,
        }
        if "code" in cols:
            stats["distinct_code"] = con.execute(
                f'SELECT COUNT(DISTINCT "code") FROM "{table}"'
            ).fetchone()[0]
        if "rt" in cols:
            stats["min_rt"], stats["max_rt"] = con.execute(
                f'SELECT MIN("rt"), MAX("rt") FROM "{table}"'
            ).fetchone()
        if table == "trade" and "code" in cols and "qty" in cols and stats["count"] > 0:
            stats["per_code"] = [
                tuple(r)
                for r in con.execute(
                    f'SELECT "code", SUM("qty"), COUNT(*) FROM "{table}"'
                    ' GROUP BY "code" ORDER BY "code"'
                ).fetchall()
            ]
        return stats
    finally:
        con.close()


def _parquet_stats(parquet_path: Path, table: str, cols: list[str]) -> dict:
    """parquet 을 다시 읽어 sqlite 와 같은 집계. 컬럼 목록도 함께."""
    target = parquet_path.as_posix().replace("'", "''")
    src = f"read_parquet('{target}')"
    con = duckdb.connect()
    try:
        columns = [d[0] for d in con.execute(f"SELECT * FROM {src} LIMIT 0").description]
        stats = {
            "columns": columns,
            "count": con.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0],
            "distinct_code": None,
            "min_rt": None,
            "max_rt": None,
            "per_code": None,
        }
        if "code" in cols:
            stats["distinct_code"] = con.execute(
                f'SELECT COUNT(DISTINCT "code") FROM {src}'
            ).fetchone()[0]
        if "rt" in cols:
            stats["min_rt"], stats["max_rt"] = con.execute(
                f'SELECT MIN("rt"), MAX("rt") FROM {src}'
            ).fetchone()
        if table == "trade" and "code" in cols and "qty" in cols and stats["count"] > 0:
            stats["per_code"] = [
                tuple(r)
                for r in con.execute(
                    f'SELECT "code", SUM("qty"), COUNT(*) FROM {src}'
                    ' GROUP BY "code" ORDER BY "code"'
                ).fetchall()
            ]
        return stats
    finally:
        con.close()


def _same(a, b) -> bool:
    """집계값 비교. 정수↔정수형 실수(pandas 경유)는 같은 값으로 본다."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and a.is_integer():
        a = int(a)
    if isinstance(b, float) and b.is_integer():
        b = int(b)
    return bool(a == b)


def verify_table(sqlite_path: Path, table: str, parquet_path: Path) -> tuple[bool, str]:
    """sqlite 테이블과 parquet 파일의 집계가 같은지. parquet 은 다시 읽어서 비교."""
    cols = _sqlite_columns(sqlite_path, table)
    want = _sqlite_stats(sqlite_path, table, cols)
    try:
        got = _parquet_stats(parquet_path, table, cols)
    except Exception as exc:
        return False, f"parquet read failed: {exc}"
    if set(got["columns"]) != set(cols):
        return False, f"columns differ: sqlite={cols} parquet={got['columns']}"
    if got["count"] != want["count"]:
        return False, f"count {want['count']} != {got['count']}"
    if want["distinct_code"] is not None and not _same(got["distinct_code"], want["distinct_code"]):
        return False, f"distinct code {want['distinct_code']} != {got['distinct_code']}"
    if want["min_rt"] is not None and (
        not _same(got["min_rt"], want["min_rt"]) or not _same(got["max_rt"], want["max_rt"])
    ):
        return False, f"rt range ({want['min_rt']},{want['max_rt']}) != ({got['min_rt']},{got['max_rt']})"
    wp, gp = want["per_code"], got["per_code"]
    if (wp is None) != (gp is None):
        return False, "per-code stats missing on one side"
    if wp is not None:
        if len(wp) != len(gp):
            return False, f"per-code rows {len(wp)} != {len(gp)}"
        for (wc, ws, wn), (gc, gs, gn) in zip(wp, gp):
            if wc != gc or wn != gn or not _same(ws, gs):
                return False, f"per-code differ at {wc!r}: sqlite=({ws},{wn}) parquet=({gs},{gn})"
    return True, "ok"


def _write_parquet(sqlite_path: Path, table: str, cols: list[str], tmp_path: Path) -> None:
    """sqlite 테이블 → 정렬된 parquet zstd. fetchmany 청크로 읽어 duckdb 에 적재."""
    duck_tmpdir = tmp_path.parent / ".duckdb_tmp"
    duck_tmpdir.mkdir(parents=True, exist_ok=True)
    try:
        scon = sqlite3.connect(sqlite_path)
        try:
            cur = scon.execute(f'SELECT * FROM "{table}"')
            con = duckdb.connect()
            try:
                con.execute("SET memory_limit='2GB'")
                tmpdir_sql = duck_tmpdir.as_posix().replace("'", "''")
                con.execute(f"SET temp_directory='{tmpdir_sql}'")
                decls = {r[1]: (r[2] or "") for r in scon.execute(f'PRAGMA table_info("{table}")')}
                col_types = []
                for c in cols:
                    d = decls.get(c, "").upper()
                    if "INT" in d:
                        t = "BIGINT"
                    elif "CHAR" in d or "TEXT" in d or "CLOB" in d:
                        t = "VARCHAR"
                    elif "REAL" in d or "FLOA" in d or "DOUB" in d:
                        t = "DOUBLE"
                    else:
                        t = "VARCHAR"
                    col_types.append((c, t))
                defs = ", ".join(f'"{c}" {t}' for c, t in col_types)
                con.execute(f"CREATE TABLE src_rows ({defs})")
                bigint_cols = [c for c, t in col_types if t == "BIGINT"]
                double_cols = [c for c, t in col_types if t == "DOUBLE"]
                while True:
                    rows = cur.fetchmany(READ_CHUNK)
                    if not rows:
                        break
                    df = pd.DataFrame(rows, columns=cols, dtype=object)
                    for c in bigint_cols:
                        df[c] = df[c].astype("Int64")
                    for c in double_cols:
                        df[c] = df[c].astype("float64")
                    con.register("chunk_df", df)
                    try:
                        con.execute("INSERT INTO src_rows SELECT * FROM chunk_df")
                    finally:
                        con.unregister("chunk_df")
                target = tmp_path.as_posix().replace("'", "''")
                con.execute(
                    f"COPY (SELECT * FROM src_rows{_order_by(cols)}) "
                    f"TO '{target}' (FORMAT parquet, COMPRESSION zstd)"
                )
            finally:
                con.close()
        finally:
            scon.close()
    finally:
        shutil.rmtree(duck_tmpdir, ignore_errors=True)


def _silent_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def archive_day(sqlite_path: Path, dst_root: Path, now: datetime) -> dict:
    """C: 일 파일 하나를 D: parquet 으로 변환·검증 후 통과 시에만 삭제.

    반환: {"status", "date", "rows", "reason"}.
    status: archived | skipped_recording | dst_missing | dst_full | failed.
    """
    sqlite_path = Path(sqlite_path)
    dst_root = Path(dst_root)
    ymd = _file_date(sqlite_path.name)
    if ymd is None:
        return {"status": "failed", "date": "?", "rows": {}, "reason": f"bad name: {sqlite_path.name}"}
    ts = now.astimezone(KST) if now.tzinfo is not None else now
    if ymd == ts.strftime("%Y%m%d") and ts.time() < RECORDING_CUTOFF:
        return {
            "status": "skipped_recording",
            "date": ymd,
            "rows": {},
            "reason": "recording until 15:40 KST",
        }
    if not dst_root.exists():
        # 부모까지 없으면 외장하드 미연결 — 만들지 않고 C: 보존
        if not dst_root.parent.exists():
            return {
                "status": "dst_missing",
                "date": ymd,
                "rows": {},
                "reason": f"dst parent missing: {dst_root.parent} (D: not mounted?)",
            }
        try:
            dst_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return {"status": "dst_missing", "date": ymd, "rows": {}, "reason": f"dst mkdir failed: {exc}"}
    try:
        free = shutil.disk_usage(str(dst_root)).free
    except OSError as exc:
        return {"status": "dst_missing", "date": ymd, "rows": {}, "reason": f"dst stat failed: {exc}"}
    size = sqlite_path.stat().st_size
    if free < size * SPACE_FACTOR:
        return {
            "status": "dst_full",
            "date": ymd,
            "rows": {},
            "reason": f"dst free {free} < size*3 {size * SPACE_FACTOR}",
        }
    present = [t for t in TABLES if _table_exists(sqlite_path, t)]
    if not present:
        return {"status": "failed", "date": ymd, "rows": {}, "reason": "no trade/book/gaps tables"}
    rows: dict[str, int] = {}
    failures: list[str] = []
    for table in present:
        cols = _sqlite_columns(sqlite_path, table)
        rows[table] = _sqlite_stats(sqlite_path, table, cols)["count"]
        final = dst_root / ymd[:4] / ymd[4:6] / f"{table}_{ymd}.parquet"
        if final.exists():
            # 이미 있으면 덮어쓰지 않고 검증만
            ok, detail = verify_table(sqlite_path, table, final)
            if not ok:
                failures.append(f"{table}: existing parquet mismatch ({detail})")
            continue
        tmp = Path(str(final) + ".tmp")
        try:
            tmp.unlink(missing_ok=True)  # 이전 실행 잔재 정리
            final.parent.mkdir(parents=True, exist_ok=True)
            _write_parquet(sqlite_path, table, cols, tmp)
        except Exception as exc:
            _silent_unlink(tmp)
            failures.append(f"{table}: write failed ({exc})")
            continue
        ok, detail = verify_table(sqlite_path, table, tmp)
        if ok:
            os.replace(tmp, final)
        else:
            _silent_unlink(tmp)
            failures.append(f"{table}: verify failed ({detail})")
    if failures:
        return {"status": "failed", "date": ymd, "rows": rows, "reason": "; ".join(failures)}
    try:
        sqlite_path.unlink()
    except OSError as exc:
        return {"status": "failed", "date": ymd, "rows": rows, "reason": f"sqlite delete failed: {exc}"}
    return {"status": "archived", "date": ymd, "rows": rows, "reason": ""}


def _print_result(res: dict) -> None:
    status = res["status"]
    if status == "archived":
        cells = " ".join(f"{t}={res['rows'].get(t, 0)}" for t in TABLES if t in res["rows"])
        print(f"[orderbook_archive] {res['date']} archived {cells}".rstrip())
    elif status == "skipped_recording":
        print(f"[orderbook_archive] {res['date']} skipped_recording ({res['reason']})")
    else:
        print(f"[orderbook_archive] {res['date']} {status}: {res['reason']}")


def run(src: Path, dst: Path, now: datetime, notify_fn) -> int:
    """src 일 파일들을 날짜 순으로 아카이브. 0=정상, 1=실패·경고(알림 1회)."""
    src, dst = Path(src), Path(dst)
    files = _iter_day_files(src) if src.is_dir() else []
    if not files:
        print("[orderbook_archive] nothing to process")
        return 0
    problems: list[str] = []
    for ymd, path in files:
        res = archive_day(path, dst, now)
        _print_result(res)
        if res["status"] not in ("archived", "skipped_recording"):
            problems.append(f"{ymd} {res['status']}: {res['reason']}")
    remain = [ymd for ymd, _ in _iter_day_files(src)]
    if len(remain) >= BACKLOG_WARN:
        problems.append(f"warning: {len(remain)} files remain: {', '.join(remain)}")
    if problems:
        notify_fn("[orderbook_archive] " + " / ".join(problems), channel="batch")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="C: orderbook 일 sqlite → D: parquet 아카이브")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--dst", default=str(DEFAULT_DST))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    src, dst = Path(args.src), Path(args.dst)
    now = datetime.now(KST)
    if args.dry_run:
        files = _iter_day_files(src) if src.is_dir() else []
        if not files:
            print("[orderbook_archive] dry-run: nothing to process")
            return 0
        for ymd, path in files:
            print(f"[orderbook_archive] dry-run: {path} date={ymd}")
            for table in [t for t in TABLES if _table_exists(path, t)]:
                print(f"[orderbook_archive] dry-run:   -> {dst / ymd[:4] / ymd[4:6] / f'{table}_{ymd}.parquet'}")
        return 0
    load_dotenv(REPO_ROOT / ".env")
    return run(src, dst, now, notify)


if __name__ == "__main__":
    raise SystemExit(main())
