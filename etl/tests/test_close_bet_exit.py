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
    fetch_unfilled_tickers,
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
        cols = {"date": "20260617", "ticker": "005930", "status": "filled",
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
                "VALUES ('20260617','005930','filled',5,1000,NULL)")

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _row(self):
        with connect_rw(self.db) as con:
            return con.execute(
                "SELECT sell_status,sell_order_no,sell_price,sell_qty,exit_reason,pnl_pct "
                "FROM close_bet_orders WHERE ticker='005930'").fetchone()

    def test_mark_ordered_then_filled(self):
        with connect_rw(self.db) as con:
            mark_ordered(con, "20260617", "005930", "0000070")
        r = self._row()
        self.assertEqual(r[0], "ordered")
        self.assertEqual(r[1], "0000070")
        with connect_rw(self.db) as con:
            mark_filled(con, "20260617", "005930", 1050, 5, "2026-06-18T15:00", "tp", 5.0)
        r = self._row()
        self.assertEqual(r[0], "filled")
        self.assertEqual((r[2], r[3], r[4], r[5]), (1050, 5, "tp", 5.0))


class TestInFlight(unittest.TestCase):
    def test_guard(self):
        self.assertTrue(is_in_flight("A", {"A"}, set()))      # ordered
        self.assertTrue(is_in_flight("A", set(), {"A"}))      # 미체결
        self.assertTrue(is_in_flight("A", {"A"}, {"A"}))
        self.assertFalse(is_in_flight("A", {"B"}, {"C"}))


class TestBrokerSellHelpers(unittest.TestCase):
    def test_place_sell_success(self):
        with patch.object(ex, "requests") as rq:
            rq.post.return_value = _resp({"accepted": True, "order_no": "0000070"})
            res = place_sell_via_broker("http://b", "005930", 3)
        self.assertEqual(res, {"order_no": "0000070", "status": "submitted", "message": ""})
        body = rq.post.call_args.kwargs["json"]
        self.assertEqual(body["side"], "sell")
        self.assertEqual(body["order_type"], "market")

    def test_place_sell_rejected_422(self):
        with patch.object(ex, "requests") as rq:
            rq.post.return_value = _resp({"detail": "한도초과"}, status=422)
            res = place_sell_via_broker("http://b", "005930", 3)
        self.assertEqual(res["status"], "rejected")
        self.assertEqual(res["order_no"], "")

    def test_fetch_unfilled_tickers(self):
        with patch.object(ex, "requests") as rq:
            rq.get.return_value = _resp([{"ticker": "005930", "order_no": "7"},
                                         {"ticker": "000660", "order_no": "8"}])
            out = fetch_unfilled_tickers("http://b")
        self.assertEqual(out, {"005930", "000660"})

    def test_fetch_sell_fills_aggregates_partial(self):
        with patch.object(ex, "requests") as rq:
            rq.get.return_value = _resp([
                {"order_no": "0000070", "cntr_uv": 1050, "cntr_qty": 2},
                {"order_no": "0000070", "cntr_uv": 1050, "cntr_qty": 3},  # 부분체결 합산
            ])
            fills = fetch_sell_fills("http://b", "20260618")
        self.assertEqual(fills["70"], {"cntr_uv": 1050, "cntr_qty": 5})


class TestExecuteSell(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            con.execute(
                "INSERT INTO close_bet_orders (date,ticker,status,qty,cntr_price,sell_status) "
                "VALUES ('20260617','005930','filled',5,1000,NULL)")
        self.pos = {"date": "20260617", "ticker": "005930", "cntr_price": 1000,
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
                "VALUES ('20260617','005930','filled',5,1000,'ordered','0000070')")
        self.pending = {"005930": {"date": "20260617", "ticker": "005930",
                                   "cntr_price": 1000, "order_no": "0000070",
                                   "exit_reason": "tp"}}

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_waits_while_in_unfilled(self):
        with patch.object(ex, "fetch_unfilled_tickers", return_value={"005930"}), \
             patch.object(ex, "fetch_sell_fills", return_value={}):
            done = settle_pending("http://b", self.db, "20260618", self.pending)
        self.assertEqual(done, [])

    def test_fills_and_computes_pnl(self):
        with patch.object(ex, "fetch_unfilled_tickers", return_value=set()), \
             patch.object(ex, "fetch_sell_fills",
                          return_value={"70": {"cntr_uv": 1050, "cntr_qty": 5}}):
            done = settle_pending("http://b", self.db, "20260618", self.pending)
        self.assertEqual(done, ["005930"])
        with connect_rw(self.db) as con:
            r = con.execute("SELECT sell_status,sell_price,sell_qty,exit_reason,pnl_pct "
                            "FROM close_bet_orders WHERE ticker='005930'").fetchone()
        self.assertEqual(r, ("filled", 1050, 5, "tp", 5.0))  # pnl=1050/1000-1=+5%

    def test_gone_but_no_fill_record_waits(self):
        with patch.object(ex, "fetch_unfilled_tickers", return_value=set()), \
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
                    "VALUES ('20260617','005930','filled',5,1000,'ordered','0000070','forced')")
                con.execute(
                    "INSERT INTO close_bet_orders (date,ticker,status,qty,cntr_price,sell_status) "
                    "VALUES ('20260617','000660','filled',3,2000,NULL)")  # 미주문 제외
            pend = load_ordered_pending(db, "20260618")
            self.assertEqual(list(pend.keys()), ["005930"])
            self.assertEqual(pend["005930"]["order_no"], "0000070")
            self.assertEqual(pend["005930"]["exit_reason"], "forced")
        finally:
            db.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
