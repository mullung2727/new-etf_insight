"""pullback 포지션 TP/SL 감시와 거래일 만기 시장가 청산 워커."""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

try:
    from scripts.pullback_config import load
    from scripts.run_pullback_order import DEFAULT_WATCHLIST_DB, create_pullback_orders_table
    from scripts.run_pullback_verify import aggregate_fills
    from scripts.run_verify import fetch_order_history, normalize_order_no
    from scripts.trading_batch_common import market_order, now_seoul
    from scripts.wl_sqlite import connect_ro, connect_rw
except ImportError:
    from pullback_config import load
    from run_pullback_order import DEFAULT_WATCHLIST_DB, create_pullback_orders_table
    from run_pullback_verify import aggregate_fills
    from run_verify import fetch_order_history, normalize_order_no
    from trading_batch_common import market_order, now_seoul
    from wl_sqlite import connect_ro, connect_rw


ROOT = Path(__file__).resolve().parents[2]
REQUEST_TIMEOUT = 15


def decide_exit(buy_bid: int | None, buy_price: int | None, tp: float, sl: float) -> str | None:
    if not buy_bid or not buy_price:
        return None
    change = buy_bid / buy_price - 1
    if change >= tp:
        return "tp"
    if change <= -sl:
        return "sl"
    return None


def sell_quantity(strategy_qty: int, tradable_qty: int) -> int:
    return max(0, min(strategy_qty, tradable_qty))


def advance_holding_day(db_path: Path, today: str, market_open: bool) -> int:
    if not market_open:
        return 0
    with connect_rw(db_path) as con:
        create_pullback_orders_table(con)
        con.execute(
            "UPDATE pullback_orders SET remaining_hold_days=remaining_hold_days-1, "
            "last_hold_count_date=?, expiry_date=CASE WHEN remaining_hold_days-1<=0 THEN ? ELSE expiry_date END "
            "WHERE status='confirmed' AND COALESCE(sell_status,'') NOT IN ('ordered','filled') "
            "AND signal_date<? AND remaining_hold_days>0 "
            "AND COALESCE(last_hold_count_date,'')<>?",
            (today, today, today, today),
        )
        return con.execute(
            "SELECT COUNT(*) FROM pullback_orders WHERE status='confirmed' "
            "AND remaining_hold_days<=0 AND expiry_date=? AND COALESCE(sell_status,'')=''",
            (today,),
        ).fetchone()[0]


def is_confirmed_market_day(db_path: Path, today: str) -> bool:
    with connect_ro(db_path) as con:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intraday_ranking'"
        ).fetchone()
        if not exists:
            return False
        return bool(con.execute("SELECT 1 FROM intraday_ranking WHERE date=? LIMIT 1", (today,)).fetchone())


def load_positions(db_path: Path) -> list[dict[str, Any]]:
    with connect_rw(db_path) as con:
        create_pullback_orders_table(con)
        rows = con.execute(
            "SELECT watchlist_date,ticker,buy_price,buy_qty,remaining_hold_days,expiry_date "
            "FROM pullback_orders WHERE status='confirmed' AND COALESCE(sell_status,'')=''"
        ).fetchall()
    keys = ("watchlist_date", "ticker", "buy_price", "buy_qty", "remaining_hold_days", "expiry_date")
    return [dict(zip(keys, row)) for row in rows]


