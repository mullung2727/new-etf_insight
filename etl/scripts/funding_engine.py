import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401

"""공용 자금 엔진 1단계 — 엔진 원장 (ETF 주문·체결·보유·손익 기록·조회)."""
import argparse
import math
import os
import sqlite3
import time
import traceback
from datetime import datetime
from pathlib import Path

from wl_sqlite import connect_ro, connect_rw
from new_etf_insight.data_paths import db_dir
from broker_client import BrokerClient
from run_verify import normalize_order_no
from trading_batch_common import (
    available_cash, read_reservations, held_quantities, now_seoul,
    write_reservation as _file_write_reservation,
    _load_reservation_file,
)
from run_close_bet_exit import fetch_balance
from dotenv import load_dotenv
from check_krx_trading_day import is_krx_trading_day
from notify import (
    send_strategy_alert, headline, HEADLINE_OK, HEADLINE_WARN, HEADLINE_FAIL,
)

try:
    from scripts import market_session
except ImportError:  # scripts/ 직접 실행
    import market_session

ETF_TICKER = "459580"  # KODEX CD금리액티브(합성)
DB = db_dir() / "funding.sqlite3"
ORDER_STATUS = ("submitted", "partial", "filled", "cancelled", "rejected", "unknown")
SELL_SLIP = 0.0002  # 매도 슬리피지 0.02% (PLAN_FUNDING_ENGINE D9)
ENGINE_KEY = "engine"  # 예약 파일에서 엔진 줄 키
PREOPEN_FILL_AT = "09:00:00"  # 장전 매도 체결 확인 시작
FILL_POLL_SEC = 5
FILL_POLL_MAX_SEC = 120
ETF_TICK = 5  # ETF 호가단위 (2,000원 이상)
BUY_TICKS_UP = 2  # 매도1호가 + 2틱 지정가


def max_order_amount() -> int:
    """1주문 최대 금액 — broker/kiwoom/config.py 와 같은 기본값. 러너가 load_dotenv 한다."""
    return int(os.getenv("MAX_ORDER_AMOUNT", "1000000"))

_SIDES = ("buy", "sell")


def ensure(db: Path) -> None:
    """원장 테이블 생성 (멱등)."""
    # PLAN_FUNDING_ENGINE D13 — members.etf: ETF 마감매수(sweep) 참여 여부
    with connect_rw(db) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS members("
            "strategy TEXT PRIMARY KEY, joined TEXT NOT NULL, "
            "unused INTEGER NOT NULL DEFAULT 0, "
            "unused_at TEXT NOT NULL DEFAULT '', "
            "etf INTEGER NOT NULL DEFAULT 1)"
        )
        cols = [row[1]
                for row in con.execute("PRAGMA table_info(members)").fetchall()]
        if "etf" not in cols:
            con.execute(
                "ALTER TABLE members ADD COLUMN etf INTEGER NOT NULL DEFAULT 1"
            )
        con.execute(
            "CREATE TABLE IF NOT EXISTS etf_orders("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, "
            "side TEXT NOT NULL CHECK(side IN ('buy','sell')), "
            "qty INTEGER NOT NULL CHECK(qty > 0), price INTEGER NOT NULL, "
            "order_no TEXT NOT NULL UNIQUE, status TEXT NOT NULL, "
            "filled_qty INTEGER NOT NULL DEFAULT 0, filled_price REAL, "
            "reason TEXT NOT NULL DEFAULT '', created TEXT NOT NULL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS etf_pnl("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, "
            "strategy TEXT NOT NULL, amount INTEGER NOT NULL, "
            "order_no TEXT NOT NULL UNIQUE)"
        )
        # PLAN_FUNDING_ENGINE P8 — 전략 간 대여 원장
        con.execute(
            "CREATE TABLE IF NOT EXISTS lendable("
            "date TEXT, strategy TEXT, amount INTEGER NOT NULL, "
            "at TEXT NOT NULL, PRIMARY KEY(date, strategy))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS borrow_caps("
            "date TEXT, borrower TEXT, max_amount INTEGER NOT NULL, "
            "at TEXT NOT NULL, PRIMARY KEY(date, borrower))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS loans("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, "
            "lender TEXT NOT NULL, borrower TEXT NOT NULL, "
            "amount INTEGER NOT NULL CHECK(amount > 0), at TEXT NOT NULL, "
            "lender_pnl INTEGER, settled INTEGER NOT NULL DEFAULT 0)"
        )


def add_member(db: Path, strategy: str, joined: str, *,
               etf: bool = True) -> None:
    """참여 전략 등록. 이미 있으면 joined 유지, etf 만 갱신."""
    # PLAN_FUNDING_ENGINE D13 — etf 인자 없이 다시 add 하면 1 로 덮어씀
    with connect_rw(db) as con:
        con.execute(
            "INSERT OR IGNORE INTO members(strategy, joined, etf)"
            " VALUES (?, ?, ?)",
            (strategy, joined, int(etf)),
        )
        con.execute(
            "UPDATE members SET etf = ? WHERE strategy = ?",
            (int(etf), strategy),
        )


def members(db: Path) -> list[str]:
    """참여 전략 목록 (strategy 정렬)."""
    with connect_ro(db) as con:
        rows = con.execute(
            "SELECT strategy FROM members ORDER BY strategy"
        ).fetchall()
    return [row[0] for row in rows]


