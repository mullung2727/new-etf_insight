import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401

"""월 1회 외장하드 DB 백업 (slot_A/slot_B 교대 2벌).

외장하드의 `new-etf_insight_backup` 폴더(사람이 최초 1회 생성) 아래
slot_A/slot_B에 교대로 덮어쓰고, 완전 성공한 슬롯을 latest.txt에 기록한다.
성공/실패/드라이브 없음 모두 batch 채널로 알림 1건.

Usage:
    cd etl && uv run python scripts/backup_db.py
"""
import fnmatch
import os
import shutil
import sqlite3
import time
from pathlib import Path

from notify import notify

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIRNAME = "new-etf_insight_backup"

_SQLITE_EXTS = {".sqlite3", ".sqlite", ".db"}
_DUCKDB_EXTS = {".duckdb"}
_EXCLUDE_NAMES = ("*.bak*", "_bak_del_*", "*-wal", "*-shm", "*.lock", "corpcode.zip")


def find_backup_root(
    letters: str = "DEFGHIJKLMNOPQRSTUVWXYZ",
    candidates: list[Path] | None = None,
) -> Path | None:
    """`X:\\new-etf_insight_backup` 폴더가 존재하는 첫 경로. 없으면 None."""
    if candidates is not None:
        for base in candidates:
            p = Path(base) / BACKUP_DIRNAME
            try:
                if p.is_dir():
                    return p
            except OSError:
                continue
        return None
    for ch in letters:
        p = Path(f"{ch}:\\{BACKUP_DIRNAME}")
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    return None


def pick_slot(backup_root: Path) -> str:
    """다음 백업 대상 슬롯. latest가 A면 B, B면 A, 없거나 그 외 값이면 A."""
    try:
        v = (Path(backup_root) / "latest.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "A"
    if v == "A":
        return "B"
    if v == "B":
        return "A"
    return "A"


def _excluded(rel: Path) -> bool:
    if "__pycache__" in rel.parts:
        return True
    return any(fnmatch.fnmatch(rel.name, pat) for pat in _EXCLUDE_NAMES)


def iter_sources(root: Path) -> list[Path]:
    """백업할 파일 절대경로 목록(정렬)."""
    root = Path(root)
    out: list[Path] = []

    def _add(p: Path) -> None:
        try:
            rel = p.relative_to(root)
        except ValueError:
            return
        if _excluded(rel):
            return
        out.append(p)

    for sub in (
        "etl/db",
        "research/private",
        "etl/exports",
        "etl/runs",
        ".claude/skills/rights-dip-funds",
        "research/rights_issue/cache",
        "research/rights_issue/out",
        "research/watchlist_pullback_strategy/minute_cache",
    ):
        base = root / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file():
                _add(p)
    broker = root / "broker"
    if broker.is_dir():
        for p in sorted(broker.iterdir()):
            if p.is_file() and (
                fnmatch.fnmatch(p.name, "*.db")
                or fnmatch.fnmatch(p.name, ".token_cache*.json")
            ):
                _add(p)
    for name in (
        "etl/scripts/close_bet.json",
        "broker-web/.env.local",
        "kiwoom-rest-api-spec.json",
    ):
        p = root / name
        if p.is_file():
            _add(p)
    for p in sorted(root.rglob(".env*")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if fnmatch.fnmatch(p.name, "*.example"):
            continue
        if "node_modules" in rel.parts or ".venv" in rel.parts or "venv" in rel.parts:
            continue
        if rel.parts[:2] == (".claude", "worktrees"):
            continue
        _add(p)
    return sorted(set(out))


def _kind(src: Path) -> str:
    ext = src.suffix.lower()
    if ext in _SQLITE_EXTS:
        return "sqlite"
    if ext in _DUCKDB_EXTS:
        return "duckdb"
    return "file"


def is_fresh(src: Path, dst: Path) -> bool:
    """백업본이 최신이면 True → 건너뜀. sqlite/duckdb는 mtime만, 일반 파일은 크기까지 비교."""
    try:
        dst_st = dst.stat()
    except OSError:
        return False
    try:
        src_st = src.stat()
    except OSError:
        return False
    src_mtime = src_st.st_mtime
    if _kind(src) == "sqlite":
        try:
            wal_mtime = (src.parent / (src.name + "-wal")).stat().st_mtime
        except OSError:
            pass
        else:
            src_mtime = max(src_mtime, wal_mtime)
    if dst_st.st_mtime < src_mtime:
        return False
    if _kind(src) == "file" and dst_st.st_size != src_st.st_size:
        return False
    return True


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_counts_sqlite(con) -> dict[str, int]:
    """sqlite_ 내부 테이블 제외, 테이블별 행 수."""
    rows = con.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND substr(name, 1, 7) != 'sqlite_'"
    ).fetchall()
    return {
        name: int(
            con.execute(f"SELECT count(*) FROM {_quote_ident(name)}").fetchone()[0]
        )
        for (name,) in rows
    }


def table_counts_duckdb(con) -> dict[str, int]:
    """BASE TABLE별 행 수. 키는 schema.table."""
    rows = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_type='BASE TABLE'"
    ).fetchall()
    return {
        f"{schema}.{table}": int(
            con.execute(
                f"SELECT count(*) FROM {_quote_ident(schema)}.{_quote_ident(table)}"
            ).fetchone()[0]
        )
        for schema, table in rows
    }


