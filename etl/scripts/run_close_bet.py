"""종가배팅 주문 배치 (15:19 실행).

llm_scores에서 score >= score_threshold 종목을 score 내림차순 / max_order_count 제한으로
선별해 broker REST API를 통해 시장가 1주 매수하고 결과를 기록한다.

핵심 사상: Kiwoom API는 무조건 broker를 통해서만 호출한다.

전제:
  - 15:10 scoring 배치(build_intraday_ranking + run_watchlist_research)가 먼저 돌아야 함.
  - broker(http://localhost:8001)가 반드시 기동되어 있어야 함.
  - 기본: dry_run=True.

Usage (from etl/):
    .venv/Scripts/python.exe scripts/run_close_bet.py --date 20260615
    .venv/Scripts/python.exe scripts/run_close_bet.py --dry-run false
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sqlite3

import requests
from dotenv import load_dotenv

try:  # 직접 실행(scripts/ on path) / 패키지 import(tests) 양쪽 지원
    from scripts.notify import send_discord
    from scripts.wl_sqlite import connect_ro, connect_rw
except ImportError:
    from notify import send_discord
    from wl_sqlite import connect_ro, connect_rw

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"

REQUEST_TIMEOUT = 15


# ── DDL ──────────────────────────────────────────────────────────────────────

def create_close_bet_orders_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS close_bet_orders (
            date        TEXT,
            ticker      TEXT,
            score       INTEGER,
            qty         INTEGER,
            order_type  TEXT,
            status      TEXT,
            order_no    TEXT,
            message     TEXT,
            raw         TEXT,
            created_at  TEXT,
            cntr_price  INTEGER,
            cntr_qty    INTEGER,
            verified_at TEXT,
            PRIMARY KEY (date, ticker)
        )
    """)


# 종가베팅 청산(익절/손절) 워커가 쓰는 추가 컬럼. 기존행은 ALTER 후 NULL.
_EXIT_COLUMNS = [
    ("sell_order_no", "TEXT"),
    ("sell_status", "TEXT"),    # ordered → filled (NULL=미청산)
    ("sell_price", "INTEGER"),
    ("sell_qty", "INTEGER"),
    ("sold_at", "TEXT"),
    ("exit_reason", "TEXT"),    # tp / sl / forced
    ("pnl_pct", "REAL"),
]


def ensure_exit_columns(con: sqlite3.Connection) -> None:
    """close_bet_orders에 청산용 컬럼을 멱등 추가. 두 번 호출해도 무동작."""
    create_close_bet_orders_table(con)
    existing = {r[1] for r in con.execute("PRAGMA table_info(close_bet_orders)")}
    for col, col_type in _EXIT_COLUMNS:
        if col not in existing:
            con.execute(f"ALTER TABLE close_bet_orders ADD COLUMN {col} {col_type}")


# ── DB 조회/저장 ──────────────────────────────────────────────────────────────

def check_precondition(watchlist_db: Path, date: str) -> int:
    """오늘 llm_scores 행 수 반환. 0이면 scoring 배치 미실행."""
    with connect_ro(watchlist_db) as con:
        return con.execute(
            "SELECT COUNT(*) FROM llm_scores WHERE date=?", [date]
        ).fetchone()[0]


def load_order_candidates(
    watchlist_db: Path,
    date: str,
    score_threshold: int,
    max_order_count: int,
) -> list[dict]:
    """score >= threshold 종목을 score DESC / max_order_count 제한으로 반환.

    이미 close_bet_orders에 (date, ticker) 행이 있는 종목은 제외한다.
    """
    with connect_ro(watchlist_db) as con:
        # close_bet_orders가 없을 수도 있으므로 LEFT JOIN 전에 테이블 존재 확인
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='close_bet_orders'"
        ).fetchall()}
        if "close_bet_orders" in tables:
            rows = con.execute("""
                SELECT l.ticker, l.score, l.name, l.close
                FROM llm_scores l
                LEFT JOIN close_bet_orders c
                    ON l.date = c.date AND l.ticker = c.ticker
                WHERE l.date = ?
                  AND l.score >= ?
                  AND c.ticker IS NULL
                ORDER BY l.score DESC
                LIMIT ?
            """, [date, score_threshold, max_order_count]).fetchall()
        else:
            rows = con.execute("""
                SELECT ticker, score, name, close
                FROM llm_scores
                WHERE date = ? AND score >= ?
                ORDER BY score DESC
                LIMIT ?
            """, [date, score_threshold, max_order_count]).fetchall()
    return [
        {"ticker": r[0], "score": r[1], "name": r[2], "close": r[3]}
        for r in rows
    ]


