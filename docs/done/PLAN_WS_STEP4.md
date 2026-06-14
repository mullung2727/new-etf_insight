# STEP 4 — SSE 엔드포인트 (routers/events.py)

**파일**: `broker/routers/events.py` (신규)
**의존성**: `sse-starlette` 설치 필요
**같이 수정**: `broker/main.py` 는 STEP 5에서 (라우터 등록)

> 여기서 파이프라인의 **오른쪽 절반**을 만든다. 키움→버스(STEP 1~3)는 됐고,
> 이제 버스→브라우저로 내보내는 문을 연다.

## 이게 뭐고 왜 필요한가

브라우저는 키움 WS에 직접 못 붙는다 (토큰 노출 위험 + CORS + 키움 프로토콜).
그래서 **브로커가 중간에서** 받은 걸 브라우저로 전달해야 한다.

전달 방식 = **SSE (Server-Sent Events)**. 브라우저가 `/events` 에 한 번 연결하면,
브로커가 그 연결을 안 끊고 이벤트를 계속 밀어넣는다. (STEP 0 의 "주인-손님" 에서
브로커가 주인, 브라우저가 손님)

```
[EventBus] ──(큐)──► [/events 엔드포인트] ──SSE──► [브라우저]
  STEP 1~3              STEP 4                    STEP 6
```

## 왜 sse-starlette (직접 안 짜고)

유지보수·가독성 결정(앞서 합의). 직접 짜면 이걸 다 손으로:
- heartbeat 타이밍 (연결 안 끊기게 주기적 ping)
- 클라이언트 끊김 감지 (탭 닫으면 정리)
- SSE 포맷 (`data: ...\n\n` 규칙)

`sse-starlette` 의 `EventSourceResponse` 가 이걸 검증된 코드로 처리. 우리 코드엔
"큐에서 꺼내 보낸다" 의도만 남는다.

## 핵심 개념

### 연결 1개 = 큐 1개

브라우저가 `/events` 에 연결할 때마다 **그 연결 전용 큐**를 새로 만든다.
- `bus.subscribe("*", queue)` → 이 큐가 모든 채널 이벤트 받음 (STEP 1 의 `"*"`)
- 연결 끊기면 `bus.unsubscribe("*", queue)` → 큐 정리 (메모리 누수 방지)

탭 2개 열면 큐 2개. 각자 독립.

### 큐에서 꺼내 흘리기

```
무한 루프:
    event = await queue.get()   # 이벤트 올 때까지 대기 (STEP 1 에서 본 것)
    yield json.dumps(event)     # 브라우저로 흘림
```

`await queue.get()` 이 핵심 — 이벤트 없으면 CPU 안 쓰고 잠듦, 키움 체결로
`bus.publish` 되면 깨어나 꺼냄.

---

## 설치

```
cd broker
uv add sse-starlette
```
`uv add` 가 `.venv` 설치 + `pyproject.toml` dependencies 추가 + lock 갱신까지 자동.
`pip install` 쓰면 toml 수동 수정 필요하니 `uv add` 써라.

---

## stream_events 동작 원리

### 입력: `request: Request`

브라우저가 `GET /events` 로 HTTP 연결을 열면, FastAPI가 그 연결 정보를 `request` 객체로 넘긴다.
이게 "누가 연결했냐"를 나타내는 핸들. 우리는 `request.is_disconnected()` 로 "이 브라우저가 탭 닫았냐?"를 체크하는 데만 쓴다.

### 전체 흐름

```
1. 브라우저가 GET /events 연결
        ↓
2. stream_events 실행 → 이 연결 전용 queue 생성
        ↓
3. bus.subscribe("*", queue) → 이제 모든 publish 가 이 queue 로 들어옴
        ↓
4. EventSourceResponse(gen()) 반환 → HTTP 연결을 끊지 않고 유지
        ↓
5. gen() 루프 돌며 queue.get() 대기 (이벤트 없으면 잠듦)
        ↓
6. 키움에서 체결 → manager.py 가 bus.publish("00", {...})
        ↓
7. queue 에 이벤트 들어옴 → gen() 깨어남
        ↓
8. yield json.dumps(event) → EventSourceResponse 가 브라우저로 전송
        ↓
9. 브라우저 탭 닫음 → is_disconnected() True → break → finally 로 unsubscribe
```

### 왜 함수가 끝나지 않냐

일반 엔드포인트는 `return` 하고 끝. SSE는 연결을 유지해야 하니 `gen()` 이 `while True` 로 계속 돈다.
`EventSourceResponse` 가 이 제너레이터를 붙잡고 "값 나올 때마다 전송" 한다.
브라우저가 끊거나 서버가 `break` 할 때까지 함수는 살아있다.

---

