"""호가 스냅샷 수집 워커 (SPEC_ORDERBOOK_SNAPSHOT_RECORDER §6~§9).

SSE 수신 스레드가 0D 이벤트를 ``parse_event`` 로 해석해 ``Grid.on_event`` 로 슬롯을 갱신하고,
메인 스레드가 KST 정수 초마다 ``Grid.flush`` 로 행을 뽑아 SQLite 에 쓴다.
신선도는 broker 가 찍은 ``_recv_ts`` 로만 판정한다(ETL 수신 시각 아님).
"""
from __future__ import annotations

import json
import logging
import math
import msvcrt
import pathlib
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Iterator

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

import requests  # noqa: E402

import orderbook_store as store  # noqa: E402
from check_krx_trading_day import trading_day_status  # noqa: E402
from orderbook_recorder_config import CODE_RE, ConfigError, load  # noqa: E402
from orderbook_store import normalize  # noqa: E402
from orderbook_symbols import WATCHLIST_DB, plan_additions, resolve_symbols  # noqa: E402
from wl_sqlite import connect_rw  # noqa: E402

KST = timezone(timedelta(hours=9))
VENUE = "KRX"
LOCK_PATH = pathlib.Path(__file__).resolve().parents[1] / "db" / "orderbook.lock"
FLUSH_DELAY = 0.2       # 격자 t 를 t+0.2초에 확정 — SSE 전달이 조금 늦은 t 이전 이벤트까지 담는다
REG_RETRY_GAP = 2.0     # §7-9 재시도 간격
SSE_RETRY_GAP = 2.0     # §6 SSE 재연결 간격
POLL_GAP = 1.0          # §4.3 오후 완료 확인 주기
HTTP_MAX_WAIT = 15.0    # broker 제어 요청 read timeout 상한 (ACK 5초 + lock 대기)

log = logging.getLogger("orderbook_recorder")


def _ticker(item: Any, acked: set[str]) -> str | None:
    """REAL item → 6자리 코드. KRX:/A 접두사는 떼고, ACK 된 KRX 등록 목록에 있을 때만 인정.

    NXT/SOR(``NXT:…_NX``, ``…_AL``)는 6자리 형식이 아니라 여기서 걸러진다 — KRX 로 재라벨링하지 않는다.
    """
    if not isinstance(item, str):
        return None
    if item.startswith("KRX:"):
        code = item[4:]
    elif len(item) == 7 and item.startswith("A"):
        code = item[1:]
    else:
        code = item
    return code if CODE_RE.match(code) and code in acked else None


def parse_event(payload: dict[str, Any], date: str, acked: set[str]) -> dict[str, Any]:
    """0D payload → 격자용 이벤트. 종목·시각이 잘못되면 ticker=None(호출부가 invalid 로 센다)."""
    invalid = {"ticker": None, "invalid_fields": 0}
    ticker = _ticker(payload.get("item"), acked)
    try:
        received = datetime.fromisoformat(payload["_recv_ts"])
    except (KeyError, TypeError, ValueError):
        return invalid
    if ticker is None or received.tzinfo is None:
        return invalid
    received = received.astimezone(KST)
    if received.strftime("%Y%m%d") != date:
        return invalid
    book, bad_fields = normalize(payload)
    return {"ticker": ticker, "r": received.timestamp(),
            "recv_ts": received.strftime("%H:%M:%S.") + f"{received.microsecond // 1000:03d}",
            "recv_iso": received.isoformat(timespec="milliseconds"),
            "book": book, "invalid_fields": bad_fields}


