import unittest
from unittest.mock import Mock

from scripts import chase_engine as ch
from scripts.sliced_exec import passive_price_fn, run_sliced, slice_qty
from tests.test_chase_engine import EventLog, FakeApi, FakeClock, TODAY


class SlicedApi(FakeApi):
    def __init__(self):
        super().__init__()
        self.books_quote = {"buy": 100, "sell": 99}

    def best_quote(self, ticker, side):
        self.calls.append(("best_quote", side))
        return self.books_quote[side]


def order(key="A", side="sell", qty=12, **kw):
    return {"key": key, "ticker": "005930", "side": side, "qty": qty,
            "cap_qty": None, "final": False, **kw}


class SliceQtyTest(unittest.TestCase):
    def test_slice_qty_split(self):
        self.assertEqual([slice_qty(10, n) for n in (4, 3, 2, 1)], [2, 3, 5, 10])
        self.assertEqual(slice_qty(11, 3), 3)
        self.assertEqual(slice_qty(5, 1), 5)

    def test_slice_cap_carry(self):
        self.assertEqual(slice_qty(10, 2, 3), 3)
        self.assertEqual(slice_qty(7, 1, 3), 3)


class SlicedRunTest(unittest.TestCase):
    def setUp(self):
        self.api = SlicedApi()
        self.clock = FakeClock(ch.at(TODAY, "09:30:00"))

    def test_passive_price_side(self):
        fn = passive_price_fn(self.api)
        self.assertEqual(fn("005930", "buy"), 99)
        self.assertEqual(fn("005930", "sell"), 100)

    def test_passive_price_client_adapter(self):
        client = Mock()
        client.best_quote.side_effect = lambda ticker, side: {"buy": 101, "sell": 100}[side]
        api = ch.ClientEngineApi(client)
        fn = passive_price_fn(api)
        self.assertEqual(fn("005930", "buy"), 100)
        self.assertEqual(fn("005930", "sell"), 101)
        client.limit_price.assert_not_called()

    def test_passive_price_no_best_quote_skips(self):
        api = FakeApi()
        api.quotes["sell"] = 100
        api.quotes["buy"] = 99
        run_sliced(TODAY, [order(qty=3)], api, self.clock,
                   slices=("09:30:00",), last_cut="09:31:00")
        self.assertFalse(any(call[0] == "place_limit" for call in api.calls))

    def test_carry_counts_fill_during_cancel(self):
        api = SlicedApi()

        class FillOnCancelApi(SlicedApi):
            def cancel(self, no, ticker):
                result = super().cancel(no, ticker)
                self.fill_map["sell"][no] = {"cntr_qty": 1}
                return result

        api = FillOnCancelApi()
        api.books_quote["buy"] = 100
        api.quotes["buy"] = 99
        clock = FakeClock(ch.at(TODAY, "09:30:00"))
        result = run_sliced(TODAY, [order(qty=3)], api, clock,
                            slices=("09:30:00",), last_cut="09:31:00")
        self.assertEqual(result["A"], 2)

    def test_cancel_only_never_market(self):
        api = SlicedApi()
        api.quotes["sell"] = None
        api.books["sell"]["123"] = {"ticker": "005930", "oso_qty": 2,
                                     "ord_price": 99, "order_no": "123"}
        state = {"p": {"ticker": "005930"}, "qty": 2, "nos": ["123"],
                 "order_no": "123", "status": "working", "side": "sell",
                 "rejects": 0, "modifies": 0, "start_at": "10:00:00",
                 "market_at": "10:05:00", "cancel_only_at": "10:04:00"}
        clock = FakeClock(ch.at(TODAY, "10:00:00"))
        ch.run_chase(TODAY, [state], api, clock, first_at="10:00:00",
                     market_at="10:05:00", end_at="10:10:00")
        self.assertFalse(any(call[0] == "place_market" for call in api.calls))
        self.assertIn("123", api.cancelled)

    def test_cancel_only_no_market(self):
        self.api.quotes["sell"] = 99
        result = run_sliced(TODAY, [order(qty=3)], self.api, self.clock,
                            slices=("09:30:00",), last_cut="09:31:00",
                            step_seconds=30)
        self.assertEqual(result["A"], 3)
        self.assertEqual(len(self.api.cancelled), 1)
        self.assertFalse(any(x[0] == "place_market" for x in self.api.placed))

    def test_cancel_unconfirmed_stops(self):
        self.api.cancel_keeps = True
        self.api.quotes["sell"] = 100
        self.api.books["sell"] = {}
        result = run_sliced(TODAY, [order(qty=3)], self.api, self.clock,
                            slices=("09:30:00", "09:32:00"), last_cut="09:33:00",
                            step_seconds=30)
        self.assertEqual(result["A"], 3)
        self.assertEqual(len([p for p in self.api.placed if p[0] == "place_limit"]), 1)

    def test_final_market_only_last(self):
        self.api.quotes["sell"] = 100
        run_sliced(TODAY, [order(qty=4, final=True)], self.api, self.clock,
                   slices=("09:30:00",), last_cut="09:31:00",
                   final_market_at="09:32:00", end_at="09:34:00",
                   step_seconds=30)
        self.assertTrue(any(p[0] == "place_market" for p in self.api.placed))

    def test_no_market_after_end(self):
        self.clock.t = ch.at(TODAY, "15:20:00")
        run_sliced(TODAY, [order(qty=4, final=True)], self.api, self.clock,
                   slices=("09:30:00",), last_cut="15:10:00",
                   final_market_at="15:15:00", end_at="15:20:00")
        self.assertFalse(any(p[0] == "place_market" for p in self.api.placed))

    def test_returns_remaining(self):
        result = run_sliced(TODAY, [order(qty=4)], self.api, self.clock,
                            slices=("09:31:00",), last_cut="09:32:00")
        self.assertEqual(result, {"A": 4})

    def test_foreign_orders_untouched(self):
        self.api.books["sell"]["999"] = {"ticker": "000001", "oso_qty": 5,
                                          "ord_price": 10, "order_no": "999"}
        run_sliced(TODAY, [order(qty=2)], self.api, self.clock,
                   slices=("09:30:00",), last_cut="09:31:00")
        self.assertIn("999", self.api.books["sell"])
        self.assertNotIn("999", self.api.cancelled)

    def test_sell_first_buy_cash_capped(self):
        os = [order("B", "buy", 10), order("S", "sell", 10)]
        run_sliced(TODAY, os, self.api, self.clock, slices=("09:30:00",),
                   last_cut="09:31:00", cash_fn=lambda: 300)
        placed = [x for x in self.api.calls if x[0] == "place_limit"]
        self.assertEqual([x[1] for x in placed[:2]], ["sell", "buy"])
        self.assertLessEqual(sum(x[3] * x[4] for x in placed if x[1] == "buy"), 300)

    def test_cash_lookup_fail_no_buy(self):
        run_sliced(TODAY, [order("B", "buy", 10)], self.api, self.clock,
                   slices=("09:30:00",), last_cut="09:31:00", cash_fn=lambda: None)
        self.assertFalse(any(x[0] == "place_limit" and x[1] == "buy"
                             for x in self.api.calls))


if __name__ == "__main__":
    unittest.main()
