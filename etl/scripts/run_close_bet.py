"""종가배팅 주문 배치 (15:19 실행).

llm_scores에서 score >= score_threshold 종목을 score 1순위·시총(krx_ohlcv) 2순위로
상위 3개 선별해, 총 500만원을 종목 수별 예산(1→300만/2→200만/3→167만)으로 나눠
broker REST API 시장가 매수(qty = 예산 // 현재가)하고 결과를 기록한다.

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

import duckdb
import requests
from dotenv import load_dotenv

try:  # 직접 실행(scripts/ on path) / 패키지 import(tests) 양쪽 지원
    from scripts.notify import send_discord
    from scripts.run_verify import (
        fetch_order_history,
        mark_confirmed,
        normalize_order_no,
    )
    from scripts.wl_sqlite import connect_ro, connect_rw
except ImportError:
    from notify import send_discord
    from run_verify import fetch_order_history, mark_confirmed, normalize_order_no
    from wl_sqlite import connect_ro, connect_rw

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"
DEFAULT_KRX_DB = Path(__file__).resolve().parents[1] / "db" / "krx_ohlcv.duckdb"

REQUEST_TIMEOUT = 15

# 총 500만원을 score 상위 N개에 분할 매수(예산은 budget_for).
_TOP_N = 3


def normalize_date_key(value: str | None = None) -> str:
    """DB/log key date. Accepts YYYYMMDD or YYYY-MM-DD; defaults to today KST."""
    if not value:
        return _now_seoul().strftime("%Y%m%d")
    compact = value.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError(f"date must be YYYYMMDD or YYYY-MM-DD: {value}")
    return compact


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
    ("pnl_pct", "REAL"),        # net 손익율(키움 ka10077 pl_rt). 폴백 시 gross.
    ("sell_cmsn", "INTEGER"),   # 당일매매수수료(원, ka10077)
    ("sell_tax", "INTEGER"),    # 당일매매세금(원, ka10077)
    ("sell_pl_won", "INTEGER"), # net 실현손익(원, ka10077 tdy_sel_pl)
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
) -> list[dict]:
    """score >= threshold 종목 전체를 score DESC로 반환(상위 N cut은 rank_and_cut 책임).

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
            """, [date, score_threshold]).fetchall()
        else:
            rows = con.execute("""
                SELECT ticker, score, name, close
                FROM llm_scores
                WHERE date = ? AND score >= ?
                ORDER BY score DESC
            """, [date, score_threshold]).fetchall()
    return [
        {"ticker": r[0], "score": r[1], "name": r[2], "close": r[3]}
        for r in rows
    ]


def load_score_rows(watchlist_db: Path, date: str) -> list[dict]:
    """Return all llm_scores rows for date, ordered by score desc."""
    with connect_ro(watchlist_db) as con:
        rows = con.execute(
            """
            SELECT ticker, score, name, close
            FROM llm_scores
            WHERE date = ?
            ORDER BY score DESC
            """,
            [date],
        ).fetchall()
    return [
        {"ticker": r[0], "score": r[1], "name": r[2], "close": r[3]}
        for r in rows
    ]


def load_close_bet_order_rows(watchlist_db: Path, date: str) -> list[dict]:
    """Return close_bet_orders rows for date. Missing table means no rows."""
    with connect_ro(watchlist_db) as con:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='close_bet_orders'"
        ).fetchall()}
        if "close_bet_orders" not in tables:
            return []
        rows = con.execute(
            """
            SELECT ticker, score, qty, status, order_no, message, created_at
            FROM close_bet_orders
            WHERE date = ?
            ORDER BY score DESC, ticker
            """,
            [date],
        ).fetchall()
    return [
        {
            "ticker": r[0],
            "score": r[1],
            "qty": r[2],
            "status": r[3],
            "order_no": r[4],
            "message": r[5],
            "created_at": r[6],
        }
        for r in rows
    ]


def load_close_bet_status(
    watchlist_db: Path,
    date: str,
    *,
    score_threshold: int = 70,
    log_dir: Path | None = None,
) -> dict:
    """Shared status snapshot for order/report paths. No ad hoc SQL in reports."""
    date_key = normalize_date_key(date)
    score_rows = load_score_rows(watchlist_db, date_key)
    threshold_rows = [row for row in score_rows if (row["score"] or 0) >= score_threshold]
    order_rows = load_close_bet_order_rows(watchlist_db, date_key)
    log_path = (log_dir or (Path(__file__).resolve().parents[1] / "logs")) / f"close-bet-{date_key}.log"
    if not order_rows and threshold_rows:
        final_judgment = "failed before order"
    elif order_rows:
        failed_or_skipped = [row for row in order_rows if row["status"] in ("failed", "skipped")]
        final_judgment = "completed" if not failed_or_skipped else "completed with failures"
    else:
        final_judgment = "no targets"
    return {
        "date_key": date_key,
        "score_threshold": score_threshold,
        "score_count": len(score_rows),
        "threshold_count": len(threshold_rows),
        "threshold_rows": threshold_rows,
        "order_count": len(order_rows),
        "order_rows": order_rows,
        "log_path": str(log_path),
        "log_exists": log_path.exists(),
        "final_judgment": final_judgment,
    }


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


# ── 예산 배분 / 수량 환산 ──────────────────────────────────────────────────────

# 총 500만원. 선정 종목 수 N별 종목당 예산(1→300만, 2→200만, 3→500만÷3 floor).
_BUDGET_BY_COUNT = {1: 3_000_000, 2: 2_000_000, 3: 5_000_000 // 3}


def budget_for(n: int) -> int:
    """선정 종목 수 N별 종목당 예산(원). 정의역 밖(N∉{1,2,3})은 0."""
    return _BUDGET_BY_COUNT.get(n, 0)


def qty_from_budget(budget: int, price: int) -> int:
    """예산 ÷ 현재가 floor. price<=0이면 0(잔여현금은 버림)."""
    if price <= 0:
        return 0
    return budget // price


def fetch_market_caps(krx_db: Path, tickers: list[str], date: str) -> dict[str, int]:
    """각 ticker의 date 이하 최신 거래일 market_cap. 없는 ticker는 키 부재."""
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    with duckdb.connect(str(krx_db), read_only=True) as con:
        rows = con.execute(
            f"""
            SELECT ticker, market_cap
            FROM ohlcv
            WHERE date = (
                SELECT MAX(date) FROM ohlcv o2
                WHERE o2.ticker = ohlcv.ticker AND o2.date <= ?
            ) AND ticker IN ({placeholders})
            """,
            [date, *tickers],
        ).fetchall()
    return {r[0]: r[1] for r in rows if r[1] is not None}


def rank_and_cut(candidates: list[dict], caps: dict[str, int], n: int = 3) -> list[dict]:
    """(score DESC, market_cap DESC)로 정렬해 상위 n개. 시총 없는 종목은 0 취급."""
    return sorted(
        candidates,
        key=lambda c: (c["score"], caps.get(c["ticker"], 0)),
        reverse=True,
    )[:n]


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


def fetch_available_cash_via_broker(broker_url: str) -> int | None:
    """GET {broker_url}/account/deposit → ord_alow_amt(주문가능금액, 원). 실패 시 None."""
    try:
        resp = requests.get(f"{broker_url}/account/deposit", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("ord_alow_amt")
        if raw is None:
            return None
        return int(raw)  # 0-padding·부호 포함 문자열 → int 안전
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


# ── 체결 확정 (매수 직후 kt00007 폴링) ───────────────────────────────────────────

def confirm_fills(
    watchlist_db: Path,
    broker_url: str,
    date: str,
    pending: dict[str, str],
    *,
    max_attempts: int = 5,
    interval: float = 1.0,
    now: datetime | None = None,
    sleep=time.sleep,
) -> dict[str, str]:
    """매수 직후 kt00007(GET /orders/history) 폴링으로 cntr_price를 즉시 확정한다.

    시장가는 보통 즉시 체결되므로 매수 종료 직후 짧게 폴링해 체결가를 채워
    status='confirmed'로 만든다(별도 16:00 체결대조 배치 의존 제거).
    `pending`: {ticker: order_no}. 반환: 폴링 안에 확정 못 한 {ticker: order_no}.
    미확정분은 submitted로 남아 16:00 run_verify가 백업 대조한다.
    """
    remaining = dict(pending)
    for attempt in range(max_attempts):
        history = fetch_order_history(broker_url, date)
        if history:
            by_no: dict[str, dict] = {}
            for item in history:
                key = normalize_order_no(item.get("order_no"))
                if not key:
                    continue
                agg = by_no.setdefault(key, {"cntr_qty": 0, "cntr_uv": 0})
                agg["cntr_qty"] += int(item.get("cntr_qty") or 0)
                if not agg["cntr_uv"]:
                    agg["cntr_uv"] = int(item.get("cntr_uv") or 0)
            for ticker, order_no in list(remaining.items()):
                match = by_no.get(normalize_order_no(order_no))
                if match and match["cntr_qty"] > 0:
                    mark_confirmed(
                        watchlist_db, date, ticker,
                        match["cntr_uv"], match["cntr_qty"], now or _now_seoul(),
                    )
                    del remaining[ticker]
        if not remaining:
            break
        if attempt < max_attempts - 1:
            sleep(interval)
    return remaining


# ── main ──────────────────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="종가배팅 주문 배치")
    parser.add_argument("--date", help="대상 날짜 YYYYMMDD; 기본: 오늘(Asia/Seoul)")
    parser.add_argument("--score-threshold", type=int, default=70)
    parser.add_argument("--order-time", default="15:19:00")
    parser.add_argument("--order-deadline-time", default="15:20:00")
    parser.add_argument("--allow-order-outside-close-window", action="store_true")
    parser.add_argument("--dry-run", default="true")
    parser.add_argument("--broker-url", default=None)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def main() -> None:
    load_dotenv(ENV_PATH)

    args = parse_args()

    dry_run = args.dry_run.lower() not in ("false", "0", "no")
    date = normalize_date_key(args.date)
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

    pool = load_order_candidates(watchlist_db, date, args.score_threshold)
    if not pool:
        print(f"[close_bet] 주문 대상 없음")
        send_discord(f"[종가베팅] {date} 주문 대상 없음{dry_tag}\nscore>={args.score_threshold} 종목 0건")
        return

    # score 1순위·시총 2순위로 상위 N개. 시총은 krx_ohlcv(별 DuckDB) → Python에서 동점깸.
    caps = fetch_market_caps(DEFAULT_KRX_DB, [c["ticker"] for c in pool], date) if DEFAULT_KRX_DB.exists() else {}
    candidates = rank_and_cut(pool, caps, n=_TOP_N)
    n_sel = len(candidates)
    if n_sel == 0:
        print(f"[close_bet] 주문 대상 없음 (선정 0건)")
        send_discord(f"[종가베팅] {date} 주문 대상 없음{dry_tag}\n선정 0건")
        return

    # 주문가능금액(예수금) 조회 → 가용을 종목수로 나눈 몫과 전략 예산 중 작은 값으로 cap.
    # 계좌에 돈이 적으면 실제 현금에 맞춰 주문해 증거금 부족 거부를 막는다.
    cash = fetch_available_cash_via_broker(broker_url)
    if cash is None:
        print(f"[close_bet] ABORT: 주문가능금액(예수금) 조회 실패")
        send_discord(f"[종가베팅] {date} 주문 ABORT{dry_tag}\n주문가능금액(예수금) 조회 실패")
        sys.exit(1)
    strat_budget = budget_for(n_sel)
    budget = min(strat_budget, cash // n_sel)
    print(f"[close_bet] 후보 {len(pool)}건 → 선정 {n_sel}건 (threshold={args.score_threshold}), "
          f"가용 {cash:,}원, 전략 종목당 {strat_budget:,}원 → 종목당 예산 {budget:,}원")

    submitted = skipped = failed = 0
    fill_pending: dict[str, str] = {}  # ticker → order_no (체결확정 대기)
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
                "qty": 0, "order_type": "market",
                "status": "skipped", "order_no": "",
                "message": "현재가 조회 실패", "raw": "{}",
            })
            skipped += 1
            report_lines.append(f"⏭ {label} score={score} skip(현재가 조회 실패)")
            continue

        qty = qty_from_budget(budget, cur_prc)
        if qty == 0:
            print(f"[close_bet] {ticker}: 예산<현재가({budget:,}<{cur_prc:,}) — skip")
            upsert_order_result(watchlist_db, {
                "date": date, "ticker": ticker, "score": score,
                "qty": 0, "order_type": "market",
                "status": "skipped", "order_no": "",
                "message": f"예산<현재가({budget}<{cur_prc})", "raw": "{}",
            })
            skipped += 1
            report_lines.append(f"⏭ {label} score={score} skip(예산<현재가)")
            continue

        result = place_order_via_broker(broker_url, ticker, qty, dry_run)
        upsert_order_result(watchlist_db, {
            "date": date, "ticker": ticker, "score": score,
            "qty": qty, "order_type": "market",
            "status": result["status"], "order_no": result["order_no"],
            "message": result["message"], "raw": json.dumps(result),
        })
        # 거래 원장(kiwoom_trade_history)은 broker가 기록한다 (POST /orders 시).
        print(f"[close_bet] {ticker} score={score} cur={cur_prc} qty={qty}(예산 {budget:,}) → {result['status']} ord={result['order_no']}")

        if result["status"] in ("submitted", "dry_run"):
            submitted += 1
            if result["status"] == "submitted" and result["order_no"]:
                fill_pending[ticker] = result["order_no"]
            report_lines.append(f"✅ {label} score={score} {qty}주 {result['status']} #{result['order_no']}")
        else:
            failed += 1
            report_lines.append(f"❌ {label} score={score} 실패: {result['message']}")

        time.sleep(float(os.getenv("ORDER_INTERVAL_SEC", "0.5")))

    # 매수 직후 체결확정 — kt00007 폴링으로 cntr_price 즉시 채움(별도 16:00 배치 의존 제거)
    if fill_pending:
        remaining = confirm_fills(watchlist_db, broker_url, date, fill_pending)
        n_conf = len(fill_pending) - len(remaining)
        print(f"[close_bet] 체결확정: {n_conf}/{len(fill_pending)} cntr_price 즉시 기록")
        report_lines.append(
            f"📥 체결확정 {n_conf}/{len(fill_pending)}"
            + (f" (미확정 {len(remaining)}: {', '.join(remaining)} → 16:00 대조 백업)" if remaining else "")
        )
        if remaining:
            print(f"[close_bet] 미확정 {list(remaining)} — submitted 유지, 16:00 체결대조 백업")

    print(f"[close_bet] 완료: submitted={submitted} skipped={skipped} failed={failed}")
    summary = (
        f"[종가베팅] {date} 주문 결과{dry_tag}\n"
        f"대상 {len(candidates)} | 매수 {submitted} | 스킵 {skipped} | 실패 {failed}\n"
        + "\n".join(report_lines)
    )
    send_discord(summary)


if __name__ == "__main__":
    main()