def member_etf(db: Path, strategy: str) -> bool:
    """전략의 ETF 참여 여부. 없는 strategy 면 KeyError."""
    # PLAN_FUNDING_ENGINE D13
    with connect_ro(db) as con:
        try:
            row = con.execute(
                "SELECT etf FROM members WHERE strategy = ?", (strategy,)
            ).fetchone()
        except sqlite3.OperationalError:
            cols = [r[1]
                    for r in con.execute(
                        "PRAGMA table_info(members)").fetchall()]
            if "etf" in cols:
                raise
            exists = con.execute(
                "SELECT 1 FROM members WHERE strategy = ?", (strategy,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"없는 strategy: {strategy!r}")
            return True
    if row is None:
        raise KeyError(f"없는 strategy: {strategy!r}")
    return bool(row[0])


def record_order(db: Path, *, date: str, side: str, qty: int, price: int,
                 order_no: str, status: str, reason: str = "",
                 created: str) -> None:
    """ETF 주문 기록 (price=주문가, 시장가면 0). 중복 order_no 금지."""
    if side not in _SIDES:
        raise ValueError(f"잘못된 side: {side!r}")
    if status not in ORDER_STATUS:
        raise ValueError(f"잘못된 status: {status!r}")
    if qty <= 0:
        raise ValueError(f"qty는 1 이상: {qty!r}")
    if not order_no:
        raise ValueError("order_no이 비어 있음")
    with connect_rw(db) as con:
        dup = con.execute(
            "SELECT 1 FROM etf_orders WHERE order_no = ?", (order_no,)
        ).fetchone()
        if dup is not None:
            raise ValueError(f"중복 order_no: {order_no!r}")
        con.execute(
            "INSERT INTO etf_orders(date, side, qty, price, order_no, status,"
            " reason, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (date, side, qty, price, order_no, status, reason, created),
        )


def update_fill(db: Path, order_no: str, *, status: str, filled_qty: int,
                filled_price: float | None) -> None:
    """체결 상태 갱신."""
    if status not in ORDER_STATUS:
        raise ValueError(f"잘못된 status: {status!r}")
    with connect_rw(db) as con:
        row = con.execute(
            "SELECT qty FROM etf_orders WHERE order_no = ?", (order_no,)
        ).fetchone()
        if row is None:
            raise KeyError(f"없는 order_no: {order_no!r}")
        if filled_qty < 0 or filled_qty > row[0]:
            raise ValueError(f"filled_qty 범위 초과: {filled_qty!r} (주문 {row[0]})")
        if filled_qty > 0 and filled_price is None:
            raise ValueError("filled_qty > 0 인데 filled_price 없음")
        con.execute(
            "UPDATE etf_orders SET status = ?, filled_qty = ?, filled_price = ?"
            " WHERE order_no = ?",
            (status, filled_qty, filled_price, order_no),
        )


def etf_position(db: Path) -> dict:
    """체결 재생 → 보유·이동평균 원가 (수수료·세금 제외)."""
    qty = 0
    cost = 0.0
    with connect_ro(db) as con:
        rows = con.execute(
            "SELECT side, filled_qty, filled_price FROM etf_orders"
            " WHERE filled_qty > 0 ORDER BY id"
        ).fetchall()
    for side, fq, fp in rows:
        if side == "buy":
            if fp is None:
                raise ValueError("매수 체결가 없음 (원장 깨짐)")
            qty += fq
            cost += fq * fp
        else:
            if fq > qty:
                raise ValueError(f"보유({qty})보다 많은 매도({fq}) (원장 깨짐)")
            avg = cost / qty
            qty -= fq
            cost -= avg * fq
            if qty == 0:
                cost = 0.0
    return {"qty": qty, "cost": round(cost)}


def sellable_qty(db: Path, balance_qty: int | None) -> int:
    """매도 가능 수량. 원장에 없는 수동 보유분은 절대 팔지 않는다."""
    if balance_qty is None:
        return 0
    return max(0, min(etf_position(db)["qty"], balance_qty))


def record_pnl(db: Path, *, date: str, order_no: str, amount: int,
               strategy: str = "rebound") -> bool:
    """ETF 실현손익 기록 (멱등). 전액 되돌림(rebound) 예산 (PLAN_FUNDING_ENGINE D12)."""
    with connect_rw(db) as con:
        dup = con.execute(
            "SELECT 1 FROM etf_pnl WHERE order_no = ?", (order_no,)
        ).fetchone()
        if dup is not None:
            return False
        con.execute(
            "INSERT INTO etf_pnl(date, strategy, amount, order_no)"
            " VALUES (?, ?, ?, ?)",
            (date, strategy, amount, order_no),
        )
        return True


def pnl_since(db: Path, strategy: str, since: str) -> int:
    """since 이후(>=) 전략 손익 합. since=""면 전부. DB 없으면 0."""
    if not Path(db).exists():
        return 0
    with connect_ro(db) as con:
        if since:
            row = con.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM etf_pnl"
                " WHERE strategy = ? AND date >= ?",
                (strategy, since),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM etf_pnl WHERE strategy = ?",
                (strategy,),
            ).fetchone()
    return int(row[0])


