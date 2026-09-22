"""run_close_bet_exit.py 판정로직 단위테스트 (G2).

검증:
  1. decide_exit: TP/SL 경계, 호가공백 skip
  2. in_trading_window / is_force_time: 연속매매창·강제청산 시각
  3. is_locked: 하한 lock 감지
  4. load_unsold_positions: filled + sell_status NULL + 과거날짜만
  5. reconcile_balance: 접두strip·zero-pad·min수량·미보유 제외
"""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from scripts import run_close_bet_exit as ex
from scripts.run_close_bet import create_close_bet_orders_table, ensure_exit_columns
from scripts.run_close_bet_exit import (
    decide_exit,
    execute_sell,
    fetch_sell_fills,
    fetch_unfilled_orders,
    in_trading_window,
    is_force_time,
    is_in_flight,
    is_locked,
    load_ordered_pending,
    load_unsold_positions,
    mark_filled,
    mark_ordered,
    place_sell_via_broker,
    reconcile_balance,
    settle_pending,
    split_positions,
)
from scripts.wl_sqlite import connect_rw

_KST = ZoneInfo("Asia/Seoul")


def _t(hms: str) -> datetime:
    h, m, s = (int(x) for x in hms.split(":"))
    return datetime(2026, 6, 18, h, m, s, tzinfo=_KST)


def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.unlink(path)
    return Path(path)


class TestDecideExit(unittest.TestCase):
    def test_tp_boundary(self):
        self.assertEqual(decide_exit(105, 100, 0.05, 0.03), "tp")   # +5.0% 정확히
        self.assertIsNone(decide_exit(1049, 1000, 0.05, 0.03))      # +4.9% 미발동

    def test_sl_boundary(self):
        self.assertEqual(decide_exit(97, 100, 0.05, 0.03), "sl")    # -3.0%
        self.assertIsNone(decide_exit(971, 1000, 0.05, 0.03))       # -2.9% 미발동

    def test_between_none(self):
        self.assertIsNone(decide_exit(100, 100, 0.05, 0.03))

    def test_blank_quote_skips(self):
        self.assertIsNone(decide_exit(0, 100, 0.05, 0.03))
        self.assertIsNone(decide_exit(None, 100, 0.05, 0.03))
        self.assertIsNone(decide_exit(105, None, 0.05, 0.03))

    def test_none_tp_sl_disables_intraday_judgement(self):
        """tp/sl null(=close_bet.json 에서 끔) → 익절·손절 폭을 넘어도 판정하지 않는다.

        이때 청산은 강제청산 시각(exit_time)에만 일어난다.
        """
        self.assertIsNone(decide_exit(200, 100, None, None))   # +100%
        self.assertIsNone(decide_exit(50, 100, None, None))    # -50%

    def test_one_sided_disable_is_treated_as_full_disable(self):
        # 익절만/손절만 쓰는 조합은 지원하지 않는다 — 하나라도 None 이면 둘 다 끔.
        self.assertIsNone(decide_exit(200, 100, 0.05, None))
        self.assertIsNone(decide_exit(50, 100, None, 0.03))


class TestTimeGates(unittest.TestCase):
    def test_trading_window(self):
        self.assertFalse(in_trading_window(_t("08:59:59"), "09:00:00", "15:20:00"))
        self.assertTrue(in_trading_window(_t("09:00:00"), "09:00:00", "15:20:00"))
        self.assertTrue(in_trading_window(_t("15:19:59"), "09:00:00", "15:20:00"))
        self.assertFalse(in_trading_window(_t("15:20:00"), "09:00:00", "15:20:00"))

    def test_force_time(self):
        self.assertFalse(is_force_time(_t("15:18:59"), "15:19:00"))
        self.assertTrue(is_force_time(_t("15:19:00"), "15:19:00"))
        self.assertTrue(is_force_time(_t("15:25:00"), "15:19:00"))


class TestShouldForce(unittest.TestCase):
    """강제청산은 연속매매 창 시작(09:00) 전에도 발동 — 08:55 동시호가 청산."""

    def test_pre_open_exit_time_fires_before_window(self):
        self.assertFalse(ex.should_force(_t("08:54:59"), "08:55:00", "15:20:00"))
        self.assertTrue(ex.should_force(_t("08:55:00"), "08:55:00", "15:20:00"))

    def test_regular_exit_time_unchanged(self):
        self.assertFalse(ex.should_force(_t("08:59:59"), "09:01:00", "15:20:00"))
        self.assertTrue(ex.should_force(_t("09:01:00"), "09:01:00", "15:20:00"))

    def test_not_after_window_end(self):
        self.assertFalse(ex.should_force(_t("15:20:00"), "08:55:00", "15:20:00"))


class TestRunLoopForceWithoutQuote(unittest.TestCase):
    """08:55 은 호가 공백일 수 있음 — 강제청산 시각이면 시세 없어도 매도."""

    def _run_once(self, hms: str, force_hms: str):
        args = MagicMock(dry_run=False, stop_time="15:25:00", force_exit_time=force_hms,
                         window_start="09:00:00", window_end="15:20:00",
                         tp=None, sl=None, poll_sec=0)
        pos = {"date": "20260617", "ticker": "005930", "leg": "single",
               "cntr_price": 70000, "qty": 3, "qty_eff": 3}
        with patch.object(ex, "build_watch_set", return_value={("005930", "single"): pos}), \
                patch.object(ex, "load_ordered_pending", return_value={}), \
                patch.object(ex, "_now_seoul", side_effect=[_t(hms), _t(hms), _t("15:25:00")]), \
                patch.object(ex, "fetch_quotes", return_value={}), \
                patch.object(ex, "fetch_unfilled_orders", return_value={}), \
                patch.object(ex, "execute_sell",
                             return_value={"order_no": "0000001", "status": "submitted", "qty": 3}) as sell, \
                patch.object(ex, "send_discord"), patch.object(ex.time, "sleep"):
            ex.run_loop(args, "http://b")
        return sell

    def test_sells_when_quotes_empty_at_force_time(self):
        sell = self._run_once("08:55:00", "08:55:00")
        sell.assert_called_once()
        self.assertEqual(sell.call_args.args[3], "forced")

    def test_pre_open_uses_krx_regular_session_uses_sor(self):
        """동시호가(09:00 전) 주문만 KRX, 장중 강제청산은 SOR."""
        self.assertEqual(self._run_once("08:55:00", "08:55:00").call_args.kwargs["exchange"], "KRX")
        self.assertEqual(self._run_once("09:01:00", "09:01:00").call_args.kwargs["exchange"], "SOR")


