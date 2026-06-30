"""종가베팅 청산 백스톱 배치 — 워커 크래시 대비 (15:19:30 평일 1회).

장중 청산 워커(run_close_bet_exit.py)가 죽어 강제청산을 누락했을 때의 최종 방어선.
워커 강제청산(15:19:00) 직후 15:19:30에 독립 시계로 1회 실행한다.

3중 대조로 멱등 보장 — 워커가 정상이면 무동작:
  잔고(kt00018 매도가능) AND ka10075 미체결(매도) AND DB sell_status
  → in-flight(매도 진행중·미체결 큐) 제외, 순수 잔존분만 시장가 매도(exit_reason='forced').

체결확인은 하지 않는다(1회 실행 후 종료). 다음 워커 재기동(load_ordered_pending) 또는
체결대조 배치가 확정. close_bet_orders 매수배치와 별도 항목, 상호 불간섭.

Usage (from etl/):
    .venv/Scripts/python.exe scripts/run_close_bet_force_exit.py --dry-run false
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

try:  # 직접 실행 / 패키지 import 양쪽 지원
    from scripts.run_close_bet_exit import (
        DEFAULT_WATCHLIST_DB,
        ENV_PATH,
        _now_seoul,
        connect_rw,
        execute_sell,
        fetch_balance,
        fetch_unfilled_tickers,
        load_unsold_positions,
        reconcile_balance,
        send_discord,
    )
except ImportError:
    from run_close_bet_exit import (
        DEFAULT_WATCHLIST_DB,
        ENV_PATH,
        _now_seoul,
        connect_rw,
        execute_sell,
        fetch_balance,
        fetch_unfilled_tickers,
        load_unsold_positions,
        reconcile_balance,
        send_discord,
    )


def select_residual(positions: list[dict], balance: dict, unfilled: set[str]) -> list[dict]:
    """순수 잔존분 선별 — 잔고 보유(reconcile) AND 미체결 큐에 없음.

    DB sell_status는 load_unsold_positions가 NULL만 반환해 이미 걸러짐(주문전송분 제외).
    """
    held = reconcile_balance(positions, balance)
    return [w for w in held if w["ticker"] not in unfilled]


def main() -> None:
    load_dotenv(ENV_PATH)
    parser = argparse.ArgumentParser(description="종가베팅 청산 백스톱 (15:19:30 1회)")
    parser.add_argument("--broker-url", default=None)
    parser.add_argument("--dry-run", default="true")
    args = parser.parse_args()
    dry_run = args.dry_run.lower() not in ("false", "0", "no")
    broker_url = args.broker_url or os.getenv("BROKER_API_URL", "http://localhost:8001")
    db_path = DEFAULT_WATCHLIST_DB
    today = _now_seoul().strftime("%Y%m%d")
    dry_tag = " (DRY-RUN)" if dry_run else ""

    with connect_rw(db_path) as con:
        positions = load_unsold_positions(con, today)
    balance = fetch_balance(broker_url)
    unfilled = fetch_unfilled_tickers(broker_url)
    residual = select_residual(positions, balance, unfilled)

    print(f"[backstop] 미청산 {len(positions)}건 → 3중대조 후 잔존 {len(residual)}건")
    if not residual:
        print("[backstop] 잔존 0 — 무동작(워커 정상)")
        return

    report: list[str] = []
    for pos in residual:
        ticker = pos["ticker"]
        if dry_run:
            msg = f"WOULD forced-sell {ticker} qty={pos.get('qty_eff')}"
            print(f"[backstop]{dry_tag} {msg}")
            report.append(msg)
            continue
        res = execute_sell(broker_url, db_path, pos, "forced")
        if res["order_no"]:
            msg = f"forced-sell {ticker} ord={res['order_no']} qty={res['qty']}"
        else:
            msg = f"⚠️ {ticker} 매도 실패({res['status']}: {res.get('message','')})"
        print(f"[backstop] {msg}")
        report.append(msg)

    send_discord(f"[종가베팅 백스톱]{dry_tag}\n" + "\n".join(report))


if __name__ == "__main__":
    main()