def set_unused(db: Path, strategy: str, amount: int, at: str) -> None:
    """전략 안 쓴 몫 갱신. 음수는 0으로 저장."""
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise ValueError(f"amount는 int: {amount!r}")
    with connect_rw(db) as con:
        row = con.execute(
            "SELECT 1 FROM members WHERE strategy = ?", (strategy,)
        ).fetchone()
        if row is None:
            raise KeyError(f"없는 strategy: {strategy!r}")
        con.execute(
            "UPDATE members SET unused = ?, unused_at = ? WHERE strategy = ?",
            (max(0, amount), at, strategy),
        )


def unused_of(db: Path, strategy: str) -> int:
    """전략 안 쓴 몫 조회."""
    with connect_ro(db) as con:
        row = con.execute(
            "SELECT unused FROM members WHERE strategy = ?", (strategy,)
        ).fetchone()
    if row is None:
        raise KeyError(f"없는 strategy: {strategy!r}")
    return int(row[0])


def total_unused(db: Path, *, etf_only: bool = False) -> int:
    """전 members unused 합. etf_only 면 etf=1 회원만. members 없으면 0."""
    # PLAN_FUNDING_ENGINE D13
    with connect_ro(db) as con:
        if etf_only:
            row = con.execute(
                "SELECT COALESCE(SUM(unused), 0) FROM members WHERE etf = 1"
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COALESCE(SUM(unused), 0) FROM members"
            ).fetchone()
    return int(row[0])


def set_lendable(db: Path, strategy: str, amount: int, *, today: str,
                 at: str) -> int:
    """오늘 빌려줄 수 있는 돈 보고 (안 쓴 몫 초과분은 줄임). 저장값 반환."""
    # PLAN_FUNDING_ENGINE P8
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise ValueError(f"amount는 int: {amount!r}")
    unused = unused_of(db, strategy)
    capped = min(max(0, amount), unused)
    with connect_rw(db) as con:
        con.execute(
            "INSERT INTO lendable(date, strategy, amount, at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(date, strategy) DO UPDATE SET"
            " amount=excluded.amount, at=excluded.at",
            (today, strategy, capped, at),
        )
    return capped


def lendable_of(db: Path, strategy: str, today: str) -> int:
    """그날 빌려줄 수 있는 돈. 없으면 0."""
    # PLAN_FUNDING_ENGINE P8
    with connect_ro(db) as con:
        row = con.execute(
            "SELECT amount FROM lendable WHERE date = ? AND strategy = ?",
            (today, strategy),
        ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def loaned_today(db: Path, lender: str, today: str) -> int:
    """그날 그 lender 가 빌려준 합."""
    # PLAN_FUNDING_ENGINE P8
    with connect_ro(db) as con:
        row = con.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM loans"
            " WHERE date = ? AND lender = ?",
            (today, lender),
        ).fetchone()
    return int(row[0])


def lendable_left(db: Path, today: str, *,
                  exclude: str = "") -> dict[str, int]:
    """전략별 남은 대여 여유 (lendable − loaned). 0 이하는 빼고 strategy 정렬."""
    # PLAN_FUNDING_ENGINE P8
    with connect_ro(db) as con:
        rows = con.execute(
            "SELECT strategy, amount FROM lendable WHERE date = ?"
            " ORDER BY strategy",
            (today,),
        ).fetchall()
        loan_rows = con.execute(
            "SELECT lender, COALESCE(SUM(amount), 0) FROM loans"
            " WHERE date = ? GROUP BY lender",
            (today,),
        ).fetchall()
    loaned = {l: int(s) for l, s in loan_rows}
    out: dict[str, int] = {}
    for strat, amt in rows:
        if strat == exclude:
            continue
        left = int(amt) - loaned.get(strat, 0)
        if left > 0:
            out[strat] = left
    return out


def settle_loans(db: Path, borrower: str, today: str, *, borrower_pnl: int,
                 deployed: int) -> list[dict]:
    """그날 borrower 미정산 loans 정산 (Q1: 빌린 몫 손익 전부 lender). 정산 행 반환."""
    # PLAN_FUNDING_ENGINE P8
    with connect_rw(db) as con:
        rows = con.execute(
            "SELECT id, lender, borrower, amount FROM loans"
            " WHERE date = ? AND borrower = ? AND settled = 0 ORDER BY id",
            (today, borrower),
        ).fetchall()
        if not rows:
            return []
        total = sum(int(r[3]) for r in rows)
        eff = 0 if deployed <= 0 else max(deployed, total)
        out: list[dict] = []
        for lid, lender, borr, amt in rows:
            amt_i = int(amt)
            pnl = 0 if eff <= 0 else round(borrower_pnl * amt_i / eff)
            con.execute(
                "UPDATE loans SET lender_pnl = ?, settled = 1 WHERE id = ?",
                (pnl, lid),
            )
            out.append({"id": lid, "lender": lender, "borrower": borr,
                        "amount": amt_i, "lender_pnl": pnl})
        return out


def loan_pnl_since(db: Path, strategy: str, since: str) -> int:
    """since 이후 빌려준 몫 손익 합 (lender 기준). DB 없으면 0."""
    # PLAN_FUNDING_ENGINE P8
    if not Path(db).exists():
        return 0
    try:
        with connect_ro(db) as con:
            if since:
                row = con.execute(
                    "SELECT COALESCE(SUM(lender_pnl), 0) FROM loans"
                    " WHERE lender = ? AND settled = 1 AND date >= ?",
                    (strategy, since),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT COALESCE(SUM(lender_pnl), 0) FROM loans"
                    " WHERE lender = ? AND settled = 1",
                    (strategy,),
                ).fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return 0
    return int(row[0])


