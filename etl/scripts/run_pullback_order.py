"""lower_low_bullish_reversal 15:19 매수 배치.

watchlist 편입 다음 거래일부터 D+5까지 매일 검사한다. 당일 장중 저가가
직전 거래일 저가보다 낮고 15:19 현재가가 당일 시가보다 높은 첫날을
매수 신호일로 정한다. lower-low가 음봉으로 끝난 날은 직전일 데이터로
편입한 뒤 다음 거래일을 계속 검사한다.

현재 단계는 외부 API나 주문 없이 신호·거래일 계산만 제공한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import duckdb
import requests
from dotenv import load_dotenv

try:
    from scripts.notify import send_discord
    from scripts.pullback_config import load
    from scripts.trading_batch_common import (
        available_cash, current_price, in_order_window, market_order, now_seoul,
        quantity_for_budget, quote_snapshot,
    )
    from scripts.wl_sqlite import connect_ro, connect_rw
except ImportError:
    from notify import send_discord
    from pullback_config import load
    from trading_batch_common import (
        available_cash, current_price, in_order_window, market_order, now_seoul,
        quantity_for_budget, quote_snapshot,
    )
    from wl_sqlite import connect_ro, connect_rw


MAX_WAIT_DAYS = 5
STRATEGY = "lower_low_bullish_reversal"
REQUEST_TIMEOUT = 15
OPEN_PULLBACK_STATUSES = ("submitted", "unconfirmed", "confirmed", "sell_ordered")
OPEN_CLOSE_BET_STATUSES = ("submitted", "unconfirmed", "confirmed")
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"
DEFAULT_KRX_DB = Path(__file__).resolve().parents[1] / "db" / "krx_ohlcv.duckdb"


def create_pullback_orders_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS pullback_orders (
            watchlist_date TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            prior_low INTEGER NOT NULL,
            day_open INTEGER NOT NULL,
            signal_price INTEGER NOT NULL,
            qty INTEGER NOT NULL,
            status TEXT NOT NULL,
            buy_order_no TEXT,
            buy_price INTEGER,
            buy_qty INTEGER,
            bought_at TEXT,
            remaining_hold_days INTEGER,
            last_hold_count_date TEXT,
            expiry_date TEXT,
            sell_order_no TEXT,
            sell_status TEXT,
            sell_price INTEGER,
            sell_qty INTEGER,
            sold_at TEXT,
            exit_reason TEXT,
            pnl_pct REAL,
            note_uid TEXT,
            message TEXT,
            raw TEXT,
            created_at TEXT NOT NULL,
            verified_at TEXT,
            PRIMARY KEY (watchlist_date, ticker)
        )
    """)
    columns = {row[1] for row in con.execute("PRAGMA table_info(pullback_orders)")}
    if "remaining_hold_days" not in columns:
        con.execute("ALTER TABLE pullback_orders ADD COLUMN remaining_hold_days INTEGER")
    if "last_hold_count_date" not in columns:
        con.execute("ALTER TABLE pullback_orders ADD COLUMN last_hold_count_date TEXT")


def is_terminal_watchlist_order(con: sqlite3.Connection, watchlist_date: str, ticker: str) -> bool:
    create_pullback_orders_table(con)
    row = con.execute(
        "SELECT 1 FROM pullback_orders WHERE watchlist_date=? AND ticker=?",
        (watchlist_date, ticker),
    ).fetchone()
    return row is not None


def load_open_pullback_tickers(con: sqlite3.Connection) -> set[str]:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pullback_orders'"
    ).fetchone()
    if not exists:
        return set()
    placeholders = ",".join("?" for _ in OPEN_PULLBACK_STATUSES)
    rows = con.execute(
        f"SELECT DISTINCT ticker FROM pullback_orders WHERE status IN ({placeholders}) "
        "AND COALESCE(sell_status, '') != 'filled'",
        OPEN_PULLBACK_STATUSES,
    )
    return {row[0] for row in rows}


def load_open_close_bet_tickers(con: sqlite3.Connection) -> set[str]:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='close_bet_orders'"
    ).fetchone()
    if not exists:
        return set()
    placeholders = ",".join("?" for _ in OPEN_CLOSE_BET_STATUSES)
    columns = {row[1] for row in con.execute("PRAGMA table_info(close_bet_orders)")}
    sell_guard = " AND COALESCE(sell_status, '') != 'filled'" if "sell_status" in columns else ""
    rows = con.execute(
        f"SELECT DISTINCT ticker FROM close_bet_orders WHERE status IN ({placeholders})" + sell_guard,
        OPEN_CLOSE_BET_STATUSES,
    )
    return {row[0] for row in rows}


def exclude_held_tickers(
    candidates: list[dict[str, Any]],
    close_bet_tickers: set[str],
    pullback_tickers: set[str],
) -> list[dict[str, Any]]:
    held = close_bet_tickers | pullback_tickers
    return [candidate for candidate in candidates if candidate["ticker"] not in held]


