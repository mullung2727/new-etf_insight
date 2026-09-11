"""호가 수집기 1초 격자·슬롯·이벤트 해석 (SPEC §8·§9, T01·T08~T15)."""
import unittest
from datetime import datetime, timedelta, timezone

from scripts.run_orderbook_recorder import Grid, parse_event

KST = timezone(timedelta(hours=9))
DATE = "20260911"


def at(hms: str) -> float:
    """'09:00:01.200' → epoch 초 (KST)."""
    return datetime.strptime(f"{DATE} {hms}", "%Y%m%d %H:%M:%S.%f" if "." in hms else "%Y%m%d %H:%M:%S") \
        .replace(tzinfo=KST).timestamp()


def iso(hms: str) -> str:
    return f"2026-09-11T{hms}+09:00"


def payload(item="KRX:005930", hms="09:00:00.500", **fids):
    return {"item": item, "_recv_ts": iso(hms), "41": "+70000", **fids}


def grid(end="10:00:00", tickers=("005930",), start="09:00:00.000"):
    g = Grid(DATE, end_ts=at(end), max_stale_sec=30)
    g.register(list(tickers))
    g.activate(list(tickers), at(start))
    return g


def feed(g, hms, ticker="005930", px="70000"):
    event = parse_event(payload(item=f"KRX:{ticker}", hms=hms, **{"41": px}), DATE, set(g.tickers()))
    g.on_event(event)


class ParseEventTest(unittest.TestCase):
    ACKED = {"005930", "0197V0"}

    def test_item_forms(self):
        for item, want in (("KRX:005930", "005930"), ("005930", "005930"),
                           ("A005930", "005930"), ("KRX:0197V0", "0197V0")):
            with self.subTest(item=item):
                self.assertEqual(parse_event(payload(item=item), DATE, self.ACKED)["ticker"], want)

    def test_other_venue_unregistered_or_bad_item_is_invalid(self):
        for item in ("NXT:005930_NX", "005930_NX", "SOR:005930_AL", "005930_AL",
                     "KRX:000660", "000660", "KRX:5930", ""):
            with self.subTest(item=item):
                self.assertIsNone(parse_event(payload(item=item), DATE, self.ACKED)["ticker"])

    def test_recv_ts_must_be_tz_aware_and_same_date(self):
        for stamp in ("2026-09-11T09:00:00.500", "2026-09-10T09:00:00.500+09:00", "garbage", None):
            with self.subTest(stamp=stamp):
                p = payload()
                p["_recv_ts"] = stamp
                self.assertIsNone(parse_event(p, DATE, self.ACKED)["ticker"])

    def test_book_and_invalid_field_count(self):
        event = parse_event(payload(**{"61": "abc", "51": "-69900"}), DATE, self.ACKED)
        self.assertEqual((event["book"]["ask1_px"], event["book"]["bid1_px"]), (70000, 69900))
        self.assertEqual(event["invalid_fields"], 1)
        self.assertEqual(event["recv_ts"], "09:00:00.500")


