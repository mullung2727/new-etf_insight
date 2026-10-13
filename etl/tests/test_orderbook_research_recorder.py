"""호가·체결 리서치 녹화기 단위 테스트 (네트워크 없이).

Usage (from etl/):
    PYTHONPATH=. uv run python -m unittest tests.test_orderbook_research_recorder -v
"""
import asyncio
import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from scripts.run_orderbook_research_recorder import (
    BACKOFF,
    BrokerWatch,
    backoff,
    close_action,
    disk_ok,
    load_token,
    parse_real,
    pick_codes,
    run_recording,
)

KST = ZoneInfo("Asia/Seoul")

FIXED_NOW = datetime(2026, 10, 13, 10, 0, tzinfo=KST)
_fixed_now = lambda: FIXED_NOW

LOGIN_OK = {"trnm": "LOGIN", "return_code": 0, "return_msg": "ok"}
REG_OK = {"trnm": "REG", "return_code": 0, "return_msg": "ok"}
REMOVE_OK = {"trnm": "REMOVE", "return_code": 0, "return_msg": "ok"}

TRADE_VALUES = {
    "20": "090001", "10": "-1722000", "15": "-3",
    "13": "+12345", "27": "+1723000", "28": "-1722000",
}


def _book_values(**over):
    vals = {"21": "090002"}
    for i in range(1, 11):
        vals[str(40 + i)] = str(1750000 + i * 100)
        vals[str(50 + i)] = str(1749000 - i * 100)
        vals[str(60 + i)] = str(100 + i)
        vals[str(70 + i)] = str(200 + i)
    vals["121"] = "1000"
    vals["125"] = "2000"
    vals.update(over)
    return vals


async def _no_sleep(sec):
    return None


class FakeWs:
    """send로 받은 LOGIN/REG/REMOVE 요청에 그때 응답을 큐에 넣고 async for로 내보낸다.

    실제 키움처럼 요청 뒤에 응답하므로, 녹화기 Session._request가 Future를
    등록하기 전에 응답이 먼저 도착해 버려지는 일이 없다.
    frames로 미리 넣어둔 REAL 등은 요청 응답과 섞여 순서대로 나간다.
    응답을 다 내보낸 뒤에는 hang=False면 async for가 끝나 close_code로
    닫힌 것으로, hang=True면 닫히지 않고 대기한다(REG 응답 후 동작 선택).
    """

    def __init__(self, frames=(), close_code=1006, close_reason="", hang=False):
        self._queue = asyncio.Queue()
        for f in frames:
            self._queue.put_nowait(f if isinstance(f, str) else json.dumps(f))
        self.sent: list = []
        self.close_code = close_code
        self.close_reason = close_reason
        self.close_called = False
        self.hang = hang
        self._release = asyncio.Event()
        self._reg_answered = False

    async def send(self, data):
        self.sent.append(data)
        try:
            m = json.loads(data) if isinstance(data, str) else {}
        except ValueError:
            m = {}
        if isinstance(m, dict) and m.get("trnm") in ("LOGIN", "REG", "REMOVE"):
            self._queue.put_nowait(json.dumps(
                {"trnm": m["trnm"], "return_code": 0, "return_msg": "ok"}))
            if m.get("trnm") == "REG":
                self._reg_answered = True

    async def close(self):
        self.close_called = True
        self._release.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._queue.empty():
            return self._queue.get_nowait()
        if not self._reg_answered:
            get_task = asyncio.ensure_future(self._queue.get())
            rel_task = asyncio.ensure_future(self._release.wait())
            done, pending = await asyncio.wait(
                [get_task, rel_task],
                return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            if get_task in done and not get_task.cancelled():
                return get_task.result()
            raise StopAsyncIteration
        if self.hang:
            await self._release.wait()
        raise StopAsyncIteration


class FakeWatch:
    def __init__(self, connected=True, disconnect_after=None):
        self._connected = connected
        self.disconnect_after = disconnect_after
        self.forced = False
        self._calls = 0
        self.stopped = False

    def is_connected(self):
        return self._connected

    def wait_connected(self, timeout):
        return self._connected

    def has_disconnected(self):
        self._calls += 1
        if self.forced:
            return True
        if self.disconnect_after is not None and self._calls > self.disconnect_after:
            return True
        return False

    def sse_lost(self):
        return False

    def stop(self):
        self.stopped = True


def _reg_frames(wss):
    out = []
    for ws in wss:
        for raw in ws.sent:
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if isinstance(m, dict) and m.get("trnm") == "REG":
                out.append(m)
    return out


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class ParseRealCase(_TmpCase):
    def test_0b_example(self):
        msg = {"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": dict(TRADE_VALUES)}]}
        tr, bk = parse_real(msg, 123)
        self.assertEqual(tr, [(123, "005930", 90001, 1722000, -3, 12345, 1723000, 1722000)])
        self.assertEqual(bk, [])

    def test_0d_values(self):
        msg = {"trnm": "REAL", "data": [{"type": "0D", "item": "005930",
                                         "values": _book_values(**{"41": "+1750000", "51": "-1749000",
                                                                     "61": "", "62": " 0"})}]}
        tr, bk = parse_real(msg, 7)
        self.assertEqual(tr, [])
        self.assertEqual(len(bk), 1)
        b = bk[0]
        self.assertEqual((b[0], b[1], b[2]), (7, "005930", 90002))
        self.assertEqual(b[3], 1750000)  # ap1
        self.assertEqual(b[13], 1749000)  # bp1
        self.assertIsNone(b[23])  # aq1 빈 값
        self.assertEqual(b[24], 0)  # aq2 " 0"
        self.assertEqual(len(b), 45)

    def test_unknown_type_skipped(self):
        msg = {"trnm": "REAL", "data": [
            {"type": "XX", "item": "005930", "values": {"20": "090001"}},
            {"type": "0B", "values": {"20": "090001"}},  # item 없음
            {"type": "0B", "item": "005930", "values": "nope"},  # values 비dict
        ]}
        tr, bk = parse_real(msg, 1)
        self.assertEqual((tr, bk), ([], []))


