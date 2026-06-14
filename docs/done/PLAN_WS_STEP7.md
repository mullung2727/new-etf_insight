# STEP 7 — 키움 WS 연결 상태 점 (헤더)

**파일**: `broker-web/components/common/broker-status.tsx` (신규), `broker-web/components/common/nav.tsx` (수정)
**정리**: `broker-web/components/common/event-logger.tsx` 삭제, `broker-web/app/layout.tsx` EventLogger 제거

> EventLogger(테스트용)를 실제 기능으로 교체. 헤더 우측에 키움 WS 연결 상태 점 추가.

---

## 목적

`system` 채널 이벤트로 키움 WS 연결 상태를 추적해 헤더에 점으로 표시.
- 초록 점: 키움 WS 연결됨
- 빨강 점: 끊김 (초기값 — 연결 확인 전까지)

---

## 전체 흐름

```
BrokerStatus 마운트
  └─ useBrokerEvents 훅 → EventSource 연결
      └─ system 이벤트 수신
          ├─ payload.type === "connected"    → setConnected(true)  → 초록 점
          └─ payload.type === "disconnected" → setConnected(false) → 빨강 점

키움 WS 끊김 (네트워크/점검)
  └─ manager.py 재연결 시도 → disconnected → 5초 후 connected
      └─ 점 빨강 → 초록 자동 복구
```

**입력**: 없음 (system 채널 이벤트 자동 구독)
**출력**: 헤더 우측에 색 점 렌더링

---

## 구조

`BrokerStatus` 컴포넌트가 이벤트 구독 + 점 렌더링 모두 담당.
Nav(이미 "use client")에서 import해 헤더 우측에 배치.

```
nav.tsx (기존, "use client")
  └─ header 우측: <BrokerStatus />
      └─ useBrokerEvents → system 채널 → useState(connected)
          └─ 점 렌더링
```

EventLogger는 이 STEP에서 제거(테스트용 임시 파일).

---

## 정답 코드

### broker-status.tsx (신규)

```tsx
"use client";

import { useState } from "react";
import { useBrokerEvents } from "@/lib/use-broker-events";

export function BrokerStatus() {
  const [connected, setConnected] = useState(false);

  useBrokerEvents((e) => {
    if (e.channel !== "system") return;
    if (e.payload.type === "connected") setConnected(true);
    if (e.payload.type === "disconnected") setConnected(false);
  });

  return (
    <div className="flex items-center gap-1.5" title={connected ? "키움 WS 연결됨" : "키움 WS 끊김"}>
      <span className={`w-2 h-2 rounded-full ${connected ? "bg-emerald-400" : "bg-red-500"}`} />
      <span className="text-xs text-muted-foreground">{connected ? "연결됨" : "끊김"}</span>
    </div>
  );
}
```

### nav.tsx (수정 — BrokerStatus 추가)

```tsx
import { BrokerStatus } from "@/components/common/broker-status";

// header 태그 내 우측에 추가:
<header className="border-b border-border px-6 py-3 flex items-center justify-between">
  <div className="flex items-center gap-6">
    {/* 기존 로고 + nav 링크 */}
  </div>
  <BrokerStatus />   {/* ← 추가 */}
</header>
```

### layout.tsx + event-logger.tsx 정리

- `layout.tsx`: `EventLogger` import 및 `<EventLogger />` 제거
- `event-logger.tsx`: 파일 삭제

---

## 코드 줄별 설명

- **`useState(false)`**
  초기값 false = 빨강. 서버 켜지고 WS 연결되면 system connected 이벤트 → true.

- **`useBrokerEvents((e) => { ... })`**
  훅에 콜백 전달. `setConnected` 는 React 보장 안정 참조라 `[]` 의존성(마운트 1회)과 함께 써도 stale closure 없음.

- **`if (e.channel !== "system") return`**
  system 외 채널(00=체결 등) 무시. 이 컴포넌트는 연결 상태만 관심.

- **`w-2 h-2 rounded-full`**
  Tailwind: 8px 원형 점. `bg-emerald-400` / `bg-red-500` 으로 색 전환.

- **`title={...}`**
  마우스 오버 시 툴팁 (브라우저 기본). 별도 라이브러리 불필요.

---

## 검증

1. 브라우저 열기 → 초기 빨강 점 확인
2. 백엔드 서버 켜져 있으면 수 초 내 초록으로 바뀌는지 확인
   (WS 매니저 connected → system 이벤트 → BrokerStatus 반응)
3. 백엔드 서버 종료 → 빨강으로 바뀌는지 확인
   (SSE 끊김 → EventSource 자동 재연결 시도 → 백엔드 재시작 후 다시 초록)

---

## 흔한 함정

- **`title` prop에 연결 상태 텍스트만 있고 점 색이 없으면** 색맹 사용자 구별 불가. 점 + 텍스트 둘 다 유지.
- **초기값 `true` 로 설정하면** 실제 연결 확인 전에 초록 → 거짓 신뢰. `false` 로 시작해야.

---

## 다음

STEP 8 — 체결 토스트 + 노트 모달.
`channel === "00"` 이벤트 수신 → 전역 토스트 표시 → "노트 작성" 클릭 → NoteModal.
