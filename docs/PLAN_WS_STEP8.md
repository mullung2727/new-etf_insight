# STEP 8 — 체결 토스트 + 노트 모달

**파일**: `broker-web/components/common/fill-toast.tsx` (신규), `broker-web/app/layout.tsx` (수정)

> WS 파이프라인 완성의 최종 단계. 체결 이벤트가 토스트로 표시되고 노트 작성을 강제.

---

## 목적

체결(`channel === "00"`) 이벤트 수신 → 토스트 표시 (수동 닫기만 가능) → "노트 작성" 클릭 → NoteModal 열림.

전역(layout.tsx)에 달아 어느 페이지에 있어도 체결 시 뜸.

---

## 전체 흐름

```
키움 체결 발생
  └─ manager.py → bus.publish("00", payload)
      └─ SSE → useBrokerEvents → channel="00", payload["913"]="체결"
          └─ setFills([...prev, newFill])  → 토스트 렌더링

사용자: "노트 작성" 클릭
  └─ setNoteSymbol(fill.symbol) + 토스트 제거
      └─ NoteModal 열림 (uid="new", symbol 자동 입력)
          └─ 저장 → onSaved → 모달 닫힘

사용자: "닫기" 클릭
  └─ 토스트만 제거 (노트 작성 없이)
```

**입력**: 없음 (SSE 자동 구독)
**출력**: 화면 우하단 토스트 + NoteModal

---

## 체결 payload 필드

키움 RT 00 채널 주요 필드:

| 필드 | 내용 |
|------|------|
| `"913"` | 체결구분 — `"체결"` 일 때만 처리 (주문접수 등 필터) |
| `"9001"` | 종목코드 (예: `"005930"`) |
| `"905"` | 매수/매도 구분 — **장중 실체결로 확인 필요** (통상 `"1"`=매수, `"2"`=매도) |
| `"910"` | 체결가 |
| `"911"` | 체결량 |

> ⚠️ `"905"` 값은 kiwoom_api.xlsx 기준으로 장중 실체결 후 확인하고 수정.

---

## 정답 코드

### fill-toast.tsx (신규)

```tsx
"use client";

import { useState } from "react";
import { useBrokerEvents } from "@/lib/use-broker-events";
import { NoteModal } from "@/components/notes/note-modal";
import { Button } from "@/components/ui/button";

type Fill = {
  id: number;
  symbol: string;
  side: string;
  price: string;
  qty: string;
};

export function FillToast() {
  const [fills, setFills] = useState<Fill[]>([]);
  const [noteSymbol, setNoteSymbol] = useState<string | null>(null);

  useBrokerEvents((e) => {
    if (e.channel !== "00") return;
    if (e.payload["913"] !== "체결") return;
    setFills((prev) => [
      ...prev,
      {
        id: Date.now(),
        symbol: String(e.payload["9001"] ?? ""),
        side:   String(e.payload["905"]  ?? ""),
        price:  String(e.payload["910"]  ?? ""),
        qty:    String(e.payload["911"]  ?? ""),
      },
    ]);
  });

  const dismiss = (id: number) =>
    setFills((prev) => prev.filter((f) => f.id !== id));

  return (
    <>
      <div className="fixed bottom-4 right-4 flex flex-col gap-2 z-50">
        {fills.map((fill) => (
          <div
            key={fill.id}
            className="bg-card border border-border rounded-lg shadow-lg p-4 w-72 flex flex-col gap-2"
          >
            <div className="flex items-center justify-between">
              <span className="font-mono font-semibold">{fill.symbol}</span>
              <span className="text-xs text-muted-foreground">
                {fill.side === "1" ? "매수" : "매도"} 체결
              </span>
            </div>
            <div className="text-sm tabular-nums">
              <span className="text-muted-foreground">체결가 </span>
              {Number(fill.price).toLocaleString()}원
              <span className="text-muted-foreground ml-3">수량 </span>
              {fill.qty}주
            </div>
            <div className="flex gap-2 justify-end">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setNoteSymbol(fill.symbol);
                  dismiss(fill.id);
                }}
              >
                노트 작성
              </Button>
              <Button size="sm" variant="ghost" onClick={() => dismiss(fill.id)}>
                닫기
              </Button>
            </div>
          </div>
        ))}
      </div>

      <NoteModal
        uid={noteSymbol !== null ? "new" : null}
        symbol={noteSymbol ?? undefined}
        onClose={() => setNoteSymbol(null)}
        onSaved={() => setNoteSymbol(null)}
      />
    </>
  );
}
```