## 정답 코드

```python
"""SSE endpoint streaming EventBus events to the browser.

Each client connection gets its own queue subscribed to "*" (all channels).
sse-starlette's EventSourceResponse handles the SSE wire format, periodic
ping, and client-disconnect cleanup; we only drain the queue.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from kiwoom.ws.event_bus import bus

router = APIRouter(tags=["events"])


@router.get("/events")
async def stream_events(request: Request):
    """Stream all broker realtime events to one browser client via SSE."""
    queue: asyncio.Queue = asyncio.Queue()
    bus.subscribe("*", queue)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    continue  # let EventSourceResponse send its keep-alive ping
                yield json.dumps(event)
        finally:
            bus.unsubscribe("*", queue)

    return EventSourceResponse(gen(), ping=15)
```

## 코드 줄별 설명

- **`queue = asyncio.Queue()` + `bus.subscribe("*", queue)`**
  이 연결 전용 큐 생성 후 전체 채널 구독. 이제 어떤 `bus.publish(...)` 든
  이 큐에 들어옴 (STEP 1 의 `"*"` 합집합 규칙).

- **`async def gen()`**
  SSE 가 흘려보낼 이벤트를 하나씩 `yield` 하는 비동기 제너레이터.
  `EventSourceResponse` 가 이걸 받아 `data: ...` 로 감싸 전송.

- **`if await request.is_disconnected(): break`**
  브라우저가 탭 닫음/새로고침 → 연결 끊김 감지하면 루프 종료.
  (직접 구현 땐 이거 잡기 까다로움 — 라이브러리가 `request` 로 제공)

- **`await asyncio.wait_for(queue.get(), timeout=15)`**
  큐에서 이벤트 대기. 15초 안 오면 `TimeoutError` → `continue` 로 위로.
  위에서 `is_disconnected` 다시 체크하니, 죽은 연결을 15초마다 청소.

- **`except asyncio.TimeoutError: continue`**
  타임아웃은 에러 아님 — 그냥 "이벤트 없었음". ping 은 라이브러리가 보냄.

- **`yield json.dumps(event)`**
  `event` 는 이미 `{"channel":..., "payload":...}` (STEP 1 포장). JSON 문자열로
  바꿔 흘림. 프론트(STEP 6)가 `JSON.parse` 로 받음.

- **`finally: bus.unsubscribe("*", queue)`**
  연결 끝날 때(정상 종료/끊김/에러 무엇이든) 반드시 구독 해제. 안 하면 죽은
  큐가 버스에 쌓여 publish 마다 헛수고 + 메모리 누수.

- **`EventSourceResponse(gen(), ping=15)`**
  `gen()` 의 yield 를 SSE 로 전송. `ping=15` = 15초마다 keep-alive 주석 자동 전송
  (연결 유지). 직접 안 짬.

---

## 검증

### 검증 1 — heartbeat (장 상관없이 지금)
STEP 5(main.py 등록) 후 서버 켜고:
```
curl -N http://localhost:8001/events
```
기대: 15초마다 `: ping` (또는 비슷한 keep-alive 주석) 수신. 끊기지 않고 유지.

### 검증 2 — 실제 이벤트 흐름 (수동 발행으로 지금 가능)
키움 체결 없이도 버스에 직접 쏴서 SSE 까지 도는지 확인.
`curl -N http://localhost:8001/events` 를 한 터미널에 띄워두고, 다른 데서
파이썬으로:
```python
from kiwoom.ws.event_bus import bus
bus.publish("00", {"913": "체결", "9001": "005930"})
```
> 단, 같은 프로세스의 bus 여야 함. 서버가 `--reload` 면 별도 프로세스라 안 통함.
> 더 쉬운 검증: STEP 3 의 WS 매니저가 켜져 있으면 `system` connected 이벤트가
> 연결 직후 한 번 흐르므로, `curl` 붙은 직후 `system` 이벤트가 보이는지 확인.

### 검증 3 — 체결 실데이터 (장중)
장중 모의매수 1주 → `curl` 에 `{"channel":"00","payload":{...}}` 뜸.

---

## 주의
- **CORS**: 브라우저 EventSource 는 CORS 적용됨. `main.py` 의 CORSMiddleware 에
  이미 `localhost:3001` + GET 허용돼 있어 그대로 동작. (추가 작업 없음)
- **`--reload` + SSE**: 파일 저장 시 서버 재시작되면 SSE 연결 끊김 → 브라우저가
  자동 재연결 (EventSource 기본 동작). 개발 중엔 정상.

## 다음
STEP 5 — `main.py` 에 lifespan(WS 매니저 start/stop) + events 라우터 등록.
이걸로 백엔드 끝. 이후 STEP 6~8 은 프론트.
