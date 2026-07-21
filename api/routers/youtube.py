from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from duck_watchlist import PROJECT_DIR, youtube_cursor
from schemas import (
    YoutubeCatalogItem,
    YoutubeCatalogRequest,
    YoutubeCollectRequest,
    YoutubeCollectSelectedRequest,
    YoutubeCollectUrlRequest,
    YoutubeCollectUrlResult,
    YoutubeMention,
    YoutubePendingItem,
    YoutubeSummarizeJob,
    YoutubeSummarizeJobRequest,
    YoutubeSummarizeRequest,
    YoutubeSummaryStock,
    YoutubeVideoSummary,
)
from youtube_jobs import get_job, list_recent_jobs, start_summarize_job
from youtube_ops import (
    list_pending,
    run_collect,
    run_collect_selected,
    run_collect_url,
    run_list_catalog,
    run_summarize,
)

router = APIRouter(tags=["youtube"])

_CHANNELS_JSON = PROJECT_DIR / "etl" / "scripts" / "youtube_channels.json"


def _channel_labels() -> dict[str, str]:
    """channel_id → 표시 이름 (label > handle > channel_id)."""
    try:
        raw = json.loads(Path(_CHANNELS_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for cid, cfg in raw.items():
        if not isinstance(cfg, dict):
            out[cid] = cid
            continue
        label = (cfg.get("label") or cfg.get("handle") or cid).strip()
        out[cid] = label or cid
    return out


def _loads(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


@router.get(
    "/youtube/mentions/{ticker}",
    response_model=list[YoutubeMention],
    operation_id="get_youtube_mentions",
    summary="Per-stock youtube mention history",
    description=(
        "한 종목의 유튜브 종목시그널 이력을 최신 date_kst 순으로 반환. "
        "from/to(date_kst) 필터. session 없음(하루 1회)."
    ),
)
def get_youtube_mentions(
    ticker: str,
    from_: str | None = Query(None, alias="from", description="date_kst >="),
    to: str | None = Query(None, description="date_kst <="),
) -> list[YoutubeMention]:
    clauses = ["ticker=?"]
    args: list[Any] = [ticker]
    if from_:
        clauses.append("date_kst>=?")
        args.append(from_)
    if to:
        clauses.append("date_kst<=?")
        args.append(to)
    where = " AND ".join(clauses)

    with youtube_cursor() as con:
        # table missing on fresh DB → empty
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "youtube_stock_insights" not in tables:
            return []
        rows = con.execute(
            "SELECT date_kst, name, mention_channels, source_video_ids, "
            "discovery_reason, analysis "
            f"FROM youtube_stock_insights WHERE {where} "
            "ORDER BY date_kst DESC",
            args,
        ).fetchall()

    out: list[YoutubeMention] = []
    for date_kst, name, channels, video_ids, reason, analysis in rows:
        out.append(
            YoutubeMention(
                date_kst=date_kst,
                name=name,
                channels=_loads(channels, []),
                video_ids=_loads(video_ids, []),
                discovery_reason=reason or "",
                analysis=analysis,
            )
        )
    return out


def _stocks_by_video_id(
    con,
    *,
    date: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, list[YoutubeSummaryStock]]:
    """youtube_stock_insights → video_id 별 종목 목록.

    source_video_ids JSON 배열을 풀어 조인. 날짜 필터는 insights.date_kst 기준
    (요약 조회 기간과 대략 맞춤; 없으면 전체).
    """
    tables = {
        r[0]
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "youtube_stock_insights" not in tables:
        return {}
    clauses: list[str] = []
    args: list[Any] = []
    if date:
        clauses.append("date_kst=?")
        args.append(date)
    else:
        if from_date:
            clauses.append("date_kst>=?")
            args.append(from_date)
        if to_date:
            clauses.append("date_kst<=?")
            args.append(to_date)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = con.execute(
        f"""
        SELECT ticker, name, discovery_reason, source_video_ids
        FROM youtube_stock_insights
        {where}
        """,
        args,
    ).fetchall()
    out: dict[str, list[YoutubeSummaryStock]] = {}
    for ticker, name, reason, vids_json in rows:
        vids = _loads(vids_json, [])
        if not isinstance(vids, list):
            continue
        t = str(ticker or "").strip()
        n = str(name or "").strip()
        if not t or not n:
            continue
        # discovery_reason 은 (date,ticker) 단위 최신값이라 여러 영상이 묶이면
        # 다른 영상 사유가 붙을 수 있음 → 영상 1개일 때만 note 사용
        note = str(reason) if reason and len(vids) == 1 else None
        item_base = {"ticker": t, "name": n, "note": note}
        for vid in vids:
            if not vid:
                continue
            key = str(vid)
            bucket = out.setdefault(key, [])
            if any(s.ticker == t for s in bucket):
                continue
            bucket.append(YoutubeSummaryStock(**item_base))
    return out


def _stocks_from_summary_json(s: dict) -> list[YoutubeSummaryStock] | None:
    """summary_json.stocks 가 있으면 그 목록, 키 자체 없으면 None(조인 폴백)."""
    if "stocks" not in s:
        return None
    raw = s.get("stocks")
    if not isinstance(raw, list):
        return []
    out: list[YoutubeSummaryStock] = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        name = str(x.get("name") or "").strip()
        if not name:
            continue
        ticker_raw = x.get("ticker")
        ticker = str(ticker_raw).strip() if ticker_raw not in (None, "") else None
        note = x.get("note")
        out.append(
            YoutubeSummaryStock(
                ticker=ticker,
                name=name,
                note=str(note) if note else None,
            )
        )
    return out


@router.get(
    "/youtube/summaries",
    response_model=list[YoutubeVideoSummary],
    operation_id="list_youtube_summaries",
    summary="Video summaries (date range)",
    description=(
        "통합 요약 목록. from/to(date_kst) 범위 필터(포함). "
        "둘 다 없으면 전체. date(단일)는 하위호환. 최신 date_kst 먼저. "
        "stocks는 youtube_stock_insights를 video_id로 조인(discovery 추출분)."
    ),
)
def list_youtube_summaries(
    date: str | None = Query(None, description="optional single date_kst (legacy)"),
    from_: str | None = Query(None, alias="from", description="date_kst >="),
    to: str | None = Query(None, description="date_kst <="),
) -> list[YoutubeVideoSummary]:
    labels = _channel_labels()
    with youtube_cursor() as con:
        tables = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "youtube_video_summaries" not in tables:
            return []
        has_videos = "youtube_videos" in tables
        clauses: list[str] = []
        args: list[Any] = []
        col = "s.date_kst" if has_videos else "date_kst"
        if date:
            clauses.append(f"{col}=?")
            args.append(date)
        else:
            if from_:
                clauses.append(f"{col}>=?")
                args.append(from_)
            if to:
                clauses.append(f"{col}<=?")
                args.append(to)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        if has_videos:
            sql = f"""
                SELECT s.channel_id, s.video_id, s.date_kst, s.summary_json,
                       v.title, v.url, length(v.transcript)
                FROM youtube_video_summaries s
                LEFT JOIN youtube_videos v
                  ON v.channel_id=s.channel_id AND v.video_id=s.video_id
                {where}
                ORDER BY s.date_kst DESC, s.video_id
            """
            rows = con.execute(sql, args).fetchall()
        else:
            sql = f"""
                SELECT channel_id, video_id, date_kst, summary_json, NULL, NULL, NULL
                FROM youtube_video_summaries
                {where}
                ORDER BY date_kst DESC, video_id
            """
            rows = con.execute(sql, args).fetchall()

        by_video = _stocks_by_video_id(
            con, date=date, from_date=from_, to_date=to
        )

    out: list[YoutubeVideoSummary] = []
    for channel_id, video_id, date_kst, summary_json, title, url, chars in rows:
        s = _loads(summary_json, {})
        if not isinstance(s, dict):
            s = {}
        embedded = _stocks_from_summary_json(s)
        # 신규 요약은 summary_json.stocks 우선(미매칭 이름 포함). 구 데이터는 insights 조인.
        stocks = (
            embedded if embedded is not None else (by_video.get(video_id) or [])
        )
        out.append(
            YoutubeVideoSummary(
                channel_id=channel_id,
                channel_label=labels.get(channel_id) or channel_id,
                video_id=video_id,
                date_kst=date_kst,
                title=title,
                url=url or f"https://www.youtube.com/watch?v={video_id}",
                headline=s.get("headline"),
                issues=s.get("issues") or [],
                bullets=s.get("bullets") or [],
                risk_or_caveat=s.get("risk_or_caveat"),
                transcript_chars=chars,
                stocks=stocks,
            )
        )
    return out


@router.get(
    "/youtube/pending",
    response_model=list[YoutubePendingItem],
    operation_id="list_youtube_pending",
    summary="Videos collected but not yet summarized",
)
def get_youtube_pending(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    channel: list[str] | None = Query(None),
) -> list[YoutubePendingItem]:
    labels = _channel_labels()
    rows = list_pending(from_date=from_, to_date=to, channel_ids=channel or None)
    return [
        YoutubePendingItem(
            channel_id=r["channel_id"],
            channel_label=labels.get(r["channel_id"]) or r["channel_id"],
            video_id=r["video_id"],
            date_kst=r["date_kst"],
            title=r.get("title"),
            url=r["url"],
            transcript_chars=r.get("transcript_chars"),
            has_transcript=bool(r.get("has_transcript")),
            status=str(r.get("status") or "ready"),
        )
        for r in rows
    ]


@router.post(
    "/youtube/collect",
    operation_id="youtube_collect",
    summary="Manual collect (RSS + transcript, no LLM)",
)
def post_youtube_collect(body: YoutubeCollectRequest) -> dict:
    if not body.from_date or not body.to_date:
        raise HTTPException(400, detail="from_date and to_date required")
    if body.from_date > body.to_date:
        raise HTTPException(400, detail="from_date > to_date")
    return run_collect(
        from_date=body.from_date,
        to_date=body.to_date,
        channel_ids=body.channel_ids,
    )


@router.post(
    "/youtube/catalog",
    response_model=list[YoutubeCatalogItem],
    operation_id="youtube_catalog",
    summary="List recent RSS videos + duration (manual, no STT)",
    description=(
        "선택 채널의 RSS 최근 영상(~15)과 재생시간(lengthSeconds)만 반환. "
        "대본·STT·요약 없음. 짧은/긴 영상 판단용."
    ),
)
def post_youtube_catalog(body: YoutubeCatalogRequest) -> list[YoutubeCatalogItem]:
    if not body.channel_ids:
        raise HTTPException(400, detail="channel_ids required")
    labels = _channel_labels()
    try:
        data = run_list_catalog(
            channel_ids=body.channel_ids,
            with_duration=body.with_duration,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(502, detail=str(e)) from e
    out: list[YoutubeCatalogItem] = []
    for v in data.get("videos") or []:
        cid = v.get("channel_id") or ""
        vid = v.get("video_id") or ""
        if not vid:
            continue
        out.append(
            YoutubeCatalogItem(
                channel_id=cid,
                channel_label=labels.get(cid) or cid,
                video_id=vid,
                title=v.get("title") or "",
                published_at_utc=v.get("published_at_utc") or "",
                date_kst=v.get("date_kst") or "",
                url=v.get("url") or f"https://www.youtube.com/watch?v={vid}",
                duration_sec=v.get("duration_sec"),
            )
        )
    return out


@router.post(
    "/youtube/collect-selected",
    operation_id="youtube_collect_selected",
    summary="Manual collect selected videos (transcript API only, no STT)",
)
def post_youtube_collect_selected(body: YoutubeCollectSelectedRequest) -> dict:
    if not body.videos:
        raise HTTPException(400, detail="videos required")
    payload = [
        {
            "channel_id": v.channel_id,
            "video_id": v.video_id,
            "title": v.title,
            "published_at_utc": v.published_at_utc,
            "date_kst": v.date_kst,
            "url": v.url,
        }
        for v in body.videos
    ]
    try:
        return run_collect_selected(videos=payload)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(502, detail=str(e)) from e


@router.post(
    "/youtube/collect-url",
    response_model=YoutubeCollectUrlResult,
    operation_id="youtube_collect_url",
    summary="Collect one video by URL (meta + transcript, no STT)",
)
def post_youtube_collect_url(body: YoutubeCollectUrlRequest) -> YoutubeCollectUrlResult:
    try:
        data = run_collect_url(url=body.url)
    except ValueError as e:
        raise HTTPException(400, detail=str(e)) from e
    except RuntimeError as e:
        # etl ValueError가 RuntimeError("...error...")로 감싸질 수 있음
        msg = str(e)
        if any(
            k in msg
            for k in (
                "영상 URL",
                "채널 주소",
                "유효한 유튜브",
                "채널 정보",
            )
        ):
            raise HTTPException(400, detail=msg) from e
        raise HTTPException(502, detail=msg) from e
    return YoutubeCollectUrlResult(
        video_id=str(data.get("video_id") or ""),
        channel_id=str(data.get("channel_id") or ""),
        date_kst=str(data.get("date_kst") or ""),
        title=str(data.get("title") or ""),
        status=str(data.get("status") or "inserted"),
        has_transcript=bool(data.get("has_transcript")),
        url=str(data.get("url") or ""),
    )


@router.post(
    "/youtube/summarize",
    operation_id="youtube_summarize",
    summary="Manual summarize (LangGraph). All channels allowed.",
)
def post_youtube_summarize(body: YoutubeSummarizeRequest) -> dict:
    return run_summarize(
        from_date=body.from_date,
        to_date=body.to_date,
        channel_ids=body.channel_ids,
        video_ids=body.video_ids,
        force=body.force,
    )


@router.post(
    "/youtube/summarize-job",
    response_model=YoutubeSummarizeJob,
    operation_id="youtube_summarize_job_start",
    summary="Start single-video summarize in background",
)
def post_youtube_summarize_job(body: YoutubeSummarizeJobRequest) -> YoutubeSummarizeJob:
    try:
        job = start_summarize_job(
            channel_id=body.channel_id,
            video_id=body.video_id,
            title=body.title,
            force=body.force,
        )
    except ValueError as e:
        raise HTTPException(409, detail=str(e)) from e
    return YoutubeSummarizeJob(**job)


@router.get(
    "/youtube/summarize-job/{job_id}",
    response_model=YoutubeSummarizeJob,
    operation_id="youtube_summarize_job_status",
    summary="Get summarize job status",
)
def get_youtube_summarize_job(job_id: str) -> YoutubeSummarizeJob:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, detail=f"job not found: {job_id}")
    return YoutubeSummarizeJob(**job)


@router.get(
    "/youtube/summarize-jobs",
    response_model=list[YoutubeSummarizeJob],
    operation_id="youtube_summarize_jobs_recent",
    summary="Recent summarize jobs",
)
def get_youtube_summarize_jobs(limit: int = Query(10, ge=1, le=50)) -> list[YoutubeSummarizeJob]:
    return [YoutubeSummarizeJob(**j) for j in list_recent_jobs(limit=limit)]
