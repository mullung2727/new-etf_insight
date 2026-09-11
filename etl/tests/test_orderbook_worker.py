"""호가 수집기 워커 생명주기 (SPEC §7·§11, T20·T21·T24·T25·T28·T29·T30).

실제 broker·SSE·시계 대신 가짜를 넣는다. FakeClock.sleep 이 시간을 진행시키고, 그때마다
SSE 로 0D 이벤트를 흘려 넣어 한 구간 전체를 몇 밀리초 안에 재현한다.
"""
import copy
import json
import re
import sqlite3
import tempfile
import time as real_time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import run_orderbook_recorder as rec
from scripts.orderbook_recorder_config import DEFAULTS

KST = timezone(timedelta(hours=9))
DATE = "20260911"


def ts(hms: str) -> float:
    return datetime.strptime(f"{DATE} {hms}", "%Y%m%d %H:%M:%S").replace(tzinfo=KST).timestamp()


class FakeClock:
    def __init__(self, start: float):
        self.t = start
        self.limit = start + 7200            # 무한 루프면 테스트가 멈추지 않고 실패하게
        self.on_sleep = None

    def now(self) -> float:
        return self.t

    def sleep(self, sec: float) -> None:
        real_time.sleep(0.001)               # 등록·REST 스레드가 돌 틈을 준다(가짜 시계라 실제 대기가 없다)
        self.t += max(sec, 0.001)
        if self.t > self.limit:
            raise AssertionError("worker did not stop (clock ran 2h past start)")
        if self.on_sleep:
            self.on_sleep(self.t)


class FakeBroker:
    def __init__(self, fail_post=0):
        self.calls: list[tuple] = []
        self.codes: set[str] = set()
        self.fail_post = fail_post

    def get_orderbook(self, timeout):
        self.calls.append(("GET",))
        return {"connected": True, "venue": "KRX", "codes": sorted(self.codes)}

    def delete_orderbook(self, timeout):
        self.calls.append(("DELETE",))
        self.codes.clear()
        return {"connected": True, "venue": "KRX", "codes": []}

    def post_orderbook(self, codes, timeout):
        self.calls.append(("POST", tuple(codes)))
        if self.fail_post:
            self.fail_post -= 1
            raise rec.BrokerError(503, "REG 0D: websocket not connected")
        self.codes.update(codes)
        return {"connected": True, "venue": "KRX", "codes": sorted(self.codes)}

    def quote_orderbook(self, code, timeout):
        self.calls.append(("QUOTE", code))
        return {"sel_fpr_bid": "+70000", "buy_fpr_bid": "-69900"}

    def posts(self):
        return [c[1] for c in self.calls if c[0] == "POST"]


class FakeSSE:
    def __init__(self, url, on_message, on_stream):
        self.on_message, self.on_stream = on_message, on_stream
        self.stopped = False

    def start(self):
        self.on_stream(True)
        self.on_message({"channel": "system", "payload": {"type": "connected"}})

    def stop(self):
        self.stopped = True

    def tick(self, t: float, tickers):
        stamp = datetime.fromtimestamp(t, KST).isoformat(timespec="milliseconds")
        for code in tickers:
            self.on_message({"channel": "0D", "payload": {
                "item": f"KRX:{code}", "_recv_ts": stamp, "41": "+70000", "51": "-69900"}})


class WorkerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db, self.lock = root / "orderbook.sqlite3", root / "orderbook.lock"
        self.wl, self.scores = root / "watchlist.sqlite3", root / "scores.json"
        con = sqlite3.connect(self.wl)
        con.execute("CREATE TABLE llm_scores (date TEXT, ticker TEXT, score INTEGER, PRIMARY KEY (date, ticker))")
        con.execute("CREATE TABLE close_bet_orders (date TEXT, ticker TEXT, status TEXT, sell_status TEXT)")
        con.executemany("INSERT INTO llm_scores VALUES (?,?,?)",
                        [("20260910", "005930", 50), ("20260910", "000660", 60)])
        con.commit()
        con.close()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["scoring_result_path"] = self.scores
        self.broker = FakeBroker()
        self.sse: FakeSSE | None = None
        self.feed = True

    def tearDown(self):
        self.tmp.cleanup()

    def sse_factory(self, url, on_message, on_stream):
        self.sse = FakeSSE(url, on_message, on_stream)
        return self.sse

    def run_at(self, hms, trading=(True, "평일"), on_sleep=None):
        self.clock = FakeClock(ts(hms))

        def tick(t):
            if on_sleep:
                on_sleep(t)
            if self.sse and self.feed:
                self.sse.tick(t - 0.05, sorted(self.broker.codes))
        self.clock.on_sleep = tick
        return rec.run(self.cfg, clock=self.clock, broker=self.broker, sse_factory=self.sse_factory,
                       db_path=self.db, lock_path=self.lock, watchlist_db=self.wl,
                       trading_day=lambda d: trading)

    def runs(self):
        if not self.db.exists():
            return []
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        out = [dict(r) for r in con.execute("SELECT * FROM orderbook_run ORDER BY run_id")]
        con.close()
        for r in out:
            r["note"] = json.loads(r["note"]) if r["note"] else None
        return out

    def snapshot_count(self, ticker=None):
        con = sqlite3.connect(self.db)
        if ticker:
            n = con.execute("SELECT count(*) FROM orderbook_snapshot WHERE ticker=?", [ticker]).fetchone()[0]
        else:
            n = con.execute("SELECT count(*) FROM orderbook_snapshot").fetchone()[0]
        con.close()
        return n

    def delay_after_first_post(self, sec: float):
        """두 번째 POST 부터 가짜 시계로 sec 초 동안 응답을 붙잡는다(메인 루프가 멈추면 시계도 멈춘다)."""
        fast, seen = self.broker.post_orderbook, []

        def slow(codes, timeout):
            if self.broker.posts():
                start, deadline = self.clock.t, real_time.monotonic() + 2
                while self.clock.t < start + sec and real_time.monotonic() < deadline:
                    real_time.sleep(0.001)
                seen.append(self.clock.t - start)
            return fast(codes, timeout)
        self.broker.post_orderbook = slow
        return seen


class HelperTest(unittest.TestCase):
    def test_iter_sse_joins_data_lines_and_skips_comments(self):
        lines = ['data: {"a": 1}', "", ": ping", "", "data: x", "data: y", "", "event: only", "", "data: cut"]
        self.assertEqual(list(rec.iter_sse(lines)), ['{"a": 1}', "x\ny"])

    def test_choose_window(self):
        cfg = copy.deepcopy(DEFAULTS)
        for hms, want in (("08:44:00", "morning"), ("09:59:59", "morning"), ("10:00:00", "afternoon"),
                          ("15:29:59", "afternoon"), ("15:30:00", None)):
            with self.subTest(hms=hms):
                self.assertEqual(rec.choose_window(cfg, ts(hms)), want)

    def test_t25_lock_blocks_second_holder_and_frees_on_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "orderbook.lock"
            first = rec.acquire_lock(path)
            self.assertIsNotNone(first)
            self.assertIsNone(rec.acquire_lock(path))
            first.close()                                       # 프로세스 종료와 같은 효과
            again = rec.acquire_lock(path)
            self.assertIsNotNone(again)
            again.close()

    def test_t30_only_realtime_and_quote_endpoints(self):
        source = Path(rec.__file__).read_text(encoding="utf-8")
        paths = set(re.findall(r'"(/[A-Za-z_/{}]+)', source))
        self.assertEqual(paths, {"/realtime/orderbook", "/quotes/{code}/orderbook", "/events"})
        self.assertNotIn("/orders", source)


class NoSubscriptionTest(WorkerBase):
    def test_t29_disabled(self):
        self.cfg["enabled"] = False
        self.assertEqual(self.run_at("08:44:00"), 0)
        self.assertEqual((self.broker.calls, self.runs()), ([], []))

    def test_t29_after_close(self):
        self.assertEqual(self.run_at("15:30:00"), 0)
        self.assertEqual(self.broker.calls, [])

    def test_t29_holiday_records_reason(self):
        self.assertEqual(self.run_at("08:44:00", trading=(False, "추석")), 0)
        self.assertEqual(self.broker.calls, [])
        [run] = self.runs()
        self.assertEqual(run["note"]["reason"], "holiday")
        self.assertIsNotNone(run["ended_at"])

    def test_t25_duplicate_run_exits_without_run_row(self):
        held = rec.acquire_lock(self.lock)
        try:
            self.assertEqual(self.run_at("08:44:00"), 0)
        finally:
            held.close()
        self.assertEqual((self.broker.calls, self.runs()), ([], []))

    def test_t28_afternoon_never_ready(self):
        self.assertEqual(self.run_at("15:29:50"), 0)
        self.assertFalse([c for c in self.broker.calls if c[0] == "POST"])
        self.assertEqual(self.runs()[0]["note"]["reason"], "candidate_not_ready")


