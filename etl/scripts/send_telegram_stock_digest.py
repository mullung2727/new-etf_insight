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
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 가드

try:
    from scripts.notify import notify
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from notify import notify

DEFAULT_DB = Path(__file__).resolve().parents[1] / "db" / "telegram_public.sqlite3"

_CHANGE_LABEL = {"new": "🆕 신규", "continued": "🔁 지속"}


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


def format_digest(date_kst: str, session: str, rows: list[dict]) -> str | None:
    """요약 메시지. 분석 종목 0이면 None(전송 스킵)."""
    if not rows:
        return None
    lines = [f"📊 텔레그램 종목 요약 {date_kst} ({session}) · {len(rows)}종목", ""]
    # 신규 먼저, 그다음 지속, 그 외
    order = {"new": 0, "continued": 1}
    rows = sorted(rows, key=lambda r: (order.get(r["change_type"], 2), -len(r["channels"])))
    cur_type = object()
    for r in rows:
        if r["change_type"] != cur_type:
            cur_type = r["change_type"]
            lines.append(_CHANGE_LABEL.get(cur_type, "· 기타"))
        themes = " ".join(f"#{t}" for t in r["themes"][:3])
        head = f"• {r['name']}({r['ticker']}) [{len(r['channels'])}채널]"
        if themes:
            head += f" {themes}"
        lines.append(head)
        if r["change_summary"]:
            lines.append(f"  {r['change_summary']}")
    return "\n".join(lines)


def run(date_kst: str, session: str, db_path: Path, dry_run: bool, channel: str | None) -> int:
    with sqlite3.connect(str(db_path)) as con:
        con.execute("PRAGMA query_only=ON")
        rows = fetch_insights(con, date_kst, session)
    msg = format_digest(date_kst, session, rows)
    if msg is None:
        print(f"[digest] {date_kst} {session}: 분석 종목 0 → 전송 스킵")
        return 0
    if dry_run:
        print(msg)
        return 0
    ok = notify(msg, channel=channel)
    print(f"[digest] {date_kst} {session}: {len(rows)}종목 전송 {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


def _self_check() -> None:
    """seed 데이터로 포맷 검증 (LLM/전송 없음)."""
    con = sqlite3.connect(":memory:")
    tsi = _import_tsi()
    tsi.ensure_schema(con)
    tsi.upsert_candidate(con, "2026-07-06", "close", "005930", "삼성전자",
                         mention_channels=["a", "b", "c"], source_post_refs=["a/1"],
                         discovery_reason="다채널 동시언급")
    tsi.update_analysis(con, "2026-07-06", "close", "005930", json.dumps(
        {"change_type": "new", "change_summary": "HBM 수요 언급 급증",
         "themes": ["반도체", "HBM"], "evidence_summary": "e"}, ensure_ascii=False))
    tsi.upsert_candidate(con, "2026-07-06", "close", "000660", "SK하이닉스",
                         mention_channels=["a"], source_post_refs=["a/2"], discovery_reason="언급")
    tsi.update_analysis(con, "2026-07-06", "close", "000660", json.dumps(
        {"change_type": "continued", "change_summary": "지속 관심", "themes": ["반도체"]},
        ensure_ascii=False))

    rows = fetch_insights(con, "2026-07-06", "close")
    assert len(rows) == 2, rows
    msg = format_digest("2026-07-06", "close", rows)
    assert msg is not None
    assert "삼성전자(005930)" in msg and "[3채널]" in msg
    assert "🆕 신규" in msg and "🔁 지속" in msg
    assert msg.index("🆕 신규") < msg.index("🔁 지속"), "신규가 먼저 와야"
    assert "#반도체" in msg
    # 분석 0 → None
    assert format_digest("2026-07-06", "close", []) is None
    print(msg)
    print("\nself-check PASS")


def _import_tsi():
    try:
        from scripts import telegram_stock_insights as tsi
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import telegram_stock_insights as tsi
    return tsi


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