class PickCodesCase(_TmpCase):
    def _make(self, rows):
        p = self.tmp / "watchlist.sqlite3"
        con = sqlite3.connect(p)
        try:
            con.execute("CREATE TABLE intraday_ranking (date TEXT, rank INTEGER, ticker TEXT,"
                        " name TEXT, volume INTEGER, close INTEGER)")
            con.executemany("INSERT INTO intraday_ranking VALUES (?,?,?,?,?,?)", rows)
            con.commit()
        finally:
            con.close()
        return p

    def test_yesterday_top30_no_future(self):
        rows = [(f"20261012", r, f"{r:06d}", f"n{r}", 100, 1000) for r in range(1, 36)]
        rows += [(f"20261013", r, f"9{r:05d}"[:6], f"m{r}", 999, 9999) for r in range(1, 31)]
        p = self._make(rows)
        src, codes = pick_codes(p, "20261013")
        self.assertEqual(src, "20261012")
        self.assertEqual(codes, [f"{r:06d}" for r in range(1, 31)])
        self.assertNotIn("20261013", [src])

    def test_too_old(self):
        rows = [("20261007", r, f"{r:06d}", "n", 1, 1) for r in range(1, 6)]
        p = self._make(rows)
        self.assertEqual(pick_codes(p, "20261013"), (None, []))

    def test_missing_db_creates_nothing(self):
        p = self.tmp / "watchlist.sqlite3"
        self.assertEqual(pick_codes(p, "20261013"), (None, []))
        self.assertFalse(p.exists())

    def test_bad_codes_filtered(self):
        rows = [("20261012", 1, "ABC", "n", 1, 1), ("20261012", 2, "005930", "n", 1, 1),
                ("20261012", 3, "0010F0", "n", 1, 1), ("20261012", 4, "0220W0", "n", 1, 1),
                ("20261012", 5, "00593", "n", 1, 1), ("20261012", 6, "abc123", "n", 1, 1)]
        p = self._make(rows)
        src, codes = pick_codes(p, "20261012")
        # today=20261012 → date<today 최신은 없음
        self.assertEqual((src, codes), (None, []))
        src, codes = pick_codes(p, "20261013")
        self.assertEqual((src, codes), ("20261012", ["005930", "0010F0", "0220W0"]))
        self.assertIn("0010F0", codes)
        self.assertNotIn("00593", codes)
        self.assertNotIn("abc123", codes)