def loan_pnl_paid_since(db: Path, strategy: str, since: str) -> int:
    """since 이후 빌린 몫 손익 합 (borrower 기준, 예산에서 뺄 몫). DB 없으면 0."""
    # PLAN_FUNDING_ENGINE P8
    if not Path(db).exists():
        return 0
    try:
        with connect_ro(db) as con:
            if since:
                row = con.execute(
                    "SELECT COALESCE(SUM(lender_pnl), 0) FROM loans"
                    " WHERE borrower = ? AND settled = 1 AND date >= ?",
                    (strategy, since),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT COALESCE(SUM(lender_pnl), 0) FROM loans"
                    " WHERE borrower = ? AND settled = 1",
                    (strategy,),
                ).fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return 0
    return int(row[0])


def pending_buy_amount(db: Path, today: str) -> int:
    """오늘 buy 주문 중 submitted/partial 의 미체결 금액 합."""
    with connect_ro(db) as con:
        rows = con.execute(
            "SELECT qty, filled_qty, price FROM etf_orders"
            " WHERE date = ? AND side = 'buy'"
            " AND status IN ('submitted', 'partial')",
            (today,),
        ).fetchall()
    return sum((qty - filled_qty) * price for qty, filled_qty, price in rows)


def engine_reserve(db: Path, today: str = "") -> int:
    """엔진 예약 줄 금액."""
    # ETF 를 사면 계좌 현금이 줄어드니 그 원가만큼 엔진 예약도 줄여야 안 옮긴 전략 가용이 그대로 (D3).
    pending = pending_buy_amount(db, today) if today else 0
    return max(0, total_unused(db) - etf_position(db)["cost"] - pending)


def sync_reservation(db: Path, *, io, today: str) -> int:
    """엔진 예약 줄 쓰기. 쓴 reserve 반환."""
    reserve = engine_reserve(db, today)
    io.write_reservation(reserve, max(0, total_unused(db)))
    return reserve


def etf_value(qty: int, price: int, slip: float = SELL_SLIP) -> int:
    """매도 가정 ETF 가치 (슬리피지 반영, 내림)."""
    if qty is None or price is None:
        return 0
    if qty <= 0 or price <= 0:
        return 0
    return int(qty * price * (1 - slip))


def cash_view(account_cash: int | None, reservations: dict | None,
              member_names: list[str]) -> int | None:
    """엔진이 쓸 수 있는 현금."""
    # member 의 옛 예약 줄이 남아 있어도 이중 차감하지 않으려고 member 키를 뺀다.
    if account_cash is None:
        return None
    if reservations is None:
        return None
    skip = set(member_names) | {ENGINE_KEY}
    total = 0
    for name, v in reservations.items():
        if name in skip:
            continue
        if not isinstance(v, dict):
            return 0
        r = v.get("reserve")
        if isinstance(r, bool) or not isinstance(r, int) or r < 0:
            return 0
        total += r
    return max(0, account_cash - total)


def available(db: Path, strategy: str, *, account_cash: int | None,
              reservations: dict | None, balance_qty: int | None,
              etf_price: int | None) -> dict:
    """전략 가용 조회 (순수 계산 + DB 읽기)."""
    cash = cash_view(account_cash, reservations, members(db))
    eqty = sellable_qty(db, balance_qty)
    if (etf_price is None or isinstance(etf_price, bool)
            or not isinstance(etf_price, (int, float)) or etf_price <= 0):
        ev = 0
    else:
        ev = etf_value(eqty, etf_price)
    pool = None if cash is None else cash + ev
    unused = unused_of(db, strategy)
    grantable = 0 if pool is None else min(unused, pool)
    return {"cash": cash, "etf_qty": eqty, "etf_value": ev,
            "pool": pool, "unused": unused, "grantable": grantable}