class Grid:
    """종목별 cur/nxt 슬롯과 다음 격자 t (SPEC §8). 모든 상태를 lock 하나로 보호한다.

    cur = r <= t 중 최신, nxt = r > t 중 최신. 원본 이벤트 큐는 두지 않는다(슬롯 2칸이 전부).
    """

    def __init__(self, date: str, *, end_ts: float, max_stale_sec: float) -> None:
        self.date, self.end_ts, self.max_stale = date, end_ts, max_stale_sec
        self._lock = threading.Lock()
        self._slots: dict[str, dict[str, Any]] = {}
        self._next_t: int | None = None
        self._flushed: int | None = None       # 마지막으로 확정(또는 건너뜀)한 격자
        self._seq = 0                          # 같은 r 이면 나중에 받은 것이 이긴다
        self._seen: set[str] = set()
        self._first: dict[str, str] = {}
        self.stats = {"skipped_grids": 0, "late_events": 0, "invalid_events": 0, "invalid_fields": 0}
        self.done = False

    def tickers(self) -> list[str]:
        with self._lock:
            return list(self._slots)

    @property
    def next_t(self) -> int | None:
        with self._lock:
            return self._next_t

    def peek(self, ticker: str) -> dict[str, Any] | None:
        """가장 최근에 받은 호가(REST 대조용)."""
        with self._lock:
            slot = self._slots.get(ticker) or {}
            held = slot.get("nxt") or slot.get("cur")
            return held[2]["book"] if held else None

    def register(self, tickers: list[str]) -> None:
        """POST 직전: 수신은 받되 아직 행은 만들지 않는다(등록 직후 도착분도 쓰기 위해)."""
        with self._lock:
            for t in tickers:
                self._slots.setdefault(t, {"start": None, "cur": None, "nxt": None})

    def activate(self, tickers: list[str], now: float) -> None:
        """ACK 성공 후: now 이상의 첫 정수 초부터 격자를 만든다."""
        start = math.ceil(now)
        with self._lock:
            for t in tickers:
                if self._slots[t]["start"] is None:
                    self._slots[t]["start"] = start
            if self._next_t is None:
                self._next_t = start

    def forget(self, tickers: list[str]) -> None:
        """등록 실패: 활성화되지 않은 종목을 뺀다."""
        with self._lock:
            for t in tickers:
                if t in self._slots and self._slots[t]["start"] is None:
                    del self._slots[t]

    def clear(self) -> None:
        """단절: 오래된 값을 30초까지 들고 있지 않고 즉시 비운다. 첫 수신 표시도 새 구독 구간으로."""
        with self._lock:
            for slot in self._slots.values():
                slot["cur"] = slot["nxt"] = None
            self._seen.clear()

    def on_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.stats["invalid_fields"] += event["invalid_fields"]
            slot = self._slots.get(event["ticker"]) if event["ticker"] else None
            if slot is None:
                self.stats["invalid_events"] += 1
                return
            self._seq += 1
            r = event["r"]
            if self._flushed is not None and r <= self._flushed:
                self.stats["late_events"] += 1     # 확정 행은 고치지 않는다. 더 최신이면 다음 격자에 쓴다
            key = "cur" if self._next_t is None or r <= self._next_t else "nxt"
            held = slot[key]
            if held is None or (r, self._seq) > held[:2]:
                slot[key] = (r, self._seq, event)
            if event["ticker"] not in self._seen:
                self._seen.add(event["ticker"])
                self._first[event["ticker"]] = event["recv_iso"]

    def pop_first_receipts(self) -> dict[str, str]:
        """구독 구간별 첫 수신 시각. 다음 격자 저장 트랜잭션에서 note 에 반영한다."""
        with self._lock:
            out, self._first = self._first, {}
            return out

    def _advance(self, new_t: int) -> None:
        self._next_t = new_t
        for slot in self._slots.values():
            if slot["nxt"] is not None and slot["nxt"][0] <= new_t:
                slot["cur"], slot["nxt"] = slot["nxt"], None

    def flush(self, now: float) -> list[dict[str, Any]] | None:
        """격자 t 의 행 후보를 복사하고 t 를 전진시킨다. 구간이 끝났으면 None.

        now >= t+1 이면 늦은 회차다 — t 부터 지나간 격자를 만들지 않고 건너뛴다(소급 금지).
        """
        with self._lock:
            t = self._next_t
            if self.done or (t is not None and t >= self.end_ts):
                self.done = True
                return None
            if t is None:                      # 한 종목도 활성화 못 함 — 종료 시각이면 끝
                if now >= self.end_ts:
                    self.done = True
                    return None
                return []
            if now < t:
                return []
            if now >= t + 1:
                new_t = math.ceil(now)
                self.stats["skipped_grids"] += new_t - t
                self._flushed = new_t - 1
                self._advance(new_t)
                return []
            ts = datetime.fromtimestamp(t, KST).strftime("%H:%M:%S")
            rows = []
            for ticker, slot in self._slots.items():
                held = slot["cur"]
                if slot["start"] is None or slot["start"] > t or held is None:
                    continue
                if 0 <= t - held[0] <= self.max_stale:
                    event = held[2]
                    rows.append({"date": self.date, "ticker": ticker, "venue": VENUE, "ts": ts,
                                 "recv_ts": event["recv_ts"], **event["book"]})
            self._flushed = t
            self._advance(t + 1)
            return rows


