# WebSocket 실시간 이벤트 구현 계획

## 목표

### 1단계 (메인) — 파이프라인 구축
키움 WS → 브로커 백엔드 → SSE → 브라우저 실시간 파이프라인 구축.
채널 추가만으로 어떤 실시간 데이터든 흘려보낼 수 있는 확장 가능한 구조.

### 2단계 — 첫 번째 채널: 체결 이벤트
파이프라인 위에 채널 `00` (주문체결) 연결.
체결 시 전역 토스트 표시 → 노트 작성 연결.

## 구조
```
브라우저 (EventSource)
    ↑ SSE (HTTP)
FastAPI 브로커 (8001)
    ↑ WebSocket
키움 서버 (wss://mockapi.kiwoom.com:10000)
```

## 두 개의 연결 — 혼동 주의
- **브라우저 ↔ 브로커**: SSE (`/events`)
- **브로커 ↔ 키움**: WebSocket (체결 알림 출처)

헤더의 연결 상태 점은 **키움 WS 상태**를 표시한다 (체결 알림이 여기서 오므로).
SSE는 끊겨도 브라우저가 자동 재연결하므로 별도 표시 안 함.
키움 WS 연결/끊김은 `system` 채널 이벤트로 브라우저에 전달.

## 기존 코드 연결점
- `kiwoom/auth.py` → `get_token()` : WS LOGIN 메시지의 token 으로 사용
- `kiwoom/conditions.py` → **실제 키움 WS 프로토콜 참고 구현** (LOGIN → PING echo → 요청)
- `kiwoom/tr.py` → `WS_LOGIN` 재사용, `WS_REG`/`WS_REMOVE` 상수 추가
- `kiwoom/config.py` → `ws_host` (현재 env의 WS 호스트)
- `broker/main.py` → lifespan에 WS 시작/종료, events 라우터 등록

## ⚠️ 키움 WS 프로토콜 핵심 (conditions.py 검증됨)
일반 REST와 다름. 반드시 지킬 것:
1. 인증은 **헤더 아님** — 연결 후 `{"trnm":"LOGIN","token":<토큰>}` 메시지 전송
2. LOGIN 응답 `return_code == 0` 확인 후에만 다음 요청(REG) 전송
3. 키움이 보내는 `{"trnm":"PING"}` 은 **받은 그대로 echo** — 안 하면 연결 끊김
4. 채널 지정은 REG 메시지의 `type` 필드로 함 (`api-id` 헤더 불필요)

---

## 단계별 구현

### STEP 0 — 전체 개요 (먼저 읽기)

> 개별 STEP에 흩어져 있어 한눈에 안 들어오는 것들을 여기 모았다.
> 코드 짜기 전에 이 그림이 머리에 있어야 STEP 1~8이 왜 이렇게 나뉘는지 이해됨.

#### 0-1. 데이터가 흐르는 길 (택배 비유)

체결 알림이 키움에서 출발해 브라우저 화면 토스트까지 가는 여정:

```
[키움 서버]   체결 발생! "삼성전자 1주 체결"
    │  (WebSocket — 브로커가 키움에 전화 걸어 계속 듣고 있음)
    ▼
[브로커: WS 매니저]   메시지 받아서 한국어로 번역
    │  STEP 3 + STEP 2
    ▼
[브로커: EventBus]    우체국. "이거 받을 사람?" 하고 큐에 넣음
    │  STEP 1
    ▼
[브로커: SSE 엔드포인트]   큐에서 꺼내 브라우저로 밀어보냄
    │  STEP 4   (SSE = 서버가 일방적으로 보내는 HTTP 스트림)
    ▼
[브라우저: EventSource 훅]   받아서 React 상태로
    │  STEP 6
    ▼
[브라우저: 토스트 + 헤더 점]   화면에 표시
       STEP 7 (연결 점) + STEP 8 (체결 토스트)
```

핵심: **연결이 2종류**다. 헷갈리기 쉬움.
- **WebSocket** = 브로커 ↔ 키움. 브로커가 *손님(클라이언트)* 으로 키움에 붙음.
- **SSE** = 브로커 ↔ 브라우저. 브로커가 *주인(서버)* 으로 브라우저에 보냄.
- 둘 다 "실시간"이지만 방향·역할이 다름. Next.js는 여기 안 낌 (브라우저가 8001에 직접 붙음).