def fetch_best_bids(broker_url: str, tickers: list[str]) -> dict[str, int]:
    if not tickers:
        return {}
    try:
        response = requests.get(f"{broker_url}/quotes", params={"codes": ",".join(tickers)}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return {row["stk_cd"]: int(row["buy_bid"]) for row in response.json() if row.get("buy_bid")}
    except Exception:
        return {}


def fetch_tradable_quantities(broker_url: str) -> dict[str, int]:
    try:
        response = requests.get(f"{broker_url}/account/balance", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        rows = response.json().get("acnt_evlt_remn_indv_tot", []) or []
        return {str(row.get("stk_cd", "")).lstrip("A"): int(row.get("trde_able_qty") or 0) for row in rows}
    except Exception:
        return {}


def mark_sell_ordered(db_path: Path, position: dict[str, Any], order_no: str, reason: str) -> None:
    with connect_rw(db_path) as con:
        con.execute(
            "UPDATE pullback_orders SET sell_status='ordered',sell_order_no=?,exit_reason=? "
            "WHERE watchlist_date=? AND ticker=? AND COALESCE(sell_status,'')=''",
            (order_no, reason, position["watchlist_date"], position["ticker"]),
        )


def settle_sell_orders(db_path: Path, broker_url: str, today: str) -> int:
    history = fetch_order_history(broker_url, today)
    if history is None:
        return 0
    fills = aggregate_fills(history)
    settled = 0
    with connect_rw(db_path) as con:
        create_pullback_orders_table(con)
        rows = con.execute(
            "SELECT watchlist_date,ticker,buy_price,sell_order_no FROM pullback_orders "
            "WHERE sell_status='ordered'"
        ).fetchall()
        for watchlist_date, ticker, buy_price, order_no in rows:
            fill = fills.get(normalize_order_no(order_no))
            if not fill:
                continue
            pnl = fill["price"] / buy_price - 1 if buy_price else None
            con.execute(
                "UPDATE pullback_orders SET status='closed',sell_status='filled',sell_price=?,"
                "sell_qty=?,sold_at=?,pnl_pct=? WHERE watchlist_date=? AND ticker=?",
                (fill["price"], fill["qty"], now_seoul().isoformat(), pnl, watchlist_date, ticker),
            )
            settled += 1
            print(
                f"[pullback-exit] CLOSED {ticker} buy={buy_price} sell={fill['price']} "
                f"pnl={f'{pnl:+.2%}' if pnl is not None else 'NA'} qty={fill['qty']} order_no={order_no}"
            )
    return settled


def run_cycle(db_path: Path, broker_url: str, config: dict[str, Any], today: str,
              force: bool, dry_run: bool) -> int:
    if force:
        advance_holding_day(db_path, today, is_confirmed_market_day(db_path, today))
    positions = load_positions(db_path)
    bids = fetch_best_bids(broker_url, [position["ticker"] for position in positions])
    tradable = fetch_tradable_quantities(broker_url)
    ordered = 0
    for position in positions:
        reason = "forced" if force and position.get("expiry_date") == today else decide_exit(
            bids.get(position["ticker"]), position["buy_price"], config["tp"], config["sl"]
        )
        if not reason:
            continue
        qty = sell_quantity(position["buy_qty"] or 0, tradable.get(position["ticker"], 0))
        if qty <= 0:
            continue
        bid = bids.get(position["ticker"])
        change = (bid / position["buy_price"] - 1) if bid and position["buy_price"] else None
        result = market_order(broker_url, position["ticker"], qty, "sell", "pullback_exit", dry_run)
        if result["status"] == "submitted" and result.get("order_no"):
            mark_sell_ordered(db_path, position, result["order_no"], reason)
            ordered += 1
            print(
                f"[pullback-exit] SELL {position['ticker']} reason={reason} "
                f"buy={position['buy_price']} bid={bid} "
                f"change={f'{change:+.2%}' if change is not None else 'NA'} qty={qty} "
                f"order_no={result['order_no']}"
            )
    return ordered


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="pullback TP/SL·만기 청산 워커")
    parser.add_argument("--broker-url", default=None)
    parser.add_argument("--poll-sec", type=float, default=5.0)
    parser.add_argument("--force-exit-time", default="15:19:00")
    parser.add_argument("--stop-time", default="15:25:00")
    parser.add_argument("--dry-run", default="true")
    args = parser.parse_args(argv)
    broker_url = args.broker_url or os.getenv("BROKER_API_URL", "http://localhost:8001")
    dry_run = args.dry_run.lower() not in ("false", "0", "no")
    config = load()
    counted = False
    while True:
        now = now_seoul()
        if now.strftime("%H:%M:%S") >= args.stop_time:
            break
        today = now.strftime("%Y%m%d")
        settle_sell_orders(DEFAULT_WATCHLIST_DB, broker_url, today)
        force = not counted and now.strftime("%H:%M:%S") >= args.force_exit_time
        run_cycle(DEFAULT_WATCHLIST_DB, broker_url, config, today, force, dry_run)
        counted = counted or force
        time.sleep(args.poll_sec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