# ── 수명주기 도우미 ──────────────────────────────────────────────────────────
def _at(date: str, hms: str) -> float:
    return datetime.strptime(date + hms, "%Y%m%d%H:%M:%S").replace(tzinfo=KST).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, KST).isoformat(timespec="milliseconds")


def choose_window(cfg: dict, now: float) -> str | None:
    """오전 종료 전이면 morning, 오후 종료 전이면 afternoon, 그 뒤면 None(구독 없이 종료)."""
    date = datetime.fromtimestamp(now, KST).strftime("%Y%m%d")
    for name in ("morning", "afternoon"):
        if now < _at(date, cfg["windows"][name]["end"]):
            return name
    return None


def acquire_lock(path: pathlib.Path):
    """첫 바이트 비차단 잠금. 성공하면 열린 핸들(프로세스 끝까지 유지), 이미 잠겼으면 None.

    OS 잠금이라 강제 종료돼도 핸들이 닫히면서 풀린다.
    """
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


def iter_sse(lines: Iterable[str]) -> Iterator[str]:
    """SSE 줄 → 빈 줄 단위 프레임의 data. 여러 data: 줄은 개행으로 합친다. 주석·data 없는 프레임은 무시."""
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                yield "\n".join(data)
            data = []
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            if field == "data":
                data.append(value[1:] if value.startswith(" ") else value)


class BrokerError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status} {detail}")
        self.status, self.detail = status, detail


