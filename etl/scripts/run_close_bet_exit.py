"""종가베팅 청산(익절/손절) 장중 워커 — 3초 폴링.

T일 15:19 매수한 오버나이트 보유를 T+1 장중 broker `/quotes`(ka10095) 3초 폴링으로
감시해 buy_bid 기준 +tp 익절 / -sl 손절, 미발동분은 force-exit-time 강제청산한다.

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

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"


# ── 순수 판정함수 (부작용 0, 단위테스트 핵심) ──────────────────────────────────

def decide_exit(
    buy_bid: int | None, cntr_price: int | None, tp: float, sl: float
) -> str | None:
    """buy_bid(시장가 매도 실현가) 기준 TP/SL 판정. 'tp'/'sl'/None.

    호가공백(buy_bid 없음/0)·체결가 없음 → None(skip).
    """
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
    return code[1:] if code[:1] == "A" and code[1:].isdigit() else code


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
        SELECT date, ticker, cntr_price, qty
        FROM close_bet_orders
        WHERE status='confirmed' AND sell_status IS NULL AND date < ?
        """,
        [today],
    ).fetchall()
    return [
        {"date": r[0], "ticker": r[1], "cntr_price": r[2], "qty": r[3]}
        for r in rows
    ]


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
                "exit_reason='missing' WHERE date=? AND ticker=? AND sell_status IS NULL",
                (_now_seoul().isoformat(), p["date"], p["ticker"]),
            )
    tickers = [p["ticker"] for p in missing]
    print(f"[exit] 잔고 미보유 {len(tickers)}건 종료 처리(missing): {', '.join(tickers)}")
    return tickers


# ── 매도 상태머신 (close_bet_orders) ──────────────────────────────────────────

def mark_ordered(con, date: str, ticker: str, order_no: str) -> None:
    """주문 전송 성공(order_no 반환) 후 'ordered' 기록. 재주문 차단용."""
    con.execute(
        "UPDATE close_bet_orders SET sell_status='ordered', sell_order_no=? "
        "WHERE date=? AND ticker=?",
        [order_no, date, ticker],
    )


def mark_filled(
    con, date: str, ticker: str, sell_price: int, sell_qty: int,
    sold_at: str, exit_reason: str, pnl_pct: float,
    sell_cmsn: int | None = None, sell_tax: int | None = None,
    sell_pl_won: int | None = None,
) -> None:
    """체결확인 후 'filled' + 실현가/손익 확정. 수수료·세금·net손익금은 ka10077분(없으면 NULL)."""
    con.execute(
        "UPDATE close_bet_orders SET sell_status='filled', sell_price=?, sell_qty=?, "
        "sold_at=?, exit_reason=?, pnl_pct=?, sell_cmsn=?, sell_tax=?, sell_pl_won=? "
        "WHERE date=? AND ticker=?",
        [sell_price, sell_qty, sold_at, exit_reason, pnl_pct,
         sell_cmsn, sell_tax, sell_pl_won, date, ticker],
    )


def is_in_flight(ticker: str, ordered: set[str], unfilled: set[str]) -> bool:
    """재주문 가드 — sell_status='ordered'(ordered set) OR ka10075 미체결(unfilled set)."""
    return ticker in ordered or ticker in unfilled


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