### layout.tsx (수정 — FillToast 추가)

```tsx
import { FillToast } from "@/components/common/fill-toast";

// body 안에 추가:
<body className="min-h-full flex flex-col">
  <Nav />
  <FillToast />          {/* ← 추가 */}
  <main className="flex-1">{children}</main>
</body>
```

---

## 코드 줄별 설명

- **`fills: Fill[]`**
  복수 토스트 지원 — 빠른 연속 체결 시 쌓임. 배열로 관리.

- **`useBrokerEvents((e) => { setFills(prev => ...) })`**
  `setFills` 는 React 보장 안정 참조. functional updater(`prev =>`) 쓰므로
  `[]` 의존성(마운트 1회)이어도 stale closure 없음.

- **`if (e.payload["913"] !== "체결") return`**
  주문접수·취소 등 체결 아닌 00채널 메시지 필터. 실제 체결만 토스트.

- **`id: Date.now()`**
  토스트 고유 키. 밀리초 단위라 동시 체결 가능성 낮지만, 필요 시 counter 변수로 교체.

- **`dismiss(id)`**
  `setFills(prev => prev.filter(...))` — functional updater라 현재 상태 보장.

- **`노트 작성` onClick**
  `setNoteSymbol(fill.symbol)` → 모달 열기 + 해당 토스트 닫기(동시).

- **`NoteModal uid={noteSymbol !== null ? "new" : null}`**
  `uid=null` 이면 모달 닫힘(Dialog `open={uid !== null}`). `"new"` 면 신규 작성 모드.
  기존 NoteModal 그대로 재사용, 추가 수정 없음.

- **`fixed bottom-4 right-4 z-50`**
  화면 우하단 고정. 다른 UI 위에 뜸. 자동 닫힘 없음.

---

## 검증

### 검증 1 — UI 확인 (장 무관, 수동 발행)

백엔드 서버 켜진 상태에서 Python으로 직접 발행:

```python
# broker 디렉토리에서 (--reload 없이 실행 중인 서버에선 안 통함)
# 대신 test_fill.py 작성 후 단독 실행:

import asyncio
from kiwoom.ws.event_bus import bus

async def main():
    bus.publish("00", {
        "913": "체결",
        "9001": "005930",
        "905": "1",
        "910": "75000",
        "911": "5",
    })

asyncio.run(main())
```

> 서버가 `--reload` 모드면 별도 프로세스라 bus 공유 안 됨. `--reload` 없이 실행하거나,
> 아래 검증 2 방법 사용.

### 검증 2 — 장중 실체결

장중 모의 지정가 매수 → 체결 시 우하단 토스트 표시 확인:
- 종목코드, 체결가, 수량 정확한지
- "닫기" → 토스트만 사라짐
- "노트 작성" → NoteModal 열리고 종목코드 자동 입력 확인
- 저장 후 투자노트 목록에서 확인

### 검증 3 — 복수 체결

연속 매수 2건 → 토스트 2개 쌓이는지 확인.

---

## 흔한 함정

- **`905` 값 매핑 틀리면** 매수를 매도로 표시. 장중 확인 필수.
- **`id: Date.now()` 충돌** — 동일 밀리초 체결 시 key 중복. 실운영에서 문제 되면 `useRef(0)` counter로 교체.
- **NoteModal `uid=null` 조건** — `uid=""` 등 falsy 값 주의. `null` 만 닫힘 트리거.

---

## 다음

파이프라인 완성. 이후 추가 작업:
- `905` 값 장중 검증 후 수정 (필요 시)
- 실시간 시세 구독 (채널 0B) — 필요 시 STEP 추가
- 조건검색 실시간 (채널 04) — 필요 시 STEP 추가
