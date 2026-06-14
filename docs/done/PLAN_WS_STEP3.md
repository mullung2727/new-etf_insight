# STEP 3 — KiwoomWSManager (WS 연결 관리)

**파일**: `broker/kiwoom/ws/manager.py` (신규)
**같이 수정**: `broker/kiwoom/tr.py` (상수 2개 추가)

> 이 STEP이 제일 큼. 처음으로 **진짜 키움에 연결**한다.
> `kiwoom/conditions.py` 의 `_run_condition_async` 가 검증된 프로토콜 패턴이다.
> 그건 1회용(단발 요청 후 끊음)이지만, 우리는 **상시 연결 + 자동 재연결**로 확장한다.

## 이게 뭐고 왜 필요한가

지금까지(STEP 1~2)는 네트워크 없는 순수 함수였다. 여기서 실제 데이터가 들어온다.

WS 매니저가 하는 일:
1. 키움 WS 서버에 연결 (손님으로 붙음)
2. LOGIN 메시지로 인증
3. 채널 `00`(체결) 구독 등록(REG)
4. 메시지 계속 수신:
   - `PING` → 그대로 echo (연결 유지)
   - `REAL` → `parse_message`(STEP 2) → `bus.publish`(STEP 1)
5. 연결 끊기면 잠깐 쉬고 자동 재연결

브로커가 켜져 있는 동안 **항상 백그라운드에서** 도는 태스크.

## 핵심 개념 2개

### (1) 백그라운드 태스크 (asyncio.create_task)

서버는 REST 요청도 받고 SSE도 처리한다. WS 수신은 그것들과 **동시에** 계속
돌아야 한다. `asyncio.create_task(...)` 로 "백그라운드에서 알아서 돌아라" 하고
띄운다. FastAPI 가 시작될 때(STEP 5 lifespan) `start()` 호출.

### (2) 자동 재연결 루프

WS는 끊긴다 (네트워크, 키움 점검, 토큰 만료 등). 끊겼다고 죽으면 안 됨.
바깥에 `while self._running:` 루프를 두고, 한 번 연결이 끝나면(끊기면) 잠깐
쉬었다가 다시 연결한다.

```
while _running:
    try:
        await _session()      # 연결~수신 (정상이면 여기서 오래 머묾)
    except 끊김/에러:
        publish system disconnected
    await sleep(재연결 딜레이)  # 잠깐 쉬고
    # 루프 위로 → 재연결
```

`_session()` 안에서 토큰을 매번 새로 읽으니(`get_token()`), 토큰 만료로
끊겨도 재연결 시 새 토큰으로 붙는다.

---

## 먼저: tr.py 상수 추가

```python
# --- Condition search (조건검색) — WebSocket only, not REST ---
# Sent over wss as {"trnm": ...}. ka10171/ka10172 are the underlying TR ids.
WS_LOGIN = "LOGIN"
WS_CONDITION_LIST = "CNSRLST"  # 조건검색 목록조회 (ka10171)
WS_CONDITION_REQ = "CNSRREQ"   # 조건검색 단발요청 (ka10172)

# --- Realtime subscription (실시간 등록/해제) ---
WS_REG = "REG"        # 실시간 등록
WS_REMOVE = "REMOVE"  # 실시간 해제

# --- Realtime channel ids (실시간 항목 type) ---
RT_FILL = "00"        # 주문체결
```

> `RT_FILL = "00"` 도 추가하면 매니저/프론트에서 매직넘버 `"00"` 안 쓰고 의미로 씀.

---

## 정답 코드 (manager.py)

```python
"""Persistent Kiwoom realtime WS client.

Holds one always-on WebSocket to Kiwoom, logs in, subscribes the configured
realtime channels, and republishes incoming REAL messages onto the EventBus.
Reconnects with a fixed backoff when the socket drops. Mirrors the LOGIN ->
PING-echo handshake proven in ``kiwoom.conditions``.
"""

from __future__ import annotations

import asyncio
import json
import logging

import websockets

from . import channels
from .event_bus import bus
from .. import tr
from ..auth import get_token
from ..config import Config, load_config

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 5.0  # seconds between reconnect attempts

# Channels to subscribe on every (r # noqa: BLE001 — log and retry any failure)connect.
_SUBSCRIBE_TYPES = [tr.RT_FILL]


class KiwoomWSManager:
    """Owns the background task that keeps the Kiwoom realtime feed flowing."""

    def __init__(self) -> None:
        self._cfg: Config | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    def _config(self) -> Config:
        if self._cfg is None:
            self._cfg = load_config()
        return self._cfg

    async def start(self) -> None:
        """Launch the background reconnect loop (idempotent)."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("KiwoomWSManager started")

    async def stop(self) -> None:
        """Stop the loop and cancel the background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("KiwoomWSManager stopped")

    async def _run_loop(self) -> None:
        """Reconnect forever until stopped."""
        while self._running:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — log and retry any failure
                logger.warning("WS session ended: %s", exc)
            bus.publish("system", {"type": "disconnected"})
            if self._running:
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _session(self) -> None:
        """One connection: LOGIN -> REG -> receive loop."""
        cfg = self._config()
        async with websockets.connect(cfg.ws_host, open_timeout=10) as ws:
            await ws.send(json.dumps({"trnm": tr.WS_LOGIN, "token": get_token()}))
            async for raw in ws:
                msg = json.loads(raw)
                trnm = msg.get("trnm")

                if trnm == "PING":
                    await ws.send(raw)  # echo back verbatim
                    continue

                if trnm == tr.WS_LOGIN:
                    if msg.get("return_code") not in (0, "0"):
                        raise RuntimeError(f"WS login failed: {msg.get('return_msg')}")
                    await self._subscribe(ws)
                    bus.publish("system", {"type": "connected"})
                    logger.info("WS connected and subscribed")
                    continue

                if trnm == "REAL":
                    for channel, values in channels.parse_message(msg):
                        bus.publish(channel, values)

    async def _subscribe(self, ws) -> None:
        """Register realtime channels after a successful login."""
        await ws.send(json.dumps({
            "trnm": tr.WS_REG,
            "grp_no": "1",
            "refresh": "1",
            "data": [{"item": [""], "type": _SUBSCRIBE_TYPES}],
        }))
```

