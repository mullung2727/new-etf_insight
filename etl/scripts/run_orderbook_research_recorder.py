"""호가·체결 리서치 녹화기 — 직전 거래일 거래량 탑30의 0B+0D를 08:30~20:00 녹화.

설계: docs/PLAN_ORDERBOOK_RESEARCH_RECORDING.md §4·§5·§6.
실행 (etl/ 에서): uv run python scripts/run_orderbook_research_recorder.py [--date YYYYMMDD]

실매매 보호 (어떤 경우에도 지킨다):
1. broker SSE connected를 모르면 키움 웹소켓을 열지 않는다.
2. broker SSE에서 disconnected를 1번이라도 받거나 SSE가 끊기면 키움 연결을 닫고 그날 녹화 종료(재접속 금지).
3. 녹화 연결이 close 1000으로 닫히면 충돌로 보고 그날 녹화 종료(재접속 금지).
4. 토큰은 broker/.token_cache.json 읽기만. 토큰 발급·REST 호출 없음. HTTP 요청은 로컬 broker(:8001) SSE 와 시작 시 상태 조회 1회뿐, 키움 REST 호출 없음.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  (cp949 가드 + path)

import argparse
import asyncio
import json
import msvcrt
import os
import re
import shutil
import sqlite3
import threading
import time
import traceback
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

import requests

from notify import notify
from check_krx_trading_day import trading_day_status
from wl_sqlite import connect_ro

KST = ZoneInfo("Asia/Seoul")

REPO_ROOT = Path(__file__).resolve().parents[2]  # new-etf_insight/
ETL_ROOT = Path(__file__).resolve().parents[1]  # etl/
OUT_DIR = ETL_ROOT / "db" / "orderbook_research"
LOCK_PATH = OUT_DIR / "recorder.lock"

WINDOW_START = dt_time(8, 30)
WINDOW_END = dt_time(20, 0)  # KST
BROKER_URL = "http://127.0.0.1:8001"
N_CODES = 30
MAX_LOOKBACK_DAYS = 5
BACKOFF = (5, 10, 20, 60)  # 초 — n번째 재접속 대기
MAX_RECONNECTS = 20  # 하루
MIN_FREE_GB = 5
DISK_CHECK_SEC = 600
FLUSH_SEC = 1.0  # sqlite 일괄 쓰기 주기
GRP_NO = "1"
TYPES = ["0B", "0D"]
TOKEN_SKEW = 600
ACK_TIMEOUT = 5.0
WS_URLS = {
    "real": "wss://api.kiwoom.com:10000/api/dostk/websocket",
    "paper": "wss://mockapi.kiwoom.com:10000/api/dostk/websocket",
}

CODE_RE = re.compile(r"^[0-9A-Z]{6}$")

BOOK_COLS = (
    ["rt", "code", "t"]
    + [f"ap{i}" for i in range(1, 11)]
    + [f"bp{i}" for i in range(1, 11)]
    + [f"aq{i}" for i in range(1, 11)]
    + [f"bq{i}" for i in range(1, 11)]
    + ["ask_tot", "bid_tot"]
)
TRADE_INSERT = "INSERT INTO trade (rt, code, t, price, qty, cum_vol, ask1, bid1) VALUES (?,?,?,?,?,?,?,?)"
BOOK_INSERT = f"INSERT INTO book ({', '.join(BOOK_COLS)}) VALUES ({', '.join('?' * len(BOOK_COLS))})"


# ── 순수 함수 ──────────────────────────────────────────────────────────

def _int(s):
    """공백·+ 제거, -는 부호 유지. 빈 문자열·파싱 불가 → None."""
    if s is None:
        return None
    t = str(s).replace(" ", "").replace("+", "")
    if t in ("", "-"):
        return None
    try:
        return int(t)
    except (ValueError, TypeError):
        return None


def _abs(s):
    v = _int(s)
    return None if v is None else abs(v)


def parse_real(msg: dict, rt: int) -> tuple[list[tuple], list[tuple]]:
    """REAL 프레임 → (trade 행, book 행). rt는 수신 시각 ns. 모르는 항목은 건너뜀."""
    trades: list[tuple] = []
    books: list[tuple] = []
    data = msg.get("data") if isinstance(msg, dict) else None
    if not isinstance(data, list):
        return trades, books
    for entry in data:
        if not isinstance(entry, dict):
            continue
        typ = entry.get("type")
        code = entry.get("item")
        vals = entry.get("values")
        if not isinstance(code, str) or not code or not isinstance(vals, dict):
            continue
        if typ == "0B":
            trades.append((
                rt, code,
                _int(vals.get("20")), _abs(vals.get("10")),
                _int(vals.get("15")), _int(vals.get("13")),
                _abs(vals.get("27")), _abs(vals.get("28")),
            ))
        elif typ == "0D":
            row = [rt, code, _int(vals.get("21"))]
            row += [_abs(vals.get(str(f))) for f in range(41, 51)]
            row += [_abs(vals.get(str(f))) for f in range(51, 61)]
            row += [_int(vals.get(str(f))) for f in range(61, 71)]
            row += [_int(vals.get(str(f))) for f in range(71, 81)]
            row += [_int(vals.get("121")), _int(vals.get("125"))]
            books.append(tuple(row))
    return trades, books


def pick_codes(db_path, today: str) -> tuple[str | None, list[str]]:
    """watchlist intraday_ranking에서 today보다 앞선 최신 date의 탑30. (source_date, codes)."""
    db_path = Path(db_path)
    if not db_path.is_file():
        return (None, [])
    try:
        anchor = datetime.strptime(today, "%Y%m%d").date()
    except ValueError:
        return (None, [])
    try:
        with connect_ro(db_path) as con:
            row = con.execute(
                "SELECT date FROM intraday_ranking WHERE date < ? ORDER BY date DESC LIMIT 1",
                (today,),
            ).fetchone()
            if row is None or row[0] is None:
                return (None, [])
            src = str(row[0])
            try:
                if (anchor - datetime.strptime(src, "%Y%m%d").date()).days > MAX_LOOKBACK_DAYS:
                    return (None, [])
            except ValueError:
                return (None, [])
            tickers = [
                r[0] for r in con.execute(
                    "SELECT ticker FROM intraday_ranking WHERE date = ? ORDER BY rank ASC",
                    (src,),
                ).fetchall()
            ]
    except Exception:
        return (None, [])
    codes = [str(t) for t in tickers if isinstance(t, str) and CODE_RE.match(t)]
    if not codes:
        return (None, [])
    return (src, codes[:N_CODES])


def load_token(root: Path, now: float) -> tuple[str | None, str | None, str]:
    """broker 토큰 캐시 읽기만. (ws_url, token, reason). 예외를 밖으로 던지지 않는다."""
    try:
        want = (os.environ.get("KIWOOM_ENV") or "paper").strip().lower() or "paper"
        raw = (Path(root) / "broker" / ".token_cache.json").read_text(encoding="utf-8")
        cache = json.loads(raw)
        if not isinstance(cache, dict):
            return (None, None, "cache_unreadable")
        env = str(cache.get("env", "")).strip().lower()
        token = cache.get("token")
        if not token or not isinstance(token, str):
            return (None, None, "cache_unreadable")
        if env != want:
            return (None, None, "env_mismatch")
        try:
            exp = float(cache.get("expires_at") or 0)
        except (TypeError, ValueError):
            exp = 0
        if exp - TOKEN_SKEW <= now:
            return (None, None, "expired")
        return (WS_URLS.get(want, WS_URLS["paper"]), token, "ok")
    except Exception:
        return (None, None, "cache_unreadable")


def close_action(code, reconnects_so_far: int) -> str:
    if code == 1000:
        return "stop_kicked"
    if reconnects_so_far >= MAX_RECONNECTS:
        return "stop_limit"
    return "reconnect"


def backoff(n: int) -> int:
    return BACKOFF[min(n, len(BACKOFF) - 1)]


def disk_ok(path: Path) -> bool:
    return shutil.disk_usage(path).free >= MIN_FREE_GB * 1024**3


# ── 저장 ───────────────────────────────────────────────────────────────

def open_day_db(path: Path) -> sqlite3.Connection:
    """일 파일 C: sqlite. 이미 있으면 이어 쓴다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS trade (rt INTEGER, code TEXT, t INTEGER,"
        " price INTEGER, qty INTEGER, cum_vol INTEGER, ask1 INTEGER, bid1 INTEGER)"
    )
    defs = ", ".join(f"{c} {'TEXT' if c == 'code' else 'INTEGER'}" for c in BOOK_COLS)
    con.execute(f"CREATE TABLE IF NOT EXISTS book ({defs})")
    con.execute(
        "CREATE TABLE IF NOT EXISTS gaps (conn TEXT, start_rt INTEGER, end_rt INTEGER, reason TEXT)"
    )
    con.commit()
    return con