def _format_mismatch(src_counts: dict[str, int], dst_counts: dict[str, int]) -> str:
    diffs = []
    for name in sorted(set(src_counts) | set(dst_counts)):
        a, b = src_counts.get(name), dst_counts.get(name)
        if a != b:
            diffs.append(f"{name} {a}!={b}")
        if len(diffs) >= 3:
            break
    return ", ".join(diffs)


def _unlink_quiet(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def backup_sqlite(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    _unlink_quiet(tmp)
    src_con = None
    dst_con = None
    try:
        src_con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
        dst_con = sqlite3.connect(str(tmp))
        src_con.backup(dst_con)
        ok = dst_con.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        mismatch = None
        if ok:
            src_counts = table_counts_sqlite(src_con)
            dst_counts = table_counts_sqlite(dst_con)
            if src_counts != dst_counts:
                mismatch = _format_mismatch(src_counts, dst_counts)
        if not ok:
            raise RuntimeError("quick_check 실패")
        if mismatch is not None:
            raise RuntimeError(f"행 수 불일치: {mismatch}")
    except Exception:
        if dst_con is not None:
            dst_con.close()
            dst_con = None
        if src_con is not None:
            src_con.close()
            src_con = None
        _unlink_quiet(tmp)
        raise
    finally:
        if dst_con is not None:
            dst_con.close()
        if src_con is not None:
            src_con.close()
    os.replace(tmp, dst)


def backup_duckdb(src: Path, dst: Path) -> None:
    import duckdb

    tmp = dst.with_name(dst.name + ".tmp")
    if (src.parent / (src.name + ".wal")).exists():
        raise RuntimeError("wal 있음 — 체크포인트 안 됨")
    # ponytail: 잠금 확인과 복사 사이 쓰기 경쟁은 남음, 배치 없는 시각(05:30)에 돌려 회피.
    try:
        con = duckdb.connect(str(src), read_only=True)
    except duckdb.Error:
        raise RuntimeError("사용 중(잠김)")
    try:
        src_counts = table_counts_duckdb(con)
    finally:
        con.close()
    _unlink_quiet(tmp)
    try:
        shutil.copy2(src, tmp)
        try:
            vcon = duckdb.connect(str(tmp), read_only=True)
            try:
                dst_counts = table_counts_duckdb(vcon)
            finally:
                vcon.close()
        except Exception:
            raise RuntimeError("tmp 검증 실패")
        if src_counts != dst_counts:
            raise RuntimeError(
                f"행 수 불일치: {_format_mismatch(src_counts, dst_counts)}"
            )
    except Exception:
        _unlink_quiet(tmp)
        raise
    os.replace(tmp, dst)


def backup_file(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    try:
        shutil.copy2(src, tmp)
    except Exception:
        _unlink_quiet(tmp)
        raise
    os.replace(tmp, dst)


def run(project_root: Path, backup_root: Path) -> dict:
    """전체 백업 수행. 실패는 파일 단위로 기록하고 계속. 소스에서 지워진 파일은 백업에서 지우지 않음."""
    project_root = Path(project_root)
    backup_root = Path(backup_root)
    copied: list[str] = []
    skipped = 0
    failed: list[tuple[str, str]] = []
    total = 0
    for src in iter_sources(project_root):
        rel = src.relative_to(project_root).as_posix()
        dst = backup_root / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if is_fresh(src, dst):
                skipped += 1
                print(f"skip {rel}")
                continue
            kind = _kind(src)
            if kind == "sqlite":
                backup_sqlite(src, dst)
            elif kind == "duckdb":
                backup_duckdb(src, dst)
            else:
                backup_file(src, dst)
            copied.append(rel)
            try:
                total += dst.stat().st_size
            except OSError:
                pass
            print(f"copy {rel}")
        except Exception as exc:
            failed.append((rel, str(exc)))
            print(f"fail {rel}: {exc}")
    return {"copied": copied, "skipped": skipped, "failed": failed, "bytes": total}


def _send(message: str) -> None:
    try:
        notify(message, channel="batch")
    except Exception as exc:
        print(f"[backup] notify failed: {exc}")


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
        backup_root = find_backup_root()
        if backup_root is None:
            _send(
                "[DB 백업] 실패: 외장하드(new-etf_insight_backup 폴더) 없음. "
                "꽂고 수동 실행: cd etl && uv run python scripts/backup_db.py"
            )
            return 1
        slot = pick_slot(backup_root)
        start = time.time()
        result = run(PROJECT_ROOT, backup_root / f"slot_{slot}")
        elapsed = int(time.time() - start)
        gb = result["bytes"] / (1024**3)
        if not result["failed"]:
            latest = backup_root / "latest.txt"
            tmp = latest.with_name(latest.name + ".tmp")
            tmp.write_text(slot, encoding="utf-8")
            os.replace(tmp, latest)
            first = f"[DB 백업] 성공 slot_{slot}"
        else:
            try:
                cur = (backup_root / "latest.txt").read_text(encoding="utf-8").strip()
            except OSError:
                cur = ""
            kept = f"slot_{cur}" if cur in ("A", "B") else "없음"
            first = f"[DB 백업] 일부 실패 slot_{slot} (최신 유지: {kept})"
        lines = [
            first,
            f"복사 {len(result['copied'])}개({gb:.1f} GB), "
            f"건너뜀 {result['skipped']}개, 실패 {len(result['failed'])}개, "
            f"소요 {elapsed // 60:02d}:{elapsed % 60:02d}",
        ]
        for rel, msg in result["failed"][:20]:
            lines.append(f"{rel}: {msg}")
        _send("\n".join(lines))
        return 1 if result["failed"] else 0
    except Exception as exc:
        _send(f"[DB 백업] 오류: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