def fetch_unfilled_tickers(broker_url: str) -> set[str]:
    """GET /orders/unfilled?side=sell → 미체결 매도주문 종목 set. 실패 시 빈 set."""
    try:
        resp = requests.get(
            f"{broker_url}/orders/unfilled", params={"side": "sell"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return {row["ticker"] for row in resp.json()}
    except Exception as exc:
        print(f"[exit] /orders/unfilled 조회 실패: {exc}")
        return set()


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


def place_sell_via_broker(broker_url: str, ticker: str, qty: int) -> dict:
    """POST /orders/strategy 시장가 매도. {order_no, status, message}. 거부/실패 시 order_no=''."""
    try:
        resp = requests.post(
            f"{broker_url}/orders/strategy",
            json={"symbol": ticker, "side": "sell", "qty": qty,
                  "order_type": "market", "source": "close_bet_exit"},
            timeout=REQUEST_TIMEOUT,
        )
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


# ── 워커 루프 ──────────────────────────────────────────────────────────────────

def build_watch_set(args, broker_url: str, today: str) -> dict[str, dict]:
    """watch set 구성. --watch-codes 있으면 DB/잔고 무시(디버그, dry-run 전용)."""
    if args.watch_codes:
        codes = [c.strip() for c in args.watch_codes.split(",") if c.strip()]
        print(f"[exit] --watch-codes 디버그 모드: {codes} (기준가=첫 폴링 buy_bid)")
        return {c: {"date": None, "ticker": c, "cntr_price": None,
                    "qty": 0, "qty_eff": 0} for c in codes}

    with connect_rw(DEFAULT_WATCHLIST_DB) as con:
        positions = load_unsold_positions(con, today)
    balance = fetch_balance(broker_url)
    mark_missing_positions(DEFAULT_WATCHLIST_DB, positions, balance)
    watch = reconcile_balance(positions, balance)
    print(f"[exit] 포지션 {len(positions)}건 → 잔고대조 후 감시 {len(watch)}건")
    return {w["ticker"]: w for w in watch}


def load_ordered_pending(db_path, today: str) -> dict[str, dict]:
    """재기동 복구 — sell_status='ordered'(주문했으나 미확정) 행을 pending으로 회수."""
    with connect_rw(db_path) as con:
        ensure_exit_columns(con)
        rows = con.execute(
            "SELECT date, ticker, cntr_price, qty, sell_order_no, exit_reason "
            "FROM close_bet_orders WHERE sell_status='ordered' AND date < ?",
            [today],
        ).fetchall()
    return {
        r[1]: {"date": r[0], "ticker": r[1], "cntr_price": r[2], "qty": r[3],
               "order_no": r[4] or "", "exit_reason": r[5] or "forced"}
        for r in rows
    }


def execute_sell(broker_url: str, db_path, pos: dict, reason: str) -> dict:
    """매도 직전 잔고 재조회 → 실보유만 시장가 매도 → order_no 반환 후 'ordered' 기록.

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
    res = place_sell_via_broker(broker_url, ticker, qty)
    res["qty"] = qty
    if res["order_no"]:
        with connect_rw(db_path) as con:
            mark_ordered(con, pos["date"], ticker, res["order_no"])
    return res


def settle_pending(broker_url: str, db_path, today: str, pending: dict) -> list[str]:
    """체결확인 — ka10075에서 사라진 매도주문을 kt00007 체결가로 filled 확정.

    반환: 이번에 filled 처리된 ticker 목록(호출자가 pending에서 제거).
    """
    unfilled = fetch_unfilled_tickers(broker_url)
    fills = fetch_sell_fills(broker_url, today)
    done: list[str] = []
    for ticker, e in list(pending.items()):
        if ticker in unfilled:
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
                        cmsn, tax, pl_won)
        done.append(ticker)
    return done


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
            for t in settle_pending(broker_url, db_path, today, pending):
                e = pending.pop(t)
                msg = f"FILLED {t} reason={e['exit_reason']}"
                print(f"[exit] {msg}")
                report.append(msg)
                send_discord(f"[종가베팅 청산] ✅ {t} 체결 reason={e['exit_reason']}")

        quotes = fetch_quotes(broker_url, list(watch.keys()))
        trading = in_trading_window(now, args.window_start, args.window_end)
        force = trading and is_force_time(now, args.force_exit_time)
        unfilled = set() if args.dry_run else fetch_unfilled_tickers(broker_url)

        for ticker in list(watch.keys()):
            q = quotes.get(ticker)
            if q is None:
                continue
            buy_bid = q.get("buy_bid")
            pos = watch[ticker]
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
                msg = (f"WOULD SELL {ticker} reason={reason} buy_bid={buy_bid} "
                       f"pnl={pnl} qty={pos.get('qty_eff')}{lock}")
                print(f"[exit]{dry_tag} {msg}")
                report.append(msg)
                del watch[ticker]
                continue

            # 실매도
            if is_in_flight(ticker, set(pending), unfilled):
                print(f"[exit] {ticker} 이미 in-flight — skip")
                del watch[ticker]
                continue
            res = execute_sell(broker_url, db_path, pos, reason)
            if res["order_no"]:
                pending[ticker] = {**pos, "order_no": res["order_no"], "exit_reason": reason}
                del watch[ticker]
                msg = f"SELL ordered {ticker} reason={reason} ord={res['order_no']} qty={res['qty']}{lock}"
                print(f"[exit] {msg}")
                report.append(msg)
            else:
                print(f"[exit] ⚠️ {ticker} 매도 실패({res['status']}: {res.get('message','')}) — 다음 폴링 재시도")
                send_discord(f"[종가베팅 청산] ⚠️ {ticker} 매도 {res['status']}: {res.get('message','')}")

        if not watch and not pending:
            print("[exit] 감시·미확정 모두 처리 — 종료")
            break
        time.sleep(args.poll_sec)

    # 종료: 미체결 잔존 알람(미확인5 정책)
    if pending:
        leftover = ", ".join(pending.keys())
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
    parser.add_argument("--force-exit-time", default="15:19:00")
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
