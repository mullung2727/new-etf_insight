"""유튜브 하루 2회 통합 시장 브리핑 — 미전송 영상 요약을 이슈별로 통합해 Discord 전송.

영상별 요약(`youtube_video_summaries.summary_json`)은 그대로 두고, 아직 어떤 브리핑에도
전달되지 않은 요약만 모아 통합 LLM 1회 → 코드 검증·점수 재계산 → 메시지 → 전송한다.
전송 성공 후에만 delivery ledger를 기록하므로, 실패한 영상은 다음 세션에서 회수된다.

선별 규칙은 하나다: **미전송 + cutoff 기준 48시간 이내 게시**. 세션 명목 window는
항상 48시간 안쪽이라 별도 조건으로 두면 죽은 조건이 된다(감사용으로 window만 저장).

Usage (from etl/):
    uv run python scripts/youtube_digest.py --date 2026-08-07 --session morning
    uv run python scripts/youtube_digest.py --date 2026-08-07 --session evening --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path 보장)

from dotenv import load_dotenv  # noqa: E402
from new_etf_insight.llm import generate_json  # noqa: E402
from notify import notify  # noqa: E402
from wl_sqlite import connect_rw  # noqa: E402
from youtube_channels import load_all_channels  # noqa: E402

DEFAULT_DB = Path(__file__).resolve().parents[1] / "db" / "youtube_public.sqlite3"
_HERE = Path(__file__).resolve().parent / "youtube_langgraph"
PROMPT_PATH = _HERE / "prompts" / "market_digest.md"
SCHEMA_PATH = _HERE / "market_digest_schema.json"

KST = dt.timezone(dt.timedelta(hours=9))
SESSION_HOUR = {"morning": 10, "evening": 18}
SESSION_LABEL = {"morning": "오전", "evening": "저녁"}
GRACE_HOURS = 48

# 항목당 약 480자(제목+요약+근거+견해차+출처+연결) → 3개면 헤더/푸터 포함 1,900자 안.
MAX_HIGHLIGHTS = 3
MIN_DISPLAY_SCORE = 60
MAX_MESSAGE_CHARS = 1900

# 텔레그램 session overview와 같은 축·배점 (두 브리핑 점수를 같은 자로 비교하기 위함).
_SCORE_LIMITS = {
    "market_impact": 25,
    "evidence_quality": 25,
    "novelty": 20,
    "investment_relevance": 20,
    "cross_channel": 10,
}

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS youtube_digest_runs (
    date_kst TEXT NOT NULL,
    session TEXT NOT NULL CHECK(session IN ('morning', 'evening')),
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('generated', 'sent')),
    source_video_ids_json TEXT NOT NULL,
    digest_json TEXT,
    message_text TEXT,
    warning_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (date_kst, session)
)
"""