# ── broker 감시 ────────────────────────────────────────────────────────

class BrokerWatch(threading.Thread):
    """broker system SSE 감시. 한번 붙은 뒤 끊기면 재연결 없이 sse_lost 기록 후 종료."""

    def __init__(self, url: str = BROKER_URL, state_fn=None) -> None:
        super().__init__(daemon=True, name="research-broker-watch")
        self._base = url.rstrip("/")
        self._url = self._base + "/events?ch=system"
        if state_fn is not None:
            self._state_fn = state_fn
        else:
            base = self._base

            def _default_state():
                return requests.get(base + "/realtime/orderbook", timeout=5).json()

            self._state_fn = _default_state
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._connected_evt = threading.Event()
        self._connected = False
        self.disconnected_at: float | None = None
        self.sse_lost_at: float | None = None
        self._ever_opened = False

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def wait_connected(self, timeout: float) -> bool:
        return self._connected_evt.wait(timeout)

    def has_disconnected(self) -> bool:
        with self._lock:
            return self.disconnected_at is not None

    def sse_lost(self) -> bool:
        with self._lock:
            return self.sse_lost_at is not None

    def stop(self) -> None:
        self._stop_evt.set()

    def _mark_connected(self) -> None:
        with self._lock:
            self._connected = True
        self._connected_evt.set()

    def _mark_disconnected(self) -> None:
        with self._lock:
            if self.disconnected_at is None:
                self.disconnected_at = time.time()

    def _mark_lost(self) -> None:
        with self._lock:
            if self.sse_lost_at is None:
                self.sse_lost_at = time.time()

    def run(self) -> None:
        session = requests.Session()
        while not self._stop_evt.is_set():
            in_stream = False
            try:
                with session.get(self._url, stream=True, timeout=(5, 75)) as resp:
                    resp.raise_for_status()
                    try:
                        st = self._state_fn()
                    except Exception:
                        self._mark_lost()
                        return
                    if not isinstance(st, dict) or st.get("connected") is not True:
                        self._mark_lost()
                        return
                    self._mark_connected()
                    in_stream = True
                    with self._lock:
                        self._ever_opened = True
                    for line in resp.iter_lines(decode_unicode=True):
                        if self._stop_evt.is_set():
                            return
                        if not line:
                            continue
                        s = line.strip() if isinstance(line, str) else line
                        if not s.startswith("data:"):
                            continue
                        payload = s[5:].strip()
                        if not payload:
                            continue
                        try:
                            data = json.loads(payload)
                        except ValueError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        t = data.get("type")
                        # {"type":"connected"} 무시 (시작 시 상태 조회로 이미 판정)
                        if t == "disconnected":
                            self._mark_disconnected()
                # 붙은 뒤 스트림이 끝나면 broker 상태를 모름 → 양보
                self._mark_lost()
                return
            except Exception:
                if in_stream or self._ever_opened:
                    self._mark_lost()
                    return
                if self._stop_evt.is_set():
                    return
                self._stop_evt.wait(2.0)  # 첫 연결 전 실패만 재시도