#### 0-2. 등장인물 (누가 무슨 일 하나)

| 이름 | 위치 | 하는 일 | STEP |
|------|------|---------|------|
| WS 매니저 | 브로커 | 키움에 WS로 붙어 메시지 수신, PING 응답 | 3 |
| 채널 파서 | 브로커 | 키움 raw 메시지 → `(채널, 값)` 으로 정리 | 2 |
| EventBus | 브로커 | 받은 이벤트를 듣는 사람들 큐에 뿌림 (우체국) | 1 |
| SSE 엔드포인트 | 브로커 | 브라우저 연결 받아 큐 내용을 스트림으로 송신 | 4 |
| EventSource 훅 | 브라우저 | SSE 받아 콜백 호출 | 6 |
| 헤더 상태 점 | 브라우저 | 키움 WS 살았나 죽었나 표시 | 7 |
| 체결 토스트 | 브라우저 | 체결되면 팝업 + 노트 작성 | 8 |

#### 0-3. 여러 STEP에 걸쳐 흐르는 약속 3가지 (이게 헷갈림의 원인)

이 3개는 한 STEP에서 안 끝나고 여러 곳을 관통한다. 미리 알아둘 것:

**(A) 이벤트 포장 형식 `{ "channel": ..., "payload": ... }`**
EventBus(STEP 1)가 이 형식으로 포장하면, 그대로 SSE(4) → 브라우저 훅(6) →
토스트(8)까지 **안 바뀌고 그대로** 흘러간다. 한 번 정하면 끝까지 같음.
- `channel`: 어느 종류냐 (`"00"`=체결, `"system"`=연결상태)
- `payload`: 실제 내용 (체결가, 종목코드 등)

**(B) `"*"` 와일드카드 채널**
"전체 다 듣기" 표시. SSE 엔드포인트(4)는 채널 하나만 고르지 않고 `"*"` 로 구독해
모든 채널을 한 줄로 브라우저에 보낸다. → STEP 1의 EventBus가 `"*"` 를 알아야 함.

**(C) `"system"` 채널 = 연결 상태 전용 통로**
체결(`"00"`) 과 별개로, 키움 WS가 **연결됨/끊김** 을 알리는 채널.
- WS 매니저(3)가 `publish("system", {"type":"connected"})` 발행
- 헤더 점(7)이 이 채널만 보고 초록/빨강 결정
- 같은 파이프라인(EventBus→SSE)을 그대로 타고 감 — 별도 통신 안 만듦

#### 0-4. 한 파일을 여러 STEP에서 건드림 (주의)

| 파일 | 건드리는 STEP | 이유 |
|------|--------------|------|
| `kiwoom/tr.py` | 3 | WS_REG/WS_REMOVE 상수 추가 |
| `broker/main.py` | 5 | WS 시작/종료 + 라우터 등록 |
| `broker-web/app/layout.tsx` | 7, 8 | 전역 SSE 수신 → 헤더 점 + 토스트 |

#### 0-5. 구현 순서가 1→8인 이유

아래에서 위로 쌓는다. 안 보이는 백엔드 먼저, 화면은 마지막.
- **1~2**: 키움/네트워크 없이 순수 파이썬으로 테스트 가능 (제일 안전한 시작)
- **3**: 진짜 키움 WS 연결 (여기서 실제 데이터 처음 들어옴)
- **4~5**: 브라우저가 받을 수 있게 SSE 문 열기
- **6~8**: 화면에 그리기

각 STEP 끝에 **검증** 방법이 있음. 통과해야 다음으로.

---

### STEP 1 — EventBus (인메모리 pub/sub)
**파일**: `broker/kiwoom/ws/event_bus.py` (신규)

구현할 것:
- `EventBus` 클래스
- `subscribe(channel: str, queue: asyncio.Queue)` : 채널 구독 등록
- `unsubscribe(channel: str, queue: asyncio.Queue)` : 구독 해제
- `publish(channel: str, data: dict)` : 채널에 이벤트 발행