def upsert_order_result(watchlist_db: Path, row: dict) -> None:
    """close_bet_orders에 주문 결과 삽입. (date, ticker) PK 중복 시 기존 행 유지."""
    with connect_rw(watchlist_db) as con:
        create_close_bet_orders_table(con)
        existing = con.execute(
            "SELECT COUNT(*) FROM close_bet_orders WHERE date=? AND ticker=?",
            [row["date"], row["ticker"]],
        ).fetchone()[0]
        if existing:
            return
        con.execute(
            """INSERT INTO close_bet_orders
               (date, ticker, score, qty, order_type, status, order_no, message, raw, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            [
                row["date"], row["ticker"], row["score"], row["qty"],
                row["order_type"], row["status"], row["order_no"],
                row["message"], row["raw"],
            ],
        )


# ── 시각 유틸 ─────────────────────────────────────────────────────────────────

def _now_seoul() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))


def _parse_hms(hms: str) -> tuple[int, int, int]:
    h, m, s = (int(x) for x in hms.split(":"))
    return h, m, s


def _is_in_order_window(order_time: str, order_deadline_time: str, allow_outside: bool) -> bool:
    if allow_outside:
        return True
    now = _now_seoul()
    h1, m1, s1 = _parse_hms(order_time)
    h2, m2, s2 = _parse_hms(order_deadline_time)
    start = now.replace(hour=h1, minute=m1, second=s1, microsecond=0)
    end   = now.replace(hour=h2, minute=m2, second=s2, microsecond=0)
    return start <= now < end


# ── broker REST API 경유 함수 ─────────────────────────────────────────────────

def fetch_price_via_broker(broker_url: str, ticker: str) -> int | None:
    """GET {broker_url}/quotes/{ticker} → price. 실패 시 None."""
    try:
        resp = requests.get(f"{broker_url}/quotes/{ticker}", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        price = resp.json().get("price")
        if price is None:
            return None
        return int(price)
    except Exception:
        return None


def place_order_via_broker(broker_url: str, ticker: str, qty: int, dry_run: bool) -> dict:
    """POST {broker_url}/orders 시장가 매수. dry_run=True면 HTTP 호출 없이 반환."""
    if dry_run:
        ts = _now_seoul().strftime("%Y%m%d%H%M%S")
        return {
            "order_no": f"DRY_{ticker}_{ts}",
            "status": "dry_run",
            "message": "dry_run — 실제 주문 없음",
        }
    try:
        resp = requests.post(
            f"{broker_url}/orders",
            json={"symbol": ticker, "side": "buy", "qty": qty,
                  "order_type": "market", "source": "close_bet"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("accepted"):
            return {
                "order_no": "",
                "status": "failed",
                "message": data.get("message", "accepted=False"),
            }
        return {
            "order_no": str(data.get("order_no") or ""),
            "status": "submitted",
            "message": data.get("message", ""),
        }
    except Exception as exc:
        return {"order_no": "", "status": "failed", "message": str(exc)}


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv(ENV_PATH)

    parser = argparse.ArgumentParser(description="종가배팅 주문 배치")
    parser.add_argument("--date", help="대상 날짜 YYYYMMDD; 기본: 오늘(Asia/Seoul)")
    parser.add_argument("--score-threshold", type=int, default=70)
    parser.add_argument("--max-order-count", type=int, default=5)
    parser.add_argument("--qty-per-symbol", type=int, default=1)
    parser.add_argument("--order-time", default="15:19:00")
    parser.add_argument("--order-deadline-time", default="15:20:00")
    parser.add_argument("--allow-order-outside-close-window", action="store_true")
    parser.add_argument("--dry-run", default="true")
    parser.add_argument("--broker-url", default=None)
    args = parser.parse_args()

    dry_run = args.dry_run.lower() not in ("false", "0", "no")
    date = args.date or _now_seoul().strftime("%Y%m%d")
    broker_url = args.broker_url or os.getenv("BROKER_API_URL", "http://localhost:8001")
    watchlist_db = DEFAULT_WATCHLIST_DB

    dry_tag = " (DRY-RUN)" if dry_run else ""

    # precondition
    cnt = check_precondition(watchlist_db, date)
    if cnt == 0:
        print(f"[close_bet] ABORT: {date} llm_scores 없음 — scoring 배치 미실행")
        send_discord(f"[종가베팅] {date} 주문 ABORT{dry_tag}\nllm_scores 없음 (15:10 scoring 배치 미실행)")
        sys.exit(1)
    print(f"[close_bet] precondition OK: llm_scores={cnt}건")

    # 시간창
    if not _is_in_order_window(args.order_time, args.order_deadline_time, args.allow_order_outside_close_window):
        now_str = _now_seoul().strftime("%H:%M:%S")
        print(f"[close_bet] ABORT: 주문 시간창 밖 (now={now_str}, window={args.order_time}~{args.order_deadline_time})")
        send_discord(f"[종가베팅] {date} 주문 ABORT{dry_tag}\n주문 시간창 밖 (now={now_str}, window={args.order_time}~{args.order_deadline_time})")
        sys.exit(1)

    candidates = load_order_candidates(
        watchlist_db, date, args.score_threshold, args.max_order_count
    )
    print(f"[close_bet] 주문 후보: {len(candidates)}건 (threshold={args.score_threshold}, max={args.max_order_count})")

    if not candidates:
        print(f"[close_bet] 주문 대상 없음")
        send_discord(f"[종가베팅] {date} 주문 대상 없음{dry_tag}\nscore>={args.score_threshold} 종목 0건")
        return

    submitted = skipped = failed = 0
    report_lines: list[str] = []
    for cand in candidates:
        if not _is_in_order_window(args.order_time, args.order_deadline_time, args.allow_order_outside_close_window):
            print(f"[close_bet] 마감 시각 초과 — 남은 종목 중단")
            report_lines.append("⏱ 마감 시각 초과 — 남은 종목 중단")
            break

        ticker = cand["ticker"]
        score  = cand["score"]
        label  = f"{cand.get('name') or ticker}({ticker})"

        cur_prc = fetch_price_via_broker(broker_url, ticker)
        if cur_prc is None:
            print(f"[close_bet] {ticker}: 현재가 조회 실패 — skip")
            upsert_order_result(watchlist_db, {
                "date": date, "ticker": ticker, "score": score,
                "qty": args.qty_per_symbol, "order_type": "market",
                "status": "skipped", "order_no": "",
                "message": "현재가 조회 실패", "raw": "{}",
            })
            skipped += 1
            report_lines.append(f"⏭ {label} score={score} skip(현재가 조회 실패)")
            continue

        result = place_order_via_broker(broker_url, ticker, args.qty_per_symbol, dry_run)
        upsert_order_result(watchlist_db, {
            "date": date, "ticker": ticker, "score": score,
            "qty": args.qty_per_symbol, "order_type": "market",
            "status": result["status"], "order_no": result["order_no"],
            "message": result["message"], "raw": json.dumps(result),
        })
        # 거래 원장(kiwoom_trade_history)은 broker가 기록한다 (POST /orders 시).
        print(f"[close_bet] {ticker} score={score} cur={cur_prc} → {result['status']} ord={result['order_no']}")

        if result["status"] in ("submitted", "dry_run"):
            submitted += 1
            report_lines.append(f"✅ {label} score={score} {result['status']} #{result['order_no']}")
        else:
            failed += 1
            report_lines.append(f"❌ {label} score={score} 실패: {result['message']}")

        time.sleep(float(os.getenv("ORDER_INTERVAL_SEC", "0.5")))

    print(f"[close_bet] 완료: submitted={submitted} skipped={skipped} failed={failed}")
    summary = (
        f"[종가베팅] {date} 주문 결과{dry_tag}\n"
        f"대상 {len(candidates)} | 매수 {submitted} | 스킵 {skipped} | 실패 {failed}\n"
        + "\n".join(report_lines)
    )
    send_discord(summary)


if __name__ == "__main__":
    main()