def rank_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return sorted(candidates, key=lambda item: (item["watchlist_date"], item["ticker"]))[:limit]


def order_budget(available_cash: int, count: int, budget_per_stock: int) -> int:
    if count <= 0:
        return 0
    return min(budget_per_stock, available_cash // count)


def order_qty(budget: int, price: int) -> int:
    return quantity_for_budget(budget, price)


def candidate_trading_days(
    watchlist_date: str,
    available_dates: Iterable[str],
    max_wait_days: int = MAX_WAIT_DAYS,
) -> list[str]:
    """watchlist일 이후 실제 존재하는 거래일을 최대 D+5까지 반환한다."""
    return sorted({date for date in available_dates if date > watchlist_date})[:max_wait_days]


def find_first_signal(
    watchlist_day: dict[str, Any],
    future_days: list[dict[str, Any]],
    max_wait_days: int = MAX_WAIT_DAYS,
) -> dict[str, Any] | None:
    """D+1~D+5에서 lower-low와 양봉을 처음 동시에 만족한 신호를 반환한다.

    과거 완료일은 ``close``, 현재 15:19 스냅샷은 ``current_price``를 종가
    대용값으로 사용한다. ``future_days``는 거래일 오름차순이어야 한다.
    """
    prior_low = watchlist_day.get("low")
    if not prior_low or prior_low <= 0:
        return None

    for wait_days, current in enumerate(future_days[:max_wait_days], start=1):
        day_open = current.get("open")
        day_low = current.get("low")
        signal_price = current.get("current_price", current.get("close"))
        valid = all(value is not None and value > 0 for value in (day_open, day_low, signal_price))
        if not valid:
            continue
        if day_low < prior_low and signal_price > day_open:
            return {
                "signal_date": current["date"],
                "prior_low": prior_low,
                "day_open": day_open,
                "day_low": day_low,
                "signal_price": signal_price,
                "wait_days": wait_days,
            }
        prior_low = day_low
    return None


def load_today_signal_candidates(
    watchlist_db: Path, krx_db: Path, today: str, broker_url: str, config: dict[str, Any]
) -> list[dict[str, Any]]:
    """watchlist D+1~D+5 중 오늘 처음 신호가 된 편입 건을 조회한다."""
    with connect_ro(watchlist_db) as con:
        watchlist_rows = con.execute(
            "SELECT date, stock_code FROM watchlist WHERE date < ? ORDER BY date, stock_code",
            (today,),
        ).fetchall()
        terminal = {
            (row[0], row[1]) for row in con.execute(
                "SELECT watchlist_date, ticker FROM pullback_orders"
            ).fetchall()
        } if con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pullback_orders'"
        ).fetchone() else set()
        close_held = load_open_close_bet_tickers(con)
        pullback_held = load_open_pullback_tickers(con)

    output = []
    with duckdb.connect(str(krx_db), read_only=True) as con:
        for watchlist_date, ticker in watchlist_rows:
            if (watchlist_date, ticker) in terminal or ticker in close_held or ticker in pullback_held:
                continue
            base = con.execute(
                "SELECT date, open, low, close FROM ohlcv WHERE ticker=? AND date=?",
                [ticker, watchlist_date],
            ).fetchone()
            if not base:
                continue
            completed = con.execute(
                "SELECT date, open, low, close FROM ohlcv "
                "WHERE ticker=? AND date>? AND date<? ORDER BY date LIMIT ?",
                [ticker, watchlist_date, today, config["max_wait_days"]],
            ).fetchall()
            if len(completed) >= config["max_wait_days"]:
                continue
            snapshot = quote_snapshot(broker_url, ticker)
            if not snapshot:
                continue
            future = [
                {"date": row[0], "open": row[1], "low": row[2], "close": row[3]}
                for row in completed
            ]
            future.append({"date": today, **snapshot})
            signal = find_first_signal(
                {"date": base[0], "open": base[1], "low": base[2], "close": base[3]},
                future,
                config["max_wait_days"],
            )
            if signal and signal["signal_date"] == today:
                output.append({"watchlist_date": watchlist_date, "ticker": ticker, **signal})
    return output


def persist_order_result(db_path: Path, candidate: dict[str, Any], qty: int, result: dict[str, Any]) -> None:
    with connect_rw(db_path) as con:
        create_pullback_orders_table(con)
        con.execute(
            "INSERT OR IGNORE INTO pullback_orders "
            "(watchlist_date, signal_date, ticker, strategy, prior_low, day_open, signal_price, "
            "qty, status, buy_order_no, message, raw, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (candidate["watchlist_date"], candidate["signal_date"], candidate["ticker"], STRATEGY,
             candidate["prior_low"], candidate["day_open"], candidate["signal_price"], qty,
             result["status"], result.get("order_no", ""), result.get("message", ""),
             json.dumps(result, ensure_ascii=False), now_seoul().isoformat()),
        )


