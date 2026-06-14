# STEP 5 — main.py 연결

**파일**: `broker/main.py` (수정)
**같이 수정**: 없음

> STEP 1~4 로 만든 부품들(EventBus, WS 매니저, SSE 엔드포인트)을 서버에 꽂는다.
> 이 STEP이 끝나면 백엔드 파이프라인 완성. 이후는 프론트(STEP 6~8).

---

## 목적

지금 main.py는 REST 라우터들만 붙어 있고, WS 매니저도 안 켜지고, `/events` 도 없다.
두 가지를 추가한다:

1. **lifespan** — 서버 시작 시 WS 매니저 켜고, 종료 시 끔
2. **events 라우터 등록** — `/events` 엔드포인트를 앱에 연결

---

## 전체 흐름 (STEP 5 이후)

```
서버 시작
  └─ lifespan → KiwoomWSManager.start()
                  └─ 백그라운드: 키움 WS 연결 → LOGIN → REG → REAL 수신 → bus.publish

브라우저 GET /events
  └─ stream_events → queue 생성 → bus.subscribe("*") → gen() 대기

키움 체결 발생
  └─ REAL 수신 → bus.publish("00", {...})
      └─ queue 에 이벤트 → gen() 깨어남 → yield → SSE → 브라우저

서버 종료
  └─ lifespan → KiwoomWSManager.stop() → WS 연결 clean close
```

**입력**: 없음 (서버 시작/종료 이벤트)
**출력**: 없음 (부품들 연결·해제)

---

## 핵심 개념: lifespan

FastAPI의 `lifespan` = "서버 시작 전/종료 후 할 일" 훅.

```python
@asynccontextmanager
async def lifespan(app):
    # 서버 시작 전
    await ws_manager.start()
    yield          # ← 여기서 서버가 요청 받기 시작
    # 서버 종료 후
    await ws_manager.stop()
```

`yield` 앞 = startup, `yield` 뒤 = shutdown. `asynccontextmanager` 가 이 구조를 만들어줌.

기존 `@app.on_event("startup")` 방식도 있지만 lifespan이 현재 FastAPI 권장.

---

## 정답 코드 (변경 부분만)

```python
# 추가할 import
from contextlib import asynccontextmanager
from kiwoom.ws.manager import KiwoomWSManager
from routers import events as events_router

# lifespan 정의 (load_dotenv() 아래, app = FastAPI(...) 위)
_ws_manager = KiwoomWSManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await _ws_manager.start()
    yield
    await _ws_manager.stop()

# app 생성 시 lifespan 추가
app = FastAPI(
    title="Kiwoom Broker API",
    description="키움증권 REST API gateway — REST + MCP (SSE).",
    version="0.1.0",
    lifespan=lifespan,   # ← 추가
)

# 라우터 등록 (기존 include_router 줄들 아래에 추가)
app.include_router(events_router.router)
```

---

## 코드 줄별 설명

- **`_ws_manager = KiwoomWSManager()`**
  모듈 레벨에서 인스턴스 하나 생성. 언더스코어(`_`)는 "외부에서 직접 쓰지 마라" 관례.
  lifespan 함수가 이걸 start/stop.

- **`@asynccontextmanager`**
  일반 `async def` 를 `async with` 블록처럼 쓸 수 있게 해주는 데코레이터.
  FastAPI가 내부적으로 `async with lifespan(app):` 으로 호출.

- **`await _ws_manager.start()`**
  STEP 3 에서 만든 start(). 백그라운드 태스크 띄우고 즉시 리턴 (블로킹 없음).
  서버가 요청 받기 전에 키움 WS 연결 시작.

- **`yield`**
  이 줄에서 서버가 실제로 요청을 받기 시작. Ctrl+C 누르면 여기서 재개해 아래 실행.

- **`await _ws_manager.stop()`**
  STEP 3 stop(). 백그라운드 태스크 취소 + WS clean close.

- **`lifespan=lifespan` (app 생성)**
  FastAPI에 lifespan 훅 연결. 이게 없으면 startup/shutdown 안 호출됨.

- **`app.include_router(events_router.router)`**
  STEP 4 의 `/events` 엔드포인트를 앱에 등록. 이 줄 없으면 404.

---

## 검증

### 검증 1 — heartbeat (지금 가능)

서버 시작:
```
cd broker
.venv\Scripts\uvicorn.exe main:app --port 8001
```

로그에서 확인:
```
INFO  kiwoom.ws.manager  KiwoomWSManager started
INFO  kiwoom.ws.manager  WS connected and subscribed
```

별도 터미널:
```
curl -N http://localhost:8001/events
```

기대: 15초마다 `: ping` 수신. 연결 유지.

### 검증 2 — system 이벤트 (지금 가능)

`curl` 붙인 직후 (WS 매니저 이미 연결 중이면):
- 연결 직후 `system connected` 이벤트가 SSE로 흘러오진 않음
  (connected는 WS 연결 시 한 번 발행 — curl 전에 이미 발행됨)
- 서버 재시작 후 curl 빠르게 연결하면 볼 수 있음

더 확실한 확인: 로그에 `WS connected and subscribed` 뜨면 파이프라인 정상.

### 검증 3 — 체결 데이터 (장중)

장중 모의 매수 → curl 에 `{"channel":"00","payload":{...}}` 수신.

---

## 흔한 함정

- **lifespan 없이 `@app.on_event` 쓰면** FastAPI 최신 버전에서 deprecation 경고.
  lifespan 방식 쓰는 게 맞음.
- **`app.include_router(events_router.router)` 를 MCP mount 전에 넣어야** MCP가 events 엔드포인트도 인식.
  순서: 라우터 등록 → `mcp.mount()`.

---

## 다음
STEP 6 — `broker-web/lib/use-broker-events.ts`. 브라우저에서 `/events` 구독하는 React 훅.
