"""공용 주문 실행 엔진 단위 테스트.

기준 엔진의 시나리오를 공용 프로토콜에 맞춰 옮겼다. 가짜는 이 파일에서
새 프로토콜(side 인자·calls 기록)에 맞춰 새로 작성했다.

실행 (etl/ 에서):
    cd etl && PYTHONPATH=".." uv run python -m unittest tests.test_chase_engine
"""
import re
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts import chase_engine as ch
from scripts.chase_engine import ClientEngineApi

TODAY = "20261002"

FIRST = "10:00:00"
BUY_START = "10:03:00"
MARKET = "10:05:00"
END = "10:06:00"


def norm(no):
    return str(no or "").strip().lstrip("0")


def mkstate(qty=400, **kw):
    s = {"p": {"ticker": "005930"}, "qty": qty,
         "nos": [], "order_no": "", "status": "pending",
         "rejects": 0, "modifies": 0}
    s.update(kw)
    return s


def mkbuy(qty=100, **kw):
    s = mkstate(qty=qty, side="buy", start_at=BUY_START, market_at=MARKET,
                p={"ticker": "000660"})
    s.update(kw)
    return s


def unfilled(*rows):
    """(주문번호, 티커[, 잔량[, 주문가]]) → {정규번호: 미체결 entry}."""
    out = {}
    for row in rows:
        qty = row[2] if len(row) > 2 else 400
        price = row[3] if len(row) > 3 else 10050
        out[norm(row[0])] = {"ticker": row[1], "oso_qty": qty,
                             "ord_price": price, "order_no": row[0]}
    return out


class FakeApi:
    """새 엔진 프로토콜 가짜 — 호출 순서를 calls에 남긴다."""

    def __init__(self):
        self.quotes = {}  # side -> int | callable
        self.books = {"sell": {}, "buy": {}}
        self.fill_map = {"sell": {}, "buy": {}}
        self.calls = []
        self.placed = []  # ("place_limit"|"place_market", side, 티커, 수량, 가격)
        self.modified = []  # (원번호, 가격, 수량)
        self.cancelled = []
        self.fail = set()  # "price" | "unfilled" | "fills" | "cancel"
        self.cancel_keeps = False  # True면 취소 성공인데 미체결 잔류
        self.place_result = None  # 지정 시 주문 강제 결과
        self.modify_result = None
        self.seq = 0

    def _no(self):
        self.seq += 1
        return str(1000 + self.seq)

    def price(self, t, side):
        self.calls.append(("price", side))
        if "price" in self.fail:
            return None
        q = self.quotes.get(side)
        return q() if callable(q) else q

    def place_limit(self, t, side, qty, price):
        self.calls.append(("place_limit", side, t, qty, price))
        if self.place_result is not None:
            return dict(self.place_result)
        no = self._no()
        self.books[side][norm(no)] = {"ticker": t, "oso_qty": qty,
                                      "ord_price": price, "order_no": no}
        self.placed.append(("place_limit", side, t, qty, price))
        return {"order_no": no, "status": "submitted", "message": ""}

    def place_market(self, t, side, qty):
        self.calls.append(("place_market", side, t, qty))
        if self.place_result is not None:
            return dict(self.place_result)
        no = self._no()
        self.placed.append(("place_market", side, t, qty, None))
        return {"order_no": no, "status": "submitted", "message": ""}

    def modify(self, no, t, price, qty):
        self.calls.append(("modify", no, price, qty))
        if self.modify_result is not None:
            return dict(self.modify_result)
        new = self._no()
        side, old = "sell", None
        for name, book in self.books.items():
            if norm(no) in book:
                side, old = name, book[norm(no)]
        for book in self.books.values():
            book.pop(norm(no), None)
        self.books[side][norm(new)] = {"ticker": t, "oso_qty": qty if qty != 0 else (old["oso_qty"] if old else 0),
                                       "ord_price": price, "order_no": new}
        self.modified.append((no, price, qty))
        return {"order_no": new, "status": "submitted", "message": ""}

    def cancel(self, no, t):
        self.calls.append(("cancel", no))
        self.cancelled.append(no)
        if "cancel" in self.fail:
            return False
        if not self.cancel_keeps:
            for book in self.books.values():
                book.pop(norm(no), None)
        return True

    def unfilled(self, side):
        self.calls.append(("unfilled", side))
        if "unfilled" in self.fail:
            return None
        return {k: dict(v) for k, v in self.books[side].items()}

    def fills(self, date, side):
        self.calls.append(("fills", side))
        if "fills" in self.fail:
            return None
        return {k: dict(v) for k, v in self.fill_map[side].items()}


class FakeClock:
    def __init__(self, now):
        self.t = now

    def now(self):
        return self.t

    def sleep_until(self, dt):
        if dt > self.t:
            self.t = dt


class EventLog:
    """on_event 콜백 대역 — (state, kind, fields)를 kinds에 남긴다."""

    def __init__(self, note=None):
        self.records = []
        self._note = note

    def __call__(self, state, kind, **fields):
        self.records.append((state, kind, fields))
        return self._note

    def kinds(self):
        return [k for _, k, _ in self.records]

    def fields_of(self, kind):
        return [f for _, k, f in self.records if k == kind]