def record_signal_note(
    broker_url: str, candidate: dict[str, Any], result: dict[str, Any], config: dict[str, Any]
) -> str:
    """주문 직후 전략 근거만 투자노트에 기록한다(체결가 확정 전).

    target_price는 평균단가가 필요해 여기서 쓰지 않는다 — 16:00 verify 배치가
    체결 확정 후 채운다. 종목당 노트는 하나이므로 기존 노트가 있으면 갱신한다.
    """
    payload = {
        "holding_period": f"매수 다음 {config['max_hold_days']}번째 거래일까지",
        "buy_reason": f"[{STRATEGY}] 전일 저가 이탈 후 15:19 양봉 반전",
        "memo": (f"watchlist={candidate['watchlist_date']}; signal={candidate['signal_date']}; "
                 f"prior_low={candidate['prior_low']}; day_open={candidate['day_open']}; "
                 f"signal_price={candidate['signal_price']}; order_no={result.get('order_no', '')}"),
    }
    notes = requests.get(
        f"{broker_url}/notes", params={"symbol": candidate["ticker"]}, timeout=REQUEST_TIMEOUT
    )
    notes.raise_for_status()
    existing = notes.json()
    if existing:
        uid = min(existing, key=lambda item: (item.get("created_at", ""), item["uid"]))["uid"]
        response = requests.patch(f"{broker_url}/notes/{uid}", json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return uid
    response = requests.post(
        f"{broker_url}/notes", json={"symbol": candidate["ticker"], **payload}, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    return response.json()["uid"]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="lower-low 양봉반전 15:19 매수 배치")
    parser.add_argument("--dry-run", default="true")
    parser.add_argument("--broker-url", default=None)
    parser.add_argument("--order-time", default="15:19:00")
    parser.add_argument("--order-deadline-time", default="15:20:00")
    parser.add_argument("--allow-order-outside-close-window", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    args = build_arg_parser().parse_args(argv)
    config = load()
    dry_run = args.dry_run.lower() not in ("false", "0", "no")
    broker_url = args.broker_url or os.getenv("BROKER_API_URL", "http://localhost:8001")
    now = now_seoul()
    if not args.allow_order_outside_close_window and not in_order_window(
        now, args.order_time, args.order_deadline_time
    ):
        return 1
    today = now.strftime("%Y%m%d")
    candidates = rank_candidates(
        load_today_signal_candidates(DEFAULT_WATCHLIST_DB, DEFAULT_KRX_DB, today, broker_url, config),
        config["max_new_positions"],
    )
    if not candidates:
        print(f"[pullback] {today} 신호 충족 종목 없음 (관측창 내 lower-low+양봉반전 미발생)")
        send_discord(f"[눌림목 매매] {today} 주문 대상 없음")
        return 0
    cash = available_cash(broker_url)
    if cash is None:
        return 1
    budget = order_budget(cash, len(candidates), config["budget_per_stock"])
    for candidate in candidates:
        if not args.allow_order_outside_close_window and not in_order_window(
            now_seoul(), args.order_time, args.order_deadline_time
        ):
            break
        price = current_price(broker_url, candidate["ticker"])
        qty = order_qty(budget, price or 0)
        if qty <= 0:
            result = {"order_no": "", "status": "skipped", "message": "예산<현재가"}
        else:
            result = market_order(
                broker_url, candidate["ticker"], qty, "buy", "pullback_order", dry_run, now=now
            )
        persist_order_result(DEFAULT_WATCHLIST_DB, candidate, qty, result)
        if not dry_run and result["status"] == "submitted":
            try:
                note_uid = record_signal_note(broker_url, candidate, result, config)
                with connect_rw(DEFAULT_WATCHLIST_DB) as con:
                    con.execute(
                        "UPDATE pullback_orders SET note_uid=? WHERE watchlist_date=? AND ticker=?",
                        (note_uid, candidate["watchlist_date"], candidate["ticker"]),
                    )
            except Exception as exc:  # 노트 실패로 주문 배치를 멈추지 않음 — verify가 재시도
                print(f"[pullback] NOTE FAIL {candidate['ticker']} {exc}")
        print(
            f"[pullback] BUY {candidate['ticker']} watchlist={candidate['watchlist_date']} "
            f"signal_date={candidate['signal_date']} prior_low={candidate['prior_low']} "
            f"day_open={candidate['day_open']} day_low={candidate['day_low']} "
            f"signal_price={candidate['signal_price']} (lower-low+양봉반전) qty={qty} "
            f"status={result['status']} order_no={result.get('order_no', '')}"
        )
    send_discord(f"[눌림목 매매] {today} 주문 처리 {len(candidates)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