class LoadTokenCase(_TmpCase):
    def _root(self, cache):
        root = self.tmp / "root"
        (root / "broker").mkdir(parents=True)
        if cache is not None:
            (root / "broker" / ".token_cache.json").write_text(json.dumps(cache), encoding="utf-8")
        return root

    def test_expired(self):
        root = self._root({"env": "paper", "token": "t", "expires_at": 1000})
        with mock.patch.dict(os.environ, {"KIWOOM_ENV": "paper"}):
            self.assertEqual(load_token(root, 2000), (None, None, "expired"))

    def test_env_mismatch(self):
        root = self._root({"env": "real", "token": "t", "expires_at": 10**12})
        with mock.patch.dict(os.environ, {"KIWOOM_ENV": "paper"}):
            self.assertEqual(load_token(root, 0), (None, None, "env_mismatch"))

    def test_unreadable(self):
        root = self._root(None)
        with mock.patch.dict(os.environ, {"KIWOOM_ENV": "paper"}):
            self.assertEqual(load_token(root, 0), (None, None, "cache_unreadable"))

    def test_ok_no_token_on_stdout(self):
        root = self._root({"env": "paper", "token": "SECRET-TOKEN-XYZ", "expires_at": 10**12})
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"KIWOOM_ENV": "paper"}):
            with contextlib.redirect_stdout(buf):
                url, token, why = load_token(root, 0)
        self.assertEqual(why, "ok")
        self.assertEqual(token, "SECRET-TOKEN-XYZ")
        self.assertTrue(url.startswith("wss://"))
        self.assertNotIn("SECRET-TOKEN-XYZ", buf.getvalue())


class CloseBackoffCase(unittest.TestCase):
    def test_close_action(self):
        self.assertEqual(close_action(1000, 0), "stop_kicked")
        self.assertEqual(close_action(1006, 0), "reconnect")
        self.assertEqual(close_action(1006, 20), "stop_limit")

    def test_backoff(self):
        self.assertEqual([backoff(i) for i in range(6)], [5, 10, 20, 60, 60, 60])
        self.assertEqual(len(BACKOFF), 4)


