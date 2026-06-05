# STEP 6 — 프론트엔드: EventSource 훅

**파일**: `broker-web/lib/use-broker-events.ts` (신규)
**같이 수정**: 없음 (STEP 7에서 layout.tsx 에 달 것)

> 백엔드 파이프라인(STEP 1~5)은 완성. 이제 브라우저 쪽.
> 브라우저가 `/events` SSE 스트림을 구독하는 React 훅을 만든다.

---

## 목적

브라우저는 `EventSource` API로 SSE 연결. 이걸 React 컴포넌트에서 쓰기 좋게
훅으로 감싸는 것이 이 STEP의 전부.

훅 사용법 (STEP 7, 8에서 이렇게 씀):
```typescript
useBrokerEvents((event) => {
  if (event.channel === "system") updateDot(event.payload)
  if (event.channel === "00")    showFillToast(event.payload)
})
```

---

## 전체 흐름

```
컴포넌트 마운트
  └─ useEffect 실행
      └─ new EventSource("http://localhost:8001/events")
          └─ 브라우저 → 백엔드 GET /events (SSE 연결 수립)

키움 체결 발생
  └─ 백엔드 bus.publish("00", {...})
      └─ SSE 스트림으로 data: {"channel":"00","payload":{...}} 전송
          └─ es.onmessage 트리거
              └─ JSON.parse → onEvent 콜백 호출
                  └─ 컴포넌트가 받아서 처리

컴포넌트 언마운트 (탭 이동 / 앱 종료)
  └─ useEffect cleanup: es.close()
      └─ SSE 연결 끊음 → 백엔드 finally: bus.unsubscribe (STEP 4)
```

**입력**: `onEvent` 콜백 (이벤트마다 호출됨)
**출력**: 없음 (side effect 훅)

---

## 핵심 개념

### EventSource (브라우저 내장 API)

```typescript
const es = new EventSource("http://localhost:8001/events");
es.onmessage = (e) => console.log(e.data);  // SSE data: ... 수신 시 호출
es.close();                                   // 연결 끊기
```

`fetch` 와 다름 — 연결 유지하며 서버가 보낼 때마다 `onmessage` 호출.
브라우저 내장이라 별도 설치 없음.

`EventSource` 객체가 가진 것:
| 속성/메서드 | 설명 |
|---|---|
| `es.onmessage` | 서버에서 `data:` 줄 수신 시 호출되는 콜백 |
| `es.onopen` | SSE 연결 수립됐을 때 호출 |
| `es.onerror` | 연결 끊김/에러 시 호출 |
| `es.close()` | 연결 강제 종료 |
| `es.readyState` | 0=연결중, 1=연결됨, 2=닫힘 |

우리가 쓰는 건 `onmessage` + `close()` 두 가지뿐.

### MessageEvent (onmessage 콜백의 인자)

```typescript
es.onmessage = (e: MessageEvent) => {
  console.log(e.data)   // 문자열: '{"channel":"00","payload":{...}}'
  console.log(e.type)   // "message"
}
```

`MessageEvent` 는 브라우저가 "메시지를 받았다"는 이벤트 객체. 여러 API가 공유(WebSocket, postMessage, SSE 모두 `MessageEvent` 사용).

우리가 쓰는 건 `e.data` 하나 — 서버가 `yield json.dumps(event)` 로 보낸 문자열이 그대로 들어있음.
그래서 `JSON.parse(e.data)` 로 다시 객체로 바꿔쓰는 것.

### useEffect + cleanup

```typescript
useEffect(() => {
  const es = new EventSource(...);
  // ... 설정 ...
  return () => es.close();  // cleanup: 컴포넌트 언마운트 시 실행
}, []);                      // [] = 마운트 1회만 실행
```

`return () => es.close()` 가 cleanup 함수. 컴포넌트가 사라질 때 자동 호출.
안 하면 탭 이동해도 SSE 연결이 계속 살아있음 (메모리 누수).

### 콜백 패턴 (onEvent)

훅이 직접 상태를 안 가짐. 이벤트를 받으면 `onEvent` 콜백으로 넘김.
각 컴포넌트가 받아서 자기 상태 업데이트. 훅은 얇게 유지.

### SSE 연결 상태 ≠ 키움 WS 연결 상태

- `es.onopen` / `es.onerror` = **브라우저 ↔ 백엔드** SSE 연결 상태
- `channel === "system"` 이벤트 = **백엔드 ↔ 키움 WS** 연결 상태

