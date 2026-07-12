"""YouTube 단일 영상 요약 백그라운드 잡 (로컬 단일유저).

파일 저장: etl/db/youtube_summarize_jobs.json
동시 실행 1개만 (이미 running 있으면 거절).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from duck_watchlist import PROJECT_DIR
from youtube_ops import run_summarize

_JOBS_PATH = PROJECT_DIR / "etl" / "db" / "youtube_summarize_jobs.json"
_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    try:
        raw = json.loads(_JOBS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"jobs": {}, "active_id": None}
    except (OSError, ValueError):
        return {"jobs": {}, "active_id": None}


def _save(data: dict[str, Any]) -> None:
    _JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _JOBS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(_JOBS_PATH)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        data = _load()
        j = (data.get("jobs") or {}).get(job_id)
        return dict(j) if isinstance(j, dict) else None


def list_recent_jobs(limit: int = 10) -> list[dict[str, Any]]:
    with _LOCK:
        data = _load()
        jobs = list((data.get("jobs") or {}).values())
    jobs.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jobs[:limit]


def _set_job(job_id: str, **fields: Any) -> None:
    with _LOCK:
        data = _load()
        jobs = data.setdefault("jobs", {})
        cur = jobs.get(job_id) or {}
        cur.update(fields)
        jobs[job_id] = cur
        if fields.get("status") in ("done", "error"):
            if data.get("active_id") == job_id:
                data["active_id"] = None
        _save(data)


def _run(job_id: str, channel_id: str, video_id: str, force: bool) -> None:
    _set_job(job_id, status="running", started_at=_now(), message="요약 실행 중")
    try:
        result = run_summarize(
            video_ids=[video_id],
            force=force,
        )
        ok = int(result.get("ok") or 0)
        skipped = int(result.get("skipped") or 0)
        errors = result.get("errors") or []
        if ok > 0:
            status = "done"
            message = f"요약 저장 완료 (ok={ok})"
        elif errors:
            status = "error"
            message = str(errors[0].get("error") if isinstance(errors[0], dict) else errors[0])
        elif skipped > 0:
            status = "done"
            message = f"스킵 (skipped={skipped}) — 이미 요약됐거나 대본 없음"
        else:
            status = "error"
            message = "대상 없음 또는 실패"
        _set_job(
            job_id,
            status=status,
            finished_at=_now(),
            message=message,
            result=result,
        )
    except Exception as exc:  # noqa: BLE001
        _set_job(
            job_id,
            status="error",
            finished_at=_now(),
            message=str(exc),
            result=None,
        )


def start_summarize_job(
    *,
    channel_id: str,
    video_id: str,
    force: bool = False,
    title: str | None = None,
) -> dict[str, Any]:
    """단일 영상 요약 잡 시작. 이미 running 있으면 ValueError."""
    global _THREAD
    if not channel_id.strip() or not video_id.strip():
        raise ValueError("channel_id and video_id required")

    with _LOCK:
        data = _load()
        active = data.get("active_id")
        if active:
            aj = (data.get("jobs") or {}).get(active) or {}
            if aj.get("status") in ("queued", "running"):
                raise ValueError(
                    f"이미 요약 진행 중: {active} ({aj.get('video_id')}) status={aj.get('status')}"
                )
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "status": "queued",
            "channel_id": channel_id,
            "video_id": video_id,
            "title": title or video_id,
            "force": force,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "message": "대기열",
            "result": None,
        }
        data.setdefault("jobs", {})[job_id] = job
        data["active_id"] = job_id
        # prune old jobs keep 30
        jobs = data["jobs"]
        if len(jobs) > 30:
            ordered = sorted(jobs.values(), key=lambda x: x.get("created_at") or "")
            for old in ordered[: len(jobs) - 30]:
                jid = old.get("job_id")
                if jid and jid != job_id and jid != active:
                    jobs.pop(jid, None)
        _save(data)

    t = threading.Thread(
        target=_run,
        args=(job_id, channel_id, video_id, force),
        name=f"yt-sum-{job_id}",
        daemon=True,
    )
    _THREAD = t
    t.start()
    return dict(job)