# ── 키움 세션 ──────────────────────────────────────────────────────────

def _default_connect(url):
    import websockets

    return websockets.connect(url, open_timeout=10)


def login_frame(token: str) -> dict:
    return {"trnm": "LOGIN", "token": token}


def reg_frame(codes, types=TYPES, grp_no: str = GRP_NO) -> dict:
    return {
        "trnm": "REG",
        "grp_no": grp_no,
        "refresh": "1",
        "data": [{"item": list(codes), "type": list(types)}],
    }


def remove_frame(codes, types=TYPES, grp_no: str = GRP_NO) -> dict:
    return {
        "trnm": "REMOVE",
        "grp_no": grp_no,
        "data": [{"item": list(codes), "type": list(types)}],
    }


class Session:
    """키움 웹소켓 1개. 단일 recv task, LOGIN/REG/REMOVE는 Future로 응답 대기."""

    def __init__(self, url: str, token: str, connect_fn=None, on_real=None) -> None:
        self.url = url
        self.token = token
        self._connect_fn = connect_fn or _default_connect
        self._on_real = on_real
        self.ws = None
        self._recv_task: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._closed = asyncio.Event()
        self._closing = False
        self.close_code = None
        self.close_reason: str | None = None

    @property
    def server_closed(self) -> bool:
        return self._closed.is_set() and not self._closing

    async def connect(self) -> None:
        self.ws = await self._connect_fn(self.url)
        self._closed.clear()
        self._closing = False
        self.close_code = None
        self.close_reason = None
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def _recv_loop(self) -> None:
        try:
            async for raw in self.ws:
                text = raw if isinstance(raw, str) else bytes(raw).decode("utf-8", errors="replace")
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                trnm = msg.get("trnm")
                if trnm == "PING":
                    try:
                        await self.ws.send(raw)  # 받은 원문 그대로 즉시 echo
                    except Exception:
                        pass
                elif trnm == "REAL":
                    if self._on_real is not None:
                        try:
                            self._on_real(text, msg)
                        except Exception:
                            pass
                elif trnm in ("LOGIN", "REG", "REMOVE"):
                    fut = self._pending.pop(trnm, None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.close_code is None:
                self.close_code = getattr(exc, "code", None)
                rsn = getattr(exc, "reason", None)
                self.close_reason = str(rsn if rsn is not None else exc)[:300]
        finally:
            try:
                if self.ws is not None and self.close_code is None:
                    self.close_code = getattr(self.ws, "close_code", None)
                    rc = getattr(self.ws, "close_reason", None)
                    if rc is not None:
                        self.close_reason = str(rc)[:300]
            except Exception:
                pass
            self._closed.set()
            for fut in list(self._pending.values()):
                if not fut.done():
                    try:
                        fut.set_exception(RuntimeError("ws closed"))
                    except Exception:
                        pass
            self._pending.clear()

    async def _request(self, frame: dict, expect: str) -> dict:
        fut = asyncio.get_running_loop().create_future()
        self._pending[expect] = fut
        await self.ws.send(json.dumps(frame, ensure_ascii=False))
        closed_task = asyncio.create_task(self._closed.wait())
        try:
            try:
                done, _ = await asyncio.wait_for(
                    asyncio.wait({fut, closed_task}, return_when=asyncio.FIRST_COMPLETED),
                    ACK_TIMEOUT,
                )
            except asyncio.TimeoutError:
                raise
            finally:
                self._pending.pop(expect, None)
            if fut in done:
                return fut.result()
            raise RuntimeError("ws closed")
        finally:
            if not closed_task.done():
                closed_task.cancel()

    async def login(self):
        resp = await self._request(login_frame(self.token), "LOGIN")
        return resp.get("return_code"), resp.get("return_msg")

    async def reg(self, codes, types=TYPES, grp_no: str = GRP_NO):
        resp = await self._request(reg_frame(codes, types, grp_no), "REG")
        return resp.get("return_code"), resp.get("return_msg")

    async def remove(self, codes, types=TYPES, grp_no: str = GRP_NO):
        resp = await self._request(remove_frame(codes, types, grp_no), "REMOVE")
        return resp.get("return_code"), resp.get("return_msg")

    async def settle(self, timeout: float = 5.0) -> None:
        try:
            await asyncio.wait_for(self._closed.wait(), timeout)
        except Exception:
            pass

    async def close(self) -> None:
        self._closing = True
        try:
            if self.ws is not None:
                try:
                    await self.ws.close()
                except Exception:
                    pass
        finally:
            if self._recv_task is not None:
                try:
                    await asyncio.wait_for(self._recv_task, timeout=3.0)
                except Exception:
                    try:
                        self._recv_task.cancel()
                    except Exception:
                        pass
            self.ws = None


class _ConnLost(Exception):
    def __init__(self, code):
        super().__init__(f"conn lost: {code}")
        self.code = code


async def _wait_backoff(secs: int, watch, sleep, now_fn, wend) -> str | None:
    """backoff 대기. 1초마다 broker·시각 확인. 중단 사유 있으면 반환."""
    for _ in range(int(secs)):
        await sleep(1.0)
        if watch.has_disconnected():
            return "broker_disconnected"
        if watch.sse_lost():
            return "broker_sse_lost"
        if now_fn().time() >= wend:
            return "window_end"
    return None


async def run_recording(
    *,
    date: str,
    codes: list[str],
    db_path,
    watch,
    connect_fn=None,
    load_token_fn=None,
    notify_fn=None,
    sleep_fn=None,
    now_ns_fn=None,
    now_fn=None,
    disk_ok_fn=None,
    out_dir=None,
    window_end=None,
    flush_sec: float = FLUSH_SEC,
    disk_check_sec: float = DISK_CHECK_SEC,
) -> dict:
    """녹화 루프. window_end면 조용히, 그 외 종료면 batch 알림 1회."""
    codes = list(codes)
    db_path = Path(db_path)
    out = Path(out_dir) if out_dir is not None else db_path.parent
    wend = window_end or WINDOW_END
    notify_f = notify_fn or notify
    disk_f = disk_ok_fn or disk_ok
    token_f = load_token_fn or (lambda: load_token(REPO_ROOT, time.time()))
    now_f = now_fn or (lambda: datetime.now(KST))
    now_ns = now_ns_fn or time.time_ns
    sleep = sleep_fn or asyncio.sleep
    res = {"reason": "", "trade": 0, "book": 0, "reconnects": 0}

    def _finish(reason: str) -> dict:
        res["reason"] = reason
        if reason != "window_end":
            try:
                notify_f(
                    f"[orderbook_research] {date} 녹화 중단: {reason}"
                    f" (trade {res['trade']}, book {res['book']}, 재접속 {res['reconnects']})",
                    channel="batch",
                )
            except Exception:
                pass
        return res

    if not disk_f(out):
        return _finish("disk_low")
    if not watch.is_connected():
        return _finish("broker_sse_lost")  # broker 상태를 모르면 키움을 열지 않음
    if now_f().time() >= wend:
        return _finish("window_end")

    con = open_day_db(db_path)
    try:
        tbuf: list[tuple] = []
        bbuf: list[tuple] = []

        def _flush() -> None:
            if tbuf:
                con.executemany(TRADE_INSERT, tbuf)
                res["trade"] += len(tbuf)
                tbuf.clear()
            if bbuf:
                con.executemany(BOOK_INSERT, bbuf)
                res["book"] += len(bbuf)
                bbuf.clear()
            con.commit()

        def _on_real(text, msg) -> None:
            try:
                tr, bk = parse_real(msg, now_ns())
            except Exception:
                return
            tbuf.extend(tr)
            bbuf.extend(bk)

        def _gap(s: int, e: int, why: str) -> None:
            con.execute("INSERT INTO gaps VALUES (?,?,?,?)", ("research", s, e, why))
            con.commit()

        async def _shut(sess) -> None:
            if sess is not None:
                try:
                    await sess.close()
                except Exception:
                    pass

        n = 0
        gap_start: int | None = None
        gap_code = None
        last_flush = time.monotonic()
        last_disk = time.monotonic()
        while True:
            ws_url, token, why = token_f()
            sess = None
            if why == "ok":
                sess = Session(ws_url, token, connect_fn=connect_fn, on_real=_on_real)
                try:
                    await sess.connect()
                    lcode, _ = await sess.login()
                    if lcode not in (0, "0"):
                        raise _ConnLost(None)
                    rcode, _ = await sess.reg(codes)
                    if rcode not in (0, "0"):
                        raise _ConnLost(None)
                except _ConnLost as cl:
                    code = cl.code
                    await sess.settle()
                    if code is None:
                        code = sess.close_code
                    if gap_start is None:
                        gap_start, gap_code = now_ns(), code
                    action = close_action(code, n)
                    if action == "reconnect":
                        stop = await _wait_backoff(backoff(n), watch, sleep, now_f, wend)
                        await _shut(sess)
                        if stop is not None:
                            _gap(gap_start, now_ns(), stop)
                            _flush()
                            return _finish(stop)
                        n += 1
                        res["reconnects"] = n
                        continue
                    _gap(gap_start, now_ns(), action)
                    _flush()
                    await _shut(sess)
                    return _finish(action)
                except Exception:
                    await sess.settle()
                    code = sess.close_code
                    if gap_start is None:
                        gap_start, gap_code = now_ns(), code
                    await _shut(sess)
                    action = close_action(code, n)
                    if action == "reconnect":
                        stop = await _wait_backoff(backoff(n), watch, sleep, now_f, wend)
                        if stop is not None:
                            _gap(gap_start, now_ns(), stop)
                            _flush()
                            return _finish(stop)
                        n += 1
                        res["reconnects"] = n
                        continue
                    _gap(gap_start, now_ns(), action)
                    _flush()
                    return _finish(action)
            else:
                # 재접속 시 토큰 재독 실패도 끊김과 같은 처리
                if gap_start is None:
                    gap_start, gap_code = now_ns(), None
                action = close_action(None, n)
                if action == "reconnect":
                    stop = await _wait_backoff(backoff(n), watch, sleep, now_f, wend)
                    if stop is not None:
                        _gap(gap_start, now_ns(), stop)
                        _flush()
                        return _finish(stop)
                    n += 1
                    res["reconnects"] = n
                    continue
                _gap(gap_start, now_ns(), action)
                _flush()
                return _finish(action)

            if gap_start is not None:
                _gap(gap_start, now_ns(), f"close_{gap_code}")
                gap_start, gap_code = None, None
            last_flush = time.monotonic()
            last_disk = time.monotonic()
            term = None
            while True:
                await sleep(1.0)
                if watch.has_disconnected():
                    term = "broker_disconnected"
                    break
                if watch.sse_lost():
                    term = "broker_sse_lost"
                    break
                if now_f().time() >= wend:
                    try:
                        await sess.remove(codes)
                    except Exception:
                        pass
                    term = "window_end"
                    break
                if time.monotonic() - last_disk >= disk_check_sec:
                    last_disk = time.monotonic()
                    if not disk_f(out):
                        term = "disk_low"
                        break
                if time.monotonic() - last_flush >= flush_sec:
                    _flush()
                    last_flush = time.monotonic()
                if sess.server_closed:
                    _flush()
                    break
            if term is not None:
                if gap_start is not None:
                    _gap(gap_start, now_ns(), term)
                    gap_start = None
                _flush()
                await _shut(sess)
                return _finish(term)
            code = sess.close_code
            await _shut(sess)
            if gap_start is None:
                gap_start, gap_code = now_ns(), code
            action = close_action(code, n)
            if action == "reconnect":
                stop = await _wait_backoff(backoff(n), watch, sleep, now_f, wend)
                if stop is not None:
                    _gap(gap_start, now_ns(), stop)
                    _flush()
                    return _finish(stop)
                n += 1
                res["reconnects"] = n
                continue
            _gap(gap_start, now_ns(), action)
            _flush()
            return _finish(action)
    finally:
        try:
            con.close()
        except Exception:
            pass


# ── main ───────────────────────────────────────────────────────────────

def _acquire_lock(path: Path):
    """첫 바이트 비차단 잠금. 성공하면 열린 핸들, 이미 잠겼으면 None."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def _selfcheck() -> int:
    assert _int(" 0") == 0
    assert _int("") is None
    assert _int("+12345") == 12345
    assert _int("-3") == -3
    assert _abs("-1722000") == 1722000
    assert _abs("") is None
    tr, bk = parse_real(
        {"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": {
            "20": "090001", "10": "-1722000", "15": "-3",
            "13": "+12345", "27": "+1723000", "28": "-1722000"}}]},
        7,
    )
    assert tr == [(7, "005930", 90001, 1722000, -3, 12345, 1723000, 1722000)], tr
    assert bk == []
    tr2, bk2 = parse_real(
        {"trnm": "REAL", "data": [
            {"type": "0D", "item": "005930", "values": {
                "21": "090002", "41": "+1750000", "51": "-1749000",
                "61": " 0", "62": "", "66": "5", "121": "100", "125": "200"}},
            {"type": "XX", "item": "005930", "values": {}},
            {"type": "0B", "values": {"20": "090001"}},
        ]},
        9,
    )
    assert tr2 == []
    assert len(bk2) == 1
    b = bk2[0]
    assert (b[0], b[1], b[2]) == (9, "005930", 90002), b[:3]
    assert b[3] == 1750000 and b[13] == 1749000, (b[3], b[13])
    assert b[23] == 0 and b[24] is None, (b[23], b[24])
    assert b[-2] == 100 and b[-1] == 200, b[-2:]
    assert close_action(1000, 0) == "stop_kicked"
    assert close_action(1006, 0) == "reconnect"
    assert close_action(None, 19) == "reconnect"
    assert close_action(1006, 20) == "stop_limit"
    assert [backoff(i) for i in range(6)] == [5, 10, 20, 60, 60, 60]
    print("selfcheck ok")
    return 0


def _pre_stop(watch, date: str, reason: str) -> int:
    try:
        watch.stop()
    except Exception:
        pass
    if reason == "window_end":
        print(f"[orderbook_research] {date} {reason} (녹화 창 종료)")
        return 0
    msg = f"[orderbook_research] {date} 녹화 중단: {reason} (trade 0, book 0, 재접속 0)"
    print(msg)
    try:
        notify(msg, channel="batch")
    except Exception:
        pass
    return 3


def _run(date: str, today: str) -> int:
    is_open, why = trading_day_status(date)
    if not is_open:
        print(f"[orderbook_research] {date} 휴장 ({why})")
        return 0
    now = datetime.now(KST)
    if date != today or now.time() >= WINDOW_END:
        print(f"[orderbook_research] {date} 녹화 창 아님 (now={now:%H:%M})")
        return 0
    src, codes = pick_codes(ETL_ROOT / "db" / "watchlist.sqlite3", date)
    if not codes:
        msg = f"[orderbook_research] {date} 종목 없음(source_date={src})"
        print(msg)
        notify(msg, channel="batch")
        return 3
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not disk_ok(OUT_DIR):
        msg = f"[orderbook_research] {date} 녹화 중단: disk_low"
        print(msg)
        notify(msg, channel="batch")
        return 3
    watch = BrokerWatch(BROKER_URL)
    watch.start()
    if not watch.wait_connected(60):
        msg = f"[orderbook_research] {date} 녹화 중단: broker_sse_lost (SSE 미연결)"
        print(msg)
        try:
            watch.stop()
        except Exception:
            pass
        notify(msg, channel="batch")
        return 3
    start_dt = datetime.combine(now.date(), WINDOW_START).replace(tzinfo=KST)
    while datetime.now(KST) < start_dt:
        time.sleep(1)
        if watch.has_disconnected():
            return _pre_stop(watch, date, "broker_disconnected")
        if watch.sse_lost():
            return _pre_stop(watch, date, "broker_sse_lost")
    deadline = time.monotonic() + 30 * 60
    while True:
        ws_url, token, why = load_token(REPO_ROOT, time.time())
        if why == "ok":
            break
        if why != "expired" or time.monotonic() >= deadline:
            msg = f"[orderbook_research] {date} 녹화 중단: token_{why}"
            print(msg)
            try:
                watch.stop()
            except Exception:
                pass
            notify(msg, channel="batch")
            return 3
        for _ in range(60):
            time.sleep(1)
            if watch.has_disconnected():
                return _pre_stop(watch, date, "broker_disconnected")
            if watch.sse_lost():
                return _pre_stop(watch, date, "broker_sse_lost")
            if datetime.now(KST).time() >= WINDOW_END:
                return _pre_stop(watch, date, "window_end")
    db_path = OUT_DIR / f"orderbook_raw_{date}.sqlite3"
    res = asyncio.run(run_recording(date=date, codes=codes, db_path=db_path, watch=watch))
    try:
        watch.stop()
    except Exception:
        pass
    print(f"[orderbook_research] {date} done reason={res['reason']}"
          f" trade={res['trade']} book={res['book']} reconnects={res['reconnects']}")
    return 0 if res["reason"] == "window_end" else 3


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="호가·체결 리서치 녹화 (0B+0D, 08:30~20:00)")
    ap.add_argument("--date", default=None, help="YYYYMMDD (기본 오늘 KST)")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args(argv)
    if args.selfcheck:
        return _selfcheck()
    load_dotenv(REPO_ROOT / ".env")
    today = datetime.now(KST).strftime("%Y%m%d")
    date = args.date or today
    try:
        datetime.strptime(date, "%Y%m%d")
    except ValueError:
        print(f"[orderbook_research] bad --date: {date}")
        return 1
    lock = _acquire_lock(LOCK_PATH)
    if lock is None:
        print("[orderbook_research] already running")
        return 0
    try:
        return _run(date, today)
    except Exception:
        traceback.print_exc()
        try:
            notify(f"[orderbook_research] {date} 예외 종료", channel="batch")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