def _step(today, states, api, clock, notes, ev, **kw):
    kw.setdefault("first_at", FIRST)
    kw.setdefault("market_at", MARKET)
    kw.setdefault("max_rejects", 3)
    kw.setdefault("on_event", ev)
    return ch._chase_step(today, states, api, clock, notes, **kw)


def _market(today, states, api, clock, notes, ev, end=None):
    return ch._market_step(today, states, api, clock, end or ch.at(TODAY, END),
                           notes, first_at=FIRST, market_at=MARKET,
                           on_event=ev)


def _run(today, states, api, clock, ev, **kw):
    kw.setdefault("first_at", FIRST)
    kw.setdefault("market_at", MARKET)
    kw.setdefault("end_at", END)
    kw.setdefault("on_event", ev)
    return ch.run_chase(today, states, api, clock, **kw)


class NoTimeLiteralsTest(unittest.TestCase):
    def test_no_hhmmss_literal(self):
        src = (Path(__file__).resolve().parent.parent
               / "scripts" / "chase_engine.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"\d{2}:\d{2}:\d{2}", src)), 0)


class SellRegressionTest(unittest.TestCase):
    """매도 경로 기존 동작 동일."""

    def setUp(self):
        self.api = FakeApi()
        self.api.quotes = {"sell": 10100}
        self.clock = FakeClock(ch.at(TODAY, "09:59:00"))
        self.ev = EventLog()

    def test_first_sell_bid_plus_tick(self):
        notes = _run(TODAY, [mkstate()], self.api, self.clock, self.ev)
        self.assertEqual(self.api.placed[0],
                         ("place_limit", "sell", "005930", 400, 10100))
        firsts = self.ev.fields_of("first_order")
        self.assertEqual(len(firsts), 1)
        self.assertEqual(firsts[0]["qty"], 400)
        self.assertEqual(firsts[0]["price"], 10100)


class BuyChaseTest(unittest.TestCase):
    """매수 추격 — 시작 전 대기·첫 주문·정정·시장가·종료 후 금지·혼합 루프."""

    def setUp(self):
        self.api = FakeApi()
        self.api.quotes = {"sell": 10100, "buy": 9875}
        self.clock = FakeClock(ch.at(TODAY, "10:02:59"))
        self.ev = EventLog()

    def test_no_order_before_start(self):
        _step(TODAY, [mkbuy()], self.api, self.clock, [], self.ev)
        self.assertEqual(self.api.placed, [])

    def test_first_buy_ask_minus_tick(self):
        clock = FakeClock(ch.at(TODAY, BUY_START))
        notes = []
        ev = EventLog()
        s = mkbuy()
        _step(TODAY, [s], self.api, clock, notes, ev)
        self.assertEqual(self.api.placed,
                         [("place_limit", "buy", "000660", 100, 9875)])
        self.assertEqual(ev.fields_of("first_order"),
                         [{"no": s["order_no"], "price": 9875, "qty": 100}])

    def test_requote_10s(self):
        clock = FakeClock(ch.at(TODAY, BUY_START))
        s = mkbuy()
        _step(TODAY, [s], self.api, clock, [], self.ev)
        first = s["order_no"]
        self.api.quotes["buy"] = 9700
        clock = FakeClock(ch.at(TODAY, "10:03:10"))
        _step(TODAY, [s], self.api, clock, [], self.ev)
        self.assertEqual(self.api.modified, [(first, 9700, 0)])
        self.assertEqual(self.ev.fields_of("modified"),
                         [{"no": s["order_no"], "price": 9700, "qty": 100,
                           "old_price": 9875}])

    def test_market_leftover(self):
        s = mkbuy(status="working", order_no="1001", nos=["1001"])
        self.api.books["buy"] = unfilled(("1001", "000660"))
        notes = []
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, notes, ev)
        self.assertEqual(self.api.cancelled, ["1001"])
        self.assertEqual(self.api.placed, [("place_market", "buy", "000660", 100, None)])
        orders = ev.fields_of("market_order")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 100)

    def test_no_orders_after_end(self):
        clock = FakeClock(ch.at(TODAY, "10:07:00"))
        ev = EventLog()
        _run(TODAY, [mkbuy()], self.api, clock, ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual(len(ev.records), 1)
        state, kind, _ = ev.records[0]
        self.assertIsNone(state)
        self.assertEqual(kind, "late_start")

    def test_mixed_sell_buy_one_loop(self):
        clock = FakeClock(ch.at(TODAY, "09:59:00"))
        ev = EventLog()
        _run(TODAY, [mkstate(), mkbuy()], self.api, clock, ev)
        kinds = [(p[0], p[1]) for p in self.api.placed]
        self.assertEqual(kinds[0], ("place_limit", "sell"))
        self.assertIn(("place_limit", "buy"), kinds)
        self.assertLess(kinds.index(("place_limit", "sell")),
                        kinds.index(("place_limit", "buy")))
        self.assertIn(("place_market", "sell"), kinds)
        self.assertIn(("place_market", "buy"), kinds)
        buy_limit = self.api.placed[kinds.index(("place_limit", "buy"))]
        self.assertEqual(buy_limit[4], 9875)


class BuyFailClosedTest(unittest.TestCase):
    """이력 조회 실패 → 주문 없음 / 결과 불명 → unknown, 재주문 없음."""

    def setUp(self):
        self.api = FakeApi()
        self.api.quotes = {"sell": 10100, "buy": 9875}
        self.clock = FakeClock(ch.at(TODAY, BUY_START))
        self.ev = EventLog()

    def test_history_fail_no_orders(self):
        self.api.fail.add("fills")
        s = mkbuy()
        _step(TODAY, [s], self.api, self.clock, [], self.ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual(s["status"], "pending")
        self.assertEqual(self.ev.records, [])

    def test_unknown_first_no_reorder(self):
        self.api.place_result = {"order_no": "", "status": "unknown",
                                 "message": "timeout"}
        s = mkbuy()
        notes = []
        ev = EventLog()
        _step(TODAY, [s], self.api, self.clock, notes, ev)
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(ev.fields_of("first_unknown"),
                         [{"qty": 100, "message": "timeout"}])
        _step(TODAY, [s], self.api, self.clock, notes, ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual([c for c in self.api.calls if c[0] == "place_limit"],
                         [("place_limit", "buy", "000660", 100, 9875)])


class SizerTest(unittest.TestCase):
    """지연 사이징 — 시작 후 확정·형제 분할·스킵."""

    def setUp(self):
        self.api = FakeApi()
        self.api.quotes = {"sell": 10100, "buy": 9875}
        self.ev = EventLog()

    def test_sizer_split_sibling(self):
        clock = FakeClock(ch.at(TODAY, BUY_START))
        states = [mkbuy(qty=None, sizer=lambda s, api, notes: [60, 40])]
        _step(TODAY, states, self.api, clock, [], self.ev)
        self.assertEqual(len(states), 2)
        self.assertEqual(sorted(p[3] for p in self.api.placed), [40, 60])
        self.assertTrue(all(p[0] == "place_limit" and p[1] == "buy"
                            for p in self.api.placed))

    def test_sizer_none_skipped(self):
        clock = FakeClock(ch.at(TODAY, BUY_START))
        states = [mkbuy(qty=None, sizer=lambda s, api, notes: None)]
        _step(TODAY, states, self.api, clock, [], self.ev)
        self.assertEqual(states[0]["status"], "skipped")
        _market(TODAY, states, self.api, clock, [], self.ev)
        self.assertEqual([p for p in self.api.placed if p[0] == "place_market"],
                         [])

    def test_sizer_waits_for_start(self):
        fired = []
        clock = FakeClock(ch.at(TODAY, "10:02:59"))
        states = [mkbuy(qty=None,
                        sizer=lambda s, api, notes: fired.append(1) or [100])]
        _step(TODAY, states, self.api, clock, [], self.ev)
        self.assertEqual((fired, self.api.placed), ([], []))
        clock = FakeClock(ch.at(TODAY, BUY_START))
        _step(TODAY, states, self.api, clock, [], self.ev)
        self.assertEqual(len(fired), 1)


class EarlyMarketTest(unittest.TestCase):
    """market_at이 이른 state는 해당 시각에 시장가 처리."""

    def setUp(self):
        self.api = FakeApi()
        self.clock = FakeClock(ch.at(TODAY, "09:59:00"))
        self.ev = EventLog()
        t_change = ch.at(TODAY, "10:02:00")
        clock = self.clock
        self.api.quotes = {"sell": lambda: 9900 if clock.now() >= t_change else 10100}

    def test_early_market_at_fires_at_its_time(self):
        a = mkstate(market_at="10:01:00")
        b = mkstate(p={"ticker": "000660"})
        times = {}
        orig_market = self.api.place_market

        def rec_market(t, side, qty):
            times[t] = self.clock.now().strftime("%H:%M:%S")
            return orig_market(t, side, qty)

        self.api.place_market = rec_market
        _run(TODAY, [a, b], self.api, self.clock, self.ev)
        self.assertEqual(times, {"005930": "10:01:00", "000660": MARKET})
        self.assertEqual(a["modifies"], 0)
        self.assertGreaterEqual(b["modifies"], 1)


class SellChaseTest(unittest.TestCase):
    """매도 추격 상세 — 정정·불명·거절·타 주문 보호."""

    def setUp(self):
        self.api = FakeApi()
        self.api.quotes = {"sell": 10100}
        self.clock = FakeClock(ch.at(TODAY, FIRST))
        self.ev = EventLog()

    def test_requote_down_then_same(self):
        s = mkstate()
        _step(TODAY, [s], self.api, self.clock, [], self.ev)
        first = s["order_no"]
        self.api.quotes["sell"] = 9900
        _step(TODAY, [s], self.api, self.clock, [], self.ev)
        self.assertEqual(self.api.modified, [(first, 9900, 0)])
        _step(TODAY, [s], self.api, self.clock, [], self.ev)
        self.assertEqual(len(self.api.modified), 1)

    def test_unknown_first_no_reorder_sell(self):
        self.api.place_result = {"order_no": "", "status": "unknown",
                                 "message": "timeout"}
        s = mkstate()
        ev = EventLog()
        _step(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(ev.fields_of("first_unknown"),
                         [{"qty": 400, "message": "timeout"}])
        _step(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual(len([c for c in self.api.calls
                              if c[0] == "place_limit"]), 1)

    def test_unknown_modify(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        self.api.books["sell"] = unfilled(("1001", "005930"))
        self.api.quotes["sell"] = 9900
        self.api.modify_result = {"order_no": "", "status": "unknown",
                                  "message": "timeout"}
        ev = EventLog()
        _step(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(ev.fields_of("modify_unknown"), [{"qty": 400}])

    def test_rejects_then_market_only(self):
        self.api.place_result = {"order_no": "", "status": "rejected",
                                 "message": "stop"}
        s = mkstate()
        ev = EventLog()
        for _ in range(3):
            _step(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(s["status"], "failed")
        self.assertEqual(ev.fields_of("limit_given_up"),
                         [{"market_at": MARKET}])
        self.api.place_result = None
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.api.placed,
                         [("place_market", "sell", "005930", 400, None)])

    def test_untouched_others(self):
        other = unfilled(("1009", "000660"), ("1010", "005930"))
        self.api.books["sell"] = dict(other)
        s = mkstate()
        ev = EventLog()
        _step(TODAY, [s], self.api, self.clock, [], ev)
        _market(TODAY, [s], self.api, self.clock, [], ev)
        for k in other:
            self.assertIn(k, self.api.books["sell"])
        self.assertEqual(self.api.cancelled, [s["nos"][0]])


class MarketFlowTest(unittest.TestCase):
    """시장가 단계 — 호출 순서·취소 확인·종료 시각·조회 실패."""

    def setUp(self):
        self.api = FakeApi()
        self.api.quotes = {"sell": 10100}
        self.clock = FakeClock(ch.at(TODAY, FIRST))
        self.ev = EventLog()

    def working(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        self.api.books["sell"] = unfilled(("1001", "005930"))
        return s

    def markets(self):
        return [c for c in self.api.calls if c[0] == "place_market"]

    def test_market_call_order(self):
        s = self.working()
        _market(TODAY, [s], self.api, self.clock, [], self.ev)
        flow = [c for c in self.api.calls
                if c[0] in ("unfilled", "cancel", "place_market")]
        self.assertEqual(flow, [("unfilled", "sell"), ("cancel", "1001"),
                                ("unfilled", "sell"),
                                ("place_market", "sell", "005930", 400)])
        self.assertEqual(len(self.markets()), 1)
        self.assertEqual(self.ev.fields_of("market_order"),
                         [{"no": s["order_no"], "qty": 400}])

    def test_cancel_unconfirmed_no_market(self):
        self.api.cancel_keeps = True
        s = self.working()
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.markets(), [])
        self.assertEqual(ev.kinds(), ["market_cancel_unconfirmed"])

    def test_cancel_failed_no_market(self):
        self.api.fail.add("cancel")
        s = self.working()
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.markets(), [])
        self.assertEqual(ev.kinds(), ["market_cancel_failed"])

    def test_past_end_no_market(self):
        clock = FakeClock(ch.at(TODAY, "10:07:00"))
        s = self.working()
        ev = EventLog()
        _market(TODAY, [s], self.api, clock, [], ev)
        self.assertEqual(self.markets(), [])
        self.assertEqual(ev.kinds(), ["market_past_end"])

    def test_unfilled_none_fail_closed(self):
        self.api.fail.add("unfilled")
        s = mkstate()
        ev = EventLog()
        _step(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual(self.api.calls, [("unfilled", "sell")])
        s2 = mkstate(status="working", order_no="1001", nos=["1001"])
        ev2 = EventLog()
        _market(TODAY, [s2], self.api, self.clock, [], ev2)
        self.assertEqual(self.markets(), [])
        self.assertEqual(ev2.kinds(), ["market_lookup_failed"])

    def test_all_filled_no_market(self):
        s = self.working()
        self.api.fill_map["sell"] = {"1001": {"cntr_qty": 400}}
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.markets(), [])
        self.assertEqual(s["status"], "done")
        self.assertEqual(ev.kinds(), ["market_all_filled"])

    def test_missing_order_treated_as_filled(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.markets(), [])
        self.assertEqual(s["status"], "done")
        self.assertEqual(ev.kinds(), ["market_not_in_unfilled"])

    def test_zero_remaining_no_market(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        self.api.books["sell"] = unfilled(("1001", "005930", 0, 10050))
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.markets(), [])
        self.assertEqual(s["status"], "done")
        self.assertEqual(ev.kinds(), ["market_zero_remaining"])

    def test_market_rejected(self):
        s = self.working()
        self.api.place_result = {"order_no": "", "status": "rejected",
                                 "message": "nope"}
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(s["status"], "working")
        self.assertEqual(ev.fields_of("market_rejected"),
                         [{"message": "nope", "status": "rejected"}])

    def test_qty_unknown_skipped(self):
        s = mkstate(qty=None)
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(s["status"], "skipped")
        self.assertEqual(self.markets(), [])
        self.assertEqual(ev.kinds(), ["market_qty_unknown"])


class HooksTest(unittest.TestCase):
    """price_fn·on_step·dry_run·notes·정정 요약."""

    def setUp(self):
        self.api = FakeApi()
        self.api.quotes = {"sell": 99999}
        self.clock = FakeClock(ch.at(TODAY, FIRST))

    def test_price_fn_used_and_same_price_skips_modify(self):
        s = mkstate(price_fn=lambda t: 12345)
        ev = EventLog()
        _step(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.api.placed,
                         [("place_limit", "sell", "005930", 400, 12345)])
        self.assertTrue(all(c[0] != "price" for c in self.api.calls))
        _step(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(self.api.modified, [])

    def test_on_step_called_each_step_and_state_joins_same_step(self):
        fired = []
        added = []

        def hook(states, api, now):
            fired.append(now)
            if not added:
                ns = mkstate()
                states.append(ns)
                added.append(ns)

        s = mkstate()
        ev = EventLog()
        _step(TODAY, [s], self.api, self.clock, [], ev, on_step=hook)
        self.assertEqual(len(fired), 1)
        self.assertNotEqual(added[0]["order_no"], "")
        self.assertEqual(len(self.api.placed), 2)
        _step(TODAY, [s], self.api, self.clock, [], ev, on_step=hook)
        self.assertEqual(len(fired), 2)

    def test_dry_run_counts_as_submitted(self):
        self.api.place_result = {"order_no": "D1", "status": "dry_run"}
        s = mkstate()
        ev = EventLog()
        _step(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(s["status"], "working")
        self.assertEqual(s["order_no"], "D1")
        self.assertEqual(s["nos"], ["D1"])
        self.assertEqual(ev.fields_of("first_order"),
                         [{"no": "D1", "price": 99999, "qty": 400}])

    def test_on_event_notes(self):
        ev = EventLog(note="hello")
        notes = []
        _step(TODAY, [mkstate()], self.api, self.clock, notes, ev)
        self.assertEqual(notes, ["hello"])
        ev_none = EventLog(note=None)
        notes2 = []
        _step(TODAY, [mkstate()], self.api, self.clock, notes2, ev_none)
        self.assertEqual(notes2, [])

    def test_modifies_summary_at_end(self):
        clock = FakeClock(ch.at(TODAY, "09:59:00"))
        t_change = ch.at(TODAY, "10:00:10")
        self.api.quotes = {"sell": lambda: 9900 if clock.now() >= t_change else 10100}
        states = [mkstate()]
        ev = EventLog()
        _run(TODAY, states, self.api, clock, ev)
        self.assertGreaterEqual(states[0]["modifies"], 1)
        self.assertEqual(ev.fields_of("modifies_summary"),
                         [{"count": states[0]["modifies"]}])


class ClientEngineApiTest(unittest.TestCase):
    """BrokerClient 어댑터 — 정규화·None 전달·취소 판정·위임."""

    def test_unfilled_normalizes_and_drops_empty(self):
        client = Mock()
        client.unfilled.return_value = [
            {"order_no": "00050", "oso_qty": 10},
            {"order_no": "", "oso_qty": 5},
            {"order_no": "50", "oso_qty": 7},
        ]
        out = ClientEngineApi(client).unfilled("sell")
        client.unfilled.assert_called_once_with("sell")
        self.assertEqual(set(out), {"50"})
        self.assertEqual(out["50"]["oso_qty"], 7)

    def test_unfilled_none_stays_none(self):
        client = Mock()
        client.unfilled.return_value = None
        self.assertIsNone(ClientEngineApi(client).unfilled("sell"))

    def test_fills_none_and_aggregation(self):
        client = Mock()
        api = ClientEngineApi(client)
        client.history.return_value = None
        self.assertIsNone(api.fills(TODAY, "sell"))
        client.history.return_value = [
            {"order_no": "00050", "cntr_qty": 100},
            {"order_no": "50", "cntr_qty": 50},
            {"order_no": "", "cntr_qty": 9},
        ]
        self.assertEqual(api.fills(TODAY, "sell"), {"50": {"cntr_qty": 150}})
        client.history.assert_called_with(TODAY, "sell")

    def test_cancel_bool_by_status(self):
        for status, want in (("cancelled", True), ("dry_run", True),
                             ("failed", False), ("unknown", False)):
            client = Mock()
            client.cancel.return_value = {"order_no": "1", "status": status}
            self.assertEqual(ClientEngineApi(client).cancel("1", "005930"),
                             want, status)

    def test_delegates_price_and_orders(self):
        client = Mock()
        api = ClientEngineApi(client)
        client.limit_price.return_value = 100
        self.assertEqual(api.price("005930", "sell"), 100)
        client.limit_price.assert_called_once_with("005930", "sell")
        res = {"order_no": "1", "status": "submitted"}
        client.place_limit.return_value = res
        self.assertEqual(api.place_limit("005930", "sell", 400, 100), res)
        client.place_limit.assert_called_once_with("005930", "sell", 400, 100)
        client.place_market.return_value = res
        self.assertEqual(api.place_market("005930", "sell", 400), res)
        client.place_market.assert_called_once_with("005930", "sell", 400)
        client.modify.return_value = res
        self.assertEqual(api.modify("1", "005930", 100, 400), res)
        client.modify.assert_called_once_with("1", "005930", 100, 400)


class PlaceFirstOrdersTest(unittest.TestCase):
    """place_first_orders — 첫 주문만 1회, 대기·정정·시장가 없음."""

    def setUp(self):
        self.api = FakeApi()
        self.ev = EventLog()

    def test_two_pending_sell_places_two(self):
        a = mkstate(price_fn=lambda t: 10100)
        b = mkstate(price_fn=lambda t: 10200, p={"ticker": "000660"})
        notes = ch.place_first_orders(TODAY, [a, b], self.api,
                                      on_event=self.ev)
        self.assertEqual(len(self.api.placed), 2)
        self.assertTrue(all(p[0] == "place_limit" for p in self.api.placed))
        for s in (a, b):
            self.assertEqual(s["status"], "working")
            self.assertNotEqual(s["order_no"], "")
        self.assertEqual(len(self.ev.fields_of("first_order")), 2)
        self.assertEqual(self.api.modified, [])
        self.assertEqual(self.api.cancelled, [])
        self.assertEqual([p for p in self.api.placed
                          if p[0] == "place_market"], [])
        self.assertIsInstance(notes, list)

    def test_unfilled_none_no_orders(self):
        self.api.fail.add("unfilled")
        s = mkstate(price_fn=lambda t: 10100)
        ch.place_first_orders(TODAY, [s], self.api, on_event=self.ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual(s["status"], "pending")
        self.assertEqual(self.ev.records, [])

    def test_fills_none_no_orders(self):
        self.api.fail.add("fills")
        s = mkstate(price_fn=lambda t: 10100)
        ch.place_first_orders(TODAY, [s], self.api, on_event=self.ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual(s["status"], "pending")

    def test_already_filled_goes_done(self):
        s = mkstate(nos=["1001"], price_fn=lambda t: 10100)
        self.api.fill_map["sell"] = {"1001": {"cntr_qty": 400}}
        ch.place_first_orders(TODAY, [s], self.api, on_event=self.ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual(s["status"], "done")

    def test_unknown_no_reorder(self):
        self.api.place_result = {"order_no": "", "status": "unknown",
                                 "message": "timeout"}
        s = mkstate(price_fn=lambda t: 10100)
        ev = EventLog()
        ch.place_first_orders(TODAY, [s], self.api, on_event=ev)
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(ev.fields_of("first_unknown"),
                         [{"qty": 400, "message": "timeout"}])
        ch.place_first_orders(TODAY, [s], self.api, on_event=ev)
        self.assertEqual([c for c in self.api.calls if c[0] == "place_limit"],
                         [("place_limit", "sell", "005930", 400, 10100)])

    def test_explicit_reject_emits_first_rejected(self):
        self.api.place_result = {"order_no": "", "status": "rejected",
                                 "message": "stop"}
        s = mkstate(price_fn=lambda t: 10100)
        ev = EventLog()
        ch.place_first_orders(TODAY, [s], self.api, on_event=ev)
        self.assertEqual(s["status"], "pending")
        self.assertEqual(s["rejects"], 1)
        self.assertEqual(ev.fields_of("first_rejected"),
                         [{"status": "rejected", "message": "stop",
                           "rejects": 1}])

    def test_non_pending_or_sizer_states_untouched(self):
        w = mkstate(status="working", order_no="1001", nos=["1001"],
                    price_fn=lambda t: 10100)
        d = mkstate(status="done", order_no="1002", nos=["1002"],
                    price_fn=lambda t: 10100)
        n = mkstate(qty=None, price_fn=lambda t: 10100)
        ch.place_first_orders(TODAY, [w, d, n], self.api, on_event=self.ev)
        self.assertEqual(self.api.placed, [])
        self.assertEqual(self.api.calls, [])
        self.assertEqual((w["status"], d["status"], n["status"]),
                         ("working", "done", "pending"))
        self.assertIsNone(n["qty"])

    def test_mixed_sides_queried_once_each(self):
        a = mkstate(price_fn=lambda t: 10100)
        b = mkstate(side="buy", qty=100, price_fn=lambda t: 9875,
                    p={"ticker": "000660"})
        ch.place_first_orders(TODAY, [a, b], self.api, on_event=self.ev)
        unf = sorted(c for c in self.api.calls if c[0] == "unfilled")
        fil = sorted(c for c in self.api.calls if c[0] == "fills")
        self.assertEqual(unf, [("unfilled", "buy"), ("unfilled", "sell")])
        self.assertEqual(fil, [("fills", "buy"), ("fills", "sell")])
        self.assertEqual([(p[0], p[1]) for p in self.api.placed],
                         [("place_limit", "sell"), ("place_limit", "buy")])


class FirstOrderEventFieldsTest(unittest.TestCase):
    """on_event 새 필드 — 정정 old_price, 시장가 message/status."""

    def setUp(self):
        self.api = FakeApi()
        self.clock = FakeClock(ch.at(TODAY, FIRST))
        self.ev = EventLog()

    def test_modified_has_old_price(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"],
                    price_fn=lambda t: 9900)
        self.api.books["sell"] = unfilled(("1001", "005930", 400, 10050))
        _step(TODAY, [s], self.api, self.clock, [], self.ev)
        self.assertEqual(self.ev.fields_of("modified"),
                         [{"no": s["order_no"], "price": 9900, "qty": 400,
                           "old_price": 10050}])

    def test_market_unknown_has_message(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        self.api.books["sell"] = unfilled(("1001", "005930"))
        self.api.place_result = {"order_no": "", "status": "unknown",
                                 "message": "boom"}
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(ev.fields_of("market_unknown"),
                         [{"qty": 400, "message": "boom"}])

    def test_market_rejected_has_status(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        self.api.books["sell"] = unfilled(("1001", "005930"))
        self.api.place_result = {"order_no": "", "status": "rejected",
                                 "message": "nope"}
        ev = EventLog()
        _market(TODAY, [s], self.api, self.clock, [], ev)
        self.assertEqual(ev.fields_of("market_rejected"),
                         [{"status": "rejected", "message": "nope"}])


class ResumePendingTest(unittest.TestCase):
    """복원된 pending 이어받기 — 살아 있는 우리 주문이 있으면 새 첫 주문 안 함."""

    def setUp(self):
        self.api = FakeApi()
        self.api.quotes = {"sell": 10100}
        self.clock = FakeClock(ch.at(TODAY, FIRST))
        self.ev = EventLog()

    def test_place_first_orders_resumes_live_order(self):
        s = mkstate(nos=["A1"], price_fn=lambda t: 10100)
        self.api.books["sell"] = unfilled(("A1", "005930"))
        ch.place_first_orders(TODAY, [s], self.api, on_event=self.ev)
        self.assertEqual([c for c in self.api.calls
                          if c[0] == "place_limit"], [])
        self.assertEqual(s["status"], "working")
        self.assertEqual(s["order_no"], "A1")
        self.assertEqual(self.ev.fields_of("resumed"), [{"no": "A1"}])

    def test_place_first_orders_ambiguous_live_orders_unknown(self):
        s = mkstate(nos=["A1", "A2"], price_fn=lambda t: 10100)
        self.api.books["sell"] = unfilled(("A1", "005930"), ("A2", "005930"))
        ch.place_first_orders(TODAY, [s], self.api, on_event=self.ev)
        self.assertEqual([c for c in self.api.calls
                          if c[0] == "place_limit"], [])
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(self.ev.fields_of("resume_ambiguous"),
                         [{"nos": ["A1", "A2"]}])
        self.assertEqual(self.ev.fields_of("resumed"), [])

    def test_run_chase_ambiguous_live_orders_no_orders(self):
        s = mkstate(nos=["A1", "A2"], price_fn=lambda t: 10100)
        self.api.books["sell"] = unfilled(("A1", "005930"), ("A2", "005930"))
        clock = FakeClock(ch.at(TODAY, "09:59:00"))
        ev = EventLog()
        _run(TODAY, [s], self.api, clock, ev)
        self.assertEqual([c for c in self.api.calls
                          if c[0] == "place_limit"], [])
        self.assertEqual(self.api.modified, [])
        self.assertEqual([c for c in self.api.calls
                          if c[0] == "place_market"], [])

    def test_run_chase_first_step_resumes_then_modifies(self):
        s = mkstate(nos=["A1"], price_fn=lambda t: 9900)
        self.api.books["sell"] = unfilled(("A1", "005930", 400, 10050))
        clock = FakeClock(ch.at(TODAY, "09:59:00"))
        ev = EventLog()
        snaps = []

        def hook(states, api, now):
            snaps.append((len([c for c in self.api.calls
                               if c[0] == "place_limit"]),
                          len(self.api.modified)))

        _run(TODAY, [s], self.api, clock, ev, on_step=hook)
        self.assertEqual(snaps[1], (0, 0))  # 첫 스텝: 주문·정정 없음
        self.assertEqual(self.api.modified, [("A1", 9900, 0)])
        self.assertEqual(ev.fields_of("resumed"), [{"no": "A1"}])

    def test_unknown_nos_places_first_order(self):
        s = mkstate(nos=["ZZZ"], price_fn=lambda t: 10100)
        ev = EventLog()
        ch.place_first_orders(TODAY, [s], self.api, on_event=ev)
        self.assertEqual(len([c for c in self.api.calls
                              if c[0] == "place_limit"]), 1)
        self.assertEqual(s["status"], "working")
        self.assertEqual(ev.fields_of("resumed"), [])
        self.assertEqual(len(ev.fields_of("first_order")), 1)

    def test_limit_given_up_uses_caller_market_at(self):
        self.api.place_result = {"order_no": "", "status": "rejected",
                                 "message": "stop"}
        s = mkstate(price_fn=lambda t: 10100)
        ev = EventLog()
        ch.place_first_orders(TODAY, [s], self.api, max_rejects=1,
                              market_at="10:09:00", on_event=ev)
        self.assertEqual(s["status"], "failed")
        self.assertEqual(ev.fields_of("limit_given_up"),
                         [{"market_at": "10:09:00"}])

    def test_limit_given_up_prefers_state_market_at(self):
        self.api.place_result = {"order_no": "", "status": "rejected",
                                 "message": "stop"}
        s = mkstate(price_fn=lambda t: 10100, market_at="10:08:00")
        ev = EventLog()
        ch.place_first_orders(TODAY, [s], self.api, max_rejects=1,
                              market_at="10:09:00", on_event=ev)
        self.assertEqual(ev.fields_of("limit_given_up"),
                         [{"market_at": "10:08:00"}])


class PlaceMarketOrdersTest(unittest.TestCase):

    def setUp(self):
        self.api = FakeApi()
        self.ev = EventLog()

    def test_pending_goes_market_once(self):
        s = mkstate()
        clock = FakeClock(ch.at(TODAY, FIRST))
        ev = EventLog()
        notes = ch.place_market_orders(TODAY, [s], self.api, clock,
                                       end_at=END, on_event=ev)
        self.assertEqual(self.api.placed,
                         [("place_market", "sell", s["p"]["ticker"],
                           s["qty"], None)])
        self.assertEqual(s["status"], "done")
        self.assertEqual(ev.fields_of("market_order"),
                         [{"no": s["order_no"], "qty": s["qty"]}])
        self.assertIsInstance(notes, list)

    def test_working_live_order_cancelled_then_market(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        self.api.books["sell"] = unfilled(("1001", s["p"]["ticker"], 100))
        clock = FakeClock(ch.at(TODAY, FIRST))
        ev = EventLog()
        ch.place_market_orders(TODAY, [s], self.api, clock,
                               end_at=END, on_event=ev)
        self.assertEqual(self.api.cancelled, ["1001"])
        self.assertEqual(self.api.placed,
                         [("place_market", "sell", s["p"]["ticker"], 100, None)])
        self.assertEqual(ev.fields_of("market_order"),
                         [{"no": s["order_no"], "qty": 100}])

    def test_already_filled_no_market(self):
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        self.api.fill_map["sell"] = {"1001": {"cntr_qty": s["qty"]}}
        clock = FakeClock(ch.at(TODAY, FIRST))
        ev = EventLog()
        ch.place_market_orders(TODAY, [s], self.api, clock,
                               end_at=END, on_event=ev)
        self.assertEqual([c for c in self.api.calls
                          if c[0] == "place_market"], [])
        self.assertEqual(ev.kinds(), ["market_all_filled"])

    def test_fills_lookup_failed_no_market(self):
        self.api.fail.add("fills")
        s = mkstate()
        clock = FakeClock(ch.at(TODAY, FIRST))
        ev = EventLog()
        ch.place_market_orders(TODAY, [s], self.api, clock,
                               end_at=END, on_event=ev)
        self.assertEqual([c for c in self.api.calls
                          if c[0] == "place_market"], [])
        self.assertEqual(ev.kinds(), ["market_lookup_failed"])

    def test_late_start_no_market(self):
        s = mkstate()
        clock = FakeClock(ch.at(TODAY, END))
        ev = EventLog()
        ch.place_market_orders(TODAY, [s], self.api, clock,
                               end_at=MARKET, on_event=ev)
        self.assertEqual([c for c in self.api.calls
                          if c[0] == "place_market"], [])
        self.assertEqual(len(ev.records), 1)
        state, kind, _ = ev.records[0]
        self.assertIsNone(state)
        self.assertEqual(kind, "late_start")


class OnEventRequiredTest(unittest.TestCase):
    """on_event 없으면 세 진입점 모두 주문 전에 ValueError."""

    def test_none_on_event_rejected_before_any_call(self):
        api = FakeApi()
        api.quotes = {"sell": 10100}
        with self.assertRaises(ValueError):
            ch.place_first_orders(TODAY, [mkstate()], api)
        self.assertEqual(api.calls, [])
        api = FakeApi()
        api.quotes = {"sell": 10100}
        clock = FakeClock(ch.at(TODAY, FIRST))
        with self.assertRaises(ValueError):
            ch.place_market_orders(TODAY, [mkstate()], api, clock, end_at=END)
        self.assertEqual(api.calls, [])
        api = FakeApi()
        api.quotes = {"sell": 10100}
        clock = FakeClock(ch.at(TODAY, "09:59:00"))
        with self.assertRaises(ValueError):
            ch.run_chase(TODAY, [mkstate()], api, clock,
                         first_at=FIRST, market_at=MARKET, end_at=END)
        self.assertEqual(api.calls, [])

    def test_modify_qty_zero_event_qty_oso(self):
        api = FakeApi()
        api.quotes = {"sell": 9900}
        clock = FakeClock(ch.at(TODAY, FIRST))
        s = mkstate(status="working", order_no="1001", nos=["1001"])
        api.books["sell"] = unfilled(("1001", "005930", 250, 10050))
        ev = EventLog()
        _step(TODAY, [s], api, clock, [], ev)
        self.assertEqual(api.modified, [("1001", 9900, 0)])
        self.assertEqual(ev.fields_of("modified"),
                         [{"no": s["order_no"], "price": 9900, "qty": 250,
                           "old_price": 10050}])


if __name__ == "__main__":
    unittest.main()