class LiveIO:
    """실제 broker 어댑터 — 테스트는 같은 메서드를 가진 가짜를 쓴다."""
    def __init__(self, broker_url: str, *, dry_run: bool = False) -> None:
        self._url = broker_url
        self._dry_run = dry_run
        self._broker = BrokerClient(
            broker_url,
            sources={"buy": "funding_buy", "sell": "funding_sell"},
            dry_run=dry_run, log_tag="funding")

    def cash(self) -> int | None:
        return available_cash(self._url, exclude_reserve=False)

    def reservations(self) -> dict | None:
        _data, corrupt = _load_reservation_file()
        if corrupt:
            return None
        return read_reservations(self._url)

    def balance_qty(self) -> int | None:
        held = held_quantities(fetch_balance(self._url))
        if held is None:
            return None
        return held.get(ETF_TICKER, 0)

    def ref_price(self, preopen: bool) -> int | None:
        if preopen:
            return self._broker.base_price(ETF_TICKER)
        return self._broker.best_quote(ETF_TICKER, "sell")

    def sell_market(self, qty: int) -> dict:
        return self._broker.place_market(ETF_TICKER, "sell", qty)

    def cancel_sell(self, no: str) -> dict:
        return self._broker.cancel(no, ETF_TICKER)

    def _fills(self, date: str, side: str) -> dict[str, dict] | None:
        rows = self._broker.history(date, side)
        if rows is None:
            return None
        agg: dict[str, dict] = {}
        for row in rows:
            key = normalize_order_no(row.get("order_no"))
            if not key:
                continue
            try:
                q = int(row.get("cntr_qty") or 0)
                uv = int(row.get("cntr_uv") or 0)
            except (TypeError, ValueError):
                continue
            slot = agg.setdefault(key, {"qty": 0, "amt": 0})
            slot["qty"] += q
            slot["amt"] += q * uv
        return {key: {"qty": slot["qty"],
                      "price": (slot["amt"] / slot["qty"]
                                if slot["qty"] > 0 else None)}
                for key, slot in agg.items()}

    def fills(self, date: str) -> dict[str, dict] | None:
        return self._fills(date, "sell")

    def buy_fills(self, date: str) -> dict[str, dict] | None:
        return self._fills(date, "buy")

    def ask(self) -> int | None:
        return self._broker.best_quote(ETF_TICKER, "buy")

    def buy_limit(self, qty: int, price: int) -> dict:
        return self._broker.place_limit(ETF_TICKER, "buy", qty, price)

    def write_reservation(self, reserve: int, capital_total: int) -> None:
        if self._dry_run:
            print(f"[funding] DRY-RUN 예약 reserve={reserve:,}"
                  f" capital_total={capital_total:,}")
            return
        _file_write_reservation(self._url, reserve, capital_total, ENGINE_KEY)

    def now(self) -> datetime:
        return now_seoul()

    def sleep(self, sec: float) -> None:
        time.sleep(sec)


_UNKNOWN_SELL_NOTE = "⚠ ETF 매도 결과 불명 — 재주문 안 함, 수동 확인"


def _record_sell_pnl(db: Path, order_no: str, date: str, fq: int,
                     avg: float | None, pos: dict) -> None:
    """매도 체결분 실현손익 기록. pos는 update_fill 전 etf_position."""
    if fq > 0 and avg is not None and pos["qty"] > 0:
        record_pnl(db, date=date, order_no=order_no,
                   amount=round((avg - pos["cost"] / pos["qty"]) * fq))


def _sell_and_confirm(db: Path, qty: int, *, io, today: str,
                      preopen: bool, reason: str) -> dict:
    """ETF 시장가 매도 후 체결 확인. {"filled_qty", "avg_price", "notes", "order_no", "recorded"}."""
    notes: list[str] = []
    # P0 실측 대기: 장전 시장가가 안 되면 하한가 지정가로 대체 (PLAN_FUNDING_ENGINE P0-3)
    r = io.sell_market(qty)
    status = r.get("status")
    order_no = r.get("order_no") or ""
    if status == "dry_run":
        notes.append(f"DRY-RUN ETF 매도 {qty}주")
        return {"filled_qty": 0, "avg_price": None, "notes": notes,
                "order_no": order_no, "recorded": False}
    if status == "submitted":
        if not order_no:
            notes.append("⚠ ETF 매도 접수번호 없음 — 수동 확인")
            return {"filled_qty": 0, "avg_price": None, "notes": notes,
                    "order_no": order_no, "recorded": False}
        record_order(db, date=today, side="sell", qty=qty, price=0,
                     order_no=order_no, status="submitted", reason=reason,
                     created=io.now().isoformat())
    elif status == "unknown":
        notes.append(_UNKNOWN_SELL_NOTE)
        if order_no:
            record_order(db, date=today, side="sell", qty=qty, price=0,
                         order_no=order_no, status="unknown", reason=reason,
                         created=io.now().isoformat())
        return {"filled_qty": 0, "avg_price": None, "notes": notes,
                "order_no": order_no, "recorded": bool(order_no)}
    else:
        notes.append(f"⚠ ETF 매도 거부: {r.get('message', '')}")
        return {"filled_qty": 0, "avg_price": None, "notes": notes,
                "order_no": order_no, "recorded": False}
    if preopen:
        now = io.now()
        target = market_session.at(now.strftime("%Y%m%d"), PREOPEN_FILL_AT).replace(tzinfo=now.tzinfo)
        while True:
            remaining = (target - io.now()).total_seconds()
            if remaining <= 0:
                break
            io.sleep(min(remaining, 30))
    pos = etf_position(db)  # 매도 직전 평균원가 (update_fill 전)
    order_key = normalize_order_no(order_no)
    fq = 0
    avg = None
    start = io.now()
    while True:
        fills = io.fills(today)
        if fills is not None:
            hit = fills.get(order_key)
            if hit is not None:
                fq = min(max(int(hit.get("qty") or 0), 0), qty)
                raw = hit.get("price")
                avg = None if raw is None else float(raw)
                if fq >= qty:
                    break
        if (io.now() - start).total_seconds() >= FILL_POLL_MAX_SEC:
            break
        io.sleep(FILL_POLL_SEC)
    update_fill(db, order_no,
                status=("filled" if fq >= qty
                        else "partial" if fq > 0 else "submitted"),
                filled_qty=fq, filled_price=avg or None)
    if fq >= qty:
        # 세금·수수료는 P0-6 분배금 확인 후 — 수수료·세금 미반영
        # 전량 체결 시에만 기록; 부분이면 reconcile 이 최종 시점에 기록
        _record_sell_pnl(db, order_no, today, fq, avg, pos)
    return {"filled_qty": fq, "avg_price": avg, "notes": notes,
            "order_no": order_no, "recorded": True}