_CREATE_DELIVERIES = """
CREATE TABLE IF NOT EXISTS youtube_digest_deliveries (
    channel_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    digest_date_kst TEXT NOT NULL,
    digest_session TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, video_id)
)
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_RUNS)
    con.execute(_CREATE_DELIVERIES)


def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def session_window(date_kst: str, session: str) -> tuple[str, str]:
    """(window_start_utc, cutoff_utc) — cutoff는 세션 실행 시각, start는 48시간 전."""
    day = dt.date.fromisoformat(date_kst)
    cutoff = dt.datetime(day.year, day.month, day.day, SESSION_HOUR[session], tzinfo=KST)
    start = cutoff - dt.timedelta(hours=GRACE_HOURS)
    return (
        start.astimezone(dt.timezone.utc).isoformat(),
        cutoff.astimezone(dt.timezone.utc).isoformat(),
    )


def fetch_unsent_summaries(
    con: sqlite3.Connection,
    *,
    window_start_utc: str,
    cutoff_utc: str,
) -> list[dict]:
    """window 안에 게시됐고 요약 내용이 있으며 아직 어떤 digest에도 전송되지 않은 영상."""
    labels = {cid: cfg.get("label") or cid for cid, cfg in load_all_channels().items()}
    start_dt = dt.datetime.fromisoformat(window_start_utc)
    cutoff_dt = dt.datetime.fromisoformat(cutoff_utc)
    # SQL은 문자열 비교라 offset이 섞이면(수집 폴백이 +09:00을 쓴다) 경계가 최대 9시간
    # 어긋난다. 하루 여유로 넓게 뽑고 파싱해서 정확히 거른다.
    cur = con.execute(
        """
        SELECT v.channel_id, v.video_id, v.title, v.url, v.published_at_utc, s.summary_json
        FROM youtube_videos v
        JOIN youtube_video_summaries s
          ON s.channel_id = v.channel_id AND s.video_id = v.video_id
        LEFT JOIN youtube_digest_deliveries d
          ON d.channel_id = v.channel_id AND d.video_id = v.video_id
        WHERE d.video_id IS NULL
          AND v.published_at_utc > ?
          AND v.published_at_utc <= ?
        ORDER BY v.published_at_utc ASC
        """,
        (
            (start_dt - dt.timedelta(days=1)).isoformat(),
            (cutoff_dt + dt.timedelta(days=1)).isoformat(),
        ),
    )
    rows: list[dict] = []
    for channel_id, video_id, title, url, published, summary_json in cur:
        try:
            published_dt = dt.datetime.fromisoformat(published)
        except (ValueError, TypeError):
            print(f"[youtube-digest] skip unparsable published_at {channel_id}/{video_id}")
            continue
        if not start_dt < published_dt <= cutoff_dt:
            continue
        try:
            summary = json.loads(summary_json) if summary_json else {}
        except (ValueError, TypeError):
            summary = {}
        if not (summary.get("headline") or summary.get("issues") or summary.get("bullets")):
            # 깨졌거나 빈 요약 — ledger에 넣으면 나중에 정상 요약으로 복구돼도 다시 못 다룬다.
            print(f"[youtube-digest] skip empty summary {channel_id}/{video_id}")
            continue
        published_kst = published_dt.astimezone(KST).strftime("%m-%d %H:%M")
        rows.append({
            "channel_id": channel_id,
            "channel_label": labels.get(channel_id, channel_id),
            "video_id": video_id,
            "source_ref": f"{channel_id}/{video_id}",
            "title": title,
            "url": url,
            "published_at_utc": published,
            "published_at_kst": published_kst,
            "summary": summary,
        })
    return rows


def get_digest_run(con: sqlite3.Connection, date_kst: str, session: str) -> dict | None:
    row = con.execute(
        "SELECT status, source_video_ids_json, message_text FROM youtube_digest_runs "
        "WHERE date_kst=? AND session=?",
        (date_kst, session),
    ).fetchone()
    if row is None:
        return None
    return {
        "status": row[0],
        "source_video_ids": json.loads(row[1]),
        "message_text": row[2],
    }


def save_generated_run(
    con: sqlite3.Connection,
    *,
    date_kst: str,
    session: str,
    window: tuple[str, str],
    source_refs: list[str],
    digest: dict,
    message_text: str | None,
    warnings: list[str],
) -> None:
    now = _now_utc()
    con.execute(
        """
        INSERT INTO youtube_digest_runs(
            date_kst, session, window_start_utc, window_end_utc, status,
            source_video_ids_json, digest_json, message_text, warning_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'generated', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date_kst, session) DO UPDATE SET
            window_start_utc=excluded.window_start_utc,
            window_end_utc=excluded.window_end_utc,
            status='generated',
            source_video_ids_json=excluded.source_video_ids_json,
            digest_json=excluded.digest_json,
            message_text=excluded.message_text,
            warning_json=excluded.warning_json,
            updated_at=excluded.updated_at
        """,
        (
            date_kst,
            session,
            window[0],
            window[1],
            json.dumps(source_refs, ensure_ascii=False),
            json.dumps(digest, ensure_ascii=False),
            message_text,
            json.dumps(warnings, ensure_ascii=False),
            now,
            now,
        ),
    )


def mark_run_sent(
    con: sqlite3.Connection,
    *,
    date_kst: str,
    session: str,
    source_refs: list[str],
) -> None:
    """run status='sent' + 모든 source delivery를 한 트랜잭션에서 확정(커밋은 호출자)."""
    now = _now_utc()
    con.execute(
        "UPDATE youtube_digest_runs SET status='sent', sent_at=?, updated_at=? "
        "WHERE date_kst=? AND session=?",
        (now, now, date_kst, session),
    )
    for ref in source_refs:
        channel_id, _, video_id = ref.partition("/")
        con.execute(
            "INSERT OR IGNORE INTO youtube_digest_deliveries("
            "channel_id, video_id, digest_date_kst, digest_session, delivered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (channel_id, video_id, date_kst, session, now),
        )


def undelivered_refs(con: sqlite3.Connection, source_refs: list[str]) -> list[str]:
    """아직 ledger에 없는 source_ref만. 다른 세션이 이미 보낸 영상의 재전송을 막는다."""
    out: list[str] = []
    for ref in source_refs:
        channel_id, _, video_id = ref.partition("/")
        hit = con.execute(
            "SELECT 1 FROM youtube_digest_deliveries WHERE channel_id=? AND video_id=?",
            (channel_id, video_id),
        ).fetchone()
        if hit is None:
            out.append(ref)
    return out


def build_digest_input(rows: list[dict]) -> str:
    """영상별 기존 요약만 직렬화 (원문 대본은 넣지 않는다)."""
    blocks: list[str] = []
    for row in rows:
        summary = row["summary"] or {}
        lines = [
            f"[{row['source_ref']} · {row['channel_label']} · {row['published_at_kst']}] {row['title']}"
        ]
        if summary.get("headline"):
            lines.append(f"- headline: {summary['headline']}")
        for issue in summary.get("issues") or []:
            lines.append(f"- issue: {issue.get('title', '')} / {issue.get('summary', '')}")
        for bullet in summary.get("bullets") or []:
            lines.append(f"- bullet: {bullet}")
        if summary.get("risk_or_caveat"):
            lines.append(f"- risk: {summary['risk_or_caveat']}")
        names = [s.get("name") for s in (summary.get("stocks") or []) if s.get("name")]
        if names:
            lines.append(f"- stocks: {', '.join(names)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def call_digest_llm(
    rows: list[dict],
    date_kst: str,
    session: str,
    *,
    generate_fn=generate_json,
) -> dict:
    """입력 영상이 없으면 LLM을 호출하지 않는다."""
    if not rows:
        return {}
    prompt = PROMPT_PATH.read_text(encoding="utf-8").format(
        date_kst=date_kst,
        session=SESSION_LABEL.get(session, session),
        videos_block=build_digest_input(rows),
    )
    raw = generate_fn(prompt, output_schema_path=SCHEMA_PATH, search=False)
    return json.loads(raw)


def validate_digest(raw: dict, input_rows: list[dict]) -> tuple[list[dict], list[str]]:
    """LLM 출력을 실제 입력과 대조해 안전한 하이라이트만 남기고 점수를 다시 합산한다."""
    warnings: list[str] = []
    allowed = {row["source_ref"]: row for row in input_rows}
    input_text = build_digest_input(input_rows)

    cleaned: list[dict] = []
    for item in (raw or {}).get("highlights", []):
        title = item.get("title", "")
        breakdown = item.get("score_breakdown") or {}
        if any(
            not isinstance(breakdown.get(key), int) or not 0 <= breakdown[key] <= limit
            for key, limit in _SCORE_LIMITS.items()
        ):
            warnings.append(f"score_out_of_range:{title}")
            continue

        refs = [ref for ref in (item.get("source_video_ids") or []) if ref in allowed]
        if not refs:
            warnings.append(f"highlight_without_valid_source:{title}")
            continue

        breakdown = {key: breakdown[key] for key in _SCORE_LIMITS}
        channels = {allowed[ref]["channel_id"] for ref in refs}
        if len(channels) < 2 and breakdown["cross_channel"] > 0:
            warnings.append(f"cross_channel_single_source:{title}")
            breakdown["cross_channel"] = 0

        links = [
            link for link in (item.get("investment_links") or [])[:5] if link in input_text
        ]
        if len(links) != len(item.get("investment_links") or []):
            warnings.append(f"investment_link_not_in_input:{title}")

        cleaned.append({
            "title": title,
            "summary": item.get("summary", ""),
            "evidence": item.get("evidence", ""),
            "view_difference": item.get("view_difference") or None,
            "uncertainty": item.get("uncertainty") or None,
            "investment_links": links,
            "source_video_ids": refs,
            "source_channels": sorted({allowed[ref]["channel_label"] for ref in refs}),
            "score_breakdown": breakdown,
            "score_total": sum(breakdown.values()),
            "first_published_at_utc": min(allowed[ref]["published_at_utc"] for ref in refs),
        })

    # 같은 source 영상 집합이면 같은 이슈 — 높은 점수만 남긴다(제목 유사도는 쓰지 않는다).
    by_sources: dict[frozenset, dict] = {}
    for item in cleaned:
        key = frozenset(item["source_video_ids"])
        prev = by_sources.get(key)
        if prev is None or item["score_total"] > prev["score_total"]:
            if prev is not None:
                warnings.append(f"duplicate_source_set:{prev['title']}")
            by_sources[key] = item
        else:
            warnings.append(f"duplicate_source_set:{item['title']}")

    highlights = sorted(
        by_sources.values(),
        key=lambda item: (-item["score_total"], item["first_published_at_utc"]),
    )
    return highlights, warnings


def _highlight_block(rank_item: dict) -> list[str]:
    lines = [f"• [{rank_item['score_total']}점] {rank_item['title']}", f"  {rank_item['summary']}"]
    if rank_item["evidence"]:
        lines.append(f"  근거: {rank_item['evidence']}")
    if rank_item["view_difference"]:
        lines.append(f"  차이: {rank_item['view_difference']}")
    if rank_item["uncertainty"]:
        lines.append(f"  불확실: {rank_item['uncertainty']}")
    lines.append(f"  출처: {' · '.join(rank_item['source_channels'])}")
    if rank_item["investment_links"]:
        lines.append(f"  연결: {' · '.join(rank_item['investment_links'])}")
    return lines


def collect_failure_note(channel_ids: list[str]) -> str | None:
    """수집 실패 채널을 사람이 읽는 한 줄로. 러너가 넘긴 channel_id를 label로 바꾼다."""
    if not channel_ids:
        return None
    labels = {cid: cfg.get("label") or cid for cid, cfg in load_all_channels().items()}
    names = " · ".join(labels.get(cid, cid) for cid in channel_ids)
    return f"⚠️ 수집 실패: {names} (해당 채널 새 영상은 다음 세션에 포함)"


def format_digest_message(
    date_kst: str,
    session: str,
    rows: list[dict],
    highlights: list[dict],
    notes: list[str] | None = None,
) -> str | None:
    """1,900자 이하. 초과하면 문자열 절단이 아니라 낮은 순위 항목부터 제외한다."""
    notes = [n for n in (notes or []) if n]
    if not rows and not notes:
        return None

    channels = len({row["channel_id"] for row in rows})
    head = (
        f"🧭 유튜브 시장 브리핑 {date_kst} {SESSION_LABEL.get(session, session)}\n"
        f"신규 영상 {len(rows)}개 · 채널 {channels}개"
    )
    if notes:
        head = head + "\n" + "\n".join(notes)

    shown = [h for h in highlights if h["score_total"] >= MIN_DISPLAY_SCORE][:MAX_HIGHLIGHTS]
    if not shown:
        if not rows:
            return head
        return f"{head}\n{MIN_DISPLAY_SCORE}점 이상 주요 이슈 없음"

    foot = "※ 정보가치 점수이며 사실 확정도·수익률 전망이 아님"
    while shown:
        body = "\n\n".join("\n".join(_highlight_block(item)) for item in shown)
        message = f"{head}\n\n🔥 주요 내용\n{body}\n\n{foot}"
        if len(message) <= MAX_MESSAGE_CHARS:
            return message
        shown = shown[:-1]
    return f"{head} · 메시지 길이 초과로 본문 생략"


def run_digest(
    *,
    date_kst: str,
    session: str,
    db_path: Path | str = DEFAULT_DB,
    channel: str | None = None,
    dry_run: bool = False,
    collect_failed: list[str] | None = None,
    summarize_failed: bool = False,
    generate_fn=generate_json,
    send_fn=notify,
) -> int:
    window = session_window(date_kst, session)
    notes = [
        note
        for note in (
            collect_failure_note(collect_failed or []),
            "⚠️ 영상 요약 단계 실패 — 일부 영상이 이번 브리핑에서 빠졌을 수 있음"
            if summarize_failed
            else None,
        )
        if note
    ]
    with connect_rw(db_path) as con:
        ensure_schema(con)
        run = get_digest_run(con, date_kst, session)
        if run and run["status"] == "sent":
            print(f"[youtube-digest] {date_kst}/{session} already sent - skip")
            return 0

        if run and run["status"] == "generated" and run["message_text"]:
            # 전송만 실패한 재실행 — 저장된 메시지를 그대로 재전송한다(LLM 재호출 없음).
            source_refs = run["source_video_ids"]
            message = run["message_text"]
            # 전송 실패 후 다음 세션이 같은 영상을 이미 배달했을 수 있다. 그때 이 메시지를
            # 다시 보내면 같은 내용이 두 번 나간다 — 남은 영상이 없으면 run만 마감한다.
            if not dry_run and not undelivered_refs(con, source_refs):
                mark_run_sent(con, date_kst=date_kst, session=session, source_refs=source_refs)
                con.commit()
                print(f"[youtube-digest] {date_kst}/{session} sources already delivered - close")
                return 0
            print(f"[youtube-digest] reuse generated run ({len(source_refs)} videos)")
        else:
            rows = fetch_unsent_summaries(
                con, window_start_utc=window[0], cutoff_utc=window[1]
            )
            if not rows:
                if not notes:
                    print(f"[youtube-digest] {date_kst}/{session} no new summaries - skip")
                    return 0
                # 경고만 있는 경우 run 행을 만들지 않는다 — 세션 슬롯을 소진해서
                # 나중에 진짜 브리핑이 막히면 안 되기 때문.
                note_only = format_digest_message(date_kst, session, rows, [], notes)
                if dry_run:
                    print(note_only)
                    return 0
                return 0 if send_fn(note_only, channel) else 1
            raw = call_digest_llm(rows, date_kst, session, generate_fn=generate_fn)
            highlights, warnings = validate_digest(raw, rows)
            warnings = warnings + notes
            message = format_digest_message(date_kst, session, rows, highlights, notes)
            source_refs = [row["source_ref"] for row in rows]
            print(
                f"[youtube-digest] videos={len(rows)} highlights={len(highlights)} "
                f"warnings={len(warnings)}"
            )
            for warning in warnings:
                print(f"[youtube-digest] warning: {warning}")
            if not dry_run:
                save_generated_run(
                    con,
                    date_kst=date_kst,
                    session=session,
                    window=window,
                    source_refs=source_refs,
                    digest={"highlights": highlights},
                    message_text=message,
                    warnings=warnings,
                )
                con.commit()

        if dry_run:
            print(message or "(no message)")
            return 0
        if not message:
            return 0
        if not send_fn(message, channel):
            print("[youtube-digest] send failed - ledger untouched, will retry next run")
            return 1
        mark_run_sent(con, date_kst=date_kst, session=session, source_refs=source_refs)
        con.commit()
        print(f"[youtube-digest] sent ({len(source_refs)} videos)")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="KST YYYY-MM-DD")
    ap.add_argument("--session", required=True, choices=sorted(SESSION_HOUR))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--channel", help="notify 채널 override (예: telegram_report)")
    ap.add_argument("--dry-run", action="store_true", help="전송·ledger 없이 메시지만 출력")
    ap.add_argument(
        "--collect-failed",
        default="",
        help="수집 실패한 channel_id CSV — 메시지에 경고 줄로 표시(러너가 채움)",
    )
    ap.add_argument(
        "--summarize-failed",
        action="store_true",
        help="영상 요약 단계가 실패했음을 메시지에 표시",
    )
    args = ap.parse_args()

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    return run_digest(
        date_kst=args.date,
        session=args.session,
        db_path=args.db,
        channel=args.channel,
        dry_run=args.dry_run,
        collect_failed=[c for c in args.collect_failed.split(",") if c.strip()],
        summarize_failed=args.summarize_failed,
    )


if __name__ == "__main__":
    raise SystemExit(main())