class TestIsLocked(unittest.TestCase):
    def test_lower_limit_lock(self):
        self.assertTrue(is_locked(700, 700, "2"))      # cur==lst
        self.assertTrue(is_locked(1000, 700, "4"))     # pred_pre_sig 하한가
        self.assertFalse(is_locked(1000, 700, "2"))    # 정상
        self.assertFalse(is_locked(None, 700, "2"))


class TestLoadUnsoldPositions(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _seed(self, con, **kw):
        cols = {"date": "20260617", "ticker": "005930", "status": "confirmed",
                "qty": 1, "cntr_price": 1000, "sell_status": None, **kw}
        con.execute(
            "INSERT INTO close_bet_orders (date,ticker,status,qty,cntr_price,sell_status) "
            "VALUES (:date,:ticker,:status,:qty,:cntr_price,:sell_status)", cols)

    def test_filters(self):
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            self._seed(con, ticker="AAA")                      # 대상
            self._seed(con, ticker="BBB", status="submitted")  # 미체결 제외
            self._seed(con, ticker="CCC", sell_status="filled")# 이미청산 제외
            self._seed(con, ticker="DDD", date="20260618")     # 오늘분 제외
            rows = load_unsold_positions(con, "20260618")
        self.assertEqual([r["ticker"] for r in rows], ["AAA"])
        self.assertEqual(rows[0]["cntr_price"], 1000)
        self.assertEqual(rows[0]["leg"], "single")


class TestSplitPositions(unittest.TestCase):
    """보유 수량을 auction(ceil)/chase(floor) 두 줄로 — 1주면 auction 만 (T1, T2)."""

    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _seed(self, ticker, qty):
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            con.execute(
                "INSERT INTO close_bet_orders (date,ticker,score,status,qty,cntr_price,cntr_qty,order_no) "
                "VALUES ('20260916',?,55,'confirmed',?,3470,?,'0001234')", (ticker, qty, qty))
        return {"date": "20260916", "ticker": ticker, "leg": "single",
                "cntr_price": 3470, "qty": qty, "qty_eff": qty}

    def _rows(self, ticker):
        with connect_rw(self.db) as con:
            return con.execute(
                "SELECT leg, qty, cntr_qty, cntr_price, score, status, order_no, sell_status "
                "FROM close_bet_orders WHERE ticker=? ORDER BY leg", (ticker,)).fetchall()

    def test_even_odd_one(self):
        positions = [self._seed("AAA", 10), self._seed("BBB", 9), self._seed("CCC", 1)]
        out = split_positions(self.db, positions)
        got = sorted((p["ticker"], p["leg"], p["qty"], p["qty_eff"]) for p in out)
        self.assertEqual(got, [
            ("AAA", "auction", 5, 5), ("AAA", "chase", 5, 5),
            ("BBB", "auction", 5, 5), ("BBB", "chase", 4, 4),
            ("CCC", "auction", 1, 1),
        ])
        self.assertEqual(self._rows("BBB"), [
            ("auction", 5, 5, 3470, 55, "confirmed", "0001234", None),
            ("chase", 4, 4, 3470, 55, "confirmed", "0001234", None),
        ])
        self.assertEqual(self._rows("CCC"), [("auction", 1, 1, 3470, 55, "confirmed", "0001234", None)])

    def test_ledger_keeps_bought_qty_when_balance_is_short(self):
        """잔고 매도가능이 기록보다 적어도 원장은 매수 수량대로 쪼갠다 — 차액이 사라지면 안 됨."""
        pos = self._seed("AAA", 10)
        pos["qty_eff"] = 7          # 미체결 매도 등으로 매도가능 7
        out = split_positions(self.db, [pos])
        self.assertEqual(sorted((p["leg"], p["qty"], p["qty_eff"]) for p in out),
                         [("auction", 5, 5), ("chase", 5, 2)])   # 주문은 7주까지만
        self.assertEqual([(r[0], r[1], r[2]) for r in self._rows("AAA")],
                         [("auction", 5, 5), ("chase", 5, 5)])   # 원장 합 = 10

    def test_partial_buy_fill_splits_each_column(self):
        """qty(주문)와 cntr_qty(체결)가 다르면 각자 쪼갠다."""
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            con.execute(
                "INSERT INTO close_bet_orders (date,ticker,score,status,qty,cntr_price,cntr_qty,order_no) "
                "VALUES ('20260916','DDD',55,'confirmed',10,3470,7,'0001234')")
        pos = {"date": "20260916", "ticker": "DDD", "leg": "single",
               "cntr_price": 3470, "qty": 10, "qty_eff": 7}
        split_positions(self.db, [pos])
        self.assertEqual([(r[0], r[1], r[2]) for r in self._rows("DDD")],
                         [("auction", 5, 4), ("chase", 5, 3)])

    def test_already_split_rows_untouched(self):
        """재기동 — leg 가 이미 auction/chase 면 재분할 안 함."""
        split_positions(self.db, [self._seed("AAA", 10)])
        again = [{"date": "20260916", "ticker": "AAA", "leg": leg, "cntr_price": 3470,
                  "qty": 5, "qty_eff": 5} for leg in ("auction", "chase")]
        out = split_positions(self.db, again)
        self.assertEqual(sorted((p["leg"], p["qty"]) for p in out), [("auction", 5), ("chase", 5)])
        self.assertEqual(len(self._rows("AAA")), 2)


class TestReconcileBalance(unittest.TestCase):
    def test_intersect_and_min_qty(self):
        positions = [
            {"ticker": "005930", "cntr_price": 1000, "qty": 5},
            {"ticker": "000660", "cntr_price": 2000, "qty": 3},  # 잔고 미보유
        ]
        balance = {"acnt_evlt_remn_indv_tot": [
            {"stk_cd": "A005930", "trde_able_qty": "000000000000002"},  # 매도가능 2
        ]}
        out = reconcile_balance(positions, balance)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ticker"], "005930")
        self.assertEqual(out[0]["qty_eff"], 2)  # min(5, 2)

    def test_empty_balance(self):
        positions = [{"ticker": "005930", "cntr_price": 1000, "qty": 5}]
        self.assertEqual(reconcile_balance(positions, {}), [])

    def test_keeps_alphanumeric_ticker_with_kiwoom_prefix(self):
        positions = [{"ticker": "0220W0", "cntr_price": 10100, "qty": 9}]
        balance = {"acnt_evlt_remn_indv_tot": [
            {"stk_cd": "A0220W0", "trde_able_qty": "000000000000009"},
        ]}
        self.assertEqual(reconcile_balance(positions, balance), [
            {"ticker": "0220W0", "cntr_price": 10100, "qty": 9, "qty_eff": 9},
        ])


def _resp(json_data, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    m.raise_for_status.return_value = None
    return m


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            con.execute(
                "INSERT INTO close_bet_orders (date,ticker,status,qty,cntr_price,sell_status) "
                "VALUES ('20260617','005930','confirmed',5,1000,NULL)")

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _row(self):
        with connect_rw(self.db) as con:
            return con.execute(
                "SELECT sell_status,sell_order_no,sell_price,sell_qty,exit_reason,pnl_pct "
                "FROM close_bet_orders WHERE ticker='005930'").fetchone()

    def test_mark_ordered_then_filled(self):
        with connect_rw(self.db) as con:
            mark_ordered(con, "20260617", "005930", "0000070", leg="single")
        r = self._row()
        self.assertEqual(r[0], "ordered")
        self.assertEqual(r[1], "0000070")
        with connect_rw(self.db) as con:
            mark_filled(con, "20260617", "005930", 1050, 5, "2026-06-18T15:00", "tp", 5.0, leg="single")
        r = self._row()
        self.assertEqual(r[0], "filled")
        self.assertEqual((r[2], r[3], r[4], r[5]), (1050, 5, "tp", 5.0))

    def test_updates_only_given_leg(self):
        with connect_rw(self.db) as con:
            con.execute("UPDATE close_bet_orders SET leg='auction'")
            con.execute(
                "INSERT INTO close_bet_orders (date,ticker,leg,status,qty,cntr_price) "
                "VALUES ('20260617','005930','chase','confirmed',5,1000)")
            mark_ordered(con, "20260617", "005930", "0000071", leg="chase")
            mark_filled(con, "20260617", "005930", 1040, 5, "t", "forced", 4.0, leg="chase")
            rows = con.execute("SELECT leg, sell_status, sell_order_no FROM close_bet_orders "
                               "ORDER BY leg").fetchall()
        self.assertEqual(rows, [("auction", None, None), ("chase", "filled", "0000071")])


class TestInFlight(unittest.TestCase):
    """같은 종목 다른 줄의 추적 중 주문은 막지 않고, 추적 안 되는 미체결 매도만 막는다 (T10)."""

    def test_guard(self):
        key = ("A", "chase")
        pending = {("A", "auction"): {"order_no": "0000070"}}
        self.assertTrue(is_in_flight(key, {key: {"order_no": "1"}}, {}))            # 이 줄 주문 중
        self.assertFalse(is_in_flight(key, pending, {"70": {"ticker": "A"}}))       # 형제 줄 주문 — 통과
        self.assertTrue(is_in_flight(key, pending, {"99": {"ticker": "A"}}))        # 추적 안 되는 주문
        self.assertFalse(is_in_flight(key, {}, {"99": {"ticker": "B"}}))


class TestBrokerSellHelpers(unittest.TestCase):
    def test_place_sell_success(self):
        with patch.object(ex, "requests") as rq:
            rq.post.return_value = _resp({"accepted": True, "order_no": "0000070"})
            res = place_sell_via_broker("http://b", "005930", 3)
        self.assertEqual(res, {"order_no": "0000070", "status": "submitted", "message": ""})
        self.assertEqual(rq.post.call_args.args[0], "http://b/orders/strategy")
        body = rq.post.call_args.kwargs["json"]
        self.assertEqual(body["side"], "sell")
        self.assertEqual(body["order_type"], "market")
        self.assertEqual(body["exchange"], "SOR")

    def test_place_sell_passes_exchange(self):
        with patch.object(ex, "requests") as rq:
            rq.post.return_value = _resp({"accepted": True, "order_no": "0000070"})
            place_sell_via_broker("http://b", "005930", 3, exchange="KRX")
        self.assertEqual(rq.post.call_args.kwargs["json"]["exchange"], "KRX")

    def test_place_sell_rejected_422(self):
        with patch.object(ex, "requests") as rq:
            rq.post.return_value = _resp({"detail": "한도초과"}, status=422)
            res = place_sell_via_broker("http://b", "005930", 3)
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["order_no"], "")

    def test_fetch_unfilled_orders(self):
        with patch.object(ex, "requests") as rq:
            rq.get.return_value = _resp([{"ticker": "005930", "order_no": "0000007", "oso_qty": 3, "ord_price": 1000},
                                         {"ticker": "000660", "order_no": "0000008", "oso_qty": 1, "ord_price": 0}])
            out = fetch_unfilled_orders("http://b")
        self.assertEqual(out, {"7": {"ticker": "005930", "oso_qty": 3, "ord_price": 1000, "order_no": "0000007"},
                               "8": {"ticker": "000660", "oso_qty": 1, "ord_price": 0, "order_no": "0000008"}})

    # fetch_realized 단위테스트는 공용 모듈로 이동 → tests/test_trading_batch_common.py

    def test_fetch_sell_fills_aggregates_partial(self):
        with patch.object(ex, "requests") as rq:
            rq.get.return_value = _resp([
                {"order_no": "0000070", "cntr_uv": 1050, "cntr_qty": 2},
                {"order_no": "0000070", "cntr_uv": 1050, "cntr_qty": 3},  # 부분체결 합산
            ])
            fills = fetch_sell_fills("http://b", "20260618")
        self.assertEqual(fills["70"], {"cntr_uv": 1050, "cntr_qty": 5})

    def test_fetch_sell_fills_malformed_response(self):
        """성공 응답인데 내용이 깨짐 → 예외 대신 조회 실패 값 (워커 폴링이 멈추지 않게)."""
        with patch.object(ex, "requests") as rq:
            rq.get.return_value = _resp({"detail": "not a list"})
            self.assertEqual(fetch_sell_fills("http://b", "20260618"), {})
            self.assertIsNone(fetch_sell_fills("http://b", "20260618", strict=True))


class TestExecuteSell(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            con.execute(
                "INSERT INTO close_bet_orders (date,ticker,status,qty,cntr_price,sell_status) "
                "VALUES ('20260617','005930','confirmed',5,1000,NULL)")
        self.pos = {"date": "20260617", "ticker": "005930", "leg": "single", "cntr_price": 1000,
                    "qty": 5, "qty_eff": 5}

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _bal(self, avail):
        return {"acnt_evlt_remn_indv_tot": [
            {"stk_cd": "A005930", "trde_able_qty": str(avail).zfill(15)}]}

    def test_sells_min_qty_and_marks_ordered(self):
        with patch.object(ex, "fetch_balance", return_value=self._bal(2)), \
             patch.object(ex, "place_sell_via_broker",
                          return_value={"order_no": "0000070", "status": "submitted"}) as p:
            res = execute_sell("http://b", self.db, self.pos, "forced")
        self.assertEqual(res["order_no"], "0000070")
        self.assertEqual(p.call_args.args[2], 2)  # min(5, 보유2)
        with connect_rw(self.db) as con:
            r = con.execute("SELECT sell_status,sell_order_no FROM close_bet_orders "
                            "WHERE ticker='005930'").fetchone()
        self.assertEqual(r, ("ordered", "0000070"))

    def test_no_qty_when_not_held(self):
        with patch.object(ex, "fetch_balance", return_value=self._bal(0)), \
             patch.object(ex, "place_sell_via_broker") as p:
            res = execute_sell("http://b", self.db, self.pos, "forced")
        self.assertEqual(res["status"], "no_qty")
        p.assert_not_called()

    def test_rejected_does_not_mark_ordered(self):
        with patch.object(ex, "fetch_balance", return_value=self._bal(5)), \
             patch.object(ex, "place_sell_via_broker",
                          return_value={"order_no": "", "status": "rejected"}):
            execute_sell("http://b", self.db, self.pos, "sl")
        with connect_rw(self.db) as con:
            r = con.execute("SELECT sell_status FROM close_bet_orders "
                            "WHERE ticker='005930'").fetchone()
        self.assertIsNone(r[0])  # ordered 미기록


class TestSettlePending(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            con.execute(
                "INSERT INTO close_bet_orders (date,ticker,status,qty,cntr_price,sell_status,sell_order_no) "
                "VALUES ('20260617','005930','confirmed',5,1000,'ordered','0000070')")
        self.key = ("005930", "single")
        self.pending = {self.key: {"date": "20260617", "ticker": "005930", "leg": "single",
                                   "cntr_price": 1000, "order_no": "0000070",
                                   "exit_reason": "tp"}}

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_waits_while_in_unfilled(self):
        with patch.object(ex, "fetch_unfilled_orders", return_value={"70": {"ticker": "005930", "oso_qty": 5}}), \
             patch.object(ex, "fetch_sell_fills", return_value={}):
            done = settle_pending("http://b", self.db, "20260618", self.pending)
        self.assertEqual(done, [])

    def test_sibling_leg_unfilled_does_not_block(self):
        """같은 종목 다른 주문번호가 미체결이어도 이 줄 주문이 사라졌으면 확정."""
        with patch.object(ex, "fetch_unfilled_orders", return_value={"99": {"ticker": "005930", "oso_qty": 5}}), \
             patch.object(ex, "fetch_sell_fills",
                          return_value={"70": {"cntr_uv": 1050, "cntr_qty": 5}}), \
             patch.object(ex, "fetch_realized", return_value=None):
            done = settle_pending("http://b", self.db, "20260618", self.pending)
        self.assertEqual(done, [self.key])

    def test_fills_gross_fallback_when_no_realized(self):
        # ka10077 미발견 → gross 계산 폴백, 수수료·세금 NULL
        with patch.object(ex, "fetch_unfilled_orders", return_value={}), \
             patch.object(ex, "fetch_sell_fills",
                          return_value={"70": {"cntr_uv": 1050, "cntr_qty": 5}}), \
             patch.object(ex, "fetch_realized", return_value=None):
            done = settle_pending("http://b", self.db, "20260618", self.pending)
        self.assertEqual(done, [self.key])
        with connect_rw(self.db) as con:
            r = con.execute("SELECT sell_status,sell_price,sell_qty,exit_reason,pnl_pct,"
                            "sell_cmsn,sell_tax,sell_pl_won "
                            "FROM close_bet_orders WHERE ticker='005930'").fetchone()
        self.assertEqual(r, ("filled", 1050, 5, "tp", 5.0, None, None, None))  # gross 1050/1000-1

    def test_fills_uses_net_realized(self):
        # ka10077 net 손익(수수료·세금 차감) 우선 저장
        realized = {"found": True, "pnl_pct": 4.78, "cmsn": 150, "tax": 1350,
                    "sel_pl_won": 47800}
        with patch.object(ex, "fetch_unfilled_orders", return_value={}), \
             patch.object(ex, "fetch_sell_fills",
                          return_value={"70": {"cntr_uv": 1050, "cntr_qty": 5}}), \
             patch.object(ex, "fetch_realized", return_value=realized):
            done = settle_pending("http://b", self.db, "20260618", self.pending)
        self.assertEqual(done, [self.key])
        with connect_rw(self.db) as con:
            r = con.execute("SELECT sell_price,pnl_pct,sell_cmsn,sell_tax,sell_pl_won "
                            "FROM close_bet_orders WHERE ticker='005930'").fetchone()
        self.assertEqual(r, (1050, 4.78, 150, 1350, 47800))

    def test_gone_but_no_fill_record_waits(self):
        with patch.object(ex, "fetch_unfilled_orders", return_value={}), \
             patch.object(ex, "fetch_sell_fills", return_value={}):
            done = settle_pending("http://b", self.db, "20260618", self.pending)
        self.assertEqual(done, [])


class TestLoadOrderedPending(unittest.TestCase):
    def test_recovers_ordered_rows(self):
        db = _fresh_db()
        try:
            with connect_rw(db) as con:
                ensure_exit_columns(con)
                con.execute(
                    "INSERT INTO close_bet_orders (date,ticker,status,qty,cntr_price,sell_status,sell_order_no,exit_reason) "
                    "VALUES ('20260617','005930','confirmed',5,1000,'ordered','0000070','forced')")
                con.execute(
                    "INSERT INTO close_bet_orders (date,ticker,status,qty,cntr_price,sell_status) "
                    "VALUES ('20260617','000660','confirmed',3,2000,NULL)")  # 미주문 제외
            pend = load_ordered_pending(db, "20260618")
            key = ("005930", "single")
            self.assertEqual(list(pend.keys()), [key])
            self.assertEqual(pend[key]["order_no"], "0000070")
            self.assertEqual(pend[key]["exit_reason"], "forced")
            self.assertEqual(pend[key]["leg"], "single")
        finally:
            db.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()


class TestMarkMissingPositions(unittest.TestCase):
    """잔고에서 사라진 포지션을 종료로 확정한다.

    기존엔 reconcile_balance 가 미보유 포지션을 조용히 탈락시키기만 해서
    sell_status 가 영원히 NULL 로 남았다(050110, 2026-07-15). 그 결과 매일
    청산 워커가 헛돌고, 중복매수 가드가 그 종목을 영구 매수차단했다.
    """

    def _db_with(self, ticker: str) -> Path:
        path = _fresh_db()
        with connect_rw(path) as con:
            create_close_bet_orders_table(con)
            ensure_exit_columns(con)
            con.execute(
                "INSERT INTO close_bet_orders(date,ticker,score,qty,order_type,status,cntr_price)"
                " VALUES('20260715',?,52,2606,'market','confirmed',1150)",
                (ticker,),
            )
        return path

    def _sell_status(self, path: Path, ticker: str):
        with connect_rw(path) as con:
            row = con.execute(
                "SELECT sell_status FROM close_bet_orders WHERE ticker=?", (ticker,)
            ).fetchone()
        return row[0]

    def test_position_absent_from_balance_is_closed_as_missing(self):
        path = self._db_with("050110")
        positions = [{"date": "20260715", "ticker": "050110", "leg": "single", "cntr_price": 1150, "qty": 11}]
        ex.mark_missing_positions(path, positions, {"acnt_evlt_remn_indv_tot": []})
        self.assertEqual(self._sell_status(path, "050110"), "missing")

    def test_held_position_is_untouched(self):
        path = self._db_with("025320")
        positions = [{"date": "20260715", "ticker": "025320", "leg": "single", "cntr_price": 1150, "qty": 75}]
        balance = {"acnt_evlt_remn_indv_tot": [
            {"stk_cd": "A025320", "trde_able_qty": "75"},
        ]}
        ex.mark_missing_positions(path, positions, balance)
        self.assertIsNone(self._sell_status(path, "025320"))

    def test_balance_lookup_failure_marks_nothing(self):
        """조회 한 번 실패로 전 포지션을 유령 처리하면 실보유분 청산이 막힌다."""
        path = self._db_with("050110")
        positions = [{"date": "20260715", "ticker": "050110", "leg": "single", "cntr_price": 1150, "qty": 11}]
        ex.mark_missing_positions(path, positions, {})
        self.assertIsNone(self._sell_status(path, "050110"))

    def test_unsold_query_skips_closed_rows(self):
        """마감된 행은 다음날부터 청산 대상에서 빠진다 — 매일 헛도는 루프 종료."""
        path = self._db_with("050110")
        positions = [{"date": "20260715", "ticker": "050110", "leg": "single", "cntr_price": 1150, "qty": 11}]
        ex.mark_missing_positions(path, positions, {"acnt_evlt_remn_indv_tot": []})
        with connect_rw(path) as con:
            self.assertEqual(load_unsold_positions(con, "20260819"), [])


# ── 반반 분할 청산 — 추격 (PLAN_CLOSE_BET_SPLIT_EXIT) ─────────────────────────


class TestChasePrice(unittest.TestCase):
    """매도1호가 − 1틱, 하한가 아래로는 안 감 (T5)."""

    def test_tick_boundaries(self):
        self.assertEqual(ex.chase_price(2000, None), 1999)   # 경계: 아래 가격대 호가단위 1
        self.assertEqual(ex.chase_price(1500, None), 1499)
        self.assertEqual(ex.chase_price(5000, None), 4995)
        self.assertEqual(ex.chase_price(3470, None), 3465)
        self.assertEqual(ex.chase_price(10000, None), 9990)

    def test_lower_limit_clamp(self):
        self.assertEqual(ex.chase_price(1000, 1000), 1000)
        self.assertEqual(ex.chase_price(1001, 1000), 1000)

    def test_no_quote(self):
        self.assertIsNone(ex.chase_price(None, 1000))
        self.assertIsNone(ex.chase_price(0, 1000))


class TestChaseClock(unittest.TestCase):
    def test_due_round(self):
        self.assertEqual(ex.due_round(_t("09:00:29")), 0)
        self.assertEqual(ex.due_round(_t("09:00:30")), 1)
        self.assertEqual(ex.due_round(_t("09:00:45")), 2)
        self.assertEqual(ex.due_round(_t("09:00:59")), 3)

    def test_chase_end(self):
        self.assertFalse(ex.is_chase_end(_t("09:00:59")))
        self.assertTrue(ex.is_chase_end(_t("09:01:00")))


def _split_db() -> Path:
    db = _fresh_db()
    with connect_rw(db) as con:
        ensure_exit_columns(con)
        for leg, qty in (("auction", 5), ("chase", 5)):
            con.execute(
                "INSERT INTO close_bet_orders (date,ticker,leg,status,qty,cntr_qty,cntr_price) "
                "VALUES ('20260916','005160',?,'confirmed',?,?,3470)", (leg, qty, qty))
    return db


def _entry(leg, order_no, kind, *, round_=0, exchange="SOR", qty=5):
    return {"date": "20260916", "ticker": "005160", "leg": leg, "cntr_price": 3470, "qty": qty,
            "order_no": order_no, "exit_reason": "forced", "kind": kind, "round": round_,
            "exchange": exchange, "history": []}


class TestRunChase(unittest.TestCase):
    QUOTE = {"005160": {"sel_bid": 3470, "lst_pric": 2430}}

    def setUp(self):
        self.db = _split_db()
        self.pos = {"date": "20260916", "ticker": "005160", "leg": "chase",
                    "cntr_price": 3470, "qty": 5, "qty_eff": 5}

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _chase(self, hms, watch, pending, unfilled, quotes=None, **patches):
        defaults = {
            "execute_sell": MagicMock(return_value={"order_no": "0000100", "status": "submitted", "qty": 5}),
            "place_sell_via_broker": MagicMock(return_value={"order_no": "0000200", "status": "submitted"}),
            "cancel_via_broker": MagicMock(return_value=True),
            "modify_via_broker": MagicMock(return_value="0000101"),
        }
        defaults.update(patches)
        with patch.multiple(ex, **defaults):
            ex.run_chase("http://b", self.db, _t(hms), watch, pending,
                         self.QUOTE if quotes is None else quotes, unfilled)
        return defaults

    def test_before_round_does_nothing(self):
        watch = {("005160", "chase"): self.pos}
        m = self._chase("09:00:29", watch, {}, {})
        m["execute_sell"].assert_not_called()
        self.assertIn(("005160", "chase"), watch)

    def test_round1_places_limit_at_ask_minus_tick(self):
        """T4 — 09:00:30 chase 줄 지정가 신규 (SOR)."""
        key = ("005160", "chase")
        watch, pending = {key: self.pos}, {}
        m = self._chase("09:00:30", watch, pending, {})
        call = m["execute_sell"].call_args
        self.assertEqual(call.kwargs["price"], 3465)
        self.assertEqual(call.kwargs["exchange"], "SOR")
        self.assertNotIn(key, watch)
        self.assertEqual((pending[key]["kind"], pending[key]["round"], pending[key]["order_no"]),
                         ("chase", 1, "0000100"))

    def test_round1_skips_without_quote(self):
        key = ("005160", "chase")
        watch = {key: self.pos}
        m = self._chase("09:00:30", watch, {}, {}, quotes={})
        m["execute_sell"].assert_not_called()
        self.assertIn(key, watch)

    def test_same_target_no_modify(self):
        """T6 — 목표가 = 현재 주문가면 정정 안 함(시간우선 유지)."""
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000100", "chase", round_=1)}
        unfilled = {"100": {"ticker": "005160", "oso_qty": 5, "ord_price": 3465}}
        m = self._chase("09:00:40", {}, pending, unfilled)
        m["modify_via_broker"].assert_not_called()
        self.assertEqual(pending[key]["round"], 2)

    def test_changed_target_modifies_and_keeps_history(self):
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000100", "chase", round_=1)}
        unfilled = {"100": {"ticker": "005160", "oso_qty": 5, "ord_price": 3465}}
        m = self._chase("09:00:40", {}, pending, unfilled,
                        quotes={"005160": {"sel_bid": 3460, "lst_pric": 2430}})
        m["modify_via_broker"].assert_called_once_with("http://b", "0000100", "005160", 3455)
        e = pending[key]
        self.assertEqual((e["order_no"], e["round"]), ("0000101", 2))
        self.assertEqual(e["history"], [("0000100", "chase", 1)])
        with connect_rw(self.db) as con:
            no = con.execute("SELECT sell_order_no FROM close_bet_orders WHERE leg='chase'").fetchone()[0]
        self.assertEqual(no, "0000101")

    def test_round_done_once(self):
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000100", "chase", round_=2)}
        unfilled = {"100": {"ticker": "005160", "oso_qty": 5, "ord_price": 3465}}
        m = self._chase("09:00:45", {}, pending, unfilled,
                        quotes={"005160": {"sel_bid": 3460, "lst_pric": 2430}})
        m["modify_via_broker"].assert_not_called()

    def test_auction_residual_joins_chase(self):
        """T7 — 동시호가 잔량 → KRX 취소 → 지정가 신규(SOR)."""
        key = ("005160", "auction")
        pending = {key: _entry("auction", "0000050", "auction", exchange="KRX")}
        unfilled = {"50": {"ticker": "005160", "oso_qty": 3, "ord_price": 0}}
        m = self._chase("09:00:30", {}, pending, unfilled)
        m["cancel_via_broker"].assert_called_once_with("http://b", "0000050", "005160", "KRX")
        call = m["place_sell_via_broker"].call_args
        self.assertEqual((call.args[2], call.kwargs["price"], call.kwargs["exchange"]), (3, 3465, "SOR"))
        e = pending[key]
        self.assertEqual((e["order_no"], e["kind"], e["exchange"], e["round"]), ("0000200", "chase", "SOR", 1))
        self.assertEqual(e["history"], [("0000050", "auction", 0)])

    def test_auction_fully_filled_untouched(self):
        key = ("005160", "auction")
        pending = {key: _entry("auction", "0000050", "auction", exchange="KRX")}
        m = self._chase("09:00:30", {}, pending, {})
        m["cancel_via_broker"].assert_not_called()

    def test_end_cancels_then_market(self):
        """T8 — 09:01 걸린 지정가 취소 → 잔량 시장가 SOR."""
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000101", "chase", round_=3)}
        unfilled = {"101": {"ticker": "005160", "oso_qty": 2, "ord_price": 3455}}
        m = self._chase("09:01:00", {}, pending, unfilled)
        m["cancel_via_broker"].assert_called_once_with("http://b", "0000101", "005160", "SOR")
        call = m["place_sell_via_broker"].call_args
        self.assertEqual((call.args[2], call.kwargs.get("price"), call.kwargs["exchange"]), (2, None, "SOR"))
        self.assertEqual(pending[key]["kind"], "market_0901")

    def test_end_cancel_failure_keeps_order(self):
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000101", "chase", round_=3)}
        unfilled = {"101": {"ticker": "005160", "oso_qty": 2, "ord_price": 3455}}
        m = self._chase("09:01:00", {}, pending, unfilled, cancel_via_broker=MagicMock(return_value=False))
        m["place_sell_via_broker"].assert_not_called()
        self.assertEqual(pending[key]["order_no"], "0000101")

    def test_end_unordered_chase_leg_market(self):
        key = ("005160", "chase")
        watch, pending = {key: self.pos}, {}
        m = self._chase("09:01:00", watch, pending, {}, quotes={})
        call = m["execute_sell"].call_args
        self.assertIsNone(call.kwargs.get("price"))
        self.assertEqual(call.kwargs["exchange"], "SOR")
        self.assertEqual(pending[key]["kind"], "market_0901")

    def test_replace_failure_retries_market(self):
        """취소 후 재주문 실패 → 남은 수량을 잃지 않게 다음 폴링에 시장가 재시도."""
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000101", "chase", round_=3)}
        unfilled = {"101": {"ticker": "005160", "oso_qty": 2, "ord_price": 3455}}
        self._chase("09:01:00", {}, pending, unfilled,
                    place_sell_via_broker=MagicMock(return_value={"order_no": "", "status": "failed"}))
        self.assertEqual(pending[key]["orphan_qty"], 2)
        m = self._chase("09:01:03", {}, pending, {})
        call = m["place_sell_via_broker"].call_args
        self.assertEqual((call.args[2], call.kwargs["exchange"]), (2, "SOR"))
        self.assertEqual((pending[key]["order_no"], pending[key]["kind"]), ("0000200", "market_0901"))
        self.assertNotIn("orphan_qty", pending[key])

    def test_end_market_not_repeated(self):
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000300", "market_0901")}
        unfilled = {"300": {"ticker": "005160", "oso_qty": 2, "ord_price": 0}}
        m = self._chase("09:01:05", {}, pending, unfilled)
        m["cancel_via_broker"].assert_not_called()


class TestRecordFillsAndSettleSplit(unittest.TestCase):
    """T9 — 체결된 주문번호만 기록 / 분할 줄은 체결 합 == 수량일 때 확정."""

    def setUp(self):
        self.db = _split_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _fills(self):
        with connect_rw(self.db) as con:
            return con.execute("SELECT order_no, kind, round, price, qty FROM close_bet_sell_fills "
                               "ORDER BY order_no").fetchall()

    def test_only_filled_orders_recorded(self):
        e = _entry("chase", "0000101", "chase", round_=2)
        e["history"] = [("0000100", "chase", 1)]
        ex.record_fills(self.db, e, {"100": {"cntr_uv": 3465, "cntr_qty": 2},
                                     "101": {"cntr_uv": 0, "cntr_qty": 0}})
        self.assertEqual(self._fills(), [("0000100", "chase", 1, 3465, 2)])
        ex.record_fills(self.db, e, {"100": {"cntr_uv": 3465, "cntr_qty": 2},
                                     "101": {"cntr_uv": 3455, "cntr_qty": 3}})   # 재기록 멱등
        self.assertEqual(self._fills(), [("0000100", "chase", 1, 3465, 2), ("0000101", "chase", 2, 3455, 3)])

    def _settle(self, pending, fills, unfilled=None):
        with patch.object(ex, "fetch_unfilled_orders", return_value=unfilled or {}), \
             patch.object(ex, "fetch_sell_fills", return_value=fills), \
             patch.object(ex, "fetch_realized") as realized:
            done = settle_pending("http://b", self.db, "20260917", pending)
        return done, realized

    def test_split_leg_waits_until_full_qty(self):
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000101", "chase", round_=2)}
        pending[key]["history"] = [("0000100", "chase", 1)]
        done, _ = self._settle(pending, {"100": {"cntr_uv": 3465, "cntr_qty": 2}})
        self.assertEqual(done, [])

    def test_split_leg_filled_weighted_gross(self):
        key = ("005160", "chase")
        pending = {key: _entry("chase", "0000101", "chase", round_=2)}
        pending[key]["history"] = [("0000100", "chase", 1)]
        done, realized = self._settle(pending, {"100": {"cntr_uv": 3465, "cntr_qty": 2},
                                                "101": {"cntr_uv": 3455, "cntr_qty": 3}})
        self.assertEqual(done, [key])
        realized.assert_not_called()   # 줄별 순손익 배분은 D12(5단계)
        with connect_rw(self.db) as con:
            r = con.execute("SELECT sell_status, sell_price, sell_qty, pnl_pct, sell_cmsn "
                            "FROM close_bet_orders WHERE leg='chase'").fetchone()
        self.assertEqual(r, ("filled", 3459, 5, -0.32, None))   # (3465*2+3455*3)/5=3459


class TestAllocateCosts(unittest.TestCase):
    """T11 — 두 줄 모두 체결되면 ka10077 수수료·세금을 매도금액 비율로 배분. 합계 = 키움 값."""

    def setUp(self):
        self.db = _split_db()
        self.realized = {"found": True, "pnl_pct": -1.0, "cmsn": 50, "tax": 69, "sel_pl_won": -349}

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _settle(self, pending, fills, realized):
        with patch.object(ex, "fetch_unfilled_orders", return_value={}), \
             patch.object(ex, "fetch_sell_fills", return_value=fills), \
             patch.object(ex, "fetch_realized", return_value=realized) as fr:
            done = settle_pending("http://b", self.db, "20260917", pending)
        return done, fr

    def _rows(self):
        with connect_rw(self.db) as con:
            return con.execute("SELECT leg, sell_cmsn, sell_tax, sell_pl_won, pnl_pct FROM close_bet_orders "
                               "ORDER BY leg").fetchall()

    def test_allocates_after_both_legs_filled(self):
        pending = {("005160", "auction"): _entry("auction", "0000050", "auction", exchange="KRX"),
                   ("005160", "chase"): _entry("chase", "0000101", "chase", round_=2)}
        fills = {"50": {"cntr_uv": 3435, "cntr_qty": 5}, "101": {"cntr_uv": 3459, "cntr_qty": 5}}
        done, fr = self._settle(pending, fills, self.realized)
        self.assertEqual(len(done), 2)
        fr.assert_called_once_with("http://b", "005160")
        rows = self._rows()
        # 매도금액 17175 : 17295 — chase 내림(cmsn 25, tax 34), 잔차 auction(25, 35)
        self.assertEqual(rows, [("auction", 25, 35, -235, -1.35), ("chase", 25, 34, -114, -0.66)])
        self.assertEqual((sum(r[1] for r in rows), sum(r[2] for r in rows)), (50, 69))

    def test_waits_for_sibling_leg(self):
        pending = {("005160", "auction"): _entry("auction", "0000050", "auction", exchange="KRX")}
        done, fr = self._settle(pending, {"50": {"cntr_uv": 3435, "cntr_qty": 5}}, self.realized)
        self.assertEqual(len(done), 1)
        fr.assert_not_called()

    def test_realized_failure_keeps_gross(self):
        pending = {("005160", "auction"): _entry("auction", "0000050", "auction", exchange="KRX"),
                   ("005160", "chase"): _entry("chase", "0000101", "chase", round_=2)}
        fills = {"50": {"cntr_uv": 3435, "cntr_qty": 5}, "101": {"cntr_uv": 3459, "cntr_qty": 5}}
        self._settle(pending, fills, None)
        self.assertEqual(self._rows(), [("auction", None, None, None, -1.01), ("chase", None, None, None, -0.32)])


class TestBuildWatchSetSplits(unittest.TestCase):
    """실모드 기동 시 잔고대조 결과를 분할한다. dry-run 은 DB 를 안 건드린다."""

    def _build(self, dry_run):
        args = MagicMock(watch_codes="", dry_run=dry_run)
        pos = [{"date": "20260916", "ticker": "005160", "leg": "single", "cntr_price": 3470, "qty": 9}]
        split = [{**pos[0], "leg": "auction", "qty": 5, "qty_eff": 5},
                 {**pos[0], "leg": "chase", "qty": 4, "qty_eff": 4}]
        bal = {"acnt_evlt_remn_indv_tot": [{"stk_cd": "A005160", "trde_able_qty": "9"}]}
        with patch.object(ex, "connect_rw"), \
             patch.object(ex, "load_unsold_positions", return_value=pos), \
             patch.object(ex, "fetch_balance", return_value=bal), \
             patch.object(ex, "mark_missing_positions"), \
             patch.object(ex, "split_positions", return_value=split) as sp:
            watch = ex.build_watch_set(args, "http://b", "20260917")
        return watch, sp

    def test_real_mode_splits(self):
        watch, sp = self._build(False)
        sp.assert_called_once()
        self.assertEqual(sorted(watch), [("005160", "auction"), ("005160", "chase")])

    def test_dry_run_does_not_split(self):
        watch, sp = self._build(True)
        sp.assert_not_called()
        self.assertEqual(list(watch), [("005160", "single")])


class TestRunLoopSplitAuction(unittest.TestCase):
    """T3 — 08:55 강제청산은 auction 줄만 KRX, chase 줄은 주문 안 냄."""

    def test_only_auction_leg_sold_at_exit_time(self):
        args = MagicMock(dry_run=False, stop_time="15:25:00", force_exit_time="08:55:00",
                         window_start="09:00:00", window_end="15:20:00", tp=None, sl=None, poll_sec=0)
        base = {"date": "20260916", "ticker": "005160", "cntr_price": 3470, "qty": 5, "qty_eff": 5}
        watch = {("005160", "auction"): {**base, "leg": "auction"},
                 ("005160", "chase"): {**base, "leg": "chase"}}
        with patch.object(ex, "build_watch_set", return_value=watch), \
                patch.object(ex, "load_ordered_pending", return_value={}), \
                patch.object(ex, "_now_seoul", side_effect=[_t("08:55:00"), _t("08:55:00"), _t("15:25:00")]), \
                patch.object(ex, "fetch_quotes", return_value={}), \
                patch.object(ex, "fetch_unfilled_orders", return_value={}), \
                patch.object(ex, "execute_sell",
                             return_value={"order_no": "0000050", "status": "submitted", "qty": 5}) as sell, \
                patch.object(ex, "send_discord"), patch.object(ex.time, "sleep"):
            ex.run_loop(args, "http://b")
        sell.assert_called_once()
        self.assertEqual(sell.call_args.args[2]["leg"], "auction")
        self.assertEqual(sell.call_args.kwargs["exchange"], "KRX")