class MorningRunTest(WorkerBase):
    def test_full_window(self):
        code = self.run_at("09:59:30")
        self.assertEqual(code, 0)
        kinds = [c[0] for c in self.broker.calls]
        self.assertEqual(kinds[:3], ["GET", "DELETE", "POST"])          # 새 프로세스 최초 등록
        self.assertEqual(kinds[-1], "DELETE")                           # finally 해제
        self.assertEqual(self.broker.posts()[0], ("000660", "005930"))
        self.assertEqual(kinds.count("QUOTE"), 1)                       # REST 대조 1회
        [run] = self.runs()
        self.assertEqual((run["mode"], run["venue"], run["symbols"], run["source_date"]),
                         ("morning", "KRX", "000660,005930", "20260910"))
        self.assertEqual(run["note"]["reason"], "window_end")
        self.assertEqual(run["rows_written"], self.snapshot_count())
        self.assertGreater(run["rows_written"], 40)                     # 2종목 × 약 29초
        subs = run["note"]["subscriptions"]
        self.assertEqual({s["ticker"] for s in subs}, {"000660", "005930"})
        self.assertTrue(all(s["first_recv_ts"] and s["end_reason"] == "window_end" for s in subs))
        self.assertTrue(self.sse.stopped)

    def test_reg_retry_exhausted_exits_1(self):
        self.broker.fail_post = 99
        self.assertEqual(self.run_at("09:59:00"), 1)
        self.assertEqual(len(self.broker.posts()), self.cfg["reg_retry"] + 1)
        self.assertEqual(self.runs()[0]["note"]["reason"], "reg_failed")

    def test_slow_restore_post_keeps_saving_existing_tickers(self):
        seen = self.delay_after_first_post(3)
        state = {"dropped": False}

        def drop_once(t):
            if not state["dropped"] and t >= ts("09:59:20"):
                state["dropped"] = True
                self.sse.on_stream(False)
                self.sse.on_stream(True)
                self.sse.on_message({"channel": "system", "payload": {"type": "connected"}})
        self.assertEqual(self.run_at("09:59:00", on_sleep=drop_once), 0)
        self.assertGreaterEqual(seen[0], 3)                     # 등록이 3초 붙잡혀도 시계(=저장 루프)는 진행
        run = self.runs()[0]
        self.assertEqual(run["note"]["stats"]["skipped_grids"], 0)
        self.assertGreaterEqual(run["rows_written"], 2 * 55)    # 기존 2종목 수집이 등록 대기 동안 이어짐
        self.assertEqual(len(run["note"]["subscriptions"]), 4)  # 복구 등록은 결국 반영

    def test_slow_failing_restore_post_retries_off_the_main_loop(self):
        seen = self.delay_after_first_post(2)
        state = {"dropped": False}

        def drop_once(t):
            if not state["dropped"] and t >= ts("09:59:20"):
                state["dropped"] = True
                self.broker.fail_post = 2
                self.sse.on_stream(False)
                self.sse.on_stream(True)
                self.sse.on_message({"channel": "system", "payload": {"type": "connected"}})
        self.assertEqual(self.run_at("09:59:00", on_sleep=drop_once), 0)
        self.assertEqual(len(self.broker.posts()), 1 + 3)       # 기존 재시도 정책 그대로
        self.assertTrue(all(s >= 2 for s in seen))
        self.assertEqual(self.runs()[0]["note"]["stats"]["skipped_grids"], 0)

    def test_slow_rest_check_does_not_block_grid_loop(self):
        """REST 가 응답을 붙잡고 있는 동안에도 저장 루프(가짜 시계)는 계속 진행해야 한다."""
        fast = self.broker.quote_orderbook
        advanced = []

        def blocked_quote(code, timeout):
            start, deadline = self.clock.t, real_time.monotonic() + 2
            while self.clock.t < start + 3 and real_time.monotonic() < deadline:
                real_time.sleep(0.001)                          # 메인 스레드에서 불렸다면 시계가 멈춰 있다
            advanced.append(self.clock.t - start)
            return fast(code, timeout)
        self.broker.quote_orderbook = blocked_quote
        self.assertEqual(self.run_at("09:59:30"), 0)
        self.assertGreaterEqual(advanced[0], 3)
        run = self.runs()[0]
        self.assertEqual(run["note"]["stats"]["skipped_grids"], 0)
        self.assertGreater(run["rows_written"], 40)

    def test_restore_retry_does_not_block_grid(self):
        state = {"dropped": False}

        def drop_once(t):
            if not state["dropped"] and t >= ts("09:59:40"):
                state["dropped"] = True
                self.broker.fail_post = 2                          # 복구 POST 가 두 번 실패
                self.sse.on_stream(False)
                self.sse.on_stream(True)
                self.sse.on_message({"channel": "system", "payload": {"type": "connected"}})
        self.assertEqual(self.run_at("09:59:30", on_sleep=drop_once), 0)
        self.assertEqual(len(self.broker.posts()), 1 + 3)
        self.assertEqual(self.runs()[0]["note"]["stats"]["skipped_grids"], 0)

    def test_window_end_during_first_registration_retry_stops(self):
        """09:59:59 첫 등록 실패 → 재시도 대기 중 10:00 → 등록 못 한 채 끝나야 한다(무한 루프 금지)."""
        self.broker.fail_post = 99
        self.assertEqual(self.run_at("09:59:59"), 1)
        self.assertLess(self.clock.t, ts("10:00:05"))
        self.assertEqual(self.runs()[0]["note"]["reason"], "reg_failed")
        self.assertEqual([c[0] for c in self.broker.calls][-1], "DELETE")

    def test_retry_then_success(self):
        self.broker.fail_post = 2
        self.assertEqual(self.run_at("09:59:30"), 0)
        self.assertEqual(len(self.broker.posts()), 3)

    def test_t24_reconnect_reposts_full_list_without_delete(self):
        events = []

        def drop_and_back(t):
            if not events and t >= ts("09:59:45"):
                events.append(t)
                self.sse.on_stream(False)                               # broker 재시작
                self.sse.on_stream(True)
                self.sse.on_message({"channel": "system", "payload": {"type": "connected"}})
        self.assertEqual(self.run_at("09:59:30", on_sleep=drop_and_back), 0)
        kinds = [c[0] for c in self.broker.calls]
        self.assertEqual(kinds.count("DELETE"), 2)                      # 최초 정리 + finally 만
        self.assertEqual(self.broker.posts(), [("000660", "005930")] * 2)
        subs = self.runs()[0]["note"]["subscriptions"]
        self.assertEqual(len(subs), 4)                                  # 구독 구간이 재연결마다 추가
        self.assertEqual(sorted(s["end_reason"] for s in subs)[:2], ["disconnected", "disconnected"])