class LoopCase(_TmpCase):
    def _run(self, **kw):
        kw.setdefault("sleep_fn", _no_sleep)
        kw.setdefault("now_fn", _fixed_now)
        with mock.patch("asyncio.sleep", _no_sleep):
            return asyncio.run(run_recording(**kw))

    def _db_counts(self, db):
        con = sqlite3.connect(db)
        try:
            t = con.execute("SELECT COUNT(*) FROM trade").fetchone()[0]
            b = con.execute("SELECT COUNT(*) FROM book").fetchone()[0]
            g = con.execute("SELECT conn, start_rt, end_rt, reason FROM gaps").fetchall()
        finally:
            con.close()
        return t, b, g

    def test_a_broker_disconnected(self):
        ws = FakeWs(frames=[LOGIN_OK, REG_OK], hang=True)
        connects = []
        async def _connect(url):
            connects.append(url)
            return ws
        watch = FakeWatch(connected=True, disconnect_after=2)
        notify_m = mock.Mock()
        db = self.tmp / "day.sqlite3"
        res = self._run(date="20261013", codes=["005930"], db_path=db, watch=watch,
                        connect_fn=_connect,
                        load_token_fn=lambda: ("ws://fake", "tok", "ok"),
                        notify_fn=notify_m, out_dir=self.tmp)
        self.assertEqual(len(connects), 1)  # 추가 접속 0
        self.assertTrue(ws.close_called)  # 키움 close 호출
        self.assertEqual(res["reason"], "broker_disconnected")
        notify_m.assert_called_once()

    def test_b_kicked_1000(self):
        ws = FakeWs(frames=[LOGIN_OK, REG_OK], close_code=1000, close_reason="Bye")
        connects = []
        async def _connect(url):
            connects.append(url)
            return ws
        watch = FakeWatch(connected=True)
        db = self.tmp / "day.sqlite3"
        res = self._run(date="20261013", codes=["005930"], db_path=db, watch=watch,
                        connect_fn=_connect,
                        load_token_fn=lambda: ("ws://fake", "tok", "ok"),
                        notify_fn=mock.Mock(), out_dir=self.tmp)
        self.assertEqual(len(connects), 1)
        self.assertEqual(res["reason"], "stop_kicked")

    def test_c_limit_1006(self):
        connects = []
        async def _connect(url):
            connects.append(url)
            return FakeWs(frames=[], close_code=1006)
        watch = FakeWatch(connected=True)
        db = self.tmp / "day.sqlite3"
        res = self._run(date="20261013", codes=["005930"], db_path=db, watch=watch,
                        connect_fn=_connect,
                        load_token_fn=lambda: ("ws://fake", "tok", "ok"),
                        notify_fn=mock.Mock(), out_dir=self.tmp)
        self.assertEqual(len(connects), 21)  # 최초 1 + 재접속 20
        self.assertEqual(res["reason"], "stop_limit")
        self.assertEqual(res["reconnects"], 20)
        _, _, gaps = self._db_counts(db)
        self.assertTrue(gaps)  # gaps 행 기록됨

    def test_d_reconnect_reregisters(self):
        wss = []
        async def _connect(url):
            # 첫 연결: LOGIN·REG 응답 후 1006으로 닫힘, 두 번째: 응답 후 hang
            ws = FakeWs(close_code=1006, hang=(len(wss) == 1))
            orig_send = ws.send
            async def _send(data):
                await orig_send(data)
                try:
                    m = json.loads(data) if isinstance(data, str) else {}
                except ValueError:
                    m = {}
                if isinstance(m, dict) and m.get("trnm") == "REG" and len(_reg_frames(wss)) >= 2:
                    watch.forced = True
            ws.send = _send
            wss.append(ws)
            return ws
        watch = FakeWatch(connected=True)
        token_calls = []
        def _token():
            token_calls.append(1)
            return ("ws://fake", "tok", "ok")
        notify_m = mock.Mock()
        db = self.tmp / "day.sqlite3"
        res = self._run(date="20261013", codes=["005930"], db_path=db, watch=watch,
                        connect_fn=_connect, load_token_fn=_token,
                        notify_fn=notify_m, out_dir=self.tmp)
        self.assertEqual(len(_reg_frames(wss)), 2)  # REG 2번 전송
        self.assertEqual(len(token_calls), 2)  # 재접속 시 토큰 다시 읽음
        _, _, gaps = self._db_counts(db)
        rows = [g for g in gaps if g[3] == "close_1006"]
        self.assertEqual(len(rows), 1)
        self.assertLess(rows[0][1], rows[0][2])  # start_rt < end_rt
        self.assertEqual(res["reason"], "broker_disconnected")
        notify_m.assert_called_once()

    def test_e_no_broker_no_connect(self):
        connects = []
        async def _connect(url):
            connects.append(url)
            return FakeWs(frames=[LOGIN_OK, REG_OK], hang=True)
        watch = FakeWatch(connected=False)
        db = self.tmp / "day.sqlite3"
        res = self._run(date="20261013", codes=["005930"], db_path=db, watch=watch,
                        connect_fn=_connect,
                        load_token_fn=lambda: ("ws://fake", "tok", "ok"),
                        notify_fn=mock.Mock(), out_dir=self.tmp)
        self.assertEqual(connects, [])
        self.assertFalse(db.exists())

    def test_f_disk_low_no_start(self):
        connects = []
        async def _connect(url):
            connects.append(url)
            return FakeWs(frames=[LOGIN_OK, REG_OK], hang=True)
        watch = FakeWatch(connected=True)
        db = self.tmp / "day.sqlite3"
        res = self._run(date="20261013", codes=["005930"], db_path=db, watch=watch,
                        connect_fn=_connect,
                        load_token_fn=lambda: ("ws://fake", "tok", "ok"),
                        notify_fn=mock.Mock(), out_dir=self.tmp,
                        disk_ok_fn=lambda p: False)
        self.assertEqual(connects, [])
        self.assertEqual(res["reason"], "disk_low")


