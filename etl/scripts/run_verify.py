"""체결 대조 배치 (16:00 실행).

close_bet_orders 중 status IN ('submitted','unconfirmed') 행의 order_no를
broker GET /orders/history (kt00007 경유) 당일 체결내역과 대조한다.
  - 매칭 성공: cntr_price/cntr_qty/verified_at 기록 + status='confirmed'
  - 매칭 실패: status='unconfirmed'

핵심 사상: Kiwoom API는 무조건 broker를 통해서만 호출한다.
Discord 보고는 OpenClaw 배치(daily-close-bet-verify.md)가 로그/DB를 읽어 수행한다.

전제:
  - kt00007은 당일만 조회 가능 — 다음날 재대조 불가(재시도 윈도우=당일).
  - order_no 포맷 차이를 normalize_order_no(0 제거)로 흡수.

Usage (from etl/):
    .venv/Scripts/python.exe scripts/run_verify.py --broker-url http://localhost:8001
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
_UNVERIFIED = ("submitted", "unconfirmed")


def _now_seoul() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))


def normalize_order_no(value: object) -> str:
    """order_no 비교용 정규화. 0-padding/타입 차이를 흡수한다.

    "0000050" → "50", "50" → "50", 50 → "50", "0000000"/""/None → "".
    """
    if value is None:
        return ""
    return str(value).strip().lstrip("0")


# ── DB ─────────────────────────────────────────────────────────────────────

def load_unverified_orders(watchlist_db: Path, date: str) -> list[dict]:
    """status가 submitted/unconfirmed인 주문(order_no 보유)을 반환."""
    with connect_ro(watchlist_db) as con:
        rows = con.execute(
            f"""
            SELECT ticker, order_no, status
            FROM close_bet_orders
            WHERE date = ?
              AND status IN {_UNVERIFIED}
              AND order_no IS NOT NULL AND order_no <> ''
            """,
            [date],
        ).fetchall()
    return [{"ticker": r[0], "order_no": r[1], "status": r[2]} for r in rows]


def mark_confirmed(
    watchlist_db: Path, date: str, ticker: str,
    cntr_price: int, cntr_qty: int, verified_at: datetime,
) -> None:
    # SQLite(py3.12+)는 datetime 기본 어댑터가 deprecated → ISO 문자열로 저장.
    verified_at_iso = verified_at.isoformat(sep=" ", timespec="seconds")
    with connect_rw(watchlist_db) as con:
        con.execute(
            """
            UPDATE close_bet_orders
            SET status='confirmed', cntr_price=?, cntr_qty=?, verified_at=?
            WHERE date=? AND ticker=?
            """,
            [cntr_price, cntr_qty, verified_at_iso, date, ticker],
        )


def mark_unconfirmed(watchlist_db: Path, date: str, ticker: str) -> None:
    with connect_rw(watchlist_db) as con:
        con.execute(
            "UPDATE close_bet_orders SET status='unconfirmed' WHERE date=? AND ticker=?",
            [date, ticker],
        )


# ── broker ───────────────────────────────────────────────────────────────────

def fetch_order_history(broker_url: str, date: str, side: str = "buy") -> list[dict] | None:
    """GET {broker_url}/orders/history?date=&side=. 실패 시 None.

    kt00007은 매수/매도를 sell_tp로 나눠 조회한다 — 매도 체결 대조는 side="sell" 필수.
    """
    try:
        resp = requests.get(
            f"{broker_url}/orders/history",
            params={"date": date, "side": side},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else None
    except Exception as exc:
        print(f"[verify] broker 체결내역 조회 실패: {exc}")
        return None


# ── main ───────────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv(ENV_PATH)

    parser = argparse.ArgumentParser(description="종가배팅 체결 대조 배치")
    parser.add_argument("--date", help="대상 날짜 YYYYMMDD; 기본: 오늘(Asia/Seoul)")
    parser.add_argument("--broker-url", default=None)
    args = parser.parse_args()

    date = args.date or _now_seoul().strftime("%Y%m%d")
    # kt00007은 실제 주문일(오늘)만 조회 가능. `date`(--date override 가능)는 close_bet_orders
    # 버킷 매칭용이라 override 시 실제 체결일과 다를 수 있음 — 분리한다.
    history_date = _now_seoul().strftime("%Y%m%d")
    broker_url = args.broker_url or os.getenv("BROKER_API_URL", "http://localhost:8001")
    watchlist_db = DEFAULT_WATCHLIST_DB

    orders = load_unverified_orders(watchlist_db, date)
    print(f"[verify] 대조 대상: {len(orders)}건 (date={date}, status={_UNVERIFIED})")
    if not orders:
        print("[verify] 대조 대상 없음 — 종료")
        return

    history = fetch_order_history(broker_url, history_date)
    if history is None:
        print("[verify] ABORT: broker 체결내역 조회 실패 — DB 변경 없이 종료")
        send_discord(f"[종가베팅] {date} 체결 대조 ABORT\nbroker 체결내역 조회 실패 — 확인 필요 (대조 대상 {len(orders)}건)")
        sys.exit(1)

    # order_no(정규화) → 체결내역 집계 (부분체결 대비 qty 합산, 단가는 첫 유효값)
    by_no: dict[str, dict] = {}
    for item in history:
        key = normalize_order_no(item.get("order_no"))
        if not key:
            continue
        agg = by_no.setdefault(key, {"cntr_qty": 0, "cntr_uv": 0})
        agg["cntr_qty"] += int(item.get("cntr_qty") or 0)
        if not agg["cntr_uv"]:
            agg["cntr_uv"] = int(item.get("cntr_uv") or 0)

    now = _now_seoul()
    confirmed = unconfirmed = 0
    report_lines: list[str] = []
    for o in orders:
        key = normalize_order_no(o["order_no"])
        match = by_no.get(key)
        if match and match["cntr_qty"] > 0:
            mark_confirmed(
                watchlist_db, date, o["ticker"],
                match["cntr_uv"], match["cntr_qty"], now,
            )
            confirmed += 1
            print(f"[verify] {o['ticker']} ord={o['order_no']} → confirmed "
                  f"price={match['cntr_uv']} qty={match['cntr_qty']}")
            report_lines.append(
                f"✅ {o['ticker']} #{o['order_no']} 체결 {match['cntr_uv']}원 x{match['cntr_qty']}"
            )
        else:
            mark_unconfirmed(watchlist_db, date, o["ticker"])
            unconfirmed += 1
            print(f"[verify] WARN {o['ticker']} ord={o['order_no']} → unconfirmed "
                  f"(체결내역 매칭 실패)")
            report_lines.append(f"⚠️ {o['ticker']} #{o['order_no']} unconfirmed (체결 매칭 실패)")

    print(f"[verify] 완료: confirmed={confirmed} unconfirmed={unconfirmed}")
    if unconfirmed:
        print(f"[verify] 미체결/미확인 {unconfirmed}건 — 확인 필요")

    summary = (
        f"[종가베팅] {date} 체결 대조 결과\n"
        f"대조 {len(orders)} | 체결확인 {confirmed} | 미확인 {unconfirmed}\n"
        + "\n".join(report_lines)
    )
    send_discord(summary)


if __name__ == "__main__":
    main()