def request_funds(db: Path, strategy: str, amount: int, *, io, today: str,
                  preopen: bool, enabled: bool = True,
                  borrow_max: int | None = None) -> dict:
    """자금 요청 — 현금이 모자랄 때만 ETF 매도로 확보 후 허가.

    {"granted", "sold_qty"(체결 합), "notes"}.
    notes 는 호출 전략이 알림에 붙일 한국어 줄(⚠ 로 시작하면 경고).
    """
    ensure(db)
    # PLAN_FUNDING_ENGINE P8 — borrow_max 검증 (None 이면 대여 없음, 기존과 동일)
    if borrow_max is not None:
        if isinstance(borrow_max, bool) or not isinstance(borrow_max, int):
            raise ValueError(f"borrow_max는 int: {borrow_max!r}")
        if borrow_max < 0:
            raise ValueError(f"borrow_max는 0 이상: {borrow_max!r}")
    notes: list[str] = []
    rec = reconcile(db, io=io, today=today)
    notes.extend(rec.get("notes", []))
    if amount <= 0:
        return {"granted": 0, "sold_qty": 0, "notes": notes}
    px = io.ref_price(preopen)
    a = available(db, strategy, account_cash=io.cash(),
                  reservations=io.reservations(),
                  balance_qty=io.balance_qty(), etf_price=px)
    cash = a["cash"]
    unused = a["unused"]
    # PLAN_FUNDING_ENGINE P8 — 대여 한도 계산
    left: dict[str, int] = {}
    can_borrow = 0
    if borrow_max is not None:
        with connect_rw(db) as con:
            con.execute(
                "INSERT INTO borrow_caps(date, borrower, max_amount, at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(date, borrower) DO UPDATE SET"
                " max_amount=excluded.max_amount, at=excluded.at",
                (today, strategy, borrow_max, io.now().isoformat()),
            )
        left = lendable_left(db, today, exclude=strategy)
        with connect_ro(db) as con:
            brow = con.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM loans"
                " WHERE date = ? AND borrower = ?",
                (today, strategy),
            ).fetchone()
        loaned_by = int(brow[0])
        can_borrow = min(borrow_max - loaned_by, sum(left.values()))
        if can_borrow < 0:
            can_borrow = 0
    if borrow_max is None:
        want = min(amount, unused)
        if want < amount:
            notes.append(f"전략 안 쓴 몫 {unused:,} 로 줄임")
    else:
        cap = unused + can_borrow
        want = min(amount, cap)
        if want < amount:
            notes.append(f"전략 안 쓴 몫 + 대여 {cap:,} 로 줄임")

    def _record_borrow(granted_amt: int) -> None:
        # PLAN_FUNDING_ENGINE P8 — 빌린 몫 loans 기록 + notes 한 줄
        if borrow_max is None:
            return
        borrowed = max(0, granted_amt - unused)
        if borrowed <= 0 or not left:
            return
        total = sum(left.values())
        if total <= 0:
            return
        lenders = sorted(left.keys())
        shares: dict[str, int] = {}
        acc = 0
        for lender in lenders:
            s = borrowed * left[lender] // total
            shares[lender] = s
            acc += s
        rest = borrowed - acc
        if rest > 0:
            shares[lenders[0]] += rest
        at = io.now().isoformat()
        with connect_rw(db) as con:
            for lender in lenders:
                amt = shares[lender]
                if amt > 0:
                    con.execute(
                        "INSERT INTO loans(date, lender, borrower, amount, at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (today, lender, strategy, amt, at),
                    )
        parts = ", ".join(f"{l} {shares[l]:,}" for l in lenders
                          if shares[l] > 0)
        notes.append(f"대여 {borrowed:,} ({parts})")
    if cash is None:
        notes.append("⚠ 주문가능 조회 실패")
        return {"granted": 0, "sold_qty": 0, "notes": notes}
    if cash >= want:
        _record_borrow(want)
        return {"granted": want, "sold_qty": 0, "notes": notes}
    if not enabled:
        _record_borrow(min(want, cash))
        return {"granted": min(want, cash), "sold_qty": 0, "notes": notes}
    short = want - cash
    if (px is None or isinstance(px, bool)
            or not isinstance(px, (int, float)) or px <= 0):
        notes.append(f"⚠ ETF 기준가 조회 실패 — 현금 {cash:,} 만")
        _record_borrow(cash)
        return {"granted": cash, "sold_qty": 0, "notes": notes}
    k = min(math.ceil(short / (px * (1 - SELL_SLIP))), a["etf_qty"])
    if k <= 0:
        notes.append(f"⚠ 팔 ETF 없음 — 부족 {short:,}")
        _record_borrow(cash)
        return {"granted": cash, "sold_qty": 0, "notes": notes}
    first = _sell_and_confirm(db, k, io=io, today=today, preopen=preopen,
                              reason=f"{strategy} {short:,}")
    notes.extend(first["notes"])
    sold_qty = first["filled_qty"]
    fresh = cash_view(io.cash(), io.reservations(), members(db))
    if fresh is not None:
        cash = fresh
    if cash < want and _UNKNOWN_SELL_NOTE not in first["notes"]:
        fq, avg = first["filled_qty"], first["avg_price"]
        floor = px * (1 - SELL_SLIP)
        unfilled = fq < k
        low_px = avg is not None and avg < floor
        if unfilled:
            notes.append(f"⚠ ETF 매도 미체결 {k - fq}주")
        if low_px:
            notes.append(f"⚠ ETF 체결가 {round(avg):,} < 예상 {round(floor):,}")
        if not unfilled and not low_px:
            notes.append("⚠ 원인 불명 부족")
        quote = io.ref_price(False)
        if (quote is None or isinstance(quote, bool)
                or not isinstance(quote, (int, float)) or quote <= 0):
            k2 = 0
        else:
            k2 = min(math.ceil((want - cash) / (quote * (1 - SELL_SLIP))),
                     sellable_qty(db, io.balance_qty()))
        if k2 > 0:
            first_no = first.get("order_no") or ""
            proceed = True
            if first["filled_qty"] < k and first.get("recorded"):
                cancel_r = io.cancel_sell(first_no)
                if cancel_r.get("status") == "cancelled":
                    pos = etf_position(db)
                    update_fill(db, first_no, status="cancelled",
                                filled_qty=first["filled_qty"],
                                filled_price=first["avg_price"])
                    _record_sell_pnl(db, first_no, today,
                                     first["filled_qty"], first["avg_price"],
                                     pos)
                else:
                    notes.append(
                        "⚠ 첫 ETF 매도 취소 실패 — 추가 매도 안 함(이중 매도 방지)")
                    proceed = False
            if proceed:
                second = _sell_and_confirm(db, k2, io=io, today=today,
                                           preopen=False,
                                           reason=f"{strategy} {want - cash:,}")
                notes.extend(second["notes"])
                sold_qty += second["filled_qty"]
        fresh2 = cash_view(io.cash(), io.reservations(), members(db))
        if fresh2 is not None:
            cash = fresh2
    granted = min(want, cash)
    if granted < want:
        notes.append(f"⚠ 부족 {want - granted:,} — 있는 만큼만")
    _record_borrow(granted)
    if sold_qty >= 1:
        sync_reservation(db, io=io, today=today)
    return {"granted": granted, "sold_qty": sold_qty, "notes": notes}


