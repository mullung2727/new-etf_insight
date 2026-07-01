# 크론 레지스트리 + 계좌패널 체결반영 — 수정 계획

## 한 줄 요약
2026-07-01 code-review(6각도) 10건 중 9건 완료. 남은 1건(SSE 연결 공유)은 다파일 리팩터·회귀위험이라 단계로 쪼개 진행.

---

## 완료 (9건)

- **1회성 스크립트 삭제** — `start-servers-0701.xml` / `run-start-servers.ps1` (PS 자동변수 `$args` 충돌), `close-bet-test-0701` (파일 삭제 + Task Scheduler 등록 해제).
- **Test-OpenClawBatchRegistry.ps1** — `$job.$field -eq ""` 가 `enabled:$false` 를 오탐하던 것 → string 타입일 때만 빈값 검사.
- **Export-OpenClawCronSpecs.ps1** — 단건(`-JobName`) export가 스칼라로 언랩되던 것 → `ConvertTo-Json -InputObject @($specs)` 로 배열 강제 (파이프는 재-언랩되므로 -InputObject 필수). + Export 시작부에서 Test- 먼저 호출.
- **isFillEvent 헬퍼** — `channel==="00" && payload["913"]==="체결"` 중복 → `use-broker-events.ts` 에 `isFillEvent(e)` 로 뽑아 account-panel·fill-toast 공유.
- **account-panel getSettings 낭비** — 체결마다 settings 재조회하던 것 → `loadAccount()`(잔고+예수금) / `load()`(settings 포함) 분리, 체결·토글은 loadAccount 만.
- **account-panel race** — 체결 연타 시 stale 응답이 최신 덮어쓰던 것 → `reqSeq` ref 세대카운터로 최신 호출만 반영.
- **스케줄 중복** — registry.json ↔ 각 job .md `## Cron` 블록 중복 → 4개 .md에서 스케줄/tz/session/delivery 라인 삭제, registry 포인터 한 줄로 교체. 스케줄 값 = registry 단일 소스.

---

## 남은 1건: SSE 연결 공유 리팩터

### 문제
`account-panel` / `fill-toast` / `account-tabs` / `pending-orders` / `broker-status` 5개가 각자 `useBrokerEvents` 로 독립 `EventSource(/events)` 를 연다. 트레이딩 페이지 한 장에 최대 5개 동시 연결 → 브라우저 도메인당 SSE 한도(~6) 근접, 같은 이벤트를 N번 파싱.

### 목표
페이지당 `/events` 연결을 **1개**로. 단, `useBrokerEvents(cb)` **시그니처는 유지** → 소비자 5개 코드 무수정.

### 방식
`useBrokerEvents` 내부만 교체:
- Provider 하나가 단일 `EventSource` 를 열고, 구독자 콜백 Set 에 이벤트를 fan-out.
- `useBrokerEvents(cb)` 는 EventSource 를 여는 대신 Provider 의 Set 에 자기 콜백을 등록/해제만.

### 단계

**1단계 — Provider + fan-out (`lib/use-broker-events.ts`)**
- `BrokerEventsProvider`: `useRef<Set<(e)=>void>>` 구독자 집합 + `useEffect` 로 단일 `EventSource` open, `onmessage` 파싱해 전 구독자 호출, unmount 시 close. `BrokerEvent` 타입·`isFillEvent` export 유지.
- `useBrokerEvents(cb)`: `useContext` 로 Set 얻어 mount 시 add / unmount 시 delete. **콜백은 ref 에 담아 등록**(매 렌더 새 클로저지만 최신값 호출, stale 방지·재구독 없음).
- 검증: 자체 self-check 불필요(런타임 브라우저 확인), tsc 통과.

**2단계 — Provider 마운트 (`app/layout.tsx`)**
- server layout `body` 안 `Nav` / `main` / `FillToast` 를 `<BrokerEventsProvider>` 로 감쌈. client provider 가 server children 감싸는 건 정상. 소비자 전부 이 하위라 컨텍스트 도달.

**3단계 — 검증**
- `tsc --noEmit` 클린.
- (수동) 브라우저 DevTools Network 에서 `/events` 연결 **1개**만 뜨는지.
- (수동) 5개 소비자 반응 유지: 체결 토스트 뜸 / 계좌 잔고·주문가능 갱신 / 미체결 목록·배지 갱신 / 헤더 연결상태 점.

각 단계 완료 후 보고 → 확인 후 다음.

### 회귀 위험
- Provider 없는 페이지(트레이딩 외)에서 `useBrokerEvents` 쓰면 no-op 이어야 함 → context 기본값을 안전한 빈 Set 으로.
- StrictMode 이중 마운트로 EventSource 두 번 열림 주의 → cleanup 에서 확실히 close.

---

## 참고
- 원 리뷰: 6각도(A/B/C + reuse/simplification/altitude + conventions) 병렬 `/code-review high`.
- conventions 각도 findings 0건.