class HttpBroker:
    """로컬 broker HTTP. 모든 호출에 read timeout 을 건다(구간 종료를 넘기지 않게 호출부가 줄인다)."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self._local = threading.local()   # 메인·등록·REST 대조 스레드가 세션을 공유하지 않게 스레드별 세션

    def _call(self, method: str, path: str, timeout: float, **kw: Any) -> dict:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = requests.Session()
        try:
            resp = session.request(method, self.base + path, timeout=(5, max(timeout, 0.1)), **kw)
        except requests.RequestException as exc:
            raise BrokerError(0, f"{method} {path}: {exc}") from None
        if resp.status_code != 200:
            raise BrokerError(resp.status_code, f"{method} {path}: {resp.text[:300]}")
        return resp.json()

    def get_orderbook(self, timeout: float) -> dict:
        return self._call("GET", "/realtime/orderbook", timeout)

    def post_orderbook(self, codes: list[str], timeout: float) -> dict:
        return self._call("POST", "/realtime/orderbook", timeout, json={"codes": codes})

    def delete_orderbook(self, timeout: float) -> dict:
        return self._call("DELETE", "/realtime/orderbook", timeout)

    def quote_orderbook(self, code: str, timeout: float) -> dict:
        return self._call("GET", f"/quotes/{code}/orderbook", timeout)


class SSEStream(threading.Thread):
    """``GET /events?ch=0D,system`` 수신 전용 스레드. 끊기면 2초 뒤 다시 붙는다."""

    def __init__(self, url: str, on_message: Callable[[dict], None],
                 on_stream: Callable[[bool], None]) -> None:
        super().__init__(daemon=True, name="orderbook-sse")
        self.url, self.on_message, self.on_stream = url, on_message, on_stream
        self._stop_evt = threading.Event()

    def run(self) -> None:
        session = requests.Session()
        while not self._stop_evt.is_set():
            try:
                with session.get(self.url, params={"ch": "0D,system"}, stream=True,
                                 timeout=(5, 30)) as resp:
                    resp.raise_for_status()
                    resp.encoding = "utf-8"
                    self.on_stream(True)
                    for data in iter_sse(resp.iter_lines(chunk_size=None, decode_unicode=True)):
                        if self._stop_evt.is_set():
                            return
                        try:
                            self.on_message(json.loads(data))
                        except ValueError:
                            continue
            except requests.RequestException as exc:
                log.warning("SSE 끊김: %s", exc)
            self.on_stream(False)
            self._stop_evt.wait(SSE_RETRY_GAP)

    def stop(self) -> None:
        # ponytail: 막힌 read 는 다음 heartbeat(≤15초)까지 안 깬다. daemon 스레드라 프로세스 종료를 막지 않는다.
        self._stop_evt.set()


class RealClock:
    def now(self) -> float:
        return time.time()

    def sleep(self, sec: float) -> None:
        if sec > 0:
            time.sleep(sec)


class RegFailed(Exception):
    pass


class Recorder:
    """한 구간(오전/오후)의 등록·격자 저장·복구. note 와 DB 는 메인 스레드만 만진다."""

    def __init__(self, cfg: dict, date: str, window: str, run_id: int, con: sqlite3.Connection,
                 clock: Any, broker: Any, watchlist_db: pathlib.Path) -> None:
        self.cfg, self.date, self.window, self.run_id, self.con = cfg, date, window, run_id, con
        self.clock, self.broker, self.watchlist_db = clock, broker, watchlist_db
        win = cfg["windows"][window]
        self.start, self.end = _at(date, win["start"]), _at(date, win["end"])
        self.grid = Grid(date, end_ts=self.end, max_stale_sec=cfg["max_stale_sec"])
        self.note: dict[str, Any] = {"window": dict(win), "reason": None, "source_updates": [],
                                     "subscriptions": [], "stats": self.grid.stats}
        self.acked: list[str] = []
        self.rows_written = 0
        self.last_sha: str | None = None
        self.cross_checked = False
        self.cross_thread: threading.Thread | None = None
        self.pending: dict[str, Any] | None = None      # 예약된 복구·추가 등록
        self.reg_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orderbook-reg")
        self.subscribed = False          # 최초 GET/DELETE 를 했으면 finally 에서 DELETE
        self.stream_up = threading.Event()
        self.connected = threading.Event()
        self.need_restore = threading.Event()
        self.dropped = threading.Event()

    # ── SSE 스레드 콜백: 격자와 이벤트 플래그만 건드린다 ──
    def on_stream(self, up: bool) -> None:
        if up:
            self.stream_up.set()
            return
        self.stream_up.clear()
        self._lost()

    def on_message(self, msg: dict) -> None:
        channel, payload = msg.get("channel"), msg.get("payload") or {}
        if channel == "0D":
            self.grid.on_event(parse_event(payload, self.date, set(self.grid.tickers())))
        elif channel == "system" and payload.get("type") == "connected":
            self.connected.set()
            self.need_restore.set()      # 새 SSE 의 sticky connected 와 재접속 connected 를 한 번의 POST 로 합친다
        elif channel == "system" and payload.get("type") == "disconnected":
            self._lost()

    def _lost(self) -> None:
        self.connected.clear()
        self.grid.clear()                # 단절 즉시 슬롯 제거
        self.dropped.set()

    # ── 메인 스레드 ──
    def _remaining(self) -> float:
        return self.end - self.clock.now()

    def _save_note(self, **fields: Any) -> None:
        store.update_run(self.con, self.run_id, note=self.note, **fields)

    def _close_subs(self, reason: str) -> None:
        now = _iso(self.clock.now())
        for sub in self.note["subscriptions"]:
            if sub["ended_at"] is None:
                sub["ended_at"], sub["end_reason"] = now, reason

    def _post_once(self, codes: list[str], attempt: int) -> bool:
        """POST 한 번. 실패는 로그만 남기고 False."""
        try:
            self.broker.post_orderbook(codes, timeout=min(HTTP_MAX_WAIT, self._remaining()))
            return True
        except BrokerError as exc:
            log.warning("0D 등록 실패 %s (%d/%d): %s", codes, attempt, self.cfg["reg_retry"] + 1, exc)
            return False

    def register_first(self, codes: list[str]) -> None:
        """최초 등록: 최초 1회 + reg_retry 회(2초 간격). 격자 시작 전이라 기다려도 잃는 격자가 없다.

        소진하거나 등록 전에 구간이 끝나면 RegFailed — 조용히 반환하면 격자 없이 저장 루프에 들어간다.
        """
        self.grid.register(codes)
        for attempt in range(1, self.cfg["reg_retry"] + 2):
            if self._remaining() <= 0:
                break
            if self._post_once(codes, attempt):
                self._apply(codes)
                return
            if attempt <= self.cfg["reg_retry"]:
                self.clock.sleep(min(REG_RETRY_GAP, max(self._remaining(), 0)))
        self.grid.forget(codes)
        raise RegFailed(f"initial 0D registration failed for {codes}")

    def schedule(self, codes: list[str]) -> None:
        """복구·추가 등록 예약. POST 는 등록 전용 스레드가 하고, 결과 반영은 메인 루프가 한다."""
        self.grid.register(codes)
        job = self.pending or {"codes": [], "attempt": 0, "next_at": self.clock.now(),
                               "future": None, "sent": []}
        job["codes"] += [c for c in codes if c not in job["codes"]]
        self.pending = job

    def _run_pending(self) -> None:
        """메인 루프에서 매 회차 호출. 끝난 POST 결과를 반영하고, 때가 되면 다음 POST 를 등록 스레드에 넘긴다.

        POST 대기(ACK 최대 5초·HTTP 최대 15초)는 등록 스레드에서만 일어난다 — 격자 저장은 멈추지 않는다.
        grid·note·DB 변경은 전부 여기(메인 스레드)서 한다. 등록 요청은 한 번에 하나.
        """
        job = self.pending
        if not job:
            return
        future = job["future"]
        if future is not None:
            if not future.done():
                return
            job["future"] = None
            sent = job["sent"]
            if future.result():
                job["codes"] = [c for c in job["codes"] if c not in sent]   # 대기 중 새로 붙은 코드만 남김
                self._apply(sent)
                if not job["codes"]:
                    self.pending = None
                    return
                job["attempt"], job["next_at"] = 0, self.clock.now()
            elif job["attempt"] > self.cfg["reg_retry"]:
                self.grid.forget(job["codes"])  # 이미 ACK 된 종목(복구 대상)은 격자에 남는다
                raise RegFailed(f"0D registration retries exhausted for {job['codes']}")
            else:
                job["next_at"] = self.clock.now() + REG_RETRY_GAP
                return
        if self.clock.now() < job["next_at"] or self._remaining() <= 0:
            return
        job["attempt"] += 1
        job["sent"] = list(job["codes"])
        job["future"] = self.reg_pool.submit(self._post_once, job["sent"], job["attempt"])

    def _apply(self, codes: list[str]) -> None:
        """ACK 성공: 격자 시작, 확정 목록·구독 구간·실행기록 갱신."""
        now = self.clock.now()
        self.grid.activate(codes, now)
        self.acked += [c for c in codes if c not in self.acked]
        self.note["subscriptions"] += [{"ticker": c, "registered_at": _iso(now), "first_recv_ts": None,
                                        "ended_at": None, "end_reason": None} for c in codes]
        self._save_note(venue=VENUE, symbols=",".join(self.acked))
        log.info("0D 등록 %s (전체 %d)", codes, len(self.acked))

    def _source_update(self, res: dict, selected: list[str], excluded: list[dict]) -> None:
        meta = res["source_meta"]
        self.note["source_updates"].append({
            "generated_at": meta.get("generated_at"), "ready_observed_at": _iso(self.clock.now()),
            "sha256": meta.get("sha256"), "selected": selected, "excluded": excluded})
        self.last_sha = meta.get("sha256")

    def resolve(self) -> tuple[str, list[str]]:
        """구간 시작 대상. 오후는 완료 산출물이 ready 가 될 때까지 1초마다 확인."""
        saw_empty = False
        while self._remaining() > 0:
            res = resolve_symbols(self.cfg, self.date, self.window, watchlist_db=self.watchlist_db,
                                  now=datetime.fromtimestamp(self.clock.now(), KST))
            if res["status"] == "ready":
                self._source_update(res, res["symbols"], res["excluded"])
                self._save_note(source_date=res["source_date"])
                return "ready", res["symbols"]
            if self.window == "morning":
                self._source_update(res, [], res["excluded"])
                self._save_note(source_date=res["source_date"])
                return "empty", []
            saw_empty = saw_empty or res["status"] == "empty"
            self.clock.sleep(min(POLL_GAP, self._remaining()))
        return ("empty" if saw_empty else "candidate_not_ready"), []

    def _wait_connected(self) -> bool:
        while self._remaining() > 0:
            if self.stream_up.is_set() and self.connected.is_set():
                return True
            self.clock.sleep(min(0.2, self._remaining()))
        return False

    def _poll_additions(self) -> None:
        """구간 도중 새 완료 산출물 → 신규 종목만 추가. 검증 실패·같은 hash 는 무시(기존 구독 유지)."""
        res = resolve_symbols(self.cfg, self.date, "afternoon", watchlist_db=self.watchlist_db,
                              now=datetime.fromtimestamp(self.clock.now(), KST))
        if res["status"] != "ready" or res["source_meta"]["sha256"] == self.last_sha:
            return
        current = self.acked + [c for c in (self.pending or {}).get("codes", []) if c not in self.acked]
        add, excluded = plan_additions(current, res["symbols"], self.cfg["symbols"]["max"])
        self._source_update(res, add, excluded)
        if add:
            self.schedule(add)
        self._save_note()

    def _cross_check(self, ticker: str, book: dict[str, Any]) -> None:
        """첫 0D 와 REST 호가를 한 번 대조(별도 스레드). 불일치·REST 오류는 경고만. note·DB 는 안 만진다."""
        try:
            rest = self.broker.quote_orderbook(ticker, timeout=min(5, max(self._remaining(), 0.1)))
            pair = (abs(int(rest["sel_fpr_bid"])), abs(int(rest["buy_fpr_bid"])))
        except (BrokerError, KeyError, TypeError, ValueError) as exc:
            log.warning("REST 호가 대조 실패 %s: %s", ticker, exc)
            return
        if pair != (book.get("ask1_px"), book.get("bid1_px")):
            log.warning("REST 호가 불일치 %s: REST 매도/매수1 %s vs 0D %s", ticker, pair,
                        (book.get("ask1_px"), book.get("bid1_px")))

    def record(self) -> str:
        """등록 후 격자 저장 루프. 반환: 종료 사유."""
        dirty = False
        while True:
            next_t = self.grid.next_t
            target = next_t + FLUSH_DELAY if next_t is not None else self.clock.now() + POLL_GAP
            self.clock.sleep(target - self.clock.now())
            if self.dropped.is_set():
                self.dropped.clear()
                self._close_subs("disconnected")
                dirty = True
            if self.need_restore.is_set() and self.acked and self.stream_up.is_set():
                self.need_restore.clear()
                self.schedule(list(self.acked))   # 현재 전체 목록 확인·복구. DELETE 는 하지 않는다
            if self.window == "afternoon":
                self._poll_additions()
            self._run_pending()
            rows = self.grid.flush(self.clock.now())
            if rows is None:
                return "window_end"
            firsts = self.grid.pop_first_receipts()
            for ticker, stamp in firsts.items():
                for sub in reversed(self.note["subscriptions"]):
                    if sub["ticker"] == ticker and sub["ended_at"] is None:
                        sub["first_recv_ts"] = sub["first_recv_ts"] or stamp
                        break
            dirty = dirty or bool(firsts)
            if rows or dirty:
                self.rows_written = store.write_round(self.con, self.run_id, rows,
                                                      rows_written=self.rows_written,
                                                      note=self.note if dirty else None)
                dirty = False
            if firsts and not self.cross_checked and self.acked:
                first = next((t for t in self.acked if t in firsts), None)
                if first:
                    self.cross_checked = True     # REST 대기가 격자 저장을 막지 않게 별도 스레드
                    self.cross_thread = threading.Thread(
                        target=self._cross_check, args=(first, self.grid.peek(first) or {}), daemon=True)
                    self.cross_thread.start()

    def execute(self, sse_factory: Callable[..., Any], events_url: str) -> str:
        """대상 해결 → SSE → 최초 등록 → 저장. 반환: 종료 사유."""
        if self.clock.now() < self.start:
            self.clock.sleep(self.start - self.clock.now())
        status, symbols = self.resolve()
        if status != "ready":
            return status
        sse = sse_factory(events_url, self.on_message, self.on_stream)
        sse.start()
        try:
            if not self._wait_connected():
                return "broker_not_connected"
            self.subscribed = True
            before = self.broker.get_orderbook(timeout=min(5, self._remaining()))
            if before.get("codes"):
                log.warning("broker 에 남은 0D 구독 정리: %s", before["codes"])
            self.broker.delete_orderbook(timeout=min(HTTP_MAX_WAIT, self._remaining()))
            self.need_restore.clear()
            self.register_first(symbols)
            return self.record()
        finally:
            # 진행 중 등록 POST 가 끝난 뒤에 종료 DELETE 가 나가야 해제 후 등록이 남지 않는다.
            self.reg_pool.shutdown(wait=True, cancel_futures=True)
            sse.stop()
            if self.cross_thread:
                self.cross_thread.join(timeout=5)


def run(cfg: dict, *, clock: Any = None, broker: Any = None, sse_factory: Callable[..., Any] = SSEStream,
        db_path: pathlib.Path = store.DB_PATH, lock_path: pathlib.Path = LOCK_PATH,
        watchlist_db: pathlib.Path = WATCHLIST_DB,
        trading_day: Callable[[str], tuple[bool, str]] = trading_day_status) -> int:
    """§7 생명주기. 반환: 종료 코드(정상·휴장·빈 목록·비활성 0, DB·등록 소진 1)."""
    clock = clock or RealClock()
    if not cfg["enabled"]:
        log.info("enabled=false — 종료")
        return 0
    lock = acquire_lock(lock_path)
    if lock is None:
        log.info("이미 실행 중 — 종료")
        return 0
    try:
        now = clock.now()
        window = choose_window(cfg, now)
        if window is None:
            log.info("오후 구간 종료 뒤 기동 — 구독 없이 종료")
            return 0
        date = datetime.fromtimestamp(now, KST).strftime("%Y%m%d")
        broker = broker or HttpBroker(cfg["broker_url"])
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect_rw(db_path) as con:
            store.ensure_schema(con)
            win = cfg["windows"][window]
            run_id = store.start_run(con, date=date, started_at=_iso(now), mode=window,
                                     note={"window": dict(win), "reason": None})
            is_open, why = trading_day(date)
            if not is_open:
                log.info("%s 휴장 (%s) — 종료", date, why)
                store.update_run(con, run_id, ended_at=_iso(clock.now()),
                                 note={"window": dict(win), "reason": "holiday", "holiday": why})
                return 0
            recorder = Recorder(cfg, date, window, run_id, con, clock, broker, watchlist_db)
            code, reason = 0, None
            try:
                reason = recorder.execute(sse_factory, cfg["broker_url"].rstrip("/") + "/events")
                if reason == "broker_not_connected":
                    code = 1
            except RegFailed as exc:
                log.error("0D 등록 재시도 소진: %s", exc)
                code, reason = 1, "reg_failed"
            except BrokerError as exc:          # 시작 시 GET/DELETE 정리 실패 등
                log.error("broker 오류: %s", exc)
                code, reason = 1, "broker_error"
            except sqlite3.Error as exc:
                log.error("DB 오류: %s", exc)
                con.rollback()
                code, reason = 1, "db_error"
            finally:
                if recorder.subscribed:
                    try:
                        broker.delete_orderbook(timeout=5)
                    except BrokerError as exc:
                        log.warning("종료 DELETE 실패: %s", exc)
                recorder.note["reason"] = reason or "error"
                recorder._close_subs(recorder.note["reason"])
                try:
                    recorder._save_note(ended_at=_iso(clock.now()), rows_written=recorder.rows_written)
                except sqlite3.Error as exc:
                    log.error("실행기록 저장 실패: %s", exc)
                    code = 1
            log.info("종료: %s, 저장 %d행, %s", recorder.note["reason"], recorder.rows_written,
                     recorder.grid.stats)
            return code
    finally:
        lock.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load()
    except ConfigError as exc:
        log.error("설정 오류 %s: %s", exc.key, exc.reason)
        return 1
    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
