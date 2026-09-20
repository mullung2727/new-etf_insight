"""종가베팅 청산(익절/손절) 장중 워커 — 3초 폴링.

T일 15:19 매수한 오버나이트 보유를 T+1 장중 broker `/quotes`(ka10095) 3초 폴링으로
감시해 buy_bid 기준 +tp 익절 / -sl 손절, 미발동분은 force-exit-time 강제청산한다.

강제청산 시각은 close_bet.json 의 `exit_time`(기본 운용값 09:01:00)에서 읽는다 — ps1 인자가
아니라 config 가 단일 소스라 값만 바꾸면 다음 기동부터 반영된다.
tp·sl 이 null 이면 장중 판정 없이 그 시각에 전량 매도한다(백테스트 채택 운용).

핵심 사상: Kiwoom API는 무조건 broker를 통해서만 호출한다. 워커는 100% REST(WS 미구독),
인메모리 영속 상태 0 — 매 기동 DB+잔고에서 watch set 재부팅.

⚠️ G2 단계: 판정로직 + 드라이런만. 실주문(POST /orders)·sell 상태머신·ka10075 체결확인은
G3에서 추가. 본 단계 dry-run은 "팔 것"을 로깅만 하고 close_bet_orders sell 컬럼을 건드리지 않는다.

Usage (from etl/):
    .venv/Scripts/python.exe scripts/run_close_bet_exit.py --dry-run true \
        --watch-codes 005930,000660 --poll-sec 3 --stop-time 15:25:00
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import load_dotenv

try:  # 직접 실행(scripts/ on path) / 패키지 import(tests) 양쪽 지원
    from scripts.notify import send_discord
    from scripts.run_close_bet import ensure_exit_columns
    from scripts.run_verify import normalize_order_no
    from scripts.wl_sqlite import connect_ro, connect_rw
    from scripts.close_bet_config import load as load_close_bet_config
    # 연속매매 창(09:00~15:20) 판정은 주문창 판정과 동일 로직 — 공용 함수를 도메인 이름으로 씀
    from scripts.trading_batch_common import (
        REQUEST_TIMEOUT, fetch_realized, held_quantities,
        in_order_window as in_trading_window, now_seoul as _now_seoul,
    )
    from scripts.run_pullback_order import _fallback_tick_size as tick_size
except ImportError:
    from notify import send_discord
    from run_close_bet import ensure_exit_columns
    from run_verify import normalize_order_no
    from wl_sqlite import connect_ro, connect_rw
    from close_bet_config import load as load_close_bet_config
    from trading_batch_common import (
        REQUEST_TIMEOUT, fetch_realized, held_quantities,
        in_order_window as in_trading_window, now_seoul as _now_seoul,
    )
    from run_pullback_order import _fallback_tick_size as tick_size

ROOT =Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"

# 반반 분할 청산 (docs/PLAN_CLOSE_BET_SPLIT_EXIT.md) — auction 은 exit_time 동시호가, chase 는 아래 시각
SPLIT_LEGS = ("auction", "chase")
CHASE_ROUNDS = ("09:00:30", "09:00:40", "09:00:50")   # 매도1호가 − 1틱 지정가 신규/정정
CHASE_END = "09:01:00"                                # 남은 지정가 취소 → 잔량 시장가


# ── 순수 판정함수 (부작용 0, 단위테스트 핵심) ──────────────────────────────────

def decide_exit(
    buy_bid: int | None, cntr_price: int | None, tp: float | None, sl: float | None
) -> str | None:
    """buy_bid(시장가 매도 실현가) 기준 TP/SL 판정. 'tp'/'sl'/None.

    tp·sl 중 하나라도 None(=close_bet.json 에서 끔)이면 장중 판정을 아예 안 한다 —
    강제청산 시각에 전량 매도하는 운용이 되고, 익절만/손절만 쓰는 조합은 지원하지 않는다.
    호가공백(buy_bid 없음/0)·체결가 없음 → None(skip).
    """
    if tp is None or sl is None:
        return None
    if not buy_bid or not cntr_price:
        return None
    chg = buy_bid / cntr_price - 1
    if chg >= tp:
        return "tp"
    if chg <= -sl:
        return "sl"
    return None


def _parse_hms(hms: str) -> tuple[int, int, int]:
    h, m, s = (int(x) for x in hms.split(":"))
    return h, m, s


def _at(now: datetime, hms: str) -> datetime:
    h, m, s = _parse_hms(hms)
    return now.replace(hour=h, minute=m, second=s, microsecond=0)


def is_force_time(now: datetime, force_hms: str) -> bool:
    """강제청산 시각 도달(now >= force-exit-time)."""
    return now >= _at(now, force_hms)


def should_force(now: datetime, force_hms: str, window_end: str) -> bool:
    """강제청산 발동 = force-exit-time 도달 AND 연속매매 마감 전.

    창 시작(09:00) 조건은 없다 — exit_time 08:55 면 개장 동시호가에 시장가로 낸다.
    """
    return is_force_time(now, force_hms) and now < _at(now, window_end)


def chase_price(sel_bid: int | None, lower_limit: int | None) -> int | None:
    """추격 지정가 = 매도1호가 − 1틱(그 아래 가격대 호가단위), 하한가 미만이면 하한가. 호가 없으면 None."""
    if not sel_bid:
        return None
    price = sel_bid - tick_size(sel_bid - 1)
    return max(price, lower_limit) if lower_limit else price


def due_round(now: datetime) -> int:
    """지금까지 도래한 추격 회차 수 (0 = 아직, 1~3)."""
    return sum(now >= _at(now, hms) for hms in CHASE_ROUNDS)


def is_chase_end(now: datetime) -> bool:
    return now >= _at(now, CHASE_END)


def is_locked(
    cur_prc: int | None, lst_pric: int | None, pred_pre_sig: str
) -> bool:
    """하한가 lock 감지(로깅용) — 시장가 매도 미체결 예상."""
    if pred_pre_sig == "4":
        return True
    return bool(cur_prc) and bool(lst_pric) and cur_prc == lst_pric


# ── 포지션 로드 + 잔고 대조 ────────────────────────────────────────────────────

def _strip_code(code: str) -> str:
    code = str(code).strip()
    return code[1:] if len(code) == 7 and code[:1] == "A" else code


def _label(key: tuple[str, str]) -> str:
    """로그 표기 — 미분할(single)은 종목코드만, 분할 줄은 종목(leg)."""
    ticker, leg = key
    return ticker if leg == "single" else f"{ticker}({leg})"


def _padint(val: object) -> int:
    try:
        return int(str(val).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0


def load_unsold_positions(con, today: str) -> list[dict]:
    """청산 대상 = confirmed(매수 체결확정) + sell_status NULL + 과거날짜(오버나이트분).

    status 생애: submitted(매수) → confirmed(run_verify 체결대조 성공). 'filled'은
    매도쪽 sell_status 값이지 status 값이 아니다.
    """
    ensure_exit_columns(con)
    rows = con.execute(
        """
        SELECT date, ticker, cntr_price, qty, leg
        FROM close_bet_orders
        WHERE status='confirmed' AND sell_status IS NULL AND date < ?
        """,
        [today],
    ).fetchall()
    return [
        {"date": r[0], "ticker": r[1], "cntr_price": r[2], "qty": r[3], "leg": r[4]}
        for r in rows
    ]


def split_positions(db_path, positions: list[dict]) -> list[dict]:
    """leg='single' 포지션을 auction(ceil)/chase(floor) 두 줄로 쪼갠다. 이미 쪼갠 줄은 그대로.

    기준 수량 = qty_eff(잔고대조 후). 1주면 auction 만. 원래 줄이 auction 이 되고 chase 줄은
    매수 컬럼을 복사해 새로 넣는다 — 두 줄 qty·cntr_qty 합 = 기준 수량. 한 트랜잭션.
    """
    out: list[dict] = []
    with connect_rw(db_path) as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(close_bet_orders)")
                if r[1] not in ("leg", "qty", "cntr_qty")]
        col_list = ", ".join(cols)
        for p in positions:
            if p["leg"] != "single":
                out.append(p)
                continue
            base = int(p["qty_eff"])
            auction, chase = base - base // 2, base // 2
            key = (p["date"], p["ticker"])
            if chase:
                con.execute(
                    f"INSERT INTO close_bet_orders ({col_list}, leg, qty, cntr_qty) "
                    f"SELECT {col_list}, 'chase', ?, ? FROM close_bet_orders "
                    "WHERE date=? AND ticker=? AND leg='single'",
                    [chase, chase, *key])
            con.execute(
                "UPDATE close_bet_orders SET leg='auction', qty=?, cntr_qty=? "
                "WHERE date=? AND ticker=? AND leg='single'",
                [auction, auction, *key])
            out.append({**p, "leg": "auction", "qty": auction, "qty_eff": auction})
            if chase:
                out.append({**p, "leg": "chase", "qty": chase, "qty_eff": chase})
    return out


def reconcile_balance(positions: list[dict], balance: dict) -> list[dict]:
    """기록 포지션 ∩ 키움 잔고 → 매도가능수량(trde_able_qty)만큼만 감시.

    미보유 종목은 제외. qty_eff = min(기록 qty, 매도가능). 부분체결·괴리 방지.
    """
    held = {
        _strip_code(h.get("stk_cd", "")): _padint(h.get("trde_able_qty"))
        for h in balance.get("acnt_evlt_remn_indv_tot", []) or []
    }
    out = []
    for p in positions:
        avail = held.get(p["ticker"], 0)
        qty_eff = min(int(p["qty"] or 0), avail)
        if qty_eff > 0:
            out.append({**p, "qty_eff": qty_eff})
    return out


def mark_missing_positions(db_path, positions: list[dict], balance: dict) -> list[str]:
    """잔고에 없는 포지션을 sell_status='missing' 으로 확정한다.

    감시 대상에서 빼기만 하면 sell_status 가 NULL 로 남아 매일 다시 조회되고,
    중복매수 가드가 그 종목을 계속 '보유중'으로 읽어 매수를 영구 차단한다.

    잔고 조회 실패 시에는 아무것도 마감하지 않는다 — 실패를 '전 종목 미보유'로
    오독하면 실제 보유분까지 유령 처리돼 청산 경로가 통째로 막힌다.
    """
    held = held_quantities(balance)
    if held is None:
        print("[exit] 잔고 조회 실패 — 미보유 마감 처리 건너뜀")
        return []

    missing = [p for p in positions if held.get(p["ticker"], 0) <= 0]
    if not missing:
        return []

    with connect_rw(db_path) as con:
        ensure_exit_columns(con)
        for p in missing:
            con.execute(
                "UPDATE close_bet_orders SET sell_status='missing', sold_at=?, "
                "exit_reason='missing' WHERE date=? AND ticker=? AND leg=? AND sell_status IS NULL",
                (_now_seoul().isoformat(), p["date"], p["ticker"], p["leg"]),
            )
    tickers = [p["ticker"] for p in missing]
    print(f"[exit] 잔고 미보유 {len(tickers)}건 종료 처리(missing): {', '.join(tickers)}")
    return tickers


# ── 매도 상태머신 (close_bet_orders) ──────────────────────────────────────────

def mark_ordered(con, date: str, ticker: str, order_no: str, *, leg: str) -> None:
    """주문 전송 성공(order_no 반환) 후 'ordered' 기록. 재주문 차단용."""
    con.execute(
        "UPDATE close_bet_orders SET sell_status='ordered', sell_order_no=? "
        "WHERE date=? AND ticker=? AND leg=?",
        [order_no, date, ticker, leg],
    )


def mark_filled(
    con, date: str, ticker: str, sell_price: int, sell_qty: int,
    sold_at: str, exit_reason: str, pnl_pct: float,
    sell_cmsn: int | None = None, sell_tax: int | None = None,
    sell_pl_won: int | None = None, *, leg: str,
) -> None:
    """체결확인 후 'filled' + 실현가/손익 확정. 수수료·세금·net손익금은 ka10077분(없으면 NULL)."""
    con.execute(
        "UPDATE close_bet_orders SET sell_status='filled', sell_price=?, sell_qty=?, "
        "sold_at=?, exit_reason=?, pnl_pct=?, sell_cmsn=?, sell_tax=?, sell_pl_won=? "
        "WHERE date=? AND ticker=? AND leg=?",
        [sell_price, sell_qty, sold_at, exit_reason, pnl_pct,
         sell_cmsn, sell_tax, sell_pl_won, date, ticker, leg],
    )


def is_in_flight(key: tuple[str, str], pending: dict, unfilled: dict[str, dict]) -> bool:
    """재주문 가드 — 이 줄이 주문 중(pending) OR 그 종목에 추적 안 되는 미체결 매도주문.

    같은 종목 다른 줄(auction/chase)의 추적 중 주문은 막지 않는다.
    unfilled: fetch_unfilled_orders 결과 {정규화 주문번호: {ticker, oso_qty}}.
    """
    if key in pending:
        return True
    tracked = {normalize_order_no(e["order_no"]) for e in pending.values()}
    return any(o["ticker"] == key[0] and no not in tracked for no, o in unfilled.items())


# ── broker REST 경유 ───────────────────────────────────────────────────────────

def fetch_quotes(broker_url: str, codes: list[str]) -> dict[str, dict]:
    """GET /quotes?codes= → {stk_cd: quote dict}. 실패 시 {}."""
    if not codes:
        return {}
    try:
        resp = requests.get(
            f"{broker_url}/quotes",
            params={"codes": ",".join(codes)},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return {q["stk_cd"]: q for q in resp.json()}
    except Exception as exc:
        print(f"[exit] /quotes 조회 실패(재시도 예정): {exc}")
        return {}


def fetch_balance(broker_url: str) -> dict:
    """GET /account/balance. 실패 시 {}."""
    try:
        resp = requests.get(f"{broker_url}/account/balance", timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"[exit] /account/balance 조회 실패: {exc}")
        return {}


def fetch_unfilled_orders(broker_url: str) -> dict[str, dict]:
    """GET /orders/unfilled?side=sell → {정규화 주문번호: {ticker, oso_qty}}. 실패 시 {}."""
    try:
        resp = requests.get(
            f"{broker_url}/orders/unfilled", params={"side": "sell"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return {normalize_order_no(row["order_no"]): {"ticker": row["ticker"],
                                                      "oso_qty": int(row.get("oso_qty") or 0),
                                                      "ord_price": int(row.get("ord_price") or 0)}
                for row in resp.json()}
    except Exception as exc:
        print(f"[exit] /orders/unfilled 조회 실패: {exc}")
        return {}


def fetch_unfilled_tickers(broker_url: str) -> set[str]:
    """미체결(매도) 종목코드 집합. high52 live(order·exit)가 이 이름으로 쓴다 —
    2026-09-18 이름을 fetch_unfilled_orders 로 바꾸면서 high52 배치가 ImportError 로 죽었다."""
    return {v["ticker"] for v in fetch_unfilled_orders(broker_url).values()}


def fetch_sell_fills(broker_url: str, date: str) -> dict[str, dict]:
    """GET /orders/history?side=sell → {normalize_order_no: {cntr_uv, cntr_qty}}.

    부분체결 대비 동일 order_no의 cntr_qty 합산, 단가는 첫 유효값.
    """
    try:
        resp = requests.get(
            f"{broker_url}/orders/history",
            params={"date": date, "side": "sell"}, timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"[exit] /orders/history(sell) 조회 실패: {exc}")
        return {}
    by_no: dict[str, dict] = {}
    for item in resp.json():
        key = normalize_order_no(item.get("order_no"))
        if not key:
            continue
        agg = by_no.setdefault(key, {"cntr_uv": 0, "cntr_qty": 0})
        agg["cntr_qty"] += int(item.get("cntr_qty") or 0)
        if not agg["cntr_uv"]:
            agg["cntr_uv"] = int(item.get("cntr_uv") or 0)
    return by_no


def place_sell_via_broker(
    broker_url: str, ticker: str, qty: int, exchange: str = "SOR", price: int | None = None
) -> dict:
    """POST /orders/strategy 매도 — price 없으면 시장가, 있으면 지정가. {order_no, status, message}.
    거부/실패 시 order_no=''.

    exchange: 개장 동시호가 주문만 KRX — SOR 은 장 시작 전 라우팅 규칙이 문서에 없어 NXT 로 샐 수 있다.
    """
    body = {"symbol": ticker, "side": "sell", "qty": qty, "order_type": "market",
            "source": "close_bet_exit", "exchange": exchange}
    if price:
        body.update(order_type="limit", price=price)
    try:
        resp = requests.post(f"{broker_url}/orders/strategy", json=body, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 422:  # 가드/거부
            return {"order_no": "", "status": "rejected",
                    "message": resp.json().get("detail", "rejected")}
        resp.raise_for_status()
        data = resp.json()
        if not data.get("accepted") or not data.get("order_no"):
            return {"order_no": "", "status": "failed",
                    "message": data.get("message", "accepted=False")}
        return {"order_no": str(data["order_no"]), "status": "submitted",
                "message": data.get("message", "")}
    except Exception as exc:
        return {"order_no": "", "status": "failed", "message": str(exc)}


def cancel_via_broker(broker_url: str, order_no: str, ticker: str, exchange: str) -> bool:
    """DELETE /orders/{order_no} 잔량 전부 취소. exchange = 원주문 거래소."""
    try:
        resp = requests.delete(f"{broker_url}/orders/{order_no}",
                               params={"symbol": ticker, "exchange": exchange}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return bool(resp.json().get("accepted"))
    except Exception as exc:
        print(f"[exit] ⚠️ {ticker} 취소 실패 ord={order_no}: {exc}")
        return False


def modify_via_broker(broker_url: str, order_no: str, ticker: str, price: int) -> str:
    """PATCH /orders/{order_no} 잔량 전부 가격 정정(SOR). 새 주문번호, 실패 시 ''."""
    try:
        resp = requests.patch(f"{broker_url}/orders/{order_no}",
                              json={"symbol": ticker, "price": price, "exchange": "SOR"},
                              timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return str(resp.json().get("order_no") or "")
    except Exception as exc:
        print(f"[exit] ⚠️ {ticker} 정정 실패 ord={order_no}: {exc}")
        return ""


# ── 워커 루프──────────────────────────────────────────────────────────────────

def build_watch_set(args, broker_url: str, today: str) -> dict[str, dict]:
    """watch set 구성. --watch-codes 있으면 DB/잔고 무시(디버그, dry-run 전용)."""
    if args.watch_codes:
        codes = [c.strip() for c in args.watch_codes.split(",") if c.strip()]
        print(f"[exit] --watch-codes 디버그 모드: {codes} (기준가=첫 폴링 buy_bid)")
        return {(c, "single"): {"date": None, "ticker": c, "leg": "single", "cntr_price": None,
                                "qty": 0, "qty_eff": 0} for c in codes}

    with connect_rw(DEFAULT_WATCHLIST_DB) as con:
        positions = load_unsold_positions(con, today)
    balance = fetch_balance(broker_url)
    mark_missing_positions(DEFAULT_WATCHLIST_DB, positions, balance)
    watch = reconcile_balance(positions, balance)
    if not args.dry_run:  # 반반 분할 청산 (docs/PLAN_CLOSE_BET_SPLIT_EXIT.md). 분할은 DB 를 쓴다 — 실모드만
        watch = split_positions(DEFAULT_WATCHLIST_DB, watch)
    print(f"[exit] 포지션 {len(positions)}건 → 잔고대조 후 감시 {len(watch)}건")
    return {(w["ticker"], w["leg"]): w for w in watch}


def load_ordered_pending(db_path, today: str) -> dict[tuple[str, str], dict]:
    """재기동 복구 — sell_status='ordered'(주문했으나 미확정) 행을 pending으로 회수. 키 (ticker, leg)."""
    with connect_rw(db_path) as con:
        ensure_exit_columns(con)
        rows = con.execute(
            "SELECT date, ticker, cntr_price, qty, sell_order_no, exit_reason, leg "
            "FROM close_bet_orders WHERE sell_status='ordered' AND date < ?",
            [today],
        ).fetchall()
    # 재기동 시 kind/거래소는 leg 로 추정 — 추격 도중(09:00:30~09:01) 재기동하면 auction 잔량 주문을 오분류할 수 있음
    return {
        (r[1], r[6]): {"date": r[0], "ticker": r[1], "leg": r[6], "cntr_price": r[2], "qty": r[3],
                       "order_no": r[4] or "", "exit_reason": r[5] or "forced",
                       "kind": r[6], "round": 0, "exchange": "KRX" if r[6] == "auction" else "SOR",
                       "history": []}
        for r in rows
    }


def execute_sell(
    broker_url: str, db_path, pos: dict, reason: str, exchange: str = "SOR", price: int | None = None
) -> dict:
    """매도 직전 잔고 재조회 → 실보유만 매도(price 없으면 시장가) → order_no 반환 후 'ordered' 기록.

    반환 {order_no, status, qty}. status: submitted / rejected / failed / no_qty.
    """
    ticker = pos["ticker"]
    balance = fetch_balance(broker_url)
    held = {
        _strip_code(h.get("stk_cd", "")): _padint(h.get("trde_able_qty"))
        for h in balance.get("acnt_evlt_remn_indv_tot", []) or []
    }
    qty = min(int(pos.get("qty") or 0) or int(pos.get("qty_eff") or 0),
              held.get(ticker, 0))
    if qty <= 0:
        return {"order_no": "", "status": "no_qty", "qty": 0}
    res = place_sell_via_broker(broker_url, ticker, qty, exchange=exchange, price=price)
    res["qty"] = qty
    if res["order_no"]:
        with connect_rw(db_path) as con:
            mark_ordered(con, pos["date"], ticker, res["order_no"], leg=pos["leg"])
    return res


def record_fills(db_path, entry: dict, fills: dict[str, dict]) -> None:
    """분할 줄이 쓴 주문번호(정정·취소 전 번호 포함) 중 체결된 것만 close_bet_sell_fills 에 기록. 재기록 멱등."""
    orders = [*entry["history"], (entry["order_no"], entry["kind"], entry["round"])]
    now = _now_seoul().isoformat()
    with connect_rw(db_path) as con:
        for order_no, kind, rnd in orders:
            f = fills.get(normalize_order_no(order_no))
            if not f or f["cntr_qty"] <= 0:
                continue
            con.execute(
                "INSERT OR REPLACE INTO close_bet_sell_fills "
                "(date, ticker, leg, order_no, kind, round, price, qty, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [entry["date"], entry["ticker"], entry["leg"], order_no, kind, rnd or None,
                 f["cntr_uv"], f["cntr_qty"], now])


def _leg_fill_totals(db_path, entry: dict) -> tuple[int, int]:
    """(체결수량 합, 체결금액 합)."""
    with connect_rw(db_path) as con:
        qty, amount = con.execute(
            "SELECT COALESCE(SUM(qty),0), COALESCE(SUM(price*qty),0) FROM close_bet_sell_fills "
            "WHERE date=? AND ticker=? AND leg=?",
            [entry["date"], entry["ticker"], entry["leg"]]).fetchone()
    return qty, amount


def allocate_costs(broker_url: str, db_path, date: str, ticker: str) -> bool:
    """분할 줄이 모두 filled 면 ka10077 종목 합계 수수료·세금을 줄별 매도금액 비율로 배분(D12).

    auction 외 줄은 원 단위 내림, 잔차는 auction 줄 — 합계가 키움 값과 정확히 같다.
    sell_pl_won = 매도금액 − 매수금액 − 배분비용, pnl_pct = sell_pl_won / 매수금액 (%).
    아직 안 끝난 줄이 있거나 ka10077 실패면 무동작(gross 유지). 반환: 배분했는지.
    """
    with connect_rw(db_path) as con:
        rows = con.execute(
            "SELECT o.leg, o.sell_status, o.cntr_price, COALESCE(SUM(f.qty),0), COALESCE(SUM(f.price*f.qty),0) "
            "FROM close_bet_orders o LEFT JOIN close_bet_sell_fills f "
            "  ON f.date=o.date AND f.ticker=o.ticker AND f.leg=o.leg "
            "WHERE o.date=? AND o.ticker=? AND o.leg IN ('auction','chase') "
            "GROUP BY o.leg ORDER BY o.leg DESC",   # chase 먼저, auction(잔차) 마지막
            [date, ticker]).fetchall()
    if not rows or any(r[1] != "filled" for r in rows):
        return False
    realized = fetch_realized(broker_url, ticker)
    if not realized:
        return False
    total_amt = sum(r[4] for r in rows)
    left = {"cmsn": int(realized["cmsn"] or 0), "tax": int(realized["tax"] or 0)}
    totals = dict(left)
    with connect_rw(db_path) as con:
        for leg, _, cntr_price, qty, amount in rows:
            share = {}
            for k in ("cmsn", "tax"):
                share[k] = left[k] if leg == "auction" else totals[k] * amount // total_amt
                left[k] -= share[k]
            buy_amt = cntr_price * qty
            pl = amount - buy_amt - share["cmsn"] - share["tax"]
            con.execute(
                "UPDATE close_bet_orders SET sell_cmsn=?, sell_tax=?, sell_pl_won=?, pnl_pct=? "
                "WHERE date=? AND ticker=? AND leg=?",
                [share["cmsn"], share["tax"], pl, round(pl / buy_amt * 100, 2), date, ticker, leg])
    return True


def settle_pending(broker_url: str, db_path, today: str, pending: dict) -> list[str]:
    """체결확인 — ka10075에서 사라진 매도주문을 kt00007 체결가로 filled 확정.

    분할 줄(auction/chase)은 여러 주문번호에 걸쳐 체결되므로 체결 이력 합이 줄 수량에 닿아야 확정,
    손익은 gross (줄별 수수료·세금 배분은 PLAN D12). 미분할(single)은 기존 규칙.
    반환: 이번에 filled 처리된 (ticker, leg) 목록(호출자가 pending에서 제거).
    """
    unfilled = fetch_unfilled_orders(broker_url)
    fills = fetch_sell_fills(broker_url, today)
    done: list[tuple[str, str]] = []
    for key, e in list(pending.items()):
        ticker = key[0]
        if key[1] in SPLIT_LEGS:
            record_fills(db_path, e, fills)
            if normalize_order_no(e["order_no"]) in unfilled:
                continue
            qty, amount = _leg_fill_totals(db_path, e)
            if qty < e["qty"]:
                continue  # 체결내역 지연·잔량 재주문 대기
            pnl = round((amount / (qty * e["cntr_price"]) - 1) * 100, 2) if e["cntr_price"] else 0.0
            with connect_rw(db_path) as con:
                mark_filled(con, e["date"], ticker, round(amount / qty), qty,
                            _now_seoul().isoformat(), e["exit_reason"], pnl, leg=e["leg"])
            done.append(key)
            allocate_costs(broker_url, db_path, e["date"], ticker)   # 형제 줄까지 끝났을 때만 동작
            continue
        if normalize_order_no(e["order_no"]) in unfilled:
            continue  # 아직 미체결
        f = fills.get(normalize_order_no(e["order_no"]))
        if not f or f["cntr_qty"] <= 0:
            continue  # 사라졌으나 체결내역 아직 — 다음 폴링 대기
        sell_price = f["cntr_uv"]
        # net 손익(수수료·세금 차감) 우선 — 키움 ka10077. 조회 실패 시 gross 폴백.
        realized = fetch_realized(broker_url, ticker)
        if realized:
            pnl = realized["pnl_pct"]
            cmsn, tax, pl_won = realized["cmsn"], realized["tax"], realized["sel_pl_won"]
        else:
            pnl = round((sell_price / e["cntr_price"] - 1) * 100, 2) if e["cntr_price"] else 0.0
            cmsn = tax = pl_won = None
        with connect_rw(db_path) as con:
            mark_filled(con, e["date"], ticker, sell_price, f["cntr_qty"],
                        _now_seoul().isoformat(), e["exit_reason"], pnl,
                        cmsn, tax, pl_won, leg=e["leg"])
        done.append(key)
    return done


def _replace_order(db_path, key, e: dict, res: dict, kind: str, rnd: int) -> str:
    """정정·취소 후 재주문 결과를 pending 항목에 반영. 옛 번호는 history 로(체결분 기록용)."""
    if e["order_no"]:
        e["history"].append((e["order_no"], e["kind"], e["round"]))
    if not res.get("order_no"):
        # 옛 주문은 이미 취소됨 — 남은 수량을 잃지 않게 다음 폴링에 시장가 재시도
        e.update(order_no="", kind="orphan", round=rnd, exchange="SOR")
        return f"⚠️ {_label(key)} 재주문 실패({res.get('status')}: {res.get('message', '')}) — 시장가 재시도 예정"
    e.update(order_no=res["order_no"], kind=kind, round=rnd, exchange="SOR")
    e.pop("orphan_qty", None)
    with connect_rw(db_path) as con:
        mark_ordered(con, e["date"], key[0], res["order_no"], leg=key[1])
    return f"{kind} {_label(key)} ord={res['order_no']}"


def run_chase(broker_url: str, db_path, now: datetime, watch: dict, pending: dict,
              quotes: dict, unfilled: dict) -> list[str]:
    """분할 줄 추격 — 09:00:30/:40/:50 매도1호가 − 1틱 지정가, 09:01 남은 지정가 취소 → 잔량 시장가.

    chase 줄: 회차 도래 시 지정가 신규, 이후 회차에 목표가가 바뀌면 정정(같으면 시간우선 유지).
    auction 줄: 동시호가 주문 잔량이 남아 있으면 취소 후 같은 규칙으로 합류.
    반환: 보고 메시지.
    """
    rnd, end = due_round(now), is_chase_end(now)
    if not rnd:
        return []
    msgs: list[str] = []

    for key in [k for k in watch if k[1] == "chase"]:
        pos = watch[key]
        q = quotes.get(key[0]) or {}
        price = None if end else chase_price(q.get("sel_bid"), q.get("lst_pric"))
        if not end and price is None:
            continue  # 호가 없음 — 다음 폴링 재시도
        res = execute_sell(broker_url, db_path, pos, "forced", exchange="SOR", price=price)
        if not res["order_no"]:
            msgs.append(f"⚠️ {_label(key)} 추격 매도 실패({res['status']}: {res.get('message', '')})")
            continue
        kind = "market_0901" if end else "chase"
        pending[key] = {**pos, "qty": res["qty"], "order_no": res["order_no"], "exit_reason": "forced",
                        "kind": kind, "round": 0 if end else rnd, "exchange": "SOR", "history": []}
        del watch[key]
        msgs.append(f"{kind} {_label(key)} ord={res['order_no']} price={price} qty={res['qty']}")

    for key, e in pending.items():
        if key[1] not in SPLIT_LEGS or e["kind"] == "market_0901":
            continue
        if e["kind"] == "orphan":
            res = place_sell_via_broker(broker_url, key[0], e["orphan_qty"], exchange="SOR")
            msgs.append(_replace_order(db_path, key, e, res, "market_0901", 0))
            continue
        o = unfilled.get(normalize_order_no(e["order_no"]))
        if not o or o["oso_qty"] <= 0:
            continue
        if end:
            if not cancel_via_broker(broker_url, e["order_no"], key[0], e["exchange"]):
                continue  # 다음 폴링 재시도
            res = place_sell_via_broker(broker_url, key[0], o["oso_qty"], exchange="SOR")
            e["orphan_qty"] = o["oso_qty"]
            msgs.append(_replace_order(db_path, key, e, res, "market_0901", 0))
            continue
        if e["round"] >= rnd:
            continue
        q = quotes.get(key[0]) or {}
        price = chase_price(q.get("sel_bid"), q.get("lst_pric"))
        if price is None:
            continue
        if e["kind"] == "auction":
            if not cancel_via_broker(broker_url, e["order_no"], key[0], e["exchange"]):
                continue
            res = place_sell_via_broker(broker_url, key[0], o["oso_qty"], exchange="SOR", price=price)
            e["orphan_qty"] = o["oso_qty"]
            msgs.append(_replace_order(db_path, key, e, res, "chase", rnd))
        elif price != o["ord_price"]:
            new_no = modify_via_broker(broker_url, e["order_no"], key[0], price)
            if new_no:
                msgs.append(_replace_order(db_path, key, e, {"order_no": new_no}, "chase", rnd))
        else:
            e["round"] = rnd
    return msgs


def run_loop(args, broker_url: str) -> None:
    today = _now_seoul().strftime("%Y%m%d")
    db_path = DEFAULT_WATCHLIST_DB
    watch = build_watch_set(args, broker_url, today)
    pending: dict[str, dict] = {} if args.dry_run else load_ordered_pending(db_path, today)
    if pending:
        print(f"[exit] 재기동 복구: 미확정 매도 {len(pending)}건 체결확인 대상")
    dry_tag = " (DRY-RUN)" if args.dry_run else ""
    report: list[str] = []

    while True:
        now = _now_seoul()
        if now >= _at(now, args.stop_time):
            print(f"[exit] stop-time({args.stop_time}) 도달 — 종료")
            break

        # 1) 체결확인(실모드) — pending 정산 먼저
        if not args.dry_run and pending:
            for key in settle_pending(broker_url, db_path, today, pending):
                e = pending.pop(key)
                label = _label(key)
                msg = f"FILLED {label} reason={e['exit_reason']}"
                print(f"[exit] {msg}")
                report.append(msg)
                send_discord(f"[종가베팅 청산] ✅ {label} 체결 reason={e['exit_reason']}")

        # 추격 중인 분할 줄도 매도1호가가 필요하다
        quotes = fetch_quotes(broker_url, sorted({t for t, _ in watch} | {t for t, leg in pending if leg in SPLIT_LEGS}))
        trading = in_trading_window(now, args.window_start, args.window_end)
        force = should_force(now, args.force_exit_time, args.window_end)
        unfilled = {} if args.dry_run else fetch_unfilled_orders(broker_url)

        for key in list(watch.keys()):
            if key[1] == "chase":
                continue  # run_chase 담당
            ticker, label = key[0], _label(key)
            q = quotes.get(ticker)
            if q is None and not force:
                continue
            q = q or {}  # 강제청산은 시세 없어도 매도 (동시호가 호가 공백 대비)
            buy_bid = q.get("buy_bid")
            pos = watch[key]
            if pos["cntr_price"] is None and buy_bid:  # 디버그 기준가
                pos["cntr_price"] = buy_bid
                print(f"[exit] {ticker} 기준가={buy_bid}(첫폴링)")

            reason = "forced" if force else (
                decide_exit(buy_bid, pos["cntr_price"], args.tp, args.sl) if trading else None
            )
            if not reason:
                continue

            pnl = (f"{(buy_bid / pos['cntr_price'] - 1) * 100:+.2f}%"
                   if buy_bid and pos["cntr_price"] else "?")
            lock = " (LOCK 예상)" if is_locked(
                q.get("cur_prc"), q.get("lst_pric"), q.get("pred_pre_sig", "")) else ""

            if args.dry_run:
                msg = (f"WOULD SELL {label} reason={reason} buy_bid={buy_bid} "
                       f"pnl={pnl} qty={pos.get('qty_eff')}{lock}")
                print(f"[exit]{dry_tag} {msg}")
                report.append(msg)
                del watch[key]
                continue

            # 실매도
            if is_in_flight(key, pending, unfilled):
                print(f"[exit] {label} 이미 in-flight — skip")
                del watch[key]
                continue
            # 연속매매 창 전 = 개장 동시호가 → KRX, 장중 → SOR
            res = execute_sell(broker_url, db_path, pos, reason,
                               exchange="SOR" if trading else "KRX")
            if res["order_no"]:
                pending[key] = {**pos, "qty": res["qty"], "order_no": res["order_no"], "exit_reason": reason,
                                "kind": key[1], "round": 0, "exchange": "SOR" if trading else "KRX",
                                "history": []}
                del watch[key]
                msg = f"SELL ordered {label} reason={reason} ord={res['order_no']} qty={res['qty']}{lock}"
                print(f"[exit] {msg}")
                report.append(msg)
            else:
                print(f"[exit] ⚠️ {label} 매도 실패({res['status']}: {res.get('message','')}) — 다음 폴링 재시도")
                send_discord(f"[종가베팅 청산] ⚠️ {label} 매도 {res['status']}: {res.get('message','')}")

        if not args.dry_run:
            for msg in run_chase(broker_url, db_path, now, watch, pending, quotes, unfilled):
                print(f"[exit] {msg}")
                report.append(msg)

        if not watch and not pending:
            print("[exit] 감시·미확정 모두 처리 — 종료")
            break
        time.sleep(args.poll_sec)

    # 종료: 미체결 잔존 알람(미확인5 정책)
    if pending:
        leftover = ", ".join(_label(k) for k in pending)
        print(f"[exit] ⚠️ 미체결 잔존: {leftover}")
        send_discord(f"[종가베팅 청산] ⚠️ 마감 미체결 잔존: {leftover}")

    summary = "\n".join(report) if report else "발동 없음"
    print(f"[exit] 종료. 요약:\n{summary}")
    send_discord(f"[종가베팅 청산 워커]{dry_tag}\n{summary}")


def main() -> None:
    load_dotenv(ENV_PATH)
    parser = argparse.ArgumentParser(description="종가베팅 청산 워커 (판정+실매도)")
    parser.add_argument("--broker-url", default=None)
    parser.add_argument("--poll-sec", type=float, default=3.0)
    parser.add_argument("--tp", type=float, default=None,
                        help="미지정 시 close_bet.json 의 tp 사용")
    parser.add_argument("--sl", type=float, default=None,
                        help="미지정 시 close_bet.json 의 sl 사용")
    parser.add_argument("--force-exit-time", default=None,
                        help="미지정 시 close_bet.json 의 exit_time 사용")
    parser.add_argument("--stop-time", default="15:25:00")
    parser.add_argument("--window-start", default="09:00:00")
    parser.add_argument("--window-end", default="15:20:00")
    parser.add_argument("--watch-codes", default="")
    parser.add_argument("--dry-run", default="true")
    args = parser.parse_args()

    # 전략값 config(close_bet.json). CLI 로 주면 override, 없으면 config 값.
    cfg = load_close_bet_config()
    if args.tp is None:
        args.tp = cfg["tp"]
    if args.sl is None:
        args.sl = cfg["sl"]
    if args.force_exit_time is None:
        args.force_exit_time = cfg["exit_time"]

    args.dry_run = args.dry_run.lower() not in ("false", "0", "no")
    if not args.dry_run and args.watch_codes:
        print("[exit] ⚠️ --watch-codes는 dry-run 전용(DB 포지션 없음). 실모드는 DB 로드만.")
        sys.exit(2)

    broker_url = args.broker_url or os.getenv("BROKER_API_URL", "http://localhost:8001")
    print(f"[exit] 시작 broker={broker_url} tp={args.tp} sl={args.sl} "
          f"force={args.force_exit_time} stop={args.stop_time} dry_run={args.dry_run}")
    run_loop(args, broker_url)


if __name__ == "__main__":
    main()