**`"*"` 와일드카드 규칙 (처음부터 포함)**:
- `subscribe("*", queue)` 한 구독자는 **모든 채널** 이벤트를 받는다
- `publish(channel, data)` 시 → 해당 channel 구독자 + `"*"` 구독자 모두에게 전달
- SSE 엔드포인트(STEP 4)는 `"*"` 로 구독해서 전체 스트림을 받는다

힌트:
- 구독자 목록: `dict[str, set[asyncio.Queue]]`
- publish는 `queue.put_nowait()` 사용 (비동기 대기 없이)
- 전역 싱글턴 인스턴스 `bus = EventBus()` 파일 하단에 선언

검증: Python 인터프리터에서 import 후
- `"00"` 구독자가 `publish("00", ...)` 받는지
- `"*"` 구독자가 `publish("00", ...)` 와 `publish("system", ...)` 둘 다 받는지

---

### STEP 2 — 채널 파싱
**파일**: `broker/kiwoom/ws/channels.py` (신규)

구현할 것:
- `parse_message(raw: dict) -> list[tuple[str, dict]]`
  - 키움 WS 실시간 응답 파싱 → `[(channel_id, values), ...]` 반환
  - `raw["trnm"] == "REAL"` 인 경우만 처리 (LOGIN/PING/REG 응답은 빈 리스트)
  - `raw["data"]` 리스트 순회 → 각 항목의 `type`(채널), `values`(딕셔너리) 추출
  - 반환 `event_data` = `values` 딕셔너리 (종목코드는 9001에 있으므로 충분)

키움 WS 실시간 응답 형태 (참고):
```json
{
  "trnm": "REAL",
  "data": [{
    "type": "00",
    "name": "주문체결",
    "item": "005930",
    "values": {
      "9001": "005930",
      "913": "체결",
      "905": "+매수",
      "910": "60700",
      "911": "1",
      "908": "094022"
    }
  }]
}
```

채널 `00` (주문체결) values 주요 필드:
| 키 | 의미 |
|----|------|
| `9001` | 종목코드 |
| `913` | 주문상태 (접수/체결/확인/취소/거부) |
| `905` | 매수/매도 구분 (+매수, +매도 등) |
| `910` | 체결가 |
| `911` | 체결량 |
| `908` | 체결시간 (HHmmss) |
| `9203` | 주문번호 |

검증: 위 예시 dict를 `parse_message()`에 넣어 `[("00", {...})]` 나오는지,
`{"trnm":"PING"}` 넣으면 `[]` 나오는지 확인

---

### STEP 3 — KiwoomWSManager (WS 연결 관리)
**파일**: `broker/kiwoom/ws/manager.py` (신규)

> **참고**: `kiwoom/conditions.py` 의 `_run_condition_async` 가 실제 검증된
> LOGIN → PING echo → 요청 흐름이다. 이 패턴을 그대로 가져와 상시 연결로 확장한다.

**tr.py 상수 추가** (먼저):
```python
WS_REG = "REG"       # 실시간 등록
WS_REMOVE = "REMOVE" # 실시간 해제
```

구현할 것:
- `KiwoomWSManager` 클래스
- `__init__`: 재연결 딜레이, `_running: bool`, `_task` 등 초기화 (ws_host는 매 연결 시 `_config()`에서 읽음)
- `start()`: 백그라운드 태스크로 `_run_loop()` 실행 (`asyncio.create_task`)
- `stop()`: `_running = False`, 태스크 취소/정리
- `_run_loop()`: `_running` 동안 연결 시도 → 끊기면 딜레이 후 재연결
- `_session()`: 1회 연결 — LOGIN → REG → 수신 루프

`_session()` 흐름 (conditions.py 패턴):
```python
async with websockets.connect(cfg.ws_host, open_timeout=10) as ws:
    await ws.send(json.dumps({"trnm": tr.WS_LOGIN, "token": get_token()}))
    while True:
        msg = json.loads(await ws.recv())
        trnm = msg.get("trnm")
        if trnm == "PING":
            await ws.send(json.dumps(msg))      # echo 필수
            continue
        if trnm == tr.WS_LOGIN:
            if msg.get("return_code") not in (0, "0"):
                raise RuntimeError(f"WS login failed: {msg.get('return_msg')}")
            await ws.send(json.dumps(REG_MESSAGE))   # 로그인 성공 후 구독
            bus.publish("system", {"type": "connected"})
            continue
        if trnm == "REAL":
            for channel, values in channels.parse_message(msg):
                bus.publish(channel, values)
```

