"""텔레그램 크로스채널 종목 요약 전송.

`telegram_stock_insights`(discover→analyze 파이프라인 결과)를 (date, session)로 읽어
사람이 읽는 요약 메시지로 만들고 notify()로 전송. discover/analyze는 별도 스크립트,
이 스크립트는 읽기+포맷+전송만(플랜: 수집·요약·전송 분리).

Usage (from etl/):
    uv run python scripts/send_telegram_stock_digest.py --date 2026-07-06 --session close
    uv run python scripts/send_telegram_stock_digest.py --date 2026-07-06 --session close --dry-run
    uv run python scripts/send_telegram_stock_digest.py --self-check
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path 보장)

from dotenv import load_dotenv  # noqa: E402
from notify import notify  # noqa: E402
from telegram_channels import load_all_channels  # noqa: E402
from telegram_session_highlights import fetch_session_highlights  # noqa: E402
from wl_sqlite import connect_ro  # noqa: E402

DEFAULT_DB = Path(__file__).resolve().parents[1] / "db" / "telegram_public.sqlite3"

_CHANGE_LABEL = {"new": "🆕", "continued": "🔁"}
# flow_data/research_note가 하나라도 붙으면 '주목', 아니면 '리포트/뉴스'
_WATCH_TYPES = {"flow_data", "research_note"}


def fetch_insights(con: sqlite3.Connection, date_kst: str, session: str) -> list[dict]:
    """(date, session)의 분석완료 insights. analysis JSON 파싱해서 dict로."""
    cur = con.execute(
        "SELECT ticker, name, mention_channels, analysis FROM telegram_stock_insights "
        "WHERE date_kst=? AND session=? AND analysis IS NOT NULL",
        (date_kst, session),
    )
    out: list[dict] = []
    for ticker, name, mention_channels, analysis in cur:
        try:
            a = json.loads(analysis) if analysis else {}
        except (ValueError, TypeError):
            a = {}
        try:
            channels = json.loads(mention_channels) if mention_channels else []
        except (ValueError, TypeError):
            channels = []
        out.append({
            "ticker": ticker, "name": name, "channels": channels,
            "change_type": a.get("change_type"),
            "change_summary": a.get("change_summary"),
            "themes": a.get("themes") or [],
        })
    return out


# 항목 기호는 뒤에 공백이 있을 때만 뗀다. 그냥 벗기면 `-10% 하락` 의 부호까지 뜯겨
# 하락이 상승으로 읽힌다.
_LIST_MARKER = re.compile(r"^[-•·]\s+")


def _summary_lines(summary: str) -> list[str]:
    """LLM 이 줄바꿈으로 끊어 준 개조식 항목을 들여쓴 줄로 편다.

    한 문단으로 오는 과거 데이터도 그대로 한 줄이 된다 — 기계적으로 쪼개지 않는다.
    """
    out = []
    for line in summary.splitlines():
        text = _LIST_MARKER.sub("", line.strip())
        if text:
            out.append(f"  - {text}")
    return out


def _section_lines(rows: list[dict]) -> list[str]:
    """섹션 내 종목 줄. 신규 먼저, 채널 수 많은 순."""
    order = {"new": 0, "continued": 1}
    rows = sorted(rows, key=lambda r: (order.get(r["change_type"], 2), -len(r["channels"])))
    lines: list[str] = []
    for r in rows:
        mark = _CHANGE_LABEL.get(r["change_type"], "·")
        themes = " ".join(f"#{t}" for t in r["themes"][:3])
        head = f"• {mark} {r['name']}({r['ticker']}) [{len(r['channels'])}채널]"
        if themes:
            head += f" {themes}"
        lines.append(head)
        if r["change_summary"]:
            lines.append(f"  {r['change_summary']}")
    return lines


def format_digest(
    date_kst: str,
    session: str,
    rows: list[dict],
    highlights: list[dict] | None = None,
) -> str | None:
    """개괄 하이라이트와 기존 종목 분석을 한 메시지로 합친다."""
    highlights = highlights or []
    if not rows and not highlights:
        return None

    lines: list[str] = []
    if highlights:
        lines += [f"🧭 텔레그램 세션 개괄 {date_kst} ({session})", "", f"🔥 중요 내용 ({len(highlights)})"]
        for item in highlights:
            lines.append(f"• [{item['score_total']}점] {item['title']}")
            lines += _summary_lines(item["summary"])
            lines.append(f"  가치: {item['importance_reason']}")
        lines += ["", "※ 정보가치 점수이며 사실 확정도·수익률 전망이 아님"]

    if rows:
        if lines:
            lines.append("")
        lines += [f"📊 종목 요약 · {len(rows)}종목", ""]
        sig = {ch: cfg.get("signal_type", "") for ch, cfg in load_all_channels().items()}
        watch = [r for r in rows if any(sig.get(ch) in _WATCH_TYPES for ch in r["channels"])]
        report = [r for r in rows if r not in watch]
        if watch:
            lines.append(f"🔥 주목 ({len(watch)})")
            lines += _section_lines(watch)
        if report:
            if watch:
                lines.append("")
            lines.append(f"📄 리포트/뉴스 ({len(report)})")
            lines += _section_lines(report)
    return "\n".join(lines)


def run(date_kst: str, session: str, db_path: Path, dry_run: bool, channel: str | None) -> int:
    with connect_ro(db_path) as con:  # wl_sqlite: query_only + 명시 close (sqlite `with`는 close 안 함)
        rows = fetch_insights(con, date_kst, session)
        highlights = fetch_session_highlights(con, date_kst, session)
    msg = format_digest(date_kst, session, rows, highlights)
    if msg is None:
        print(f"[digest] {date_kst} {session}: 중요 내용·분석 종목 0 → 전송 스킵")
        return 0
    if dry_run:
        print(msg)
        return 0
    load_dotenv()  # notify는 os.getenv로 웹훅 조회 — 진입점에서 .env 로드 필수(안 하면 조용히 스킵)
    ok = notify(msg, channel=channel)
    print(f"[digest] {date_kst} {session}: {len(rows)}종목 전송 {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


def _self_check() -> None:
    """seed 데이터로 포맷 검증 (LLM/전송 없음)."""
    con = sqlite3.connect(":memory:")
    import telegram_stock_insights as tsi  # scripts/ on path (via _bootstrap)
    tsi.ensure_schema(con)
    # 삼성전자: flow_data(awake) 포함 → 주목 섹션
    tsi.upsert_candidate(con, "2026-07-06", "close", "005930", "삼성전자",
                         mention_channels=["awake_realtimeCheck", "butler_works"],
                         source_post_refs=["a/1"], discovery_reason="수급+리포트")
    tsi.update_analysis(con, "2026-07-06", "close", "005930", json.dumps(
        {"change_type": "new", "change_summary": "HBM 수요 언급 급증",
         "themes": ["반도체", "HBM"], "evidence_summary": "e"}, ensure_ascii=False))
    # SK하이닉스: report_feed만 → 리포트 섹션
    tsi.upsert_candidate(con, "2026-07-06", "close", "000660", "SK하이닉스",
                         mention_channels=["butler_works"], source_post_refs=["a/2"],
                         discovery_reason="리포트 발간")
    tsi.update_analysis(con, "2026-07-06", "close", "000660", json.dumps(
        {"change_type": "continued", "change_summary": "지속 관심", "themes": ["반도체"]},
        ensure_ascii=False))

    rows = fetch_insights(con, "2026-07-06", "close")
    assert len(rows) == 2, rows
    msg = format_digest("2026-07-06", "close", rows)
    assert msg is not None
    assert "삼성전자(005930)" in msg and "[2채널]" in msg
    assert "🔥 주목" in msg and "📄 리포트/뉴스" in msg
    assert msg.index("🔥 주목") < msg.index("📄 리포트/뉴스"), "주목이 먼저"
    # 삼성전자(주목) 가 SK(리포트)보다 앞
    assert msg.index("삼성전자") < msg.index("SK하이닉스"), "주목 종목이 리포트보다 앞"
    assert "🆕" in msg and "#반도체" in msg
    # 분석 0 → None
    assert format_digest("2026-07-06", "close", []) is None
    print(msg)
    print("\nself-check PASS")


def main() -> int:
    p = argparse.ArgumentParser(description="텔레그램 크로스채널 종목 요약 전송")
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--date", help="KST 일자 YYYY-MM-DD")
    p.add_argument("--session", default="close", help="morning|close|evening")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--channel", help="notify 채널 override (discord/telegram/…)")
    p.add_argument("--dry-run", action="store_true", help="전송 없이 메시지만 출력")
    args = p.parse_args()

    if args.self_check:
        _self_check()
        return 0
    if not args.date:
        p.error("--date 필요 (또는 --self-check)")
    return run(args.date, args.session, args.db, args.dry_run, args.channel)


if __name__ == "__main__":
    raise SystemExit(main())