def sweep(db: Path, *, io, today: str, enabled: bool = True) -> dict:
    """마감 매수 — 남는 돈으로 ETF 를 종가 단일가에 지정가 매수.

    {"ordered_qty", "notes"}.
    """
    ensure(db)
    notes: list[str] = []
    if not enabled:
        sync_reservation(db, io=io, today=today)
        return {"ordered_qty": 0, "notes": notes}
    # PLAN_FUNDING_ENGINE D13 — sweep 는 etf=1 회원 몫만 쓴다
    idle = (total_unused(db, etf_only=True) - etf_position(db)["cost"]
            - pending_buy_amount(db, today))
    # PLAN_FUNDING_ENGINE P8 — 빌려줄 돈은 ETF 에 안 넣고 현금 대기 (Q3)
    with connect_ro(db) as con:
        row_l = con.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM lendable WHERE date = ?",
            (today,),
        ).fetchone()
        row_c = con.execute(
            "SELECT COALESCE(SUM(max_amount), 0) FROM borrow_caps"
            " WHERE date = ?",
            (today,),
        ).fetchone()
    hold_back = min(int(row_l[0]), int(row_c[0]))
    if hold_back < 0:
        hold_back = 0
    idle = max(0, idle - hold_back)
    if hold_back > 0:
        notes.append(f"대여 대기 현금 {hold_back:,} (ETF 제외)")
    cash = cash_view(io.cash(), io.reservations(), members(db))
    if cash is None:
        notes.append("⚠ 주문가능 조회 실패")
        sync_reservation(db, io=io, today=today)
        return {"ordered_qty": 0, "notes": notes}
    budget = min(idle, cash)
    ask = io.ask()
    if isinstance(ask, bool) or not isinstance(ask, (int, float)) or ask <= 0:
        notes.append("⚠ ETF 매도1호가 조회 실패")
        sync_reservation(db, io=io, today=today)
        return {"ordered_qty": 0, "notes": notes}
    px = int(ask) + ETF_TICK * BUY_TICKS_UP
    q = budget // px
    if q < 1:
        sync_reservation(db, io=io, today=today)
        return {"ordered_qty": 0, "notes": notes}
    cap = max_order_amount()
    if cap < px:
        notes.append(f"⚠ 1건 상한 {cap:,} < 1주 {px:,} — 매수 안 함"
                     " (MAX_ORDER_AMOUNT 확인)")
        sync_reservation(db, io=io, today=today)
        return {"ordered_qty": 0, "notes": notes}
    per = max(1, cap // px)
    ordered = 0
    rest = q
    while rest > 0:
        n = min(per, rest)
        r = io.buy_limit(n, px)
        status = r.get("status")
        order_no = r.get("order_no") or ""
        if status == "submitted":
            if not order_no:
                notes.append("⚠ ETF 매수 접수번호 없음 — 수동 확인")
                break
            record_order(db, date=today, side="buy", qty=n, price=px,
                         order_no=order_no, status="submitted", reason="sweep",
                         created=io.now().isoformat())
            ordered += n
            rest -= n
        elif status == "dry_run":
            notes.append(f"DRY-RUN ETF 매수 {n}주 @{px:,}")
            rest -= n
        elif status == "unknown":
            notes.append("⚠ ETF 매수 결과 불명 — 재주문 안 함")
            if order_no:
                record_order(db, date=today, side="buy", qty=n, price=px,
                             order_no=order_no, status="unknown",
                             reason="sweep", created=io.now().isoformat())
            break
        else:
            notes.append(f"⚠ ETF 매수 거부: {r.get('message', '')}")
            break
    # P0 실측 대기: 마감 미체결분 애프터마켓 매수 (PLAN_FUNDING_ENGINE P0-5) — 실측 전엔 다음날로 넘김
    sync_reservation(db, io=io, today=today)
    return {"ordered_qty": ordered, "notes": notes}


def reconcile(db: Path, *, io, today: str) -> dict:
    """미체결 주문 재대조 (매수·매도, 지난 날짜 포함). {"filled_qty", "notes"}."""
    ensure(db)
    notes: list[str] = []
    with connect_ro(db) as con:
        rows = con.execute(
            "SELECT date, side, order_no, qty FROM etf_orders"
            " WHERE status IN ('submitted', 'partial') ORDER BY id"
        ).fetchall()
    if not rows:
        sync_reservation(db, io=io, today=today)
        return {"filled_qty": 0, "notes": notes}
    groups: list[tuple[str, str]] = []
    for d, s, _no, _q in rows:
        if (d, s) not in groups:
            groups.append((d, s))
    cache: dict[tuple[str, str], dict | None] = {}
    for d, s in groups:
        fills = io.buy_fills(d) if s == "buy" else io.fills(d)
        if fills is None:
            notes.append(f"⚠ {d} {s} 체결 조회 실패 — 다음에 재대조")
        cache[(d, s)] = fills
    total = 0
    for d, s, order_no, qty in rows:
        fills = cache[(d, s)]
        if fills is None:
            continue
        hit = fills.get(normalize_order_no(order_no)) or {}
        fq = min(max(int(hit.get("qty") or 0), 0), qty)
        raw = hit.get("price")
        avg = None if raw is None else float(raw)
        if fq >= qty:
            status = "filled"
        elif d < today:
            status = "cancelled"
        elif fq > 0:
            status = "partial"
        else:
            status = "submitted"
        if s == "sell" and status in ("filled", "cancelled"):
            pos = etf_position(db)
            update_fill(db, order_no, status=status, filled_qty=fq,
                        filled_price=avg)
            _record_sell_pnl(db, order_no, d, fq, avg, pos)
        else:
            update_fill(db, order_no, status=status, filled_qty=fq,
                        filled_price=avg)
        total += fq
    sync_reservation(db, io=io, today=today)
    return {"filled_qty": total, "notes": notes}


def confirm_buys(db: Path, *, io, today: str) -> dict:
    """매수 체결 확인 (15:31 실행용) — reconcile 얇은 래퍼."""
    return reconcile(db, io=io, today=today)


def main(argv=None) -> int:
    """15:21 마감매수 진입점 — sweep → confirm 시각까지 대기 → confirm → 알림.

    종료 코드: 0 정상(휴장일 skip 포함), 3 예외 발생(FAIL 알림 보냄 —
    러너 중복 알림 금지), 2 알림 자체 실패(러너가 대신 알림).
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="공용 자금 엔진 15:21 마감매수")
    parser.add_argument("--broker-url", default="http://localhost:8001")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-at", default="15:31:00")
    args = parser.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    today = now_seoul().strftime("%Y%m%d")
    if not is_krx_trading_day(today):
        print(f"[funding] {today}: 휴장일 — sweep skip (알림 없음)")
        return 0
    try:
        io = LiveIO(args.broker_url, dry_run=args.dry_run)
        s = sweep(DB, io=io, today=today)
        now = io.now()
        target = market_session.at(now.strftime("%Y%m%d"), args.confirm_at).replace(tzinfo=now.tzinfo)
        while True:
            remaining = (target - io.now()).total_seconds()
            if remaining <= 0:
                break
            io.sleep(min(remaining, 30))
        c = confirm_buys(DB, io=io, today=today)
        notes = list(s.get("notes", [])) + list(c.get("notes", []))
        pos = etf_position(DB)
        mark = (HEADLINE_WARN
                if any(n.startswith("⚠") for n in notes)
                else HEADLINE_OK)
        result = f"주문 {s['ordered_qty']}주·체결 {c['filled_qty']}주"
        body = "\n".join([
            f"매수 주문: {s['ordered_qty']}주",
            f"체결: {c['filled_qty']}주",
            f"보유: {pos['qty']}주 (원가 {pos['cost']:,}원)",
            *notes,
        ])
        msg = headline(mark, "자금엔진", "15:21 마감매수", result, body)
        print(msg)
        send_strategy_alert(msg, "FUNDING_DISCORD_WEBHOOK_URL")
        return 0
    except Exception:
        traceback.print_exc()
        try:
            fail_msg = headline(
                HEADLINE_FAIL, "자금엔진", "15:21 마감매수", "예외 발생",
                traceback.format_exc(limit=5)[-1500:])
            ok = send_strategy_alert(fail_msg, "FUNDING_DISCORD_WEBHOOK_URL")
        except Exception:
            return 2
        return 3 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
