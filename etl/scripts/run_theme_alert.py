"""테마 알림 — 1단계(스키마·판정·문구) + 2단계(원문 읽기·LLM·main).

설계: docs/PLAN_THEME_REEMERGENCE_ALERT.md.
시간은 전부 UTC ISO 문자열로 저장, 비교 시 datetime.fromisoformat.
"""
from __future__ import annotations

import argparse
import json
import sqlite3  # noqa: F401  (con 타입 힌트용)
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path)

from dotenv import load_dotenv  # noqa: E402  (2단계: main 진입점)
from notify import notify  # noqa: E402  (2단계: 기본 send_fn)
from telegram_channels import load_discovery_channels  # noqa: E402  (2단계: 텔레그램 범위)

_BASE = Path(__file__).resolve().parent
DEFAULT_DB_PATH = _BASE.parent / "db" / "theme_alert.sqlite3"
DEFAULT_CONFIG_PATH = _BASE / "theme_alert" / "theme_alert.json"

_KST = timedelta(hours=9)
_MAX_LINKS = 5


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_config(path=DEFAULT_CONFIG_PATH) -> dict:
    """설정 json에서 quiet_days, max_past_refs 읽음. 키 없으면 KeyError."""
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return {"quiet_days": cfg["quiet_days"], "max_past_refs": cfg["max_past_refs"]}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
  theme_id   INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,      -- 대표 이름
  registered INTEGER NOT NULL DEFAULT 0, -- 1 = 사용자 등록 테마
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS theme_aliases (
  alias_norm TEXT PRIMARY KEY,          -- 정규화 표기 (공백 제거·소문자)
  theme_id   INTEGER NOT NULL REFERENCES themes(theme_id),
  source     TEXT NOT NULL,             -- 'manual' | 'llm'
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS theme_mentions (
  theme_id     INTEGER NOT NULL REFERENCES themes(theme_id),
  source       TEXT NOT NULL,           -- 'telegram' | 'youtube'
  ref          TEXT NOT NULL,           -- post_ref | video_id
  posted_at    TEXT NOT NULL,           -- UTC ISO
  collected_at TEXT NOT NULL,           -- 원본 DB created_at
  PRIMARY KEY (theme_id, source, ref)
);
CREATE TABLE IF NOT EXISTS theme_alerts (
  alert_id       INTEGER PRIMARY KEY,
  theme_id       INTEGER NOT NULL REFERENCES themes(theme_id),
  kind           TEXT NOT NULL,         -- 'new' | 'progress' | 'baseline'(첫 가동, 미전송)
  stage          TEXT NOT NULL,         -- 결정 15 단계
  evidence_level TEXT NOT NULL,         -- 결정 17
  refs_json      TEXT NOT NULL,         -- [{source, ref}]
  message        TEXT NOT NULL,
  sent_at        TEXT                   -- --no-send면 NULL
);
CREATE TABLE IF NOT EXISTS theme_processed (
  source       TEXT NOT NULL,
  ref          TEXT NOT NULL,
  processed_at TEXT NOT NULL,
  PRIMARY KEY (source, ref)
);
"""


def ensure_schema(con: "sqlite3.Connection") -> None:
    con.executescript(_SCHEMA)


def normalize_alias(text: str) -> str:
    """모든 공백 제거 + 소문자."""
    return "".join(text.split()).lower()


def resolve_theme(
    con: "sqlite3.Connection", raw_name: str, llm_match: str | None = None
) -> int:
    """원문 테마 표기를 theme_id로 묶음 (결정 10)."""
    norm = normalize_alias(raw_name)
    row = con.execute(
        "SELECT theme_id FROM theme_aliases WHERE alias_norm = ?", (norm,)
    ).fetchone()
    if row is not None:
        return row[0]
    if llm_match is not None:
        row = con.execute(
            "SELECT theme_id FROM themes WHERE name = ?", (llm_match,)
        ).fetchone()
        if row is not None:
            theme_id = row[0]
            con.execute(
                "INSERT INTO theme_aliases (alias_norm, theme_id, source, created_at)"
                " VALUES (?, ?, 'llm', ?)",
                (norm, theme_id, _now_utc_iso()),
            )
            con.commit()
            return theme_id
    for theme_id, name in con.execute("SELECT theme_id, name FROM themes"):
        if normalize_alias(name) == norm:
            con.execute(
                "INSERT INTO theme_aliases (alias_norm, theme_id, source, created_at)"
                " VALUES (?, ?, 'llm', ?)",
                (norm, theme_id, _now_utc_iso()),
            )
            con.commit()
            return theme_id
    cur = con.execute(
        "INSERT INTO themes (name, registered, created_at) VALUES (?, 0, ?)",
        (raw_name, _now_utc_iso()),
    )
    theme_id = cur.lastrowid
    con.execute(
        "INSERT INTO theme_aliases (alias_norm, theme_id, source, created_at)"
        " VALUES (?, ?, 'llm', ?)",
        (norm, theme_id, _now_utc_iso()),
    )
    con.commit()
    return theme_id


def last_mention_at(con: "sqlite3.Connection", theme_id: int) -> str | None:
    row = con.execute(
        "SELECT MAX(posted_at) FROM theme_mentions WHERE theme_id = ?", (theme_id,)
    ).fetchone()
    return row[0] if row else None


def classify(
    con: "sqlite3.Connection", theme_id: int, posted_at: str, quiet_days
) -> tuple[str, float | None]:
    """이번 원문(posted_at, 아직 theme_mentions에 없음)을 신규/진전 후보로 판정."""
    last = last_mention_at(con, theme_id)
    if last is None:
        return ("new", None)
    gap_days = (
        datetime.fromisoformat(posted_at) - datetime.fromisoformat(last)
    ).total_seconds() / 86400
    if gap_days >= quiet_days:
        return ("new", gap_days)
    return ("progress_candidate", gap_days)


def last_stage(con: "sqlite3.Connection", theme_id: int) -> str | None:
    """theme_alerts에서 alert_id 가장 큰 행의 stage (kind='baseline' 포함)."""
    row = con.execute(
        "SELECT stage FROM theme_alerts WHERE theme_id = ?"
        " ORDER BY alert_id DESC LIMIT 1",
        (theme_id,),
    ).fetchone()
    return row[0] if row else None


def should_send_progress(prev_stage: str | None, new_stage: str) -> bool:
    if prev_stage is None:
        return True
    return prev_stage != new_stage


def format_message(
    theme_name: str,
    registered,
    kind: str,
    stage: str,
    summary: str,
    evidence_level: str,
    refs: list,
    gap_days=None,
    prev_stage: str | None = None,
) -> str:
    """알림 문구 (결정 1·13). refs: [{posted_at, collected_at, url, label}]."""
    if kind == "new":
        if gap_days is None:
            head = f"[신규 테마] {theme_name} (첫 관측)"
        else:
            head = f"[신규 테마] {theme_name} ({int(gap_days)}일 만에 재등장)"
    else:  # progress
        prev = prev_stage if prev_stage is not None else "기록 없음"
        head = f"[진전] {theme_name} — {prev} → {stage}"
    if registered:
        head = "⭐ " + head
    earliest = min(refs, key=lambda r: r["posted_at"])
    posted_kst = datetime.fromisoformat(earliest["posted_at"]) + _KST
    collected_kst = datetime.fromisoformat(earliest["collected_at"]) + _KST
    lines = [
        head,
        f"- 요약: {summary}",
        f"- 근거 수준: {evidence_level}",
        f"- 게시 {posted_kst:%m/%d %H:%M} · 수집 {collected_kst:%m/%d %H:%M}",
    ]
    ordered = sorted(refs, key=lambda r: r["posted_at"])
    for r in ordered[:_MAX_LINKS]:
        lines.append(f"- [{r['label']}] {r['url']}")
    if len(ordered) > _MAX_LINKS:
        lines.append(f"- 외 {len(ordered) - _MAX_LINKS}건")
    return "\n".join(lines)


def save_run(con: "sqlite3.Connection", mentions: list, alerts: list, processed: list) -> None:
    """mentions/alerts/processed를 한 트랜잭션으로 저장."""
    with con:
        con.executemany(
            "INSERT OR IGNORE INTO theme_mentions"
            " (theme_id, source, ref, posted_at, collected_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (m["theme_id"], m["source"], m["ref"], m["posted_at"], m["collected_at"])
                for m in mentions
            ],
        )
        con.executemany(
            "INSERT INTO theme_alerts"
            " (theme_id, kind, stage, evidence_level, refs_json, message, sent_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    a["theme_id"],
                    a["kind"],
                    a["stage"],
                    a["evidence_level"],
                    a["refs_json"],
                    a["message"],
                    a["sent_at"],
                )
                for a in alerts
            ],
        )
        now = _now_utc_iso()
        con.executemany(
            "INSERT OR IGNORE INTO theme_processed (source, ref, processed_at)"
            " VALUES (?, ?, ?)",
            [(p["source"], p["ref"]) + (now,) for p in processed],
        )


def is_processed(con: "sqlite3.Connection", source: str, ref: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM theme_processed WHERE source = ? AND ref = ?", (source, ref)
    ).fetchone()
    return row is not None


# ── 2단계: 원문 읽기 + LLM 호출 ──

_STAGES = ("언급/전망", "검토", "협의", "계약·발주", "취소")
_EVIDENCE_LEVELS = ("참여 가능성", "증권사 전망", "공식 확정")
_SUMMARY_BATCH_SIZE = 10

THEME_ALERT_DIR = _BASE / "theme_alert"
PROMPTS_DIR = THEME_ALERT_DIR / "prompts"
THEME_EXTRACT_SCHEMA = THEME_ALERT_DIR / "theme_extract_schema.json"
THEME_SUMMARY_SCHEMA = THEME_ALERT_DIR / "theme_summary_schema.json"

DEFAULT_TELEGRAM_DB_PATH = _BASE.parent / "db" / "telegram_public.sqlite3"
DEFAULT_YOUTUBE_DB_PATH = _BASE.parent / "db" / "youtube_public.sqlite3"

PROJECT_ROOT = _BASE.parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

_KST_TZ = timezone(_KST)


def connect_ro(path) -> "sqlite3.Connection":
    """원본 DB 읽기 전용 연결. 이 연결로 쓰기하면 sqlite3.OperationalError."""
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def _telegram_row_to_item(post_ref, posted_at, created_at, channel, text) -> dict:
    return {
        "source": "telegram",
        "ref": post_ref,
        "posted_at": posted_at,
        "collected_at": created_at,
        "label": channel,
        "url": f"https://telegram.me/{post_ref}",
        "text": " ".join((text or "").split())[:500],
    }


def load_telegram_items(con_src, con_state, start_date, end_date, channels) -> list:
    """기간 내·탐색 채널·미처리 글만 item 목록으로. channels는 dict도 됨."""
    names = sorted(set(channels))
    if not names:
        return []
    rows = con_src.execute(
        "SELECT post_ref, posted_at_utc, created_at, channel, text FROM telegram_posts"
        f" WHERE date_kst BETWEEN ? AND ? AND channel IN ({','.join('?' * len(names))})"
        " ORDER BY posted_at_utc",
        (start_date, end_date, *names),
    ).fetchall()
    return [
        _telegram_row_to_item(*row)
        for row in rows
        if not is_processed(con_state, "telegram", row[0])
    ]


def _youtube_text(title, summary) -> str:
    parts = [title or ""]
    if isinstance(summary, dict):
        if summary.get("headline"):
            parts.append(summary["headline"])
        for issue in summary.get("issues") or []:
            t = (issue.get("title") or "").strip()
            s = (issue.get("summary") or "").strip()
            bit = f"{t}: {s}" if t and s else (t or s)
            if bit:
                parts.append(bit)
    return " ".join(" / ".join(parts).split())[:1500]


def _youtube_row_to_item(video_id, published_at, created_at, title, url, summary_json) -> dict:
    try:
        summary = json.loads(summary_json or "{}")
    except ValueError:
        summary = {}
    return {
        "source": "youtube",
        "ref": video_id,
        "posted_at": published_at,
        "collected_at": created_at,
        "label": (title or "")[:30],
        "url": url,
        "text": _youtube_text(title, summary),
    }


def load_youtube_items(con_src, con_state, start_date, end_date) -> list:
    """요약 있는 영상만 읽음. 요약 없는 영상은 processed 표시도 안 됨(결정 6)."""
    rows = con_src.execute(
        "SELECT v.video_id, v.published_at_utc, v.created_at, v.title, v.url, s.summary_json"
        " FROM youtube_videos v JOIN youtube_video_summaries s"
        " ON v.channel_id = s.channel_id AND v.video_id = s.video_id"
        " WHERE v.date_kst BETWEEN ? AND ?"
        " ORDER BY v.published_at_utc",
        (start_date, end_date),
    ).fetchall()
    return [
        _youtube_row_to_item(*row)
        for row in rows
        if not is_processed(con_state, "youtube", row[0])
    ]


def fetch_texts(con_tg, con_yt, refs) -> list:
    """(source, ref) 목록 → item 형식 dict 목록. 없는 원문은 건너뜀."""
    out = []
    for r in refs:
        src, ref = r["source"], r["ref"]
        if src == "telegram" and con_tg is not None:
            row = con_tg.execute(
                "SELECT post_ref, posted_at_utc, created_at, channel, text"
                " FROM telegram_posts WHERE post_ref = ?",
                (ref,),
            ).fetchone()
            if row:
                out.append(_telegram_row_to_item(*row))
        elif src == "youtube" and con_yt is not None:
            row = con_yt.execute(
                "SELECT v.video_id, v.published_at_utc, v.created_at, v.title, v.url, s.summary_json"
                " FROM youtube_videos v JOIN youtube_video_summaries s"
                " ON v.channel_id = s.channel_id AND v.video_id = s.video_id"
                " WHERE v.video_id = ?",
                (ref,),
            ).fetchone()
            if row:
                out.append(_youtube_row_to_item(*row))
    return out


def _item_line(it) -> str:
    return f"[{it['ref']} · {it['label']}] {it['text']}"


def build_extract_prompt(items, theme_names, registered_names) -> str:
    items_block = "\n".join(_item_line(it) for it in items)
    theme_block = "\n".join(f"- {n}" for n in sorted(theme_names)) or "(없음)"
    reg_block = "\n".join(f"- {n}" for n in sorted(registered_names)) or "(없음)"
    return (
        (PROMPTS_DIR / "theme_extract.md")
        .read_text(encoding="utf-8")
        .replace("{items_block}", items_block)
        .replace("{theme_names_block}", theme_block)
        .replace("{registered_block}", reg_block)
    )


def build_summary_prompt(blocks) -> str:
    """blocks: [{theme_id, name, current:[item], past:[item]}]."""
    parts = []
    for b in blocks:
        cur = "\n".join(_item_line(it) for it in b["current"]) or "(없음)"
        past = "\n".join(_item_line(it) for it in b["past"]) or "(없음)"
        parts.append(
            f"### 테마 {b['theme_id']}: {b['name']}\n[이번 원문]\n{cur}\n[과거 원문]\n{past}"
        )
    return (
        (PROMPTS_DIR / "theme_summary.md")
        .read_text(encoding="utf-8")
        .replace("{themes_block}", "\n\n".join(parts))
    )


def past_mentions(con_state, theme_id, limit) -> list:
    """posted_at 내림차순 상위 [{source, ref}]."""
    rows = con_state.execute(
        "SELECT source, ref FROM theme_mentions WHERE theme_id = ?"
        " ORDER BY posted_at DESC LIMIT ?",
        (theme_id, limit),
    ).fetchall()
    return [{"source": s, "ref": r} for s, r in rows]


def theme_name_and_registered(con_state, theme_id) -> tuple:
    row = con_state.execute(
        "SELECT name, registered FROM themes WHERE theme_id = ?", (theme_id,)
    ).fetchone()
    return (row[0], row[1]) if row else ("?", 0)


def process_batch(items, con_state, cfg, llm_fn, send_fn, mode, fetch_texts_fn) -> dict:
    """원문 묶음 1건 처리. mode ∈ "send" | "no_send" | "backfill".

    llm_fn(prompt, schema_path) -> str(JSON). items가 비면 LLM 호출 없이 반환.
    """
    result = {"items": len(items), "themes": 0, "alerts": []}
    if mode not in ("send", "no_send", "backfill"):
        raise ValueError(f"unknown mode: {mode}")
    if not items:
        return result
    by_ref = {it["ref"]: it for it in items}

    # 1. 추출 호출 1회 — 입력에 없는 ref는 버리고 refs가 비면 테마 버림
    theme_rows = con_state.execute("SELECT name, registered FROM themes").fetchall()
    prompt = build_extract_prompt(
        items, [r[0] for r in theme_rows], [r[0] for r in theme_rows if r[1]]
    )
    extracted = json.loads(llm_fn(prompt, THEME_EXTRACT_SCHEMA)).get("themes", []) or []

    # 2. 테마 묶기 — 같은 테마가 여러 개 나오면 합침
    groups: dict = {}
    for t in extracted:
        name = (t.get("name") or "").strip()
        refs = [r for r in (t.get("refs") or []) if r in by_ref]
        if not name or not refs:
            continue
        theme_id = resolve_theme(con_state, name, t.get("match_existing"))
        g = groups.setdefault(theme_id, [])
        g.extend(r for r in refs if r not in g)

    # 3. 판정 — 테마별 가장 이른 posted_at으로, mentions 저장 전에
    decisions = {}
    for theme_id, refs in groups.items():
        earliest = min(by_ref[r]["posted_at"] for r in refs)
        decisions[theme_id] = classify(con_state, theme_id, earliest, cfg["quiet_days"])
    result["themes"] = len(groups)

    # 5. 요약 호출 — new·progress_candidate만, 10개씩 (backfill은 건너뜀)
    alerts = []
    if mode != "backfill":
        cand_ids = sorted(
            tid for tid, (kind, _gap) in decisions.items() if kind != "repeat"
        )
        summaries = {}
        for i in range(0, len(cand_ids), _SUMMARY_BATCH_SIZE):
            batch = cand_ids[i : i + _SUMMARY_BATCH_SIZE]
            blocks = []
            for tid in batch:
                name, _reg = theme_name_and_registered(con_state, tid)
                current = sorted(
                    (by_ref[r] for r in groups[tid]), key=lambda it: it["posted_at"]
                )
                past = fetch_texts_fn(past_mentions(con_state, tid, cfg["max_past_refs"]))
                blocks.append(
                    {"theme_id": tid, "name": name, "current": current, "past": past}
                )
            out = (
                json.loads(llm_fn(build_summary_prompt(blocks), THEME_SUMMARY_SCHEMA)).get(
                    "themes", []
                )
                or []
            )
            for s in out:
                summaries[s.get("theme_id")] = s
        for tid in cand_ids:
            s = summaries.get(tid)
            if s is None:
                print(f"[theme_alert] 요약 누락: theme_id={tid} (알림 생략)")
                continue
            stage, ev = s.get("stage"), s.get("evidence_level")
            if stage not in _STAGES or ev not in _EVIDENCE_LEVELS:
                print(
                    f"[theme_alert] 요약 값 범위 밖: theme_id={tid}"
                    f" stage={stage} evidence={ev} (알림 생략)"
                )
                continue
            kind, gap = decisions[tid]
            name, registered = theme_name_and_registered(con_state, tid)
            current = sorted(
                (by_ref[r] for r in groups[tid]), key=lambda it: it["posted_at"]
            )
            if kind == "new":
                alert_kind, prev = "new", None
            else:
                prev = last_stage(con_state, tid)
                if not should_send_progress(prev, stage):
                    continue
                alert_kind = "progress"
            message = format_message(
                name,
                registered,
                alert_kind,
                stage,
                s.get("summary") or "",
                ev,
                current,
                gap_days=gap if alert_kind == "new" else None,
                prev_stage=prev if alert_kind == "progress" else None,
            )
            # 6. 전송 — 테마 1개 = 1통
            if mode == "send":
                ok = send_fn(message)
                if ok:
                    sent_at = _now_utc_iso()
                else:
                    sent_at = None
                    print(f"[theme_alert] 전송 실패: {name}")
            else:
                print(message)
                sent_at = None
            alerts.append(
                {
                    "theme_id": tid,
                    "kind": alert_kind,
                    "stage": stage,
                    "evidence_level": ev,
                    "refs_json": json.dumps(
                        [{"source": it["source"], "ref": it["ref"]} for it in current],
                        ensure_ascii=False,
                    ),
                    "message": message,
                    "sent_at": sent_at,
                }
            )
            result["alerts"].append(message)

    # 7. 한 번에 저장 — 테마 없는 원문도 processed에
    mentions = [
        {
            "theme_id": tid,
            "source": by_ref[r]["source"],
            "ref": r,
            "posted_at": by_ref[r]["posted_at"],
            "collected_at": by_ref[r]["collected_at"],
        }
        for tid, refs in groups.items()
        for r in refs
    ]
    save_run(
        con_state,
        mentions,
        alerts,
        [{"source": it["source"], "ref": it["ref"]} for it in items],
    )
    return result


def write_baseline(con_state, cfg, llm_fn, fetch_texts_fn, since_iso) -> int:
    """--backfill 마무리: since 이후 언급 테마마다 baseline 1행. 전송 없음."""
    tids = [
        r[0]
        for r in con_state.execute(
            "SELECT DISTINCT theme_id FROM theme_mentions WHERE posted_at >= ?"
            " ORDER BY theme_id",
            (since_iso,),
        ).fetchall()
    ]
    alerts = []
    for i in range(0, len(tids), _SUMMARY_BATCH_SIZE):
        batch = tids[i : i + _SUMMARY_BATCH_SIZE]
        blocks, used = [], {}
        for tid in batch:
            name, _reg = theme_name_and_registered(con_state, tid)
            texts = fetch_texts_fn(past_mentions(con_state, tid, cfg["max_past_refs"]))
            blocks.append({"theme_id": tid, "name": name, "current": texts, "past": []})
            used[tid] = texts
        out = (
            json.loads(llm_fn(build_summary_prompt(blocks), THEME_SUMMARY_SCHEMA)).get(
                "themes", []
            )
            or []
        )
        summaries = {s.get("theme_id"): s for s in out}
        for tid in batch:
            s = summaries.get(tid)
            if s is None:
                print(f"[theme_alert] baseline 요약 누락: theme_id={tid} (생략)")
                continue
            if s.get("stage") not in _STAGES or s.get("evidence_level") not in _EVIDENCE_LEVELS:
                print(f"[theme_alert] baseline 값 범위 밖: theme_id={tid} (생략)")
                continue
            alerts.append(
                {
                    "theme_id": tid,
                    "kind": "baseline",
                    "stage": s["stage"],
                    "evidence_level": s["evidence_level"],
                    "refs_json": json.dumps(
                        [{"source": t["source"], "ref": t["ref"]} for t in used[tid]],
                        ensure_ascii=False,
                    ),
                    "message": "",
                    "sent_at": None,
                }
            )
    if alerts:
        save_run(con_state, [], alerts, [])
    return len(alerts)


def _default_llm_fn(prompt, schema_path) -> str:
    from new_etf_insight.llm import generate_json

    return generate_json(prompt, output_schema_path=Path(schema_path), search=False)


def _open_ro_optional(path):
    try:
        return connect_ro(path)
    except sqlite3.OperationalError as exc:
        print(f"[theme_alert] 원본 DB 열기 실패, 건너뜀: {path} ({exc})")
        return None


def main(
    argv=None,
    llm_fn=None,
    send_fn=None,
    state_db_path=None,
    telegram_db_path=None,
    youtube_db_path=None,
    config_path=None,
    channels=None,
) -> int:
    """진입점. llm_fn·send_fn·DB 경로 주입 가능(테스트용). 반환: 종료코드(항상 0).

    Usage (from etl/):
        uv run python scripts/run_theme_alert.py --source telegram|youtube --date YYYY-MM-DD
            [--start-date YYYY-MM-DD] [--session 라벨] [--no-send] [--backfill]
    """
    parser = argparse.ArgumentParser(description="신규·재부각 테마 알림 (2단계)")
    parser.add_argument("--source", required=True, choices=["telegram", "youtube"])
    parser.add_argument("--date", required=True, help="YYYY-MM-DD (KST)")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD (KST)")
    parser.add_argument("--session", default="", help="세션 라벨(실패 메시지에만 사용)")
    parser.add_argument("--no-send", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args(argv)

    if llm_fn is None:
        llm_fn = _default_llm_fn
    if send_fn is None:

        def send_fn(m):
            return notify(m, channel="theme_alert")

    try:
        load_dotenv(ENV_PATH)
        cfg = load_config(config_path or DEFAULT_CONFIG_PATH)
        end = date.fromisoformat(args.date)
        if args.start_date:
            start = date.fromisoformat(args.start_date)
        elif args.source == "youtube":
            start = end - timedelta(days=cfg["quiet_days"])
        else:
            start = end

        state_path = Path(state_db_path) if state_db_path else DEFAULT_DB_PATH
        state_path.parent.mkdir(parents=True, exist_ok=True)
        con_state = sqlite3.connect(state_path)
        ensure_schema(con_state)
        con_tg = _open_ro_optional(telegram_db_path or DEFAULT_TELEGRAM_DB_PATH)
        con_yt = _open_ro_optional(youtube_db_path or DEFAULT_YOUTUBE_DB_PATH)
        try:

            def fetch_texts_fn(refs):
                return fetch_texts(con_tg, con_yt, refs)

            if args.source == "telegram":
                if con_tg is None:
                    raise sqlite3.OperationalError(
                        "telegram 원본 DB를 열 수 없음:"
                        f" {telegram_db_path or DEFAULT_TELEGRAM_DB_PATH}"
                    )
                discovered = (
                    list(channels) if channels is not None else list(load_discovery_channels())
                )

                def load_items(s, e):
                    return load_telegram_items(con_tg, con_state, s, e, discovered)

            else:
                if con_yt is None:
                    raise sqlite3.OperationalError(
                        "youtube 원본 DB를 열 수 없음:"
                        f" {youtube_db_path or DEFAULT_YOUTUBE_DB_PATH}"
                    )

                def load_items(s, e):
                    return load_youtube_items(con_yt, con_state, s, e)

            if args.backfill:
                day, days = start, 0
                while day <= end:
                    d = day.isoformat()
                    process_batch(
                        load_items(d, d), con_state, cfg, llm_fn, send_fn, "backfill",
                        fetch_texts_fn,
                    )
                    day += timedelta(days=1)
                    days += 1
                since_iso = (
                    datetime(start.year, start.month, start.day, tzinfo=_KST_TZ)
                    .astimezone(timezone.utc)
                    .isoformat()
                )
                n = write_baseline(con_state, cfg, llm_fn, fetch_texts_fn, since_iso)
                print(f"[theme_alert] backfill {args.source} {start}~{end} ({days}일) baseline={n}")
            else:
                mode = "no_send" if args.no_send else "send"
                res = process_batch(
                    load_items(start.isoformat(), end.isoformat()),
                    con_state, cfg, llm_fn, send_fn, mode, fetch_texts_fn,
                )
                print(
                    f"[theme_alert] {args.source} {start}~{end}"
                    f" items={res['items']} themes={res['themes']} alerts={len(res['alerts'])}"
                )
        finally:
            con_state.close()
            if con_tg is not None:
                con_tg.close()
            if con_yt is not None:
                con_yt.close()
    except Exception as exc:
        label = f"{args.source} {args.date}" + (f" {args.session}" if args.session else "")
        first = str(exc).strip().split("\n")[0] if str(exc).strip() else type(exc).__name__
        msg = f"[테마 알림 실패] {label}: {first}"
        if args.no_send or args.backfill:
            print(msg)
        else:
            try:
                send_fn(msg)
            except Exception as send_exc:
                print(f"[theme_alert] 실패 알림 전송도 실패: {send_exc}")
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