REG 메시지 (채널 00 구독):
```json
{
  "trnm": "REG",
  "grp_no": "1",
  "refresh": "1",
  "data": [{"item": [""], "type": ["00"]}]
}
```

연결 상태 → `system` 채널로 발행:
- LOGIN 성공 직후: `bus.publish("system", {"type": "connected"})`
- 연결 끊김/예외 시 (재연결 루프 진입 전): `bus.publish("system", {"type": "disconnected"})`

힌트: websockets 16.x. 헤더 안 씀(LOGIN 메시지로 인증). `websockets.connect(url, open_timeout=10)`

검증: 서버 시작 후 로그에 "WS connected" 또는 LOGIN 성공 출력 확인

---

### STEP 4 — SSE 엔드포인트 (`sse-starlette` 사용)
**파일**: `broker/routers/events.py` (신규)
**의존성 추가**: `sse-starlette` (broker/.venv 에 설치)

> **왜 라이브러리?** (유지보수·가독성 결정)
> SSE 직접 구현은 heartbeat 타이밍, 클라이언트 끊김 감지, 제너레이터 정리(`finally`)를
> 손으로 관리해야 해서 버그 자리가 많다. `sse-starlette` 의 `EventSourceResponse` 가
> 이걸 검증된 코드로 처리한다 (ping 자동, `request.is_disconnected` 내장).
> EventBus(STEP 1)·WS 매니저(STEP 3)는 직접 유지 — 대체재가 없거나 우리 코드가 더 얇음.
> SSE 만 "표준이 있고 까다로운 곳" 이라 위임.

설치:
```
broker/.venv/Scripts/pip install sse-starlette
# pyproject.toml dependencies 에도 sse-starlette 추가
```

구현할 것:
- `GET /events` : SSE 스트림
- 클라이언트 연결 시 `bus.subscribe("*", queue)` (전체 채널)
- 큐에서 이벤트 꺼내 yield (형식은 `EventSourceResponse` 가 `data:` 로 감쌈)
- heartbeat: `EventSourceResponse(..., ping=15)` 로 15초마다 자동 (직접 안 짬)
- 클라이언트 끊기면 `bus.unsubscribe("*", queue)` 정리 (`finally`)

페이로드: `bus` 가 이미 `{"channel": ..., "payload": ...}` 로 포장(STEP 1)하므로
그대로 `json.dumps` 해서 보냄. 프론트(STEP 6)가 `JSON.parse` 로 받음.

`sse-starlette` 힌트:
```python
import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from kiwoom.ws.event_bus import bus

router = APIRouter(tags=["events"])


@router.get("/events")
async def stream_events(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    bus.subscribe("*", queue)

    async def gen():
        try:
            while True:
                # 클라이언트가 탭 닫으면 끊김 감지하고 종료
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    continue  # ping 은 EventSourceResponse 가 알아서 보냄
                yield json.dumps(event)  # data: 래핑은 라이브러리가 함
        finally:
            bus.unsubscribe("*", queue)

    return EventSourceResponse(gen(), ping=15)
```

> 직접 구현(`StreamingResponse` + `: keepalive` 수동) 대비: heartbeat·끊김감지·SSE 포맷을
> 라이브러리가 처리 → 우리 코드엔 "큐에서 꺼내 보낸다" 의도만 남음.

검증: `curl -N http://localhost:8001/events` → 15초마다 `: ping` 주석 수신 확인
(`EventSourceResponse` 가 보내는 표준 ping)

---

### STEP 5 — main.py 연결
**파일**: `broker/main.py` (수정)

추가할 것:
1. lifespan 으로 WS 시작/종료:
```python
from contextlib import asynccontextmanager
from kiwoom.ws.manager import KiwoomWSManager

ws_manager = KiwoomWSManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await ws_manager.start()
    yield
    await ws_manager.stop()

app = FastAPI(..., lifespan=lifespan)
```

2. events 라우터 등록:
```python
from routers import events as events_router
app.include_router(events_router.router)
```

