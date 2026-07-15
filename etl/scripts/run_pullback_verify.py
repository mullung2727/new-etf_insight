"""pullback 주문 체결검증과 투자노트 전략 메타데이터 기록."""
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import load_dotenv

try:
    from scripts.pullback_config import load
    from scripts.run_pullback_order import DEFAULT_WATCHLIST_DB, create_pullback_orders_table
    from scripts.run_verify import fetch_order_history, normalize_order_no
    from scripts.trading_batch_common import now_seoul
    from scripts.wl_sqlite import connect_ro, connect_rw
except ImportError:
    from pullback_config import load
    from run_pullback_order import DEFAULT_WATCHLIST_DB, create_pullback_orders_table
    from run_verify import fetch_order_history, normalize_order_no
    from trading_batch_common import now_seoul
    from wl_sqlite import connect_ro, connect_rw


ROOT = Path(__file__).resolve().parents[2]
REQUEST_TIMEOUT = 15
NoteRecorder = Callable[[dict[str, Any]], str]


def aggregate_fills(history: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for item in history:
        key = normalize_order_no(item.get("order_no"))
        qty = int(item.get("cntr_qty") or 0)
        price = int(item.get("cntr_uv") or 0)
        if not key or qty <= 0 or price <= 0:
            continue
        value = totals.setdefault(key, {"qty": 0, "value": 0})
        value["qty"] += qty
        value["value"] += qty * price
    return {
        key: {"price": round(value["value"] / value["qty"]), "qty": value["qty"]}
        for key, value in totals.items()
    }


def _pending_rows(db_path: Path, date: str) -> list[dict[str, Any]]:
    with connect_rw(db_path) as con:
        create_pullback_orders_table(con)
        rows = con.execute(
            "SELECT watchlist_date,signal_date,ticker,status,buy_order_no,buy_price,buy_qty,"
            "bought_at,note_uid,prior_low,day_open,signal_price FROM pullback_orders "
            "WHERE signal_date=? AND (status IN ('submitted','unconfirmed') "
            "OR (status='confirmed' AND note_uid IS NULL))",
            (date,),
        ).fetchall()
    keys = ("watchlist_date", "signal_date", "ticker", "status", "buy_order_no",
            "buy_price", "buy_qty", "bought_at", "note_uid", "prior_low", "day_open", "signal_price")
    return [dict(zip(keys, row)) for row in rows]


def verify_orders(
    db_path: Path, date: str, history: list[dict[str, Any]], max_hold_days: int,
    note_recorder: NoteRecorder, verified_at: datetime,
) -> dict[str, int]:
    fills = aggregate_fills(history)
    summary = {"confirmed": 0, "unconfirmed": 0, "noted": 0, "note_failed": 0}
    for order in _pending_rows(db_path, date):
        if order["status"] != "confirmed":
            fill = fills.get(normalize_order_no(order["buy_order_no"]))
            if not fill:
                with connect_rw(db_path) as con:
                    con.execute(
                        "UPDATE pullback_orders SET status='unconfirmed' "
                        "WHERE watchlist_date=? AND ticker=?",
                        (order["watchlist_date"], order["ticker"]),
                    )
                summary["unconfirmed"] += 1
                continue
            bought_at = verified_at.isoformat(sep=" ", timespec="seconds")
            with connect_rw(db_path) as con:
                con.execute(
                    "UPDATE pullback_orders SET status='confirmed',buy_price=?,buy_qty=?,"
                    "bought_at=?,remaining_hold_days=?,verified_at=? "
                    "WHERE watchlist_date=? AND ticker=?",
                    (fill["price"], fill["qty"], bought_at, max_hold_days, bought_at,
                     order["watchlist_date"], order["ticker"]),
                )
            order.update(buy_price=fill["price"], buy_qty=fill["qty"], bought_at=bought_at,
                         remaining_hold_days=max_hold_days, status="confirmed")
            summary["confirmed"] += 1
        try:
            note_uid = note_recorder(order)
        except Exception:
            summary["note_failed"] += 1
            continue
        with connect_rw(db_path) as con:
            con.execute(
                "UPDATE pullback_orders SET note_uid=? WHERE watchlist_date=? AND ticker=?",
                (note_uid, order["watchlist_date"], order["ticker"]),
            )
        summary["noted"] += 1
    return summary


def record_investment_note(broker_url: str, order: dict[str, Any], config: dict[str, Any]) -> str:
    notes_response = requests.get(
        f"{broker_url}/notes", params={"symbol": order["ticker"]}, timeout=REQUEST_TIMEOUT
    )
    notes_response.raise_for_status()
    notes = notes_response.json()
    payload = {
        "target_price": int(order["buy_price"] * (1 + config["tp"])),
        "holding_period": f"매수 다음 {config['max_hold_days']}번째 거래일까지",
        "buy_reason": "[lower_low_bullish_reversal] 전일 저가 이탈 후 15:19 양봉 반전",
        "memo": (f"watchlist={order['watchlist_date']}; signal={order['signal_date']}; "
                 f"prior_low={order['prior_low']}; day_open={order['day_open']}; "
                 f"signal_price={order['signal_price']}; order_no={order['buy_order_no']}"),
    }
    if notes:
        selected = min(notes, key=lambda item: (item.get("created_at", ""), item["uid"]))
        response = requests.patch(f"{broker_url}/notes/{selected['uid']}", json=payload, timeout=REQUEST_TIMEOUT)
        uid = selected["uid"]
    else:
        response = requests.post(
            f"{broker_url}/notes", json={"symbol": order["ticker"], **payload}, timeout=REQUEST_TIMEOUT
        )
        uid = ""
    response.raise_for_status()
    if not uid:
        uid = response.json()["uid"]
    sync = requests.post(
        f"{broker_url}/notes/sync-trades", params={"date": order["signal_date"]}, timeout=REQUEST_TIMEOUT
    )
    sync.raise_for_status()
    return uid


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="pullback 체결검증·투자노트 연동")
    parser.add_argument("--date")
    parser.add_argument("--broker-url", default=None)
    args = parser.parse_args(argv)
    date = args.date or now_seoul().strftime("%Y%m%d")
    broker_url = args.broker_url or os.getenv("BROKER_API_URL", "http://localhost:8001")
    history = fetch_order_history(broker_url, date)
    if history is None:
        return 1
    config = load()
    verify_orders(
        DEFAULT_WATCHLIST_DB, date, history, config["max_hold_days"],
        lambda order: record_investment_note(broker_url, order, config), now_seoul(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
