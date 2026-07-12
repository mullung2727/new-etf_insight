"""YouTube 수집·요약 실행 헬퍼.

- pending: api 프로세스에서 sqlite 직접 조회
- collect/summarize: etl `uv run` 서브프로세스 (api venv에 transcript/llm 의존 없음)
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import duck_watchlist
from duck_watchlist import PROJECT_DIR

_ETL = PROJECT_DIR / "etl"
_CHANNELS_JSON = _ETL / "scripts" / "youtube_channels.json"
_OPS_SCRIPT = _ETL / "scripts" / "youtube_ops_cli.py"


def _youtube_db_path() -> Path:
    """런타임 경로 (테스트 monkeypatch 반영)."""
    return Path(duck_watchlist.YOUTUBE_DB_PATH)


def _load_channel_ids(only: list[str] | None) -> list[str]:
    if only:
        return list(only)
    try:
        raw = json.loads(_CHANNELS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(raw.keys()) if isinstance(raw, dict) else []


def youtube_write_con() -> sqlite3.Connection:
    path = _youtube_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _etl_cli(payload: dict[str, Any], *, timeout: int = 3600) -> dict[str, Any]:
    """etl/scripts/youtube_ops_cli.py 를 uv run 으로 실행. stdout JSON."""
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("uv 실행 파일을 찾을 수 없습니다 (PATH)")
    if not _OPS_SCRIPT.exists():
        raise RuntimeError(f"ops script missing: {_OPS_SCRIPT}")

    proc = subprocess.run(
        [uv, "run", "python", str(_OPS_SCRIPT)],
        cwd=str(_ETL),
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        # try parse json error
        try:
            data = json.loads(out.splitlines()[-1] if out else "{}")
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(str(data["error"]))
        except (json.JSONDecodeError, IndexError):
            pass
        raise RuntimeError(err or out or f"etl exit {proc.returncode}")
    # last JSON line
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"etl no json output: {out[:500]}")


def run_collect(
    *,
    from_date: str,
    to_date: str,
    channel_ids: list[str] | None = None,
) -> dict[str, Any]:
    targets = _load_channel_ids(channel_ids)
    return _etl_cli(
        {
            "op": "collect",
            "from_date": from_date,
            "to_date": to_date,
            "channel_ids": targets,
            "db": str(_youtube_db_path()),
        },
        timeout=1800,
    )


def run_list_catalog(
    *,
    channel_ids: list[str] | None = None,
    with_duration: bool = True,
) -> dict[str, Any]:
    """RSS 목록 + 재생시간. 대본/STT/LLM 없음. 채널 필수(전체 자동 금지)."""
    targets = list(channel_ids or [])
    if not targets:
        raise ValueError("channel_ids required (select channels first)")
    return _etl_cli(
        {
            "op": "list_catalog",
            "channel_ids": targets,
            "with_duration": with_duration,
        },
        timeout=300,
    )


def run_collect_selected(
    *,
    videos: list[dict[str, Any]],
) -> dict[str, Any]:
    """선택 영상만 자막 수집. STT 없음. videos: channel_id+video_id 필수."""
    if not videos:
        raise ValueError("videos required")
    return _etl_cli(
        {
            "op": "collect_selected",
            "videos": videos,
            "db": str(_youtube_db_path()),
        },
        timeout=1800,
    )


def run_collect_url(*, url: str) -> dict[str, Any]:
    """영상 URL 1건 메타+대본 수집. STT 없음."""
    u = (url or "").strip()
    if not u:
        raise ValueError("영상 URL을 입력하세요.")
    return _etl_cli(
        {
            "op": "collect_url",
            "url": u,
            "db": str(_youtube_db_path()),
        },
        timeout=300,
    )


def list_pending(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    channel_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """미요약 목록. etl 패키지 import 없이 sqlite만."""
    con = youtube_write_con()
    try:
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "youtube_videos" not in tables:
            return []
        if "youtube_video_summaries" not in tables:
            join = ""
            where_extra = ""
        else:
            join = (
                "LEFT JOIN youtube_video_summaries s "
                "ON s.channel_id=v.channel_id AND s.video_id=v.video_id"
            )
            where_extra = "AND s.video_id IS NULL"

        # 대기 = 요약 없는 수집 건 전부 (자막 없어도 노출 — 수집 후 조회에 안 보이던 문제 수정)
        clauses: list[str] = []
        args: list[Any] = []
        if from_date:
            clauses.append("v.date_kst>=?")
            args.append(from_date)
        if to_date:
            clauses.append("v.date_kst<=?")
            args.append(to_date)
        if channel_ids:
            placeholders = ",".join("?" * len(channel_ids))
            clauses.append(f"v.channel_id IN ({placeholders})")
            args.extend(channel_ids)

        where = (" AND ".join(clauses)) if clauses else "1=1"
        sql = f"""
            SELECT v.channel_id, v.video_id, v.date_kst, v.title, v.url,
                   v.transcript, length(v.transcript)
            FROM youtube_videos v
            {join}
            WHERE {where} {where_extra}
            ORDER BY v.date_kst DESC, v.video_id
        """
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()

    out: list[dict[str, Any]] = []
    for r in rows:
        tr = r[5]
        has_tr = tr is not None and str(tr).strip() != ""
        out.append(
            {
                "channel_id": r[0],
                "video_id": r[1],
                "date_kst": r[2],
                "title": r[3],
                "url": r[4] or f"https://www.youtube.com/watch?v={r[1]}",
                "transcript_chars": r[6] if has_tr else 0,
                "has_transcript": has_tr,
                "status": "ready" if has_tr else "no_transcript",
            }
        )
    return out


def run_summarize(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    channel_ids: list[str] | None = None,
    video_ids: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    return _etl_cli(
        {
            "op": "summarize",
            "from_date": from_date,
            "to_date": to_date,
            "channel_ids": channel_ids,
            "video_ids": video_ids,
            "force": force,
            "db": str(_youtube_db_path()),
        },
        timeout=7200,
    )
