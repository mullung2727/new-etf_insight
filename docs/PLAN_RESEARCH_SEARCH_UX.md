# PLAN: 리서치 종목검색 UX 개선

## 목표

`broker-web/app/research/page.tsx`의 종목검색을 디자인시스템(shadcn 토큰) 기반으로 다듬는다.
현재 드롭다운은 raw 색(`bg-white`/`gray-*`)을 박아 테마·배경과 비슷해져 안 보이고, 선택 종목이
`"종목명 (종목코드)"` 텍스트로 input에 박혀 있어 컴포넌트감이 없다.

## 배경 — 디자인시스템은 이미 있다

- base shadcn 토큰: `bg-popover / text-popover-foreground / hover:bg-accent / text-muted-foreground / border / ring`
  (다크·라이트 자동 대응). `components/ui/*`가 전부 이 토큰을 씀(dialog/select/badge 등).
- fin-scope 확장 토큰(`text-fin-*`, gold)은 DART 재무 화면 전용 — /research엔 **쓰지 않는다**.
- `components/financial/SearchBar.tsx`는 fin-scope 골드 터미널 룩(인라인 스타일 다수) → 스타일 재활용 대상 아님.
  단 **자동완성 로직 패턴**(키보드 ↑↓/Enter/Esc, 외부클릭 닫기, 정확코드 상단정렬)은 참고한다.

## 성공 기준

- 자동완성 드롭다운이 raw 색 없이 토큰만 사용 → 어느 테마/배경에서도 또렷.
- 후보 항목: **종목명 강조 + 종목코드 옅은 보조표기**(`text-muted-foreground`). 코드로도 검색 가능(유지).
- 종목 선택 시 input이 **칩(Badge)+X** 로 바뀐다. X 클릭 → 선택 해제 + **조회 목록·선택·job 초기화**(종목 교체 준비).
- 조회 버튼이 **검색창 바로 옆**에 있고, 목록 로드는 버튼 클릭 시에만(자동조회 아님, 기존 유지).
- 날짜 초기값: **종료일=오늘, 시작일=오늘−3개월**. 사용자가 바꾸면 **localStorage에 저장**, 다음 방문 시 저장값 우선.
- 기존 선택 다운로드 e2e(체크박스/토글/다운로드 body)는 그대로 통과.

## 결정

- 날짜 기억: **localStorage** (zustand 미설치·신규 의존성 과함, 날짜 2개엔 `mount 읽기 + onChange 쓰기`로 충분).
- 날짜 정책: 저장값이 있으면 **유지**(열 때마다 오늘로 리셋하지 않음). "오늘로" 리셋 버튼은 이번 범위 밖.
- 코드 표기: 완전 제거 아님, **옅게 유지**(동명 종목 구분).
- 칩: 기존 `Badge` + `lucide-react` `XIcon` 재활용. 신규 Command/Popover 컴포넌트 **추가 안 함**.
- 서버(api) 변경 없음 — 프론트 전용 작업.

---

## 1단계: 날짜 기본값 + 기억 (순수 유틸 + 단위 테스트)

### 변경

- `broker-web/lib/research-prefs.ts` 신규(순수 함수, DOM/스토리지 주입 가능하게).
  - `todayISO(now: Date): string` → `YYYY-MM-DD`(로컬 기준).
  - `monthsAgoISO(now: Date, months: number): string`.
  - `initialRange(now: Date, saved: {since?: string; until?: string} | null): {since: string; until: string}`
    - saved.since/until 있으면 그 값, 없으면 `until=todayISO(now)`, `since=monthsAgoISO(now, 3)`.
  - `loadSavedRange(): {...} | null` / `saveRange(r): void` → `localStorage["research.dateRange"]` JSON. try/catch(스토리지 비활성 안전).

### TDD

- `__tests__/research-prefs.spec.ts` (순수 단위 — bs-topn.spec.ts처럼 브라우저 불필요):
  - `initialRange` 기본값: `now=2026-07-04` → `until=2026-07-04`, `since=2026-04-04`.
  - 월 경계: `now=2026-01-15` → `since=2025-10-15`(연도 넘어감).
  - saved 우선: `saved={since:"2026-01-01",until:"2026-02-01"}` 그대로 반환.
  - 일 경계(말일): `now=2026-05-31` → 3개월 전 `2026-02-28`(2월 없는 날 clamp). ← Date 산술 함정 검증.

### 놓치기 쉬운 포인트

- `new Date().toISOString()`은 UTC라 한국 자정 근처 날짜가 하루 밀린다 → **로컬 연/월/일**로 조립.
- 3개월 전 계산에 `setMonth(m-3)`는 5/31→3/3 롤오버 발생 → 말일 clamp 처리 필요.

---