class AfternoonRunTest(WorkerBase):
    def write_scores(self, tickers, stamp="2026-09-11T15:02:38+09:00"):
        con = sqlite3.connect(self.wl)
        con.executemany("INSERT OR REPLACE INTO llm_scores VALUES (?,?,?)",
                        [(DATE, t, 40) for t in tickers])
        con.commit()
        con.close()
        doc = {"generated_at": stamp, "db_write": True,
               "results": [{"date": DATE, "candidate_count": len(tickers), "scored_count": len(tickers),
                            "scores": [{"date": DATE, "ticker": t, "probability_score": 40} for t in tickers]}]}
        self.scores.write_text(json.dumps(doc), encoding="utf-8")

    def test_t20_t21_ready_then_new_file_adds_only_new(self):
        self.write_scores(["0011A0"])
        added = []

        def new_file(t):
            if not added and t >= ts("15:29:40"):
                added.append(t)
                self.write_scores(["0011A0", "446540"], stamp="2026-09-11T15:29:39+09:00")
        self.assertEqual(self.run_at("15:29:30", on_sleep=new_file), 0)
        self.assertEqual(self.broker.posts(), [("0011A0",), ("446540",)])
        self.assertEqual([c[0] for c in self.broker.calls].count("DELETE"), 2)   # 구간 중 REMOVE 없음
        [run] = self.runs()
        self.assertEqual((run["source_date"], run["symbols"]), (DATE, "0011A0,446540"))
        updates = run["note"]["source_updates"]
        self.assertEqual([u["selected"] for u in updates], [["0011A0"], ["446540"]])
        self.assertTrue(all(u["sha256"] and u["ready_observed_at"] for u in updates))

    def test_slow_addition_post_keeps_saving_existing_ticker(self):
        """_poll_additions → schedule → 등록 스레드 경로. 추가 POST 가 3초 붙잡혀도 기존 종목 저장은 이어진다."""
        self.write_scores(["0011A0"])
        seen = self.delay_after_first_post(3)
        added = []

        def new_file(t):
            if not added and t >= ts("15:29:20"):
                added.append(t)
                self.write_scores(["0011A0", "446540"], stamp="2026-09-11T15:29:19+09:00")
        self.assertEqual(self.run_at("15:29:00", on_sleep=new_file), 0)
        self.assertGreaterEqual(seen[0], 3)
        self.assertEqual(self.broker.posts(), [("0011A0",), ("446540",)])
        [run] = self.runs()
        self.assertEqual(run["note"]["stats"]["skipped_grids"], 0)
        self.assertGreaterEqual(self.snapshot_count("0011A0"), 55)   # 추가 대기 동안에도 매초 저장
        self.assertIn("446540", {s["ticker"] for s in run["note"]["subscriptions"]})
        self.assertGreater(self.snapshot_count("446540"), 0)
        self.assertEqual(run["symbols"], "0011A0,446540")


if __name__ == "__main__":
    unittest.main()
