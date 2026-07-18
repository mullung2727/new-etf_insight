"""텔레그램 하루 1회 롤업 — 3세션 합산 '오늘 주목 종목' 추천.

같은 날(date_kst)의 morning/close/evening 분석완료 insights를 종목 단위로 합쳐
점수화하고 TOP N을 사람이 읽는 메시지로 notify() 전송. evening 배치 뒤 하루 1번 실행 상정.
discover/analyze/세션별 digest는 별도 스크립트 — 이건 하루 결과를 다시 모아 랭킹만 한다.

Usage (from etl/):
    uv run python scripts/send_telegram_daily_rollup.py --date 2026-07-16
    uv run python scripts/send_telegram_daily_rollup.py --date 2026-07-16 --dry-run
    uv run python scripts/send_telegram_daily_rollup.py --self-check
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path 보장)

import datetime as dt  # noqa: E402

from dotenv import load_dotenv  # noqa: E402
from notify import notify  # noqa: E402
from telegram_channels import load_all_channels  # noqa: E402
from wl_sqlite import connect_ro, connect_rw  # noqa: E402

DEFAULT_DB = Path(__file__).resolve().parents[1] / "db" / "telegram_public.sqlite3"

# 최신 세션 우선순위(요약·논조는 늦은 세션 것이 현재에 가깝다)
_SESSION_RANK = {"morning": 0, "close": 1, "evening": 2}
_WATCH_TYPES = {"flow_data", "research_note"}
_DEFAULT_TOP_N = 8

# 그날 최종 추천 TOP N 스냅샷. 점수식이 바뀌어도 "그날 왜 추천했나"를 복기·백테스트
# 할 수 있게 당시 점수·근거를 얼려둔다. 원본 세부는 telegram_stock_insights 참조.
_CREATE_DAILY_ROLLUP = """
CREATE TABLE IF NOT EXISTS telegram_daily_rollup (
    date_kst TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score INTEGER NOT NULL,
    session_count INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    is_new INTEGER NOT NULL,
    has_flow INTEGER NOT NULL,
    themes TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(date_kst, ticker)
)
"""


def ensure_rollup_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_DAILY_ROLLUP)


def persist_rollup(con: sqlite3.Connection, date_kst: str, top: list[dict]) -> None:
    """그날 TOP N 스냅샷 저장. 멱등 — 같은 날 재실행 시 전체 교체(rank 변동·축소 대응)."""
    ensure_rollup_schema(con)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute("DELETE FROM telegram_daily_rollup WHERE date_kst=?", (date_kst,))
    con.executemany(
        "INSERT INTO telegram_daily_rollup("
        "date_kst, ticker, name, rank, score, session_count, channel_count, "
        "is_new, has_flow, themes, reason, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                date_kst, g["ticker"], g["name"], i, g["score"],
                len(g["sessions"]), len(g["channels"]),
                1 if g["any_new"] else 0, 1 if g["has_flow"] else 0,
                json.dumps(g["themes"], ensure_ascii=False),
                (g["latest"] or {}).get("change_summary"), now,
            )
            for i, g in enumerate(top, 1)
        ],
    )


def fetch_day(con: sqlite3.Connection, date_kst: str) -> list[dict]:
    """하루 전 세션의 분석완료 insights. analysis JSON 파싱."""
    cur = con.execute(
        "SELECT session, ticker, name, mention_channels, analysis "
        "FROM telegram_stock_insights WHERE date_kst=? AND analysis IS NOT NULL",
        (date_kst,),
    )
    out: list[dict] = []
    for session, ticker, name, mention_channels, analysis in cur:
        try:
            a = json.loads(analysis) if analysis else {}
        except (ValueError, TypeError):
            a = {}
        try:
            channels = json.loads(mention_channels) if mention_channels else []
        except (ValueError, TypeError):
            channels = []
        out.append({
            "session": session, "ticker": ticker, "name": name, "channels": channels,
            "change_type": a.get("change_type"),
            "change_summary": a.get("change_summary"),
            "themes": a.get("themes") or [],
        })
    return out


def aggregate_day(rows: list[dict], sig: dict[str, str]) -> list[dict]:
    """세션별 insights를 종목 단위로 합산·점수화. 점수 내림차순 정렬.

    점수 = 세션수*2 + 채널수 + flow_data(+3) + research_note(+1) + 신규등장(+2).
    # ponytail: 단순 가중합 휴리스틱. 실전 분포 보고 튜닝하면 됨.
    """
    by_ticker: dict[str, dict] = {}
    for r in rows:
        g = by_ticker.get(r["ticker"])
        if g is None:
            g = by_ticker[r["ticker"]] = {
                "ticker": r["ticker"], "name": r["name"],
                "sessions": set(), "channels": set(), "themes": [],
                "any_new": False, "latest": None,
            }
        g["sessions"].add(r["session"])
        g["channels"].update(r["channels"])
        if r["change_type"] == "new":
            g["any_new"] = True
        for th in r["themes"]:
            if th not in g["themes"]:
                g["themes"].append(th)
        cur_rank = _SESSION_RANK.get(r["session"], -1)
        if g["latest"] is None or cur_rank >= _SESSION_RANK.get(g["latest"]["session"], -1):
            g["latest"] = r

    ranked: list[dict] = []
    for g in by_ticker.values():
        types = {sig.get(ch, "") for ch in g["channels"]}
        g["has_flow"] = "flow_data" in types
        has_research = "research_note" in types
        g["score"] = (
            len(g["sessions"]) * 2
            + len(g["channels"])
            + (3 if g["has_flow"] else 0)
            + (1 if has_research else 0)
            + (2 if g["any_new"] else 0)
        )
        ranked.append(g)
    ranked.sort(key=lambda g: (-g["score"], -len(g["channels"])))
    return ranked


def format_rollup(date_kst: str, ranked: list[dict], top_n: int = _DEFAULT_TOP_N) -> str | None:
    """TOP N 추천 메시지. 분석 종목 0이면 None(전송 스킵)."""
    if not ranked:
        return None
    top = ranked[:top_n]
    lines = [f"🌙 오늘의 텔레그램 주목 종목 {date_kst} · TOP {len(top)}", ""]
    for i, g in enumerate(top, 1):
        mark = "🆕" if g["any_new"] else "🔁"
        flow = " 🔥수급" if g["has_flow"] else ""
        themes = " ".join(f"#{t}" for t in g["themes"][:3])
        head = (f"{i}. {mark} {g['name']}({g['ticker']}) "
                f"[{len(g['sessions'])}세션·{len(g['channels'])}채널]{flow} {themes}").rstrip()
        lines.append(head)
        latest = g["latest"]
        if latest and latest["change_summary"]:
            lines.append(f"   {latest['change_summary']}")
    return "\n".join(lines)


def run(date_kst: str, db_path: Path, dry_run: bool, channel: str | None, top_n: int) -> int:
    sig = {ch: cfg.get("signal_type", "") for ch, cfg in load_all_channels().items()}
    with connect_ro(db_path) as con:  # wl_sqlite: query_only + 명시 close
        rows = fetch_day(con, date_kst)
    ranked = aggregate_day(rows, sig)
    msg = format_rollup(date_kst, ranked, top_n)
    if msg is None:
        print(f"[rollup] {date_kst}: 분석 종목 0 → 전송 스킵")
        return 0
    if dry_run:
        print(msg)
        return 0
    with connect_rw(db_path) as con:  # 전송 전 그날 추천 스냅샷 저장(전송 실패와 무관하게 기록)
        persist_rollup(con, date_kst, ranked[:top_n])
    load_dotenv()  # notify는 os.getenv로 웹훅 조회 — 진입점에서 .env 로드 필수
    ok = notify(msg, channel=channel)
    print(f"[rollup] {date_kst}: TOP {min(top_n, len(ranked))} 전송 {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


def _self_check() -> None:
    """seed 데이터로 집계·랭킹·포맷 검증 (LLM/전송 없음)."""
    con = sqlite3.connect(":memory:")
    import telegram_stock_insights as tsi  # scripts/ on path (via _bootstrap)
    tsi.ensure_schema(con)

    def seed(session, ticker, name, channels, ctype, summ, themes):
        tsi.upsert_candidate(con, "2026-07-06", session, ticker, name,
                             mention_channels=channels, source_post_refs=[f"{ticker}/1"],
                             discovery_reason="t")
        tsi.update_analysis(con, "2026-07-06", session, ticker, json.dumps(
            {"change_type": ctype, "change_summary": summ, "themes": themes},
            ensure_ascii=False))

    # A: 3세션·수급(flow_data)·신규 → 최고점
    seed("morning", "083450", "GST", ["awake_realtimeCheck"], "new", "수급 유입", ["반도체"])
    seed("close", "083450", "GST", ["awake_realtimeCheck", "butler_works"], "continued", "리포트 추가", ["반도체", "장비"])
    seed("evening", "083450", "GST", ["corevalue"], "continued", "저녁 속보", ["반도체"])
    # B: 1세션·리포트만 → 저점
    seed("close", "000660", "SK하이닉스", ["butler_works"], "continued", "리포트 발간", ["반도체"])

    rows = fetch_day(con, "2026-07-06")
    sig = {ch: cfg.get("signal_type", "") for ch, cfg in load_all_channels().items()}
    ranked = aggregate_day(rows, sig)
    assert len(ranked) == 2, ranked
    gst, sk = ranked[0], ranked[1]
    assert gst["ticker"] == "083450", "GST가 1위여야"
    assert gst["score"] > sk["score"], (gst["score"], sk["score"])
    assert gst["sessions"] == {"morning", "close", "evening"}
    assert gst["channels"] == {"awake_realtimeCheck", "butler_works", "corevalue"}
    assert gst["has_flow"] is True and sk["has_flow"] is False
    assert gst["latest"]["change_summary"] == "저녁 속보", "최신 세션(evening) 요약"

    msg = format_rollup("2026-07-06", ranked)
    assert msg is not None
    assert "🌙 오늘의 텔레그램 주목 종목" in msg
    assert "1. 🆕 GST(083450) [3세션·3채널] 🔥수급" in msg
    assert msg.index("GST") < msg.index("SK하이닉스"), "GST가 먼저"
    assert format_rollup("2026-07-06", []) is None

    # persist: 저장 후 읽기, 멱등(재실행 시 전체 교체) 검증
    persist_rollup(con, "2026-07-06", ranked)
    got = con.execute(
        "SELECT rank, ticker, score, session_count, has_flow, reason "
        "FROM telegram_daily_rollup WHERE date_kst=? ORDER BY rank", ("2026-07-06",)
    ).fetchall()
    assert len(got) == 2, got
    assert got[0][:2] == (1, "083450") and got[0][4] == 1, got[0]  # GST rank1, has_flow
    assert got[0][5] == "저녁 속보", got[0]  # reason = 최신 세션 요약
    persist_rollup(con, "2026-07-06", ranked)  # 재실행
    n = con.execute("SELECT COUNT(*) FROM telegram_daily_rollup WHERE date_kst=?", ("2026-07-06",)).fetchone()[0]
    assert n == 2, f"멱등 실패, 중복 적재: {n}"
    print(msg)
    print("\nself-check PASS")


def main() -> int:
    p = argparse.ArgumentParser(description="텔레그램 하루 1회 롤업 (오늘 주목 종목 TOP N)")
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--date", help="KST 일자 YYYY-MM-DD")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--channel", help="notify 채널 override (discord/telegram/…)")
    p.add_argument("--top-n", type=int, default=_DEFAULT_TOP_N)
    p.add_argument("--dry-run", action="store_true", help="전송 없이 메시지만 출력")
    args = p.parse_args()

    if args.self_check:
        _self_check()
        return 0
    if not args.date:
        p.error("--date 필요 (또는 --self-check)")
    return run(args.date, args.db, args.dry_run, args.channel, args.top_n)


if __name__ == "__main__":
    raise SystemExit(main())