## 2단계: 종목 선택 칩 + 토큰 드롭다운

### 변경

- `broker-web/app/research/page.tsx`
  - 드롭다운 `<ul>`: `bg-white/shadow/gray-*` 제거 → `bg-popover text-popover-foreground border rounded-md shadow-md`.
    항목 `hover:bg-accent hover:text-accent-foreground`, 코드 `text-muted-foreground`.
  - 선택 상태 표시 전환:
    - 미선택: 검색 input 노출.
    - 선택됨: input 숨기고 **`Badge`(종목명 + 코드 옅게) + X 버튼**(`XIcon`). X → `clearSelection()`:
      `setSelected(null); setQ(""); setData(null); setSelectedIds(new Set()); setJob(null); setCandidates([])`.
  - 조회 버튼을 **검색/칩과 한 줄**로 이동(날짜는 아랫줄 유지).
  - (선택) 키보드 ↑↓/Enter/Esc + 외부클릭 닫기 — SearchBar 로직 참고. v1 필수는 아니나 있으면 반영.
  - `text-gray-600`(목록 요약 문구)도 `text-muted-foreground`로 교체.

### TDD

- `__tests__/research-search-ux.spec.ts` (Playwright route mock):
  - 후보 드롭다운에 종목명·코드 둘 다 렌더, 코드 요소가 `text-muted-foreground` 클래스인지.
  - 선택 → `Badge`에 종목명 보이고 검색 input 사라짐.
  - 칩 X 클릭 → input 복귀, 리포트 테이블(`data`) 사라짐, 선택 0.
  - 조회 버튼이 검색행에 존재(role=button name=조회) & 클릭 시 목록 로드.

### 놓치기 쉬운 포인트

- 드롭다운 `bg-popover`는 반투명 아님 → 뒤 비침 없음. 토큰만으로 대비 확보.
- 칩 X 버튼은 `<button>`로 감싸 접근성(aria-label "선택 해제") + `type="button"`(폼 submit 방지).
- 코드 "옅게"는 색만, 검색은 여전히 `search?q=코드`로 동작해야 함(백엔드 그대로).

---

## 3단계: 날짜 기본값·기억 결선

### 변경

- `page.tsx`
  - `since/until` 초기값을 `useState(() => { const r = initialRange(new Date(), loadSavedRange()); ... })`로 지연 초기화.
  - `onChange`에서 `setSince/ setUntil` 후 `saveRange({since, until})`.
  - 첫 렌더 값이 SSR/CSR 불일치 없도록 `"use client"` 컴포넌트라 문제 없음(이미 client).

### TDD

- `research-search-ux.spec.ts`에 추가:
  - 최초 진입 시 `시작일` input value = 오늘−3개월, `종료일` = 오늘(브라우저 clock 기준 계산과 대조).
  - 날짜 변경 → `page.reload()` → 변경값 유지(localStorage). (동일 context 유지되게 `test.use` 또는 같은 page).

### 놓치기 쉬운 포인트

- Playwright는 test마다 새 context(스토리지 비움) → persistence 테스트는 **한 test 안에서 reload**로 검증.
- 날짜 비교는 문자열(`YYYY-MM-DD`)로. 테스트에서 오늘값은 프로덕션과 동일 로직(`initialRange`)으로 재계산해 대조(하드코딩 금지).

---

## 4단계: 통합 검증 + 커밋

### 검증 명령

```powershell
cd broker-web
npm run lint
npx playwright test __tests__/research-prefs.spec.ts __tests__/research-search-ux.spec.ts __tests__/research-selective-download.spec.ts
```

수동:

1. `/research` 진입 → 드롭다운·칩·날짜 기본값 눈으로 확인(다크/라이트 토글 포함).
2. 종목 선택 → 칩 표시 → X → 초기화.
3. 날짜 바꾸고 새로고침 → 유지.

### 놓치기 쉬운 포인트

- 기존 `research-selective-download.spec.ts`가 `"삼성전자 (005930)"` input 값이나 특정 셀렉터에 의존하면
  칩 전환으로 깨질 수 있음 → **그 spec 셀렉터 먼저 점검**하고 필요한 부분만 갱신(기능 회귀 아님, 셀렉터만).
- `page.tsx:63` 기존 lint 경고(`set-state-in-effect`, 자동완성 effect)는 이번 범위 밖 — 건드리지 않음.

## 작업 순서

1. `research-prefs.ts` + 단위 테스트 → 실패 확인 → 구현 → 통과.
2. 드롭다운 토큰화 + 칩/X + 조회버튼 이동, e2e 추가 → 실패 → 구현 → 통과.
3. 날짜 기본값/기억 결선, e2e 추가 → 통과.
4. 셀렉터 회귀 점검 + lint + 전체 spec + 수동 확인 → 커밋.
