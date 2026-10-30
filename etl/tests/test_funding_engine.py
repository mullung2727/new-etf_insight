"""공용 자금 엔진 원장 테스트 (unittest, 네트워크 없음)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from scripts import funding_engine as fe
from scripts.wl_sqlite import connect_ro


class FundingEngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "funding.sqlite3"
        fe.ensure(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _buy_filled(self, order_no, qty, price, date="20260105"):
        fe.record_order(self.db, date=date, side="buy", qty=qty, price=0,
                        order_no=order_no, status="submitted",
                        created=f"{date}T090000")
        fe.update_fill(self.db, order_no, status="filled",
                       filled_qty=qty, filled_price=price)

    def _sell_filled(self, order_no, qty, price, date="20260106"):
        fe.record_order(self.db, date=date, side="sell", qty=qty, price=0,
                        order_no=order_no, status="submitted",
                        created=f"{date}T090000")
        fe.update_fill(self.db, order_no, status="filled",
                       filled_qty=qty, filled_price=price)

    def test_ensure_twice_ok(self):
        fe.ensure(self.db)
        fe.ensure(self.db)

    def test_add_member_duplicate_keeps_joined(self):
        fe.add_member(self.db, "rebound", "20260101")
        fe.add_member(self.db, "rebound", "20260202")
        with connect_ro(self.db) as con:
            row = con.execute(
                "SELECT joined FROM members WHERE strategy = 'rebound'"
            ).fetchone()
        self.assertEqual(row[0], "20260101")

    def test_members_sorted(self):
        fe.add_member(self.db, "swing", "20260101")
        fe.add_member(self.db, "rebound", "20260101")
        fe.add_member(self.db, "envelope", "20260101")
        self.assertEqual(fe.members(self.db), ["envelope", "rebound", "swing"])

    def test_record_order_bad_side(self):
        with self.assertRaises(ValueError):
            fe.record_order(self.db, date="20260105", side="hold", qty=1,
                            price=0, order_no="o1", status="submitted",
                            created="20260105T090000")

    def test_record_order_bad_status(self):
        with self.assertRaises(ValueError):
            fe.record_order(self.db, date="20260105", side="buy", qty=1,
                            price=0, order_no="o1", status="weird",
                            created="20260105T090000")

    def test_record_order_bad_qty(self):
        for bad in (0, -3):
            with self.assertRaises(ValueError, msg=f"qty={bad}"):
                fe.record_order(self.db, date="20260105", side="buy", qty=bad,
                                price=0, order_no="o1", status="submitted",
                                created="20260105T090000")

    def test_record_order_empty_order_no(self):
        with self.assertRaises(ValueError):
            fe.record_order(self.db, date="20260105", side="buy", qty=1,
                            price=0, order_no="", status="submitted",
                            created="20260105T090000")

    def test_record_order_duplicate(self):
        fe.record_order(self.db, date="20260105", side="buy", qty=1, price=0,
                        order_no="o1", status="submitted",
                        created="20260105T090000")
        with self.assertRaises(ValueError):
            fe.record_order(self.db, date="20260105", side="buy", qty=1,
                            price=0, order_no="o1", status="submitted",
                            created="20260105T090000")

    def test_update_fill_missing(self):
        with self.assertRaises(KeyError):
            fe.update_fill(self.db, "xxx", status="filled", filled_qty=1,
                           filled_price=100)

    def test_update_fill_over_qty(self):
        fe.record_order(self.db, date="20260105", side="buy", qty=3, price=0,
                        order_no="o1", status="submitted",
                        created="20260105T090000")
        with self.assertRaises(ValueError):
            fe.update_fill(self.db, "o1", status="partial", filled_qty=4,
                           filled_price=100)

    def test_update_fill_negative(self):
        fe.record_order(self.db, date="20260105", side="buy", qty=3, price=0,
                        order_no="o1", status="submitted",
                        created="20260105T090000")
        with self.assertRaises(ValueError):
            fe.update_fill(self.db, "o1", status="partial", filled_qty=-1,
                           filled_price=100)

    def test_etf_position_avg_cost_flow(self):
        self._buy_filled("b1", 3, 1000000)
        self._buy_filled("b2", 2, 1010000)
        self.assertEqual(fe.etf_position(self.db), {"qty": 5, "cost": 5020000})
        self._sell_filled("s1", 2, 1010000)
        self.assertEqual(fe.etf_position(self.db), {"qty": 3, "cost": 3012000})
        self._sell_filled("s2", 3, 1010000)
        self.assertEqual(fe.etf_position(self.db), {"qty": 0, "cost": 0})

    def test_etf_position_ignores_unfilled(self):
        self._buy_filled("b1", 2, 100)
        fe.record_order(self.db, date="20260105", side="buy", qty=10, price=0,
                        order_no="u1", status="submitted",
                        created="20260105T091000")
        self.assertEqual(fe.etf_position(self.db), {"qty": 2, "cost": 200})

    def test_etf_position_oversell_raises(self):
        self._buy_filled("b1", 1, 100)
        self._sell_filled("s1", 2, 100)
        with self.assertRaises(ValueError):
            fe.etf_position(self.db)

    def test_sellable_qty(self):
        self._buy_filled("b1", 3, 100)
        self.assertEqual(fe.sellable_qty(self.db, 10), 3)
        self.assertEqual(fe.sellable_qty(self.db, 1), 1)
        self.assertEqual(fe.sellable_qty(self.db, None), 0)

    def test_record_pnl_idempotent(self):
        self.assertTrue(fe.record_pnl(self.db, date="20260106", order_no="s1",
                                      amount=5000))
        self.assertFalse(fe.record_pnl(self.db, date="20260106", order_no="s1",
                                       amount=5000))
        self.assertEqual(fe.pnl_since(self.db, "rebound", ""), 5000)

    def test_pnl_since_filters(self):
        fe.record_pnl(self.db, date="20260101", order_no="a", amount=1000)
        fe.record_pnl(self.db, date="20260201", order_no="b", amount=2000)
        fe.record_pnl(self.db, date="20260201", order_no="c", amount=700,
                       strategy="swing")
        self.assertEqual(fe.pnl_since(self.db, "rebound", "20260201"), 2000)
        self.assertEqual(fe.pnl_since(self.db, "rebound", ""), 3000)
        self.assertEqual(fe.pnl_since(self.db, "swing", ""), 700)

    def test_pnl_since_missing_db_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.sqlite3"
            self.assertEqual(fe.pnl_since(missing, "rebound", ""), 0)

    # --- 2단계: 가용 계산 ---
    def test_set_unused_missing(self):
        with self.assertRaises(KeyError):
            fe.set_unused(self.db, "rights_dip", 100, "20260105T150000")

    def test_set_unused_bad_amount(self):
        fe.add_member(self.db, "rights_dip", "20260101")
        for bad in ("100", True, False, 1.5, None):
            with self.assertRaises(ValueError, msg=f"amount={bad!r}"):
                fe.set_unused(self.db, "rights_dip", bad, "20260105T150000")

    def test_set_unused_negative_zero(self):
        fe.add_member(self.db, "rights_dip", "20260101")
        fe.set_unused(self.db, "rights_dip", -500, "20260105T150000")
        self.assertEqual(fe.unused_of(self.db, "rights_dip"), 0)

    def test_unused_of_missing(self):
        with self.assertRaises(KeyError):
            fe.unused_of(self.db, "rights_dip")

    def test_total_unused(self):
        self.assertEqual(fe.total_unused(self.db), 0)
        fe.add_member(self.db, "a", "20260101")
        fe.add_member(self.db, "b", "20260101")
        fe.set_unused(self.db, "a", 3000000, "20260105T150000")
        fe.set_unused(self.db, "b", 4000000, "20260105T150000")
        self.assertEqual(fe.total_unused(self.db), 7000000)

    def test_engine_reserve(self):
        fe.add_member(self.db, "a", "20260101")
        fe.add_member(self.db, "b", "20260101")
        fe.set_unused(self.db, "a", 5000000, "20260105T150000")
        fe.set_unused(self.db, "b", 2000000, "20260105T150000")
        self._buy_filled("b1", 2, 1070000)
        self.assertEqual(fe.engine_reserve(self.db), 4860000)

    def test_engine_reserve_floor_zero(self):
        fe.add_member(self.db, "a", "20260101")
        fe.set_unused(self.db, "a", 1000000, "20260105T150000")
        self._buy_filled("b1", 2, 1070000)
        self.assertEqual(fe.engine_reserve(self.db), 0)

    def test_etf_value(self):
        self.assertEqual(fe.etf_value(3, 1073485), 3219810)
        self.assertEqual(fe.etf_value(0, 1073485), 0)
        self.assertEqual(fe.etf_value(3, 0), 0)

    def test_cash_view(self):
        reservations = {
            "rebound": {"reserve": 4000000},
            "jevq": {"reserve": 1000000},
            "engine": {"reserve": 3000000},
            "rights_dip": {"reserve": 2000000},
        }
        self.assertEqual(
            fe.cash_view(10000000, reservations, ["rights_dip"]), 5000000)

    def test_cash_view_bad_reserve(self):
        self.assertEqual(
            fe.cash_view(10000000, {"rebound": {"reserve": "4000000"}}, []), 0)
        self.assertEqual(
            fe.cash_view(10000000, {"rebound": {"reserve": -100}}, []), 0)
        self.assertEqual(
            fe.cash_view(10000000, {"rebound": {"reserve": True}}, []), 0)

    def test_cash_view_non_dict_fail_closed(self):
        self.assertEqual(
            fe.cash_view(10000000, {"rebound": 4000000}, []), 0)

    def test_cash_view_none(self):
        self.assertIsNone(
            fe.cash_view(None, {"rebound": {"reserve": 100}}, []))

    def test_cash_view_none_reservations_fail_closed(self):
        self.assertEqual(fe.cash_view(10000000, None, []), 0)
        self.assertIsNone(fe.cash_view(None, None, []))

    def test_cash_view_negative_floor(self):
        self.assertEqual(
            fe.cash_view(100, {"rebound": {"reserve": 500}}, []), 0)

    def test_available(self):
        fe.add_member(self.db, "rights_dip", "20260101")
        fe.set_unused(self.db, "rights_dip", 5000000, "20260105T150000")
        self._buy_filled("b1", 2, 1070000)
        out = fe.available(
            self.db, "rights_dip", account_cash=3000000,
            reservations={"rebound": {"reserve": 1000000}},
            balance_qty=2, etf_price=1073485)
        ev = fe.etf_value(2, 1073485)
        self.assertEqual(out["cash"], 2000000)
        self.assertEqual(out["etf_qty"], 2)
        self.assertEqual(out["etf_value"], ev)
        self.assertEqual(out["pool"], 2000000 + ev)
        self.assertEqual(out["unused"], 5000000)
        self.assertEqual(out["grantable"], min(5000000, 2000000 + ev))

    def test_available_no_balance_no_price(self):
        fe.add_member(self.db, "rights_dip", "20260101")
        fe.set_unused(self.db, "rights_dip", 5000000, "20260105T150000")
        self._buy_filled("b1", 2, 1070000)
        out = fe.available(
            self.db, "rights_dip", account_cash=3000000,
            reservations={}, balance_qty=None, etf_price=1073485)
        self.assertEqual(out["etf_qty"], 0)
        self.assertEqual(out["etf_value"], 0)
        out2 = fe.available(
            self.db, "rights_dip", account_cash=3000000,
            reservations={}, balance_qty=2, etf_price=None)
        self.assertEqual(out2["etf_qty"], 2)
        self.assertEqual(out2["etf_value"], 0)


_UNSET_RESERVATIONS: object = object()


class FakeIO:
    """request_funds 가짜 IO — 네트워크·실제 sleep 없음.

    cash 는 값 또는 호출 순서대로 꺼내는 목록(마지막 유지),
    sells 는 sell_market 호출 순서대로 반환할 응답 목록(마지막 반복),
    fills_plan 은 fills 호출 순서대로 반환할 값 목록(마지막 반복).
    """

    def __init__(self, *, cash=0, reservations=_UNSET_RESERVATIONS,
                 balance_qty=0, ref_price=0, now=None, sells=None,
                 fills_plan=None, ask=None, buys=None, buy_fills_plan=None,
                 cancel_plan=None):
        self._cash = cash
        self._cash_i = 0
        self._reservations = {} if reservations is _UNSET_RESERVATIONS \
            else reservations
        self._balance_qty = balance_qty
        self._ref_price = ref_price
        self._now = now or datetime(2026, 1, 5, 9, 30)
        self._sells = list(sells or [])
        self._fills = list(fills_plan or [])
        self._ask = ask
        self._buys = list(buys or [])
        self._buy_fills = list(buy_fills_plan or [])
        self._cancels = list(cancel_plan or [])
        self.sell_calls = []
        self.fills_calls = []
        self.ref_calls = []
        self.sleeps = []
        self.buy_calls = []
        self.buy_fills_calls = []
        self.reserve_calls = []
        self.cancel_calls = []

    def cash(self):
        v = self._cash
        if isinstance(v, list):
            v = v[min(self._cash_i, len(v) - 1)] if v else None
            self._cash_i += 1
        return v

    def reservations(self):
        return self._reservations

    def balance_qty(self):
        return self._balance_qty

    def ref_price(self, preopen):
        self.ref_calls.append(preopen)
        return self._ref_price

    def sell_market(self, qty):
        self.sell_calls.append(qty)
        if not self._sells:
            return {"order_no": "", "status": "rejected",
                    "message": "sells 계획 없음"}
        r = dict(self._sells[min(len(self.sell_calls) - 1,
                                 len(self._sells) - 1)])
        r.setdefault("message", "")
        return r

    def fills(self, date):
        self.fills_calls.append(self._now)
        if not self._fills:
            return {}
        return self._fills[min(len(self.fills_calls) - 1,
                               len(self._fills) - 1)]

    def buy_fills(self, date):
        self.buy_fills_calls.append(date)
        if not self._buy_fills:
            return {}
        return self._buy_fills[min(len(self.buy_fills_calls) - 1,
                                   len(self._buy_fills) - 1)]

    def cancel_sell(self, order_no):
        self.cancel_calls.append(order_no)
        if not self._cancels:
            return {"order_no": order_no, "status": "cancelled"}
        r = dict(self._cancels[min(len(self.cancel_calls) - 1,
                                   len(self._cancels) - 1)])
        r.setdefault("order_no", order_no)
        return r

    def ask(self):
        return self._ask

    def buy_limit(self, qty, price):
        self.buy_calls.append((qty, price))
        if not self._buys:
            return {"order_no": "", "status": "rejected",
                    "message": "buys 계획 없음"}
        r = dict(self._buys[min(len(self.buy_calls) - 1,
                                len(self._buys) - 1)])
        r.setdefault("message", "")
        return r

    def write_reservation(self, reserve, capital_total):
        self.reserve_calls.append((reserve, capital_total))

    def now(self):
        return self._now

    def sleep(self, sec):
        self.sleeps.append(sec)
        self._now += timedelta(seconds=sec)


class RequestFundsTest(unittest.TestCase):
    STRAT = "rights_dip"
    DATE = "20260105"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "funding.sqlite3"
        fe.ensure(self.db)
        fe.add_member(self.db, self.STRAT, "20260101")

    def tearDown(self):
        self._tmp.cleanup()

    def _buy_filled(self, order_no, qty, price):
        fe.record_order(self.db, date=self.DATE, side="buy", qty=qty, price=0,
                        order_no=order_no, status="submitted",
                        created=f"{self.DATE}T090000")
        fe.update_fill(self.db, order_no, status="filled",
                       filled_qty=qty, filled_price=price)

    def _orders(self):
        with connect_ro(self.db) as con:
            return con.execute(
                "SELECT order_no, status, filled_qty, filled_price"
                " FROM etf_orders WHERE side = 'sell' ORDER BY id").fetchall()

    def _request(self, amount, io, unused, **kw):
        fe.set_unused(self.db, self.STRAT, unused, f"{self.DATE}T090000")
        kw.setdefault("preopen", False)
        return fe.request_funds(self.db, self.STRAT, amount, io=io,
                                today=self.DATE, **kw)

    def test_cash_enough_no_sell(self):
        io = FakeIO(cash=4000000, ref_price=1073485)
        out = self._request(3000000, io, 5000000)
        self.assertEqual(out["granted"], 3000000)
        self.assertEqual(out["sold_qty"], 0)
        self.assertEqual(out["notes"], [])
        self.assertEqual(io.sell_calls, [])

    def test_clamped_to_unused(self):
        io = FakeIO(cash=3000000, ref_price=1073485)
        out = self._request(5000000, io, 2000000)
        self.assertEqual(out["granted"], 2000000)
        self.assertEqual(out["notes"], ["전략 안 쓴 몫 2,000,000 로 줄임"])
        self.assertEqual(io.sell_calls, [])

    def test_short_1_5m_sells_2(self):
        self._buy_filled("b1", 2, 1070000)
        io = FakeIO(cash=[8500000, 10650000], balance_qty=2,
                    ref_price=1073485,
                    sells=[{"order_no": "S1", "status": "submitted"}],
                    fills_plan=[{"S1": {"qty": 2, "price": 1073000}}])
        out = self._request(10000000, io, 10000000)
        self.assertEqual(io.sell_calls, [2])
        self.assertEqual(out["granted"], 10000000)
        self.assertEqual(out["sold_qty"], 2)
        self.assertEqual(self._orders(),
                         [("S1", "filled", 2, 1073000.0)])

    def test_preopen_waits_until_0900(self):
        self._buy_filled("b1", 1, 1060000)
        io = FakeIO(cash=[1000000, 2100000], balance_qty=1,
                    ref_price=1070000, now=datetime(2026, 1, 5, 8, 40),
                    sells=[{"order_no": "S1", "status": "submitted"}],
                    fills_plan=[{"S1": {"qty": 1, "price": 1070000}}])
        out = self._request(2000000, io, 5000000, preopen=True)
        self.assertTrue(io.fills_calls)
        for at in io.fills_calls:
            self.assertGreaterEqual(at, datetime(2026, 1, 5, 9, 0))
        self.assertEqual(out["granted"], 2000000)
        self.assertEqual(out["sold_qty"], 1)

    def test_never_filled_gives_up_after_120s(self):
        self._buy_filled("b1", 4, 1070000)
        start = datetime(2026, 1, 5, 9, 30)
        io = FakeIO(cash=3000000, balance_qty=4, ref_price=1073485,
                    now=start,
                    sells=[{"order_no": "S1", "status": "submitted"},
                           {"order_no": "S2", "status": "submitted"}],
                    fills_plan=[{}])
        out = self._request(5000000, io, 10000000)
        self.assertGreaterEqual((io.now() - start).total_seconds(), 240)
        self.assertEqual(self._orders(),
                         [("S1", "cancelled", 0, None),
                          ("S2", "submitted", 0, None)])
        self.assertTrue(any("미체결" in n for n in out["notes"]))
        self.assertEqual(io.sell_calls, [2, 2])
        self.assertEqual(io.cancel_calls, ["S1"])
        self.assertEqual(out["granted"], 3000000)

    def test_low_fill_price_retries_once(self):
        self._buy_filled("b1", 4, 1070000)
        io = FakeIO(cash=[10500000, 11900000, 12970000], balance_qty=4,
                    ref_price=1073485,
                    sells=[{"order_no": "S1", "status": "submitted"},
                           {"order_no": "S2", "status": "submitted"}],
                    fills_plan=[{"S1": {"qty": 2, "price": 700000}},
                                {"S1": {"qty": 2, "price": 700000},
                                 "S2": {"qty": 1, "price": 1073000}}])
        out = self._request(12000000, io, 12000000)
        self.assertEqual(io.sell_calls, [2, 1])
        self.assertIn("⚠ ETF 체결가 700,000 < 예상 1,073,270", out["notes"])
        self.assertFalse(any("미체결" in n for n in out["notes"]))
        self.assertEqual(out["granted"], 12000000)

    def test_unknown_no_reorder(self):
        self._buy_filled("b1", 2, 1070000)
        io = FakeIO(cash=3000000, balance_qty=2, ref_price=1073485,
                    sells=[{"order_no": "", "status": "unknown",
                            "message": "timeout"}])
        out = self._request(5000000, io, 10000000)
        self.assertEqual(io.sell_calls, [2])
        self.assertEqual(io.fills_calls, [])
        self.assertEqual(out["granted"], 3000000)
        self.assertIn("⚠ ETF 매도 결과 불명 — 재주문 안 함, 수동 확인",
                      out["notes"])
        self.assertEqual(self._orders(), [])

    def test_rejected_grants_cash(self):
        self._buy_filled("b1", 4, 1070000)
        io = FakeIO(cash=3000000, balance_qty=4, ref_price=1073485,
                    sells=[{"order_no": "", "status": "rejected",
                            "message": "잔고 부족"}])
        out = self._request(5000000, io, 10000000)
        self.assertEqual(out["granted"], 3000000)
        self.assertTrue(any("거부" in n for n in out["notes"]))
        self.assertEqual(io.sell_calls, [2, 2])

    def test_no_balance_no_etf_to_sell(self):
        self._buy_filled("b1", 2, 1070000)
        io = FakeIO(cash=3000000, balance_qty=None, ref_price=1073485)
        out = self._request(5000000, io, 10000000)
        self.assertEqual(out["granted"], 3000000)
        self.assertTrue(any("팔 ETF 없음" in n for n in out["notes"]))
        self.assertEqual(io.sell_calls, [])

    def test_disabled_no_sell(self):
        self._buy_filled("b1", 2, 1070000)
        io = FakeIO(cash=3000000, balance_qty=2, ref_price=1073485)
        out = self._request(5000000, io, 10000000, enabled=False)
        self.assertEqual(out["granted"], 3000000)
        self.assertEqual(out["sold_qty"], 0)
        self.assertEqual(io.sell_calls, [])

    def test_realized_pnl(self):
        self._buy_filled("b1", 2, 1070000)
        io = FakeIO(cash=[3500000, 5646000], balance_qty=2,
                    ref_price=1073485,
                    sells=[{"order_no": "S1", "status": "submitted"}],
                    fills_plan=[{"S1": {"qty": 2, "price": 1073000}}])
        out = self._request(5000000, io, 10000000)
        self.assertEqual(out["granted"], 5000000)
        self.assertEqual(fe.pnl_since(self.db, "rebound", ""), 6000)

    def test_cash_none_grants_zero(self):
        io = FakeIO(cash=None, ref_price=1073485)
        out = self._request(5000000, io, 10000000)
        self.assertEqual(out["granted"], 0)
        self.assertIn("⚠ 주문가능 조회 실패", out["notes"])
        self.assertEqual(io.sell_calls, [])

    def test_sold_syncs_reservation(self):
        self._buy_filled("b1", 2, 1070000)
        io = FakeIO(cash=[8500000, 10650000], balance_qty=2,
                    ref_price=1073485,
                    sells=[{"order_no": "S1", "status": "submitted"}],
                    fills_plan=[{"S1": {"qty": 2, "price": 1073000}}])
        out = self._request(10000000, io, 10000000)
        self.assertEqual(out["sold_qty"], 2)
        self.assertEqual(io.reserve_calls,
                         [(7860000, 10000000), (10000000, 10000000)])

    def test_no_sell_no_reservation_sync(self):
        io = FakeIO(cash=4000000, ref_price=1073485)
        out = self._request(3000000, io, 5000000)
        self.assertEqual(out["sold_qty"], 0)
        self.assertEqual(io.reserve_calls, [(5000000, 5000000)])

    def test_sell_dry_run_records_nothing(self):
        self._buy_filled("b1", 2, 1070000)
        io = FakeIO(sells=[{"order_no": "DRY_1", "status": "dry_run"}])
        out = fe._sell_and_confirm(self.db, 2, io=io, today=self.DATE,
                                   preopen=False, reason="test")
        self.assertEqual(out["notes"], ["DRY-RUN ETF 매도 2주"])
        self.assertEqual(out["filled_qty"], 0)
        self.assertEqual(self._orders(), [])

    def test_none_reservations_cash_zero(self):
        io = FakeIO(cash=5000000, reservations=None, ref_price=1073485)
        out = self._request(3000000, io, 5000000)
        self.assertEqual(out["granted"], 0)
        self.assertTrue(any("팔 ETF 없음" in n for n in out["notes"]))

    def test_unfilled_cancels_first_then_retries(self):
        self._buy_filled("b1", 4, 1070000)
        start = datetime(2026, 1, 5, 9, 30)
        io = FakeIO(cash=3000000, balance_qty=4, ref_price=1073485,
                    now=start,
                    sells=[{"order_no": "S1", "status": "submitted"},
                           {"order_no": "S2", "status": "submitted"}],
                    fills_plan=[{}])
        out = self._request(5000000, io, 10000000)
        self.assertEqual(io.cancel_calls, ["S1"])
        self.assertEqual(io.sell_calls, [2, 2])
        self.assertEqual(self._orders(),
                         [("S1", "cancelled", 0, None),
                          ("S2", "submitted", 0, None)])
        self.assertEqual(out["granted"], 3000000)

    def test_cancel_failure_skips_second_sell(self):
        self._buy_filled("b1", 4, 1070000)
        start = datetime(2026, 1, 5, 9, 30)
        io = FakeIO(cash=3000000, balance_qty=4, ref_price=1073485,
                    now=start,
                    sells=[{"order_no": "S1", "status": "submitted"},
                           {"order_no": "S2", "status": "submitted"}],
                    fills_plan=[{}],
                    cancel_plan=[{"status": "failed"}])
        out = self._request(5000000, io, 10000000)
        self.assertEqual(io.cancel_calls, ["S1"])
        self.assertEqual(io.sell_calls, [2])
        self.assertIn("⚠ 첫 ETF 매도 취소 실패 — 추가 매도 안 함(이중 매도 방지)",
                      out["notes"])

    def test_cancelled_partial_first_records_pnl(self):
        self._buy_filled("b1", 4, 1070000)
        start = datetime(2026, 1, 5, 9, 30)
        io = FakeIO(cash=3000000, balance_qty=4, ref_price=1073485,
                    now=start,
                    sells=[{"order_no": "S1", "status": "submitted"},
                           {"order_no": "S2", "status": "submitted"}],
                    fills_plan=[{"S1": {"qty": 1, "price": 1073000}}])
        out = self._request(10000000, io, 10000000)
        self.assertEqual(io.sell_calls, [4, 3])
        self.assertEqual(io.cancel_calls, ["S1"])
        self.assertEqual(self._orders(),
                         [("S1", "cancelled", 1, 1073000.0),
                          ("S2", "submitted", 0, None)])
        self.assertEqual(fe.pnl_since(self.db, "rebound", ""), 3000)
        self.assertEqual(out["sold_qty"], 1)


class SweepTest(unittest.TestCase):
    DATE = "20260105"
    ASK = 1073485
    PX = 1073495  # ASK + 5 * 2

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "funding.sqlite3"
        fe.ensure(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _set_unused(self, **mapping):
        for strat, amt in mapping.items():
            fe.add_member(self.db, strat, "20260101")
            fe.set_unused(self.db, strat, amt, f"{self.DATE}T090000")

    def _buy_orders(self):
        with connect_ro(self.db) as con:
            return con.execute(
                "SELECT order_no, qty, price, status FROM etf_orders"
                " WHERE side = 'buy' ORDER BY id").fetchall()

    def test_split_by_max_order_amount(self):
        self._set_unused(a=5000000, b=2000000)
        io = FakeIO(cash=8000000, ask=self.ASK,
                    buys=[{"order_no": "B1", "status": "submitted"},
                          {"order_no": "B2", "status": "submitted"}])
        with mock.patch.dict(os.environ, {"MAX_ORDER_AMOUNT": "5000000"}):
            out = fe.sweep(self.db, io=io, today=self.DATE)
        self.assertEqual(io.buy_calls, [(4, self.PX), (2, self.PX)])
        self.assertEqual(out["ordered_qty"], 6)
        self.assertEqual(self._buy_orders(),
                         [("B1", 4, self.PX, "submitted"),
                          ("B2", 2, self.PX, "submitted")])

    def test_cash_bound(self):
        self._set_unused(a=10000000)
        io = FakeIO(cash=3000000, ask=self.ASK,
                    buys=[{"order_no": "B1", "status": "submitted"}])
        with mock.patch.dict(os.environ, {"MAX_ORDER_AMOUNT": "10000000"}):
            out = fe.sweep(self.db, io=io, today=self.DATE)
        # idle 1000만 > cash 300만 → 300만 // 1,073,495 = 2주
        self.assertEqual(out["ordered_qty"], 2)
        self.assertEqual(io.buy_calls, [(2, self.PX)])

    def test_quantity_zero_no_order(self):
        self._set_unused(a=500)
        io = FakeIO(cash=500, ask=self.ASK,
                    buys=[{"order_no": "B1", "status": "submitted"}])
        out = fe.sweep(self.db, io=io, today=self.DATE)
        self.assertEqual(out["ordered_qty"], 0)
        self.assertEqual(out["notes"], [])
        self.assertEqual(io.buy_calls, [])
        self.assertEqual(len(io.reserve_calls), 1)

    def test_unknown_stops_remaining(self):
        self._set_unused(a=5000000, b=2000000)
        io = FakeIO(cash=8000000, ask=self.ASK,
                    buys=[{"order_no": "B1", "status": "unknown",
                           "message": "timeout"},
                          {"order_no": "B2", "status": "submitted"}])
        with mock.patch.dict(os.environ, {"MAX_ORDER_AMOUNT": "5000000"}):
            out = fe.sweep(self.db, io=io, today=self.DATE)
        self.assertEqual(io.buy_calls, [(4, self.PX)])
        self.assertIn("⚠ ETF 매수 결과 불명 — 재주문 안 함", out["notes"])
        self.assertEqual(self._buy_orders(),
                         [("B1", 4, self.PX, "unknown")])
        self.assertEqual(out["ordered_qty"], 0)

    def test_syncs_pending_reserve(self):
        self._set_unused(a=5000000, b=2000000)
        io = FakeIO(cash=8000000, ask=self.ASK,
                    buys=[{"order_no": "B1", "status": "submitted"},
                          {"order_no": "B2", "status": "submitted"}])
        with mock.patch.dict(os.environ, {"MAX_ORDER_AMOUNT": "5000000"}):
            fe.sweep(self.db, io=io, today=self.DATE)
        self.assertEqual(io.reserve_calls,
                         [(7000000 - 6 * self.PX, 7000000)])

    def test_engine_reserve_floor_zero_with_pending(self):
        self._set_unused(a=1000000)
        fe.record_order(self.db, date=self.DATE, side="buy", qty=2,
                        price=self.PX, order_no="B1", status="submitted",
                        reason="sweep", created=f"{self.DATE}T152100")
        self.assertEqual(fe.engine_reserve(self.db, self.DATE), 0)
        self.assertEqual(fe.engine_reserve(self.db), 1000000)

    def test_disabled_no_order_still_syncs(self):
        self._set_unused(a=7000000)
        io = FakeIO(cash=8000000, ask=self.ASK,
                    buys=[{"order_no": "B1", "status": "submitted"}])
        out = fe.sweep(self.db, io=io, today=self.DATE, enabled=False)
        self.assertEqual(out["ordered_qty"], 0)
        self.assertEqual(io.buy_calls, [])
        self.assertEqual(io.reserve_calls, [(7000000, 7000000)])

    def test_dry_run_records_nothing(self):
        self._set_unused(a=5000000, b=2000000)
        io = FakeIO(cash=8000000, ask=self.ASK,
                    buys=[{"order_no": "DRY_1", "status": "dry_run"}])
        with mock.patch.dict(os.environ, {"MAX_ORDER_AMOUNT": "5000000"}):
            out = fe.sweep(self.db, io=io, today=self.DATE)
        self.assertEqual(self._buy_orders(), [])
        self.assertEqual(out["ordered_qty"], 0)
        self.assertEqual(out["notes"],
                         [f"DRY-RUN ETF 매수 4주 @{self.PX:,}",
                          f"DRY-RUN ETF 매수 2주 @{self.PX:,}"])

    def test_none_reservations_no_order(self):
        self._set_unused(a=7000000)
        io = FakeIO(cash=8000000, reservations=None, ask=self.ASK,
                    buys=[{"order_no": "B1", "status": "submitted"}])
        with mock.patch.dict(os.environ, {"MAX_ORDER_AMOUNT": "10000000"}):
            out = fe.sweep(self.db, io=io, today=self.DATE)
        self.assertEqual(out["ordered_qty"], 0)
        self.assertEqual(io.buy_calls, [])

    def test_cap_below_one_share_skips(self):
        self._set_unused(a=7000000)
        io = FakeIO(cash=8000000, ask=self.ASK,
                    buys=[{"order_no": "B1", "status": "submitted"}])
        with mock.patch.dict(os.environ, {"MAX_ORDER_AMOUNT": "1000000"}):
            out = fe.sweep(self.db, io=io, today=self.DATE)
        self.assertEqual(out["ordered_qty"], 0)
        self.assertEqual(io.buy_calls, [])
        self.assertIn(f"⚠ 1건 상한 1,000,000 < 1주 {self.PX:,} — 매수 안 함"
                      " (MAX_ORDER_AMOUNT 확인)", out["notes"])
        self.assertEqual(len(io.reserve_calls), 1)


class ConfirmBuysTest(unittest.TestCase):
    DATE = "20260105"
    PX = 1073495

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "funding.sqlite3"
        fe.ensure(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _set_unused(self, **mapping):
        for strat, amt in mapping.items():
            fe.add_member(self.db, strat, "20260101")
            fe.set_unused(self.db, strat, amt, f"{self.DATE}T090000")

    def _buy_order(self, order_no, qty, status="submitted", date=None):
        d = date or self.DATE
        fe.record_order(self.db, date=d, side="buy", qty=qty,
                        price=self.PX, order_no=order_no, status=status,
                        reason="sweep", created=f"{d}T152100")

    def _buy_state(self, order_no):
        with connect_ro(self.db) as con:
            return con.execute(
                "SELECT status, filled_qty, filled_price FROM etf_orders"
                " WHERE order_no = ?", (order_no,)).fetchone()

    def test_full_fill(self):
        self._set_unused(a=7000000)
        self._buy_order("B1", 4)
        io = FakeIO(now=datetime(2026, 1, 5, 15, 31),
                    buy_fills_plan=[{"B1": {"qty": 4, "price": 1073490}}])
        out = fe.confirm_buys(self.db, io=io, today=self.DATE)
        self.assertEqual(out["filled_qty"], 4)
        self.assertEqual(tuple(self._buy_state("B1")),
                         ("filled", 4, 1073490.0))
        self.assertEqual(fe.etf_position(self.db),
                         {"qty": 4, "cost": 4 * 1073490})
        self.assertEqual(io.reserve_calls,
                         [(7000000 - 4 * 1073490, 7000000)])

    def test_expired_unfilled_cancelled(self):
        self._set_unused(a=7000000)
        self._buy_order("B1", 4, date="20260104")
        io = FakeIO(now=datetime(2026, 1, 5, 15, 31),
                    buy_fills_plan=[{}])
        out = fe.confirm_buys(self.db, io=io, today=self.DATE)
        self.assertEqual(tuple(self._buy_state("B1")),
                         ("cancelled", 0, None))
        self.assertEqual(out["filled_qty"], 0)
        self.assertEqual(io.reserve_calls, [(7000000, 7000000)])

    def test_before_close_stays_submitted(self):
        self._set_unused(a=7000000)
        self._buy_order("B1", 4)
        io = FakeIO(now=datetime(2026, 1, 5, 15, 25),
                    buy_fills_plan=[{}])
        out = fe.confirm_buys(self.db, io=io, today=self.DATE)
        self.assertEqual(tuple(self._buy_state("B1"))[0], "submitted")
        self.assertEqual(out["filled_qty"], 0)
        self.assertEqual(io.reserve_calls,
                         [(7000000 - 4 * self.PX, 7000000)])


class FundingEngineMainTest(unittest.TestCase):
    """main() 15:21 진입점 테스트 — 전부 patch, 네트워크·sleep 없음."""

    NOW = datetime(2026, 10, 30, 15, 31, 5)  # confirm-at 지난 시각 → 대기 없음

    def test_non_trading_day_skips(self):
        with mock.patch.object(fe, "load_dotenv"), \
                mock.patch.object(fe, "is_krx_trading_day",
                                  return_value=False), \
                mock.patch.object(fe, "sweep") as m_sweep, \
                mock.patch.object(fe, "confirm_buys") as m_confirm, \
                mock.patch.object(fe, "send_strategy_alert") as m_alert:
            rc = fe.main([])
        self.assertEqual(rc, 0)
        self.assertEqual(m_sweep.call_count, 0)
        self.assertEqual(m_confirm.call_count, 0)
        self.assertEqual(m_alert.call_count, 0)

    def test_normal_ok_headline_and_order(self):
        io = FakeIO(now=self.NOW)
        mgr = mock.Mock()
        with mock.patch.object(fe, "load_dotenv"), \
                mock.patch.object(fe, "is_krx_trading_day",
                                  return_value=True), \
                mock.patch.object(fe, "LiveIO", return_value=io) as m_live, \
                mock.patch.object(
                    fe, "sweep",
                    return_value={"ordered_qty": 6, "notes": []}) as m_sweep, \
                mock.patch.object(
                    fe, "confirm_buys",
                    return_value={"filled_qty": 6, "notes": []}) as m_confirm, \
                mock.patch.object(
                    fe, "etf_position",
                    return_value={"qty": 10, "cost": 10700000}), \
                mock.patch.object(fe, "send_strategy_alert",
                                  return_value=True) as m_alert:
            mgr.attach_mock(m_sweep, "sweep")
            mgr.attach_mock(m_confirm, "confirm_buys")
            rc = fe.main([])
        self.assertEqual(rc, 0)
        m_live.assert_called_once_with("http://localhost:8001", dry_run=False)
        mgr.assert_has_calls([
            mock.call.sweep(fe.DB, io=io, today=mock.ANY),
            mock.call.confirm_buys(fe.DB, io=io, today=mock.ANY),
        ])
        self.assertEqual(io.sleeps, [])
        self.assertEqual(m_alert.call_count, 1)
        msg, env = m_alert.call_args[0]
        self.assertEqual(env, "FUNDING_DISCORD_WEBHOOK_URL")
        self.assertTrue(msg.startswith("✅ 자금엔진 15:21 마감매수 — "),
                        msg.splitlines()[0])
        for part in ("매수 주문: 6주", "체결: 6주", "보유: 10주",
                     "10,700,000"):
            self.assertIn(part, msg)

    def test_warn_headline_on_warning_note(self):
        io = FakeIO(now=self.NOW)
        with mock.patch.object(fe, "load_dotenv"), \
                mock.patch.object(fe, "is_krx_trading_day",
                                  return_value=True), \
                mock.patch.object(fe, "LiveIO", return_value=io), \
                mock.patch.object(
                    fe, "sweep",
                    return_value={"ordered_qty": 0,
                                  "notes": ["⚠ ETF 매수 거부: 잔고 부족"]}), \
                mock.patch.object(
                    fe, "confirm_buys",
                    return_value={"filled_qty": 0, "notes": []}), \
                mock.patch.object(fe, "etf_position",
                                  return_value={"qty": 0, "cost": 0}), \
                mock.patch.object(fe, "send_strategy_alert",
                                  return_value=True) as m_alert:
            rc = fe.main([])
        self.assertEqual(rc, 0)
        msg = m_alert.call_args[0][0]
        self.assertTrue(msg.startswith("⚠ 자금엔진 15:21 마감매수 — "),
                        msg.splitlines()[0])
        self.assertIn("⚠ ETF 매수 거부: 잔고 부족", msg)

    def test_sweep_exception_fail_alert_returns_3(self):
        io = FakeIO(now=self.NOW)
        with mock.patch.object(fe, "load_dotenv"), \
                mock.patch.object(fe, "is_krx_trading_day",
                                  return_value=True), \
                mock.patch.object(fe, "LiveIO", return_value=io), \
                mock.patch.object(fe, "sweep",
                                  side_effect=RuntimeError("boom")), \
                mock.patch.object(fe, "send_strategy_alert",
                                  return_value=True) as m_alert:
            rc = fe.main([])
        self.assertEqual(rc, 3)
        msg = m_alert.call_args[0][0]
        self.assertTrue(msg.startswith("❌ 자금엔진 15:21 마감매수 — "),
                        msg.splitlines()[0])

    def test_alert_failure_returns_2(self):
        cases = [
            {"side_effect": RuntimeError("webhook down")},
            {"return_value": False},
        ]
        for kw in cases:
            with self.subTest(kw=kw):
                io = FakeIO(now=self.NOW)
                with mock.patch.object(fe, "load_dotenv"), \
                        mock.patch.object(fe, "is_krx_trading_day",
                                          return_value=True), \
                        mock.patch.object(fe, "LiveIO", return_value=io), \
                        mock.patch.object(fe, "sweep",
                                          side_effect=RuntimeError("boom")), \
                        mock.patch.object(fe, "send_strategy_alert", **kw):
                    rc = fe.main([])
                self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