class GridTest(unittest.TestCase):
    def test_t01_two_tickers_make_separate_rows(self):
        g = grid(tickers=("005930", "000660"), start="08:59:59.500")     # 첫 격자 09:00:00
        feed(g, "08:59:59.700", "005930", "70000")
        feed(g, "08:59:59.800", "000660", "200000")
        rows = g.flush(at("09:00:00.300"))
        self.assertEqual(sorted((r["ticker"], r["ask1_px"]) for r in rows),
                         [("000660", 200000), ("005930", 70000)])
        self.assertEqual({r["ts"] for r in rows}, {"09:00:00"})

    def test_t08_hundred_events_in_one_second_make_one_row(self):
        g = grid()
        g.flush(at("09:00:00.000"))
        for i in range(100):
            feed(g, f"09:00:00.{i * 10:03d}", px=str(70000 + i))
        rows = g.flush(at("09:00:01.000"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ask1_px"], 70099)

    def test_t09_event_just_before_t_then_twenty_after(self):
        g = grid()
        g.flush(at("09:00:00.000"))
        feed(g, "09:00:00.900", px="1")                          # t=09:00:01 의 0.1초 전
        for i in range(20):
            feed(g, f"09:00:01.{(i + 1) * 40:03d}", px=str(100 + i))   # t 이후
        first = g.flush(at("09:00:01.050"))
        second = g.flush(at("09:00:02.050"))
        self.assertEqual((first[0]["ts"], first[0]["ask1_px"]), ("09:00:01", 1))
        self.assertEqual((second[0]["ts"], second[0]["ask1_px"]), ("09:00:02", 119))

    def test_same_recv_ts_uses_last_arrival(self):
        g = grid(start="09:00:00.600")                           # 첫 격자 09:00:01
        feed(g, "09:00:00.500", px="1")
        feed(g, "09:00:00.500", px="2")
        self.assertEqual(g.flush(at("09:00:01.100"))[0]["ask1_px"], 2)

    def test_t10_late_flush_skips_grids_without_backfill(self):
        g = grid()
        feed(g, "09:00:00.100")
        rows = g.flush(at("09:00:02.300"))                       # t=09:00:00 인데 2.3초 늦음
        self.assertEqual(rows, [])
        self.assertEqual(g.stats["skipped_grids"], 3)            # 00·01·02 건너뜀
        self.assertEqual(g.flush(at("09:00:03.100"))[0]["ts"], "09:00:03")

    def test_t11_freshness_boundary_is_inclusive(self):
        g = grid(start="09:00:00.000")
        feed(g, "09:00:00.000")
        g.flush(at("09:00:00.000"))
        for s in range(1, 30):
            g.flush(at(f"09:00:{s:02d}.000"))
        self.assertEqual(len(g.flush(at("09:00:30.000"))), 1)    # t - r = 30
        self.assertEqual(g.flush(at("09:00:31.000")), [])       # 31 > 30

    def test_t12_old_event_is_not_refreshed_by_arrival_time(self):
        g = grid(start="09:01:00.000")
        feed(g, "09:00:20.000")                                  # 40초 전에 받은 이벤트가 이제야 도착
        self.assertEqual(g.flush(at("09:01:00.100")), [])

    def test_t13_no_data_and_disconnect_clear(self):
        g = grid()
        self.assertEqual(g.flush(at("09:00:00.100")), [])
        feed(g, "09:00:00.500")
        g.clear()                                                # 단절 → 오래된 값을 30초간 들고 있지 않음
        self.assertEqual(g.flush(at("09:00:01.100")), [])

    def test_t14_first_grid_is_first_whole_second_after_activation(self):
        g = Grid(DATE, end_ts=at("10:00:00"), max_stale_sec=30)
        g.register(["005930"])
        feed(g, "08:45:01.100")                                  # 등록 직후 이미 받은 값도 사용
        g.activate(["005930"], at("08:45:01.200"))
        rows = g.flush(at("08:45:02.100"))
        self.assertEqual(rows[0]["ts"], "08:45:02")

    def test_new_ticker_gets_no_rows_before_activation(self):
        g = grid()
        feed(g, "08:59:59.900")
        g.register(["000660"])
        feed(g, "09:00:00.200", "000660")
        g.activate(["000660"], at("09:00:00.300"))              # 첫 격자 09:00:01
        self.assertEqual([r["ticker"] for r in g.flush(at("09:00:00.400"))], ["005930"])
        self.assertEqual(sorted(r["ticker"] for r in g.flush(at("09:00:01.100"))), ["000660", "005930"])

    def test_t15_window_end_is_exclusive(self):
        g = grid(end="09:00:02", start="09:00:00.000")
        feed(g, "09:00:00.000")
        self.assertEqual(len(g.flush(at("09:00:00.200"))), 1)
        self.assertEqual(len(g.flush(at("09:00:01.200"))), 1)
        self.assertIsNone(g.flush(at("09:00:02.200")))           # 09:00:02 = 종료 → 행 없음, 끝
        self.assertTrue(g.done)

    def test_never_activated_grid_ends_at_window_end(self):
        g = Grid(DATE, end_ts=at("10:00:00"), max_stale_sec=30)
        g.register(["005930"])
        self.assertEqual(g.flush(at("09:59:59.500")), [])
        self.assertIsNone(g.flush(at("10:00:00.200")))
        self.assertTrue(g.done)

    def test_late_event_counts_and_never_rewrites(self):
        g = grid()
        feed(g, "09:00:00.100", px="1")
        g.flush(at("09:00:00.200"))                              # t=09:00:00 확정
        feed(g, "08:59:59.900", px="9")                          # 확정 격자 이하 늦은 수신
        self.assertEqual(g.stats["late_events"], 1)
        self.assertEqual(g.flush(at("09:00:01.100"))[0]["ask1_px"], 1)   # 더 오래된 값이라 무시

    def test_unregistered_and_invalid_events_are_counted(self):
        g = grid()
        g.on_event(parse_event(payload(item="KRX:000660"), DATE, set(g.tickers())))
        g.on_event(parse_event(payload(**{"61": "x"}), DATE, set(g.tickers())))
        self.assertEqual(g.stats["invalid_events"], 1)
        self.assertEqual(g.stats["invalid_fields"], 1)

    def test_rows_insert_into_store_schema(self):
        import tempfile
        from pathlib import Path

        from scripts import orderbook_store as store
        from scripts.wl_sqlite import connect_rw

        g = grid(start="08:59:59.500")
        feed(g, "08:59:59.700")
        rows = g.flush(at("09:00:00.100"))
        with tempfile.TemporaryDirectory() as tmp, connect_rw(Path(tmp) / "ob.sqlite3") as con:
            store.ensure_schema(con)
            run_id = store.start_run(con, date=DATE, started_at="s", mode="morning", note={})
            store.write_round(con, run_id, rows, rows_written=0)
            got = con.execute("SELECT ticker, ts, recv_ts, ask1_px FROM orderbook_snapshot").fetchall()
        self.assertEqual(got, [("005930", "09:00:00", "08:59:59.700", 70000)])

    def test_first_receipt_is_reported_once_per_subscription(self):
        g = grid()
        feed(g, "09:00:00.100")
        feed(g, "09:00:00.200")
        self.assertEqual(g.pop_first_receipts(), {"005930": "2026-09-11T09:00:00.100+09:00"})
        self.assertEqual(g.pop_first_receipts(), {})
        g.clear()
        feed(g, "09:00:05.000")
        self.assertEqual(g.pop_first_receipts(), {"005930": "2026-09-11T09:00:05.000+09:00"})


if __name__ == "__main__":
    unittest.main()
