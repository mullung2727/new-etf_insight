"""주문 실행 엔진 — 3층 구조 중 실행층.

추격(지정가 진입 후 일정 간격 정정, 잔량 시장가 정리)만 수행한다. 판단(수량·
시각)은 전략의 state가, 기록·문구는 on_event 콜백이 맡고, 이 모듈은 조회·
주문·상태 전이만 한다.

state 키:
  p: {"ticker": ...} 등 주문 식별 정보.
  qty: 목표 수량. None이면 sizer가 정할 때까지 주문 안 함.
  nos: 이 모듈이 낸 주문번호 목록. order_no: 현재 유효 번호(정정 시 바뀜).
  status: pending | working | done | skipped | failed | unknown.
  rejects: 지정가 명시 거절 연속 횟수. modifies: 정정 접수 횟수.
  side: 매도 쪽이 기본값, 매수 쪽도 가능 — api 호출에 그대로 전달.
  start_at: 이 시각 전에는 손대지 않음(없으면 즉시 진행).
  market_at: 시장가 처리 시각(없으면 run_chase 기본값).
  sizer: (state, api, notes) -> [수량] | None. qty가 None이고 시작 시각이
    지났을 때 한 번 호출한다. 빈값이면 skipped, 여러 수량이면 첫 조각은
    이 state가, 나머지는 형제 state로 같은 루프에 합류한다.
  price_fn: (ticker) -> int | None. 있으면 api.price 대신 쓴다(고정 목표가용).

api 프로토콜:
  price(t, side) -> int | None — 추격 지정가.
  place_limit(t, side, qty, price) -> dict — {"order_no", "status"}.
  place_market(t, side, qty) -> dict.
  modify(no, t, price, qty) -> dict — 엔진은 qty=0(잔량 전부)으로 호출.
  cancel(no, t) -> bool.
  unfilled(side) -> {정규 주문번호: entry} | None.
  fills(date, side) -> {정규 주문번호: {"cntr_qty": int}} | None.

on_event(state, kind, **fields) -> str | None — 기록·문구 콜백. 반환 문자열은
notes에 append하고, None이면 무시한다. kind와 fields:
  first_order(no, price, qty) / first_unknown(qty, message)
  first_rejected(status, message, rejects) / limit_given_up(market_at)
  resumed(no) — 첫 주문 전, 살아 있는 우리 주문을 이어받음
  resume_ambiguous(nos) — 살아 있는 우리 주문이 2건 이상, unknown 으로 중단
  modified(no, price, qty, old_price) / modify_unknown(qty)
  market_qty_unknown() / market_lookup_failed() / market_all_filled()
  market_not_in_unfilled() / market_zero_remaining()
  market_cancel_failed() / market_cancel_unconfirmed() / market_past_end()
  market_order(no, qty) / market_unknown(qty, message)
  market_rejected(status, message)
  late_start() — state 대신 None 전달. modifies_summary(count).

place_first_orders는 첫 주문만 1회 내고 끝난다(대기·정정·시장가 없음).
이후 추격이 필요하면 같은 state를 run_chase에 넘긴다(working이면 정정부터).
place_market_orders는 시장가만 1회 낸다(정정·대기 없음).

접수로 취급하는 결과: status가 submitted 또는 dry_run이고 order_no가 있음.
unknown이면 상태를 unknown으로 바꾸고 손을 뗀다(재주문 금지).

fail closed 불변식:
  - 미체결·체결 조회가 실패(None)하면 주문 안 함. 결과가 불명(unknown)이면
    재주문 금지.
  - 취소가 실패하거나 재조회에서 취소가 확인되지 않으면 시장가 안 냄.
  - 현재가와 같은 가격으로는 정정 안 함.
  - 종료 시각 이후에는 시장가 금지.
  - 넘겨받은 state의 주문번호만 취소·정정하고, 남의 미체결은 건드리지 않음.
  - 살아 있는 우리 주문이 있으면 새 첫 주문 안 함.
  - 살아 있는 우리 주문이 2건 이상이면 unknown 으로 손 뗌.
  - on_event 없으면 실행 거부(기록 없는 주문 금지).
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta

try:
    from scripts.broker_client import BrokerClient, fills_by_order
except ImportError:  # 다른 실행 경로 대비
    from broker_client import BrokerClient, fills_by_order
try:
    from scripts.run_verify import normalize_order_no
except ImportError:  # 다른 실행 경로 대비
    from run_verify import normalize_order_no
try:
    from scripts.trading_batch_common import now_seoul
except ImportError:  # 다른 실행 경로 대비
    from trading_batch_common import now_seoul

__all__ = ["Clock", "at", "run_chase", "place_first_orders", "ClientEngineApi"]


class Clock:
    """실시간 시계."""

    def now(self) -> datetime:
        return now_seoul()

    def sleep_until(self, dt: datetime) -> None:
        if (left := (dt - self.now()).total_seconds()) > 0:
            time.sleep(left)


def at(today: str, hms: str) -> datetime:
    """YYYYMMDD + 시분초 문자열 → KST datetime."""
    h, m, s = (int(x) for x in hms.split(":"))
    day = datetime.strptime(today, "%Y%m%d")
    return now_seoul().replace(year=day.year, month=day.month, day=day.day,
                               hour=h, minute=m, second=s, microsecond=0)


def _submitted(res: dict) -> bool:
    return res.get("status") in ("submitted", "dry_run") and bool(res.get("order_no"))


def _entry(unfilled: dict, order_no: str) -> dict | None:
    """미체결 목록에서 우리 번호 찾기 (정규화 비교)."""
    want = normalize_order_no(order_no)
    return unfilled.get(want) if want else None


def _fills(nos: list[str], history: dict) -> int:
    """우리 번호 전체 체결 합산."""
    want = {normalize_order_no(n) for n in nos}
    return sum((history.get(k) or {}).get("cntr_qty") or 0 for k in want)


def _bounds(s: dict, today: str, first_at: str, market_at: str) -> None:
    """state 스케줄 기본값 채움."""
    s.setdefault("side", "sell")
    if "start_dt" not in s:
        s["start_dt"] = at(today, s.get("start_at") or first_at)
    if s.get("cancel_only_at"):
        s["market_dt"] = at(today, s["cancel_only_at"])
    elif "market_dt" not in s:
        s["market_dt"] = at(today, s.get("market_at") or market_at)


def _emit(on_event: Callable | None, state: dict | None, kind: str,
          notes: list[str], **fields) -> None:
    """on_event 호출 — 문자열 반환만 notes에 append."""
    if on_event is None:
        return
    note = on_event(state, kind, **fields)
    if isinstance(note, str):
        notes.append(note)


def _try_first_order(today: str, s: dict, u: dict, hist: dict | None,
                     api, notes: list[str], *, market_at: str,
                     max_rejects: int, on_event: Callable | None = None) -> None:
    """pending 첫 주문 — 전제 조회 실패 시 주문 안 함 (fail closed)."""
    t = s["p"]["ticker"]
    side = s.get("side", "sell")
    if hist is None:  # 체결 여부 모르면 주문 금지
        return
    filled = _fills(s["nos"], hist)
    if filled >= s["qty"]:  # 이미 다 채웠으면 주문 금지
        s["status"] = "done"
        return
    if u is None:  # 미체결 모르면 주문 금지 (호출부가 걸러 오지만 fail closed)
        return
    live = [n for n in s["nos"] if _entry(u, n) is not None]
    if len(live) > 1:  # 살아 있는 우리 주문이 2건 이상 — 수동 확인, 주문·정정 없음
        s["status"] = "unknown"
        _emit(on_event, s, "resume_ambiguous", notes, nos=live)
        return
    if len(live) == 1:  # 복원된 pending — 살아 있는 우리 주문이 있으면 이어받음
        s["order_no"] = live[0]
        s["status"] = "working"
        _emit(on_event, s, "resumed", notes, no=live[0])
        return
    price_fn = s.get("price_fn")
    price = price_fn(t) if price_fn is not None else api.price(t, side)
    if not price:  # 호가 없음은 포기 사유 아님 — 다음 스텝 재시도
        return
    qty = s["qty"] - filled
    res = api.place_limit(t, side, qty, price)
    if _submitted(res):
        no = str(res["order_no"])
        s["nos"].append(no)
        s["order_no"] = no
        s["status"] = "working"
        s["rejects"] = 0
        _emit(on_event, s, "first_order", notes, no=no, price=price, qty=qty)
        return
    if res.get("status") == "unknown":  # 나갔을 수 있음 — 수동 확인, 재주문 금지
        s["status"] = "unknown"
        _emit(on_event, s, "first_unknown", notes, qty=qty,
              message=str(res.get("message", "")))
        return
    s["rejects"] += 1
    _emit(on_event, s, "first_rejected", notes,
          status=str(res.get("status")), message=str(res.get("message", "")),
          rejects=s["rejects"])
    if s["rejects"] >= max_rejects:  # 지정가 포기 — 시장가는 시도
        s["status"] = "failed"
        _emit(on_event, s, "limit_given_up", notes,
              market_at=s.get("market_at") or market_at)


def _chase_step(today: str, states: list[dict], api, clock, notes: list[str],
                *, first_at: str, market_at: str, max_rejects: int,
                on_event: Callable | None = None,
                on_step: Callable | None = None) -> None:
    """1스텝 처리 — sizer 확정 + 정정(working) + 첫 주문 재시도(pending).

    이 모듈이 낸 주문번호만 손댄다. start_at·market_at을 지정한 state만 시각
    게이팅 — 미지정 state는 호출 즉시 진행한다.
    """
    now = clock.now()
    if on_step is not None:  # 전략이 state 추가·수량 조정 (같은 스텝에 반영)
        on_step(states, api, now)
    for s in list(states):
        _bounds(s, today, first_at, market_at)
        if s.get("qty") is None and s.get("sizer") is not None and now >= s["start_dt"]:
            parts = s["sizer"](s, api, notes)
            s["sizer"] = None
            if not parts:
                s["status"] = "skipped"
            else:
                s["qty"] = parts[0]
                for q in parts[1:]:  # 분할 — 형제 state로 같은 루프에 합류
                    states.append({**s, "qty": q, "nos": [], "order_no": "",
                                   "status": "pending", "rejects": 0, "modifies": 0,
                                   "sizer": None})
    due = lambda s: (s.get("start_at") is None or s["start_dt"] <= now) \
        and (s.get("market_at") is None or now < s["market_dt"])
    live = [s for s in states if s["status"] in ("pending", "working")
            and s.get("qty") is not None and due(s)]
    if not live:
        return
    sides = []
    for s in live:
        if s.get("side", "sell") not in sides:
            sides.append(s.get("side", "sell"))
    u = {side: api.unfilled(side) for side in sides}
    live = [s for s in live if u[s.get("side", "sell")] is not None]
    if not live:  # 조회 실패한 쪽은 이번 스텝 건너뜀
        return
    # 체결내역은 첫 주문 대기 state가 있는 쪽만 (None이면 첫 주문만 중단, 정정은 진행)
    hist = {}
    for side in sides:
        if any(s["status"] == "pending" for s in live if s.get("side", "sell") == side):
            hist[side] = api.fills(today, side)
        else:
            hist[side] = {}
    for s in live:
        side = s.get("side", "sell")
        t = s["p"]["ticker"]
        if s["status"] == "pending":
            _try_first_order(today, s, u[side], hist[side], api, notes,
                             market_at=market_at, max_rejects=max_rejects,
                             on_event=on_event)
            continue
        ent = _entry(u[side], s["order_no"])
        if ent is None:  # 체결(또는 외부 취소) — 이후 정정 안 함
            continue
        try:
            oso = int(ent.get("oso_qty") or 0)
            oprice = int(ent.get("ord_price") or 0)
        except (TypeError, ValueError):
            continue
        if oso <= 0:
            continue
        price_fn = s.get("price_fn")
        price = price_fn(t) if price_fn is not None else api.price(t, side)
        if not price or price == oprice:
            continue
        # qty 0 = 잔량 전부 (kt10002) — 조회~정정 사이 체결돼도 '정정가능수량 초과' 안 남
        res = api.modify(s["order_no"], t, price, 0)
        if _submitted(res):
            no = str(res["order_no"])
            s["nos"].append(no)
            s["order_no"] = no
            s["modifies"] += 1
            _emit(on_event, s, "modified", notes, no=no, price=price, qty=oso,
                  old_price=oprice)
        elif res.get("status") == "unknown":  # 새 번호를 모름 — 중단·수동 확인
            s["status"] = "unknown"
            _emit(on_event, s, "modify_unknown", notes, qty=oso)
        # 명시 실패면 그대로 둠 (다음 스텝 재시도)


def _market_step(today: str, states: list[dict], api, clock, end: datetime,
                 notes: list[str], *, first_at: str, market_at: str,
                 on_event: Callable | None = None) -> None:
    """시장가 단계 — 잔량 취소 확인 후 시장가. 확인 실패면 시장가 안 냄."""
    live = [s for s in states if s["status"] in ("pending", "working", "failed")]
    if not live:
        return
    for s in live:
        _bounds(s, today, first_at, market_at)
    sides = []
    for s in live:
        if s.get("side", "sell") not in sides:
            sides.append(s.get("side", "sell"))
    hist = {side: api.fills(today, side) for side in sides}
    u = {side: api.unfilled(side) for side in sides}
    for s in live:
        side = s.get("side", "sell")
        t = s["p"]["ticker"]
        if s.get("qty") is None:  # sizer 미발화 등 수량 미확정 — 시장가 금지
            s["status"] = "skipped"
            _emit(on_event, s, "market_qty_unknown", notes)
            continue
        hh, uu = hist[side], u[side]
        if hh is None or uu is None:  # 잔량 모르면 시장가 금지
            _emit(on_event, s, "market_lookup_failed", notes)
            continue
        remain = s["qty"] - _fills(s["nos"], hh)
        if remain <= 0:
            s["status"] = "done"
            _emit(on_event, s, "market_all_filled", notes)
            continue
        mqty = remain
        if s["order_no"]:  # 지정가 낸 적 있음 (미주문은 바로 시장가)
            ent = _entry(uu, s["order_no"])
            if ent is None:
                # 미체결 목록에 없음 = 방금 체결됐을 수 있다. 체결내역은 늦게
                # 반영될 수 있어 remain이 부풀려진다 → 우리가 취소한 게 아니면
                # 시장가 내지 않는다 (중복 방지)
                s["status"] = "done"
                _emit(on_event, s, "market_not_in_unfilled", notes)
                continue
            try:
                oso = int(ent.get("oso_qty") or 0)
            except (TypeError, ValueError):
                oso = 0
            if oso <= 0:
                s["status"] = "done"
                _emit(on_event, s, "market_zero_remaining", notes)
                continue
            mqty = min(remain, oso)  # 체결내역 지연 시 remain 부풀림 — 실시간 잔량이 상한
            if not api.cancel(s["order_no"], t):
                _emit(on_event, s, "market_cancel_failed", notes)
                continue
            u2 = api.unfilled(side)
            if u2 is None or _entry(u2, s["order_no"]) is not None:
                _emit(on_event, s, "market_cancel_unconfirmed", notes)
                continue
        if clock.now() >= end:  # 지연으로 밀렸으면 창 밖 주문 금지
            _emit(on_event, s, "market_past_end", notes)
            continue
        res = api.place_market(t, side, mqty)
        if _submitted(res):
            no = str(res["order_no"])
            s["nos"].append(no)
            s["order_no"] = no
            s["status"] = "done"
            _emit(on_event, s, "market_order", notes, no=no, qty=mqty)
        elif res.get("status") == "unknown":
            s["status"] = "unknown"
            _emit(on_event, s, "market_unknown", notes, qty=mqty,
                  message=str(res.get("message", "")))
        else:
            _emit(on_event, s, "market_rejected", notes,
                  status=str(res.get("status")),
                  message=str(res.get("message", "")))


def _cancel_only_step(today: str, states: list[dict], api, notes: list[str],
                      *, first_at: str, market_at: str,
                      on_event: Callable | None = None) -> None:
    """취소 후 확인만 하고 잔량을 carry_qty로 보존한다."""
    live = [s for s in states if s["status"] in ("pending", "working")]
    for s in live:
        _bounds(s, today, first_at, market_at)
    sides = list(dict.fromkeys(s.get("side", "sell") for s in live))
    unfilled = {side: api.unfilled(side) for side in sides}
    for s in live:
        side, ticker = s.get("side", "sell"), s["p"]["ticker"]
        order_no = s.get("order_no", "")
        if unfilled[side] is None:
            s["status"] = "unknown"
            continue
        ent = _entry(unfilled[side], order_no) if order_no else None
        oso = None
        if ent is not None:
            try:
                oso = int(ent.get("oso_qty") or 0)
            except (TypeError, ValueError):
                s["status"] = "unknown"
                continue
            if oso > 0:
                if not api.cancel(s["order_no"], ticker):
                    s["status"] = "unknown"
                    continue
                check = api.unfilled(side)
                if check is None or _entry(check, s["order_no"]) is not None:
                    s["status"] = "unknown"
                    continue
        fills = api.fills(today, side)
        if fills is None:
            s["status"] = "unknown"
            continue
        remaining = max(0, s["qty"] - _fills(s["nos"], fills))
        if order_no and ent is None and remaining > 0:
            s["status"] = "unknown"
            continue
        s["carry_qty"] = min(oso, remaining) if oso is not None else remaining
        s["status"] = "carried"


def place_first_orders(today: str, states: list[dict], api, *, max_rejects: int = 3,
                       market_at: str = "",
                       on_event: Callable | None = None) -> list[str]:
    """첫 주문만 1회 낸다. 대기·정정·시장가 없음. 반환 = notes.

    같은 state를 run_chase에 넘기면 추격이 이어진다(working이면 정정부터).
    미체결·체결 조회가 실패(None)하면 그 쪽은 주문 안 함(fail closed).
    market_at: limit_given_up 이벤트에 실을 예정 시장가 시각.
    """
    notes: list[str] = []
    if on_event is None:
        raise ValueError("on_event 필수 — 주문 기록 없는 실행 금지")
    targets = [s for s in states
               if s.get("status") == "pending" and s.get("qty") is not None]
    for s in targets:
        s.setdefault("side", "sell")
    sides = []
    for s in targets:
        if s["side"] not in sides:
            sides.append(s["side"])
    u = {side: api.unfilled(side) for side in sides}
    hist = {side: api.fills(today, side) for side in sides}
    for s in targets:
        side = s["side"]
        if u[side] is None:  # 조회 실패한 쪽은 주문 금지
            continue
        _try_first_order(today, s, u[side], hist[side], api, notes,
                         market_at=s.get("market_at") or market_at,
                         max_rejects=max_rejects, on_event=on_event)
    return notes


def place_market_orders(today: str, states: list[dict], api, clock, *, end_at: str,
                        on_event: Callable | None = None) -> list[str]:
    """시장가만 1회 낸다(정정·대기 없음). 미주문(pending)은 바로 잔량 시장가, 살아 있는 지정가(working)는 취소·재조회 확인 후 잔량 시장가. 조회 실패·취소 미확인·이미 다 체결·end_at 이후면 내지 않음(fail closed). 예: 추격 창이 끝난 뒤 남은 수량을 단일가 시간에 시장가로 넘길 때. 반환 = notes."""
    if on_event is None:
        raise ValueError("on_event 필수 — 주문 기록 없는 실행 금지")
    notes: list[str] = []
    end = at(today, end_at)
    if clock.now() >= end:
        _emit(on_event, None, "late_start", notes)
        return notes
    _market_step(today, states, api, clock, end, notes, first_at=end_at,
                 market_at=end_at, on_event=on_event)
    return notes


def run_chase(today: str, states: list[dict], api, clock, *, first_at: str,
              market_at: str, end_at: str, step_seconds: int = 10,
              max_rejects: int = 3, on_event: Callable | None = None,
              on_step: Callable | None = None) -> list[str]:
    """추격 본체 — broker·시계는 주입 객체로만. 여러 side의 state 혼합 1루프."""
    if on_event is None:
        raise ValueError("on_event 필수 — 주문 기록 없는 실행 금지")
    notes: list[str] = []
    end = at(today, end_at)
    if clock.now() >= end:  # 창 끝 이후 — 아무것도 안 함
        _emit(on_event, None, "late_start", notes)
        return notes
    for s in states:
        _bounds(s, today, first_at, market_at)
    start = min([s["start_dt"] for s in states], default=at(today, first_at))
    last_market = max([s["market_dt"] for s in states], default=at(today, market_at))

    def _fire_due_markets() -> None:  # 이른 market_dt state를 해당 시각에 시장가 처리
        for mkt in sorted({s["market_dt"] for s in states
                           if not s.get("mkt_done") and s["market_dt"] <= clock.now()}):
            grp = [s for s in states if s["market_dt"] == mkt and not s.get("mkt_done")]
            cutoff = [s for s in grp if s.get("cancel_only_at")]
            ordinary = [s for s in grp if s not in cutoff]
            if cutoff:
                _cancel_only_step(today, cutoff, api, notes,
                                  first_at=first_at, market_at=market_at,
                                  on_event=on_event)
            if ordinary:
                _market_step(today, ordinary, api, clock, end, notes,
                             first_at=first_at, market_at=market_at, on_event=on_event)
            for s in grp:
                s["mkt_done"] = True

    if clock.now() < start:  # 창 전 — 시작까지 대기
        clock.sleep_until(start)
    if clock.now() < last_market:
        _fire_due_markets()
        _chase_step(today, states, api, clock, notes, first_at=first_at,
                    market_at=market_at, max_rejects=max_rejects,
                    on_event=on_event, on_step=on_step)  # 첫 스텝 즉시
        n = 1
        while True:  # start + n*step, market_at 전까지
            nxt = start + timedelta(seconds=n * step_seconds)
            if nxt >= last_market:
                break
            n += 1
            if nxt <= clock.now():  # 늦게 시작 — 지난 격자는 건너뜀
                continue
            clock.sleep_until(nxt)
            if clock.now() >= last_market:
                break
            _fire_due_markets()
            _chase_step(today, states, api, clock, notes, first_at=first_at,
                        market_at=market_at, max_rejects=max_rejects,
                        on_event=on_event, on_step=on_step)
    for mkt in sorted({s["market_dt"] for s in states  # state별 시장가 시각 순으로
                       if not s.get("mkt_done")}):
        if clock.now() < mkt:
            clock.sleep_until(mkt)
        grp = [s for s in states if s["market_dt"] == mkt and not s.get("mkt_done")]
        cutoff = [s for s in grp if s.get("cancel_only_at")]
        ordinary = [s for s in grp if s not in cutoff]
        if cutoff:
            _cancel_only_step(today, cutoff, api, notes,
                              first_at=first_at, market_at=market_at,
                              on_event=on_event)
        if ordinary:
            _market_step(today, ordinary, api, clock, end, notes,
                         first_at=first_at, market_at=market_at, on_event=on_event)
    for s in states:
        if s["modifies"]:
            _emit(on_event, s, "modifies_summary", notes, count=s["modifies"])
    return notes


class ClientEngineApi:
    """BrokerClient 어댑터 — 엔진 api 프로토콜 제공."""

    def __init__(self, client: BrokerClient) -> None:
        self._client = client

    def price(self, t: str, side: str) -> int | None:
        return self._client.limit_price(t, side)

    def best_quote(self, t: str, side: str) -> int | None:
        return self._client.best_quote(t, side)

    def place_limit(self, t: str, side: str, qty: int, price: int) -> dict:
        return self._client.place_limit(t, side, qty, price)

    def place_market(self, t: str, side: str, qty: int) -> dict:
        return self._client.place_market(t, side, qty)

    def modify(self, no: str, t: str, price: int, qty: int) -> dict:
        return self._client.modify(no, t, price, qty)

    def cancel(self, no: str, t: str) -> bool:
        return self._client.cancel(no, t)["status"] in ("cancelled", "dry_run")

    def unfilled(self, side: str) -> dict | None:
        rows = self._client.unfilled(side)
        if rows is None:
            return None
        out = {}
        for row in rows:
            key = normalize_order_no(row.get("order_no"))
            if key:
                out[key] = row
        return out

    def fills(self, date: str, side: str) -> dict | None:
        return fills_by_order(self._client.history(date, side))