class StoreCase(_TmpCase):
    def test_append_same_day(self):
        real_t = {"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": dict(TRADE_VALUES)}]}
        real_b = {"trnm": "REAL", "data": [{"type": "0D", "item": "005930", "values": _book_values()}]}
        made = []
        def _factory():
            ws = FakeWs(frames=[LOGIN_OK, REG_OK, real_t, real_b], hang=True)
            made.append(ws)
            return ws
        async def _connect(url):
            return _factory()
        for _ in range(2):  # 같은 날 재실행
            watch = FakeWatch(connected=True, disconnect_after=3)
            db = self.tmp / "orderbook_raw_20261013.sqlite3"
            with mock.patch("asyncio.sleep", _no_sleep):
                res = asyncio.run(run_recording(
                    date="20261013", codes=["005930"], db_path=db, watch=watch,
                    connect_fn=_connect,
                    load_token_fn=lambda: ("ws://fake", "tok", "ok"),
                    notify_fn=mock.Mock(), out_dir=self.tmp, sleep_fn=_no_sleep, now_fn=_fixed_now))
            self.assertEqual(res["reason"], "broker_disconnected")
        con = sqlite3.connect(db)
        try:
            t = con.execute("SELECT COUNT(*) FROM trade").fetchone()[0]
            b = con.execute("SELECT COUNT(*) FROM book").fetchone()[0]
            row = con.execute("SELECT rt, code, t, price, qty, cum_vol, ask1, bid1 FROM trade"
                              " ORDER BY rowid LIMIT 1").fetchone()
        finally:
            con.close()
        self.assertEqual((t, b), (2, 2))  # 기존 행 유지 + 이어쓰기
        self.assertEqual(row[1:], ("005930", 90001, 1722000, -3, 12345, 1723000, 1722000))


class _FakeSSE:
    """BrokerWatch SSE 가짜 응답 (네트워크 없이 context manager 흉내)."""

    def __init__(self, lines=()):
        self._lines = list(lines)

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp

    def get(self, *args, **kwargs):
        return self._resp


class BrokerWatchCase(unittest.TestCase):
    def _start(self, lines, state_fn):
        resp = _FakeSSE(lines)
        patcher = mock.patch(
            "scripts.run_orderbook_research_recorder.requests.Session",
            return_value=_FakeSession(resp),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        w = BrokerWatch(state_fn=state_fn)
        w.start()
        w.join(timeout=5)
        self.addCleanup(lambda: (w.stop(), w.join(timeout=5)))
        return w

    def test_open_connected_true(self):
        w = self._start([], lambda: {"connected": True})
        self.assertTrue(w.wait_connected(1))

    def test_open_connected_false(self):
        w = self._start([], lambda: {"connected": False})
        self.assertFalse(w.wait_connected(1))
        self.assertTrue(w.sse_lost())

    def test_state_error_marks_lost(self):
        def _boom():
            raise RuntimeError("no broker")

        w = self._start([], _boom)
        self.assertTrue(w.sse_lost())

    def test_disconnected_event(self):
        w = self._start(['data: {"type":"disconnected"}'], lambda: {"connected": True})
        self.assertTrue(w.has_disconnected())

    def test_stream_end_marks_lost(self):
        w = self._start([], lambda: {"connected": True})
        self.assertTrue(w.sse_lost())


class NoTokenEndpointCase(unittest.TestCase):
    def test_no_token_issuance_string(self):
        from scripts import run_orderbook_research_recorder as rec

        text = Path(rec.__file__).read_text(encoding="utf-8")
        self.assertNotIn("oauth2", text.lower())


if __name__ == "__main__":
    unittest.main()