> 주의: `--reload` 개발 모드에서는 파일 변경 시 WS가 끊겼다 재연결됨 (정상).
> 운영 시 `--reload` 빼면 안정적.

검증: 서버 시작 → `/events` curl 연결 → 키움 WS LOGIN 성공 로그 확인

---

### STEP 6 — 프론트엔드: EventSource 훅
**파일**: `broker-web/lib/use-broker-events.ts` (신규)

> URL은 `broker-client.ts` 와 동일하게 `NEXT_PUBLIC_BROKER_API_URL` 사용 (하드코딩 금지).

구현할 것:
```typescript
const BASE = process.env.NEXT_PUBLIC_BROKER_API_URL ?? "http://localhost:8001";

export type BrokerEvent = {
  channel: string;
  payload: Record<string, unknown>;
};

export function useBrokerEvents(
  onEvent: (e: BrokerEvent) => void,
  onStatus?: (connected: boolean) => void,
) {
  useEffect(() => {
    const es = new EventSource(`${BASE}/events`);
    es.onmessage = (e) => onEvent(JSON.parse(e.data) as BrokerEvent);
    return () => es.close();
  }, []);
}
```

> 키움 WS 연결 상태는 SSE 의 `onopen/onerror` 가 아니라 **`system` 채널 이벤트**로 판단.
> (`channel === "system"`, `payload.type === "connected" | "disconnected"`)

검증: 컴포넌트에 달아서 console.log 로 이벤트 수신 확인

---

### STEP 7 — 연결 상태 표시 (헤더) — 키움 WS 기준
**파일**: `broker-web/app/layout.tsx` (수정)

추가할 것:
- 키움 WS 연결 상태 관리 (`wsConnected: boolean`)
- `useBrokerEvents` 의 이벤트 중 `channel === "system"` 처리:
  - `payload.type === "connected"` → `setWsConnected(true)`
  - `payload.type === "disconnected"` → `setWsConnected(false)`
- 헤더에 상태 점:
  - 초록 점: 키움 WS 연결됨
  - 빨강 점: 끊김
- 초기값 false (연결 확인 전까지 끊김으로 표시)

---

### STEP 8 — 체결 토스트 + 노트 모달
**파일**: `broker-web/components/common/fill-toast.tsx` (신규)

구현할 것:
- 체결 이벤트 필터: `channel === "00"` && `payload["913"] === "체결"`
- 토스트 내용:
  - 종목코드(9001), 매수/매도(905), 체결가(910), 체결량(911)
  - "노트 작성" 버튼
  - "닫기" 버튼 (수동으로만 닫힘, 자동 닫힘 없음)
- "노트 작성" 클릭 → `NoteModal` 모달 열기 (symbol 자동 입력)

layout.tsx 에서 `useBrokerEvents` 로 이벤트 수신 → 체결 시 `FillToast` 표시 (전역).

---

## 폴더 구조 (완성 후)

```
broker/
  kiwoom/
    tr.py             # WS_REG / WS_REMOVE 상수 추가
    ws/
      __init__.py
      event_bus.py    # STEP 1
      channels.py     # STEP 2
      manager.py      # STEP 3
  routers/
    events.py         # STEP 4
  main.py             # STEP 5 (lifespan + 라우터)

broker-web/
  lib/
    use-broker-events.ts        # STEP 6
  components/common/
    fill-toast.tsx              # STEP 8
  app/
    layout.tsx                  # STEP 7, 8 연결
```

## 의존성
- Python: `websockets` (이미 16.0 설치됨 — 추가 설치 불필요)
- 프론트: 추가 패키지 없음 (EventSource 브라우저 내장)

## 알려진 한계 (나중에)
- **모의↔실전 토글 시 WS 재연결 안 됨**: `/settings` 로 env 바꿔도 WS 매니저는
  기존 호스트 연결을 유지한다. 지금은 env 변경 후 **서버 재시작** 필요.
  (추후 `set_runtime_env` 시 WS 매니저에 재연결 신호 보내는 작업으로 해결)
- **부분 체결**: `913 == "체결"` 이 부분체결마다 여러 번 올 수 있음. 지금은 매번 토스트.
- **다른 웹사이트 방문 중 알림**: Web Push + Service Worker 필요. 범위 밖.
```