헤더 점(STEP 7)은 키움 WS 상태를 보여야 함 → `system` 채널 이벤트로 판단.
SSE 자체가 끊기면 EventSource가 자동 재연결 시도 (브라우저 기본 동작).

---

## 정답 코드

```typescript
"use client";

import { useEffect } from "react";

const BASE = process.env.NEXT_PUBLIC_BROKER_API_URL ?? "http://localhost:8001";

export type BrokerEvent = {
  channel: string;
  payload: Record<string, unknown>;
};

export function useBrokerEvents(onEvent: (e: BrokerEvent) => void): void {
  useEffect(() => {
    const es = new EventSource(`${BASE}/events`);

    es.onmessage = (e: MessageEvent) => {
      try {
        onEvent(JSON.parse(e.data) as BrokerEvent);
      } catch {
        // 파싱 실패 무시 (keepalive ping 등 비JSON 수신 시)
      }
    };

    return () => es.close();
  }, []);
}
```

---

## 코드 줄별 설명

- **`"use client"`**
  Next.js App Router에서 브라우저 API(`EventSource`, `useEffect`) 쓰려면 필수.
  없으면 서버 컴포넌트로 인식 → EventSource 없다고 에러.

- **`const BASE = process.env.NEXT_PUBLIC_BROKER_API_URL ?? "http://localhost:8001"`**
  broker-client.ts 와 동일 패턴. 환경변수 우선, 없으면 localhost 폴백.
  `NEXT_PUBLIC_` 접두사 = 브라우저에 노출 허용.

- **`export type BrokerEvent`**
  `{channel: string, payload: Record<string, unknown>}` — STEP 1 EventBus 포장 구조와 동일.
  STEP 7, 8 에서 이 타입으로 받음.

- **`useEffect(() => { ... }, [])`**
  `[]` = 의존성 없음 → 컴포넌트 마운트 시 딱 1회 실행.
  재렌더링마다 SSE 새로 열리지 않음.

- **`new EventSource(\`${BASE}/events\`)`**
  브라우저 → 백엔드 SSE 연결 수립. GET /events 를 열고 유지.

- **`es.onmessage = (e) => { ... }`**
  서버에서 `data: ...` 라인 올 때마다 호출. `e.data` = 문자열.

- **`JSON.parse(e.data) as BrokerEvent`**
  백엔드가 `json.dumps(event)` 로 보낸 것을 파싱. `as BrokerEvent` = 타입 단언.

- **`try { ... } catch { }`**
  keepalive ping(`: ping`)은 `onmessage` 를 트리거하지 않지만,
  혹시 모를 비JSON 수신에 대비. 파싱 실패 시 조용히 무시.

- **`return () => es.close()`**
  cleanup. 컴포넌트 언마운트 시 SSE 연결 닫음 → 백엔드 큐 정리(STEP 4 finally).

---

## 검증

### 검증 1 — console.log 로 이벤트 수신 확인

layout.tsx (또는 아무 클라이언트 컴포넌트)에 임시로:

```typescript
"use client";
import { useBrokerEvents } from "@/lib/use-broker-events";

export default function TestPage() {
  useBrokerEvents((e) => console.log("broker event:", e));
  return <div>check console</div>;
}
```

브라우저 개발자도구 Console → 15초마다 keepalive ping 아닌 실제 이벤트 없어도
Network 탭에서 `/events` 연결이 유지되고 있으면 정상.

### 검증 2 — system 이벤트 수신

서버 켜져 있으면 WS 매니저가 연결 중. 이미 connected 발행됐으니
페이지 로드 직후엔 못 받음 (이미 지나감).

서버 재시작 후 브라우저 빠르게 열면:
```
broker event: {channel: "system", payload: {type: "connected"}}
```

### 검증 3 — 체결 이벤트 (장중)

장중 모의 매수 → Console에:
```
broker event: {channel: "00", payload: {913: "체결", 9001: "005930", ...}}
```

---

## 흔한 함정

- **`"use client"` 빠뜨림** → 서버에서 렌더링 시 `EventSource is not defined` 에러.
- **`useEffect` 의존성 배열에 `onEvent` 넣으면** → 렌더링마다 SSE 재연결.
  `onEvent` 는 콜백이라 렌더링마다 새 참조 → 무한 재연결. `[]` 유지.
- **cleanup `es.close()` 빠뜨림** → 페이지 이동해도 SSE 살아있음.
  백엔드에 죽은 큐가 쌓임.

---

## 다음
STEP 7 — `broker-web/app/layout.tsx` 수정.
`useBrokerEvents` 달고 `system` 채널로 키움 WS 연결 상태 점(초록/빨강) 헤더에 표시.