## 코드 줄별 설명

### 연결/태스크 관리

- **`start()` — `if self._running: return`**
  중복 호출 방어(idempotent). 이미 돌고 있으면 또 안 띄움.
  `asyncio.create_task(self._run_loop())` → 백그라운드로 루프 던지고 즉시 리턴.
  (안 기다림. 그래야 서버 시작이 안 막힘.)

- **`stop()` — `self._task.cancel()`**
  루프 태스크에 취소 신호. `await self._task` 로 정리 끝까지 기다림.
  `CancelledError` 는 정상 종료라 무시.

### 재연결 루프

- **`while self._running:`**
  서버 살아있는 동안 무한 재연결. `stop()` 이 `_running=False` 로 끊음.

- **`except asyncio.CancelledError: raise`**
  취소는 "그만해" 신호 → 다시 던져서 루프 진짜 종료. (안 잡으면 영원히 안 멈춤)

- **`except Exception: log`**
  그 외 모든 에러(연결 끊김 등)는 로그만 찍고 재연결로. 죽지 않음.

- **`bus.publish("system", {"type": "disconnected"})`**
  세션 끝(끊김)마다 발행 → 헤더 점(STEP 7) 빨강.

- **`await asyncio.sleep(_RECONNECT_DELAY)`**
  바로 재연결하면 키움 서버 두드려대니 5초 쉼.

### 세션 (실제 프로토콜)

- **`websockets.connect(cfg.ws_host, open_timeout=10)`**
  현재 env(`cfg.ws_host`)로 연결. `async with` 라 블록 나가면 자동 close.
  헤더 안 넣음 — 인증은 메시지로.

- **`await ws.send(... WS_LOGIN ... get_token())`**
  연결 직후 LOGIN 전송. `get_token()` 으로 매번 최신 토큰 (재연결 시 갱신됨).

- **`async for raw in ws:`**
  메시지 올 때마다 루프 한 바퀴. 연결 끊기면 루프 자동 종료 → `_run_loop` 로 빠져 재연결.

- **`PING → await ws.send(raw)`**
  받은 raw 문자열 그대로 echo. **이거 안 하면 키움이 끊음.** 가장 흔한 실수.

- **`WS_LOGIN → return_code 확인`**
  0 아니면 로그인 실패 → 예외 → 재연결 루프. 성공이면 구독 + connected 발행.

- **`REAL → parse_message → publish`**
  STEP 2 파서로 `(채널, 값)` 뽑아 STEP 1 버스로. 이 한 줄이 파이프라인의 심장.

- **`_subscribe` — REG 메시지**
  로그인 성공 후에만 호출. `type: ["00"]` 로 체결 채널 등록.
  `refresh: "1"` = 등록 시 기존 유지(재연결마다 새로 등록).

---

## 검증 (장 종료 후에도 가능한 범위)

장 종료 상태에선 **체결(REAL) 데이터는 안 옴**. 하지만 연결 골격은 다 확인 가능:

### 검증 1 — 단독 실행 (FastAPI 없이)

`broker/test_ws_manager.py` 같은 임시 스크립트:

```python
import asyncio
import logging
from kiwoom.ws.manager import KiwoomWSManager
from kiwoom.ws.event_bus import bus

logging.basicConfig(level=logging.INFO)

async def main():
    # system 채널 구독해서 연결 상태 직접 확인
    q = asyncio.Queue()
    bus.subscribe("system", q)

    mgr = KiwoomWSManager()
    await mgr.start()

    # 연결~로그인 기다렸다 system 이벤트 확인
    event = await asyncio.wait_for(q.get(), timeout=15)
    print("system 이벤트:", event)   # {'channel':'system','payload':{'type':'connected'}}

    # 10초 더 살려두며 PING echo 동작 관찰 (로그)
    await asyncio.sleep(10)
    await mgr.stop()

asyncio.run(main())
```

기대:
- 로그에 `WS connected and subscribed`
- `system 이벤트: {'channel': 'system', 'payload': {'type': 'connected'}}`
- 10초 동안 끊김 없음 (PING echo 정상). 끊기면 `WS session ended` 로그 뜸.

### 검증 2 — 체결 데이터 (장중에만)
장중 + 모의 매수 1주 → 로그/버스에 `("00", {...})` 들어오는지. (지금은 스킵)

> 토큰 발급 직후 `.token_cache.json` 변경으로 `--reload` 가 재시작될 수 있음.
> 단독 스크립트(검증 1)는 `--reload` 무관하니 깔끔하게 확인됨.

## 흔한 함정
- **PING echo 빠뜨림** → 수십 초 후 조용히 끊김. 로그에 `session ended` 반복되면 의심.
- **로그인 전에 REG 보냄** → 키움이 무시. 반드시 LOGIN 성공 후 구독.
- **`except` 가 `CancelledError` 까지 삼킴** → `stop()` 해도 안 멈춤. 그래서 따로 `raise`.

## 다음
STEP 4 — `routers/events.py` (SSE 엔드포인트). 버스의 `"*"` 를 구독해 브라우저로 스트림.
