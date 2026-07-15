# 종목 분석 일원화 — 기획·TDD 설계

> **상태**: **Phase 1~6 완료** (2026-07-15). 종목 분석 일원화 + 언급 통합 탭까지.
> 이 문서는 **목적·구조·단계별 목표·TDD 절차**. 각 Phase는 테스트 먼저(red) → 구현(green) → 정리 순.

## 한 줄

흩어진 종목별 조회 탭(**재무제표 · 리포트**)을 기존 종목 허브 `/stock/[code]`로 흡수하고,
**종목 검색 진입점**을 추가해 `종목 분석` 하나로 일원화한다.

---

## 목적 / 배경

현재 종목별 자료가 두 축으로 쪼개져 있다.

| 위치 | 정체 | 진입 |
|---|---|---|
| `/stock/[code]` 허브 | 개요·차트·**재무**·텔레그램·유튜브·노트 탭 | watchlist·홈·유튜브표 **클릭만** (검색 불가) |
| `/financial` (재무제표) | DART 다기간 재무 비교표 | **종목 검색** 가능 |
| `/research` (리포트) | 증권사 리포트 목록·다운로드 | **종목 검색** 가능 |

**핵심 관찰**
- 허브의 `재무` 탭(`components/stock/financial-tab.tsx`)은 이미 `/financial`과 **같은 CompareTable**을 렌더. 재무는 사실상 중복.
- 허브엔 **검색 진입이 없고**, `/financial`·`/research`엔 검색이 있음.
- 즉 허브 = 이미 "합친 것". 빠진 건 **리포트 탭 + 검색 진입 + 이름**뿐.

**목표**: 허브를 유일 종착지로. 검색은 재무제표 페이지 UX를 살려 랜딩으로. nav 축소.

---

## 결정 사항 (확정)

| 항목 | 결정 |
|---|---|
| nav 라벨/개념 | **종목 분석** (라우트 `/stock` 유지) |
| 유튜브 nav | **이번엔 유지** (허브 탭엔 이미 있음, 전역 nav만 존치) |
| 리포트 탭 범위 | 현 `/research` 기능 **통째 이관** (검색 제외: 목록·선택·다운로드·진행률) |

---

## 식별자 결론 (브릿지 불필요)

- 검색 인덱스 `/api/corps`는 각 항목에 `corp_code` **와** `stock_code`를 모두 보유 (`CompareSearchBar.tsx`).
- 리포트·유튜브·텔레그램 API는 `stock_code`(6자리 ticker) 키. 검색 결과에서 바로 얻음 → 매핑 코드 불필요.
- 재무만 `corp_code` 필요 → `financial-tab.tsx`가 `corpCodeForStock`로 이미 해결.

**주의**: corps 인덱스는 `stock_code` 있는 종목만(상장·DART 등록). 미등록·일부 ETF는 검색에 안 뜸. 종목 리서치 범위엔 무방.

---

## 목표 구조

```
nav:  ETF분석 · 트레이딩 · 투자노트 · [종목 분석] · 지표랭킹 · watchlist · 종가배팅 · 유튜브 · 설정
                                    │
        /stock  (검색 랜딩, 신규)   │  종목 검색 → 자동완성 → 선택
                                    ▼
        /stock/[code]?tab=…  (허브, 기존)
          개요 · 차트 · 재무 · 리포트(신규) · 언급(텔레그램+유튜브 통합·신규) · 노트
```

> `텔레그램`·`유튜브` 개별 탭은 **언급** 탭 하나로 통합(Phase 6). 소스 필터 토글로 개별 조회 커버.

- `/financial`·`/research` 라우트 제거(→ `/stock` 리다이렉트).
- 기존 허브 진입(watchlist·홈·유튜브표 클릭)은 그대로 유지.

---

## 테스트 이관 매핑

| 기존 스펙 | 처리 |
|---|---|
| `__tests__/research-search-ux.spec.ts` | `/research` 검색 UX → **삭제**(검색은 `/stock` 랜딩으로 대체, Phase 2 스펙이 커버) |
| `__tests__/research-selective-download.spec.ts` | 목록·선택·다운로드 → **경로만** `/stock/[code]?tab=research`로 갱신해 존치 |
| `__tests__/financial-toggle.e2e.spec.ts` | 현재 `/financial` 토글 → **경로 갱신** `/stock/[code]?tab=financial` (Phase 3) |
| `__tests__/stock-hub.spec.ts` | `STOCK_TABS` 변경분(`research` 추가·`telegram`/`youtube`→`mentions`) **어서션 보강** (Phase 1·6) |
| `__tests__/financial-compare.spec.ts`, `api-compare*.spec.ts` | 로직·API 불변 → **유지** |

---

## 단계별 목표 (TDD)

각 Phase: **① 실패 테스트 작성 → ② 구현 → ③ green 확인 → ④ 사용자 보고**.
러너: `npm test`(= `rtk playwright test`). 단일 스펙: `rtk playwright test __tests__/<파일>`.

### Phase 1 · 리포트 탭 추가

**목표**: 허브에 `리포트` 탭. `/research`의 목록·선택·다운로드·진행률을 탭 컴포넌트로 이관.

**Red (먼저 작성)**
- `__tests__/stock-hub.spec.ts` 보강: `STOCK_TABS` includes `"research"`, `resolveTab("research") === "research"`, `stockHubHref(code,{tab:"research"})` == `/stock/{code}?tab=research`.
- `__tests__/research-selective-download.spec.ts` 복제→`research-tab.e2e.spec.ts`: `page.goto('/stock/005930?tab=research')` 후 목록 표시·체크박스 선택·다운로드 job 진행률(기존 mock 재사용).

**Green (구현)**
- new `components/stock/research-tab.tsx`: `app/research/page.tsx`에서 **검색·자동완성 제외** 전부 이관. props `{ code: string; name: string }`.
  - **selected 제거 확정**: `selected`·`candidates`·`q`·자동완성 effect·`clearSelection`·종목 검색/해제 UI **전부 삭제**. 기존 `selected.code`→`code`, `selected.name`→`name` prop으로 치환.
  - **진입 시 자동 조회**: mount `useEffect`에서 `loadReports()` 1회 호출(조회 버튼 별도 클릭 불필요). 기간·재조회 버튼은 유지.
  - **code 변경 초기화**: 탭은 `app/stock/[code]/page.tsx`가 code별로 다른 페이지라 remount됨. 안전하게 `<ResearchTab key={code} …/>`로 명시 → 목록·selectedIds·job 리셋 보장.
- `lib/stock-hub.ts`: `STOCK_TABS`에 `"research"` 추가.
- `app/stock/[code]/page.tsx`: `TAB_LABEL.research = "리포트"`, import + `tab === "research"` 분기(`<ResearchTab key={code} code={code} name={name ?? code} />`).

**DoD**: 위 2개 스펙 green. 허브에서 리포트 목록·다운로드 실동작.

---

### Phase 2 · 검색 랜딩(전역 진입점)

**목표**: `/stock`(코드 없음) = 종목 검색 페이지. 선택 시 허브로.

**Red**
- new `__tests__/stock-search-landing.e2e.spec.ts`: `/api/corps` mock → `/stock` 방문 → 입력 `삼성전자` → 자동완성 선택 → URL `/stock/005930?tab=financial` 로 이동 검증.

**Green**
- `CompareSearchBar`에 **`variant` prop 추가**(`"financial" | "stock"`, 기본 `"financial"`):
  - 표시 문구 분기 — 헤더 `DART Financial Compare · 연간 5년`, 푸터 `CORP_CODE: … · fs_div 자동` 은 `variant==="financial"`에서만. `variant==="stock"`은 중립 문구(예 헤더 `종목 검색`, 푸터 `종목을 선택하면 종목 분석 허브로 이동`).
  - 자동완성·corps 인덱스 로직은 **공유**(재무 전용 문구만 감춤). 콜백만으로 부족하다는 지적 수용 → 표시 분기 명시.
- `CompareSearchBar.onSearch` 시그니처 확장: `(corpCode, corpName, stockCode)` — corps 항목에 `stock_code` 있음. 재무제표 페이지(=Phase 5에서 리다이렉트) 호출부도 인자 무시로 호환.
- new `app/stock/page.tsx`(현재 없음): `<CompareSearchBar variant="stock" onSearch={(_, name, stock) => router.push(stockHubHref(stock,{tab:"financial",name}))} />`.

> 대안(과하면): 순수 검색 컴포넌트로 분리. variant 분기가 더 작아 우선.

**DoD**: 스펙 green. `/stock`에서 검색·선택 → 허브 진입.

---

### Phase 3 · 재무 탭 완성(standalone 대체)

**목표**: 허브 `재무` 탭에 annual/quarterly 토글 → `/financial` 기능 완전 대체.

**Red**
- `__tests__/financial-toggle.e2e.spec.ts` 경로를 `/stock/005930?tab=financial`로 갱신 → red 확인. 토글은 **링크 네비게이션**(`?fin_mode=` 변경)이라 클릭 후 URL·재렌더 검증으로 조정.

**Green (client/server 경로 확정)**
- **브라우저에서 `@/lib/dart` 직접 호출 금지**(서버 전용, API 키). 토글은 client fetch 대신 **URL 파라미터 서버 재렌더**로 처리 — 허브 탭이 이미 URL 딥링크 구조라 가장 작음.
  - `financial-tab.tsx`(서버): `stock_code → corp_code` 해결은 지금처럼 서버에서. `mode` prop 추가 → `fetchCompare(corp, mode==="quarterly"?8:5, mode)` (여전히 서버 호출, 브라우저 아님).
  - `app/stock/[code]/page.tsx`: `searchParams.fin_mode` 읽어 `<FinancialTab code={code} mode={finMode} />`로 전달.
  - 토글 UI = client 소컴포넌트(`financial-mode-toggle.tsx`): `annual`/`quarterly` 링크(`stockHubHref` + `fin_mode`), 현재 mode만 표시. 데이터 fetch 안 함.
- **대안(리뷰안)**: client wrapper가 `/api/financial/compare?corp_code=…&mode=…` 호출(이미 존재, route.ts). URL 방식이 더 작아 우선, 필요 시 이걸로.

**DoD**: 갱신 스펙 green. 허브 재무 탭 annual↔quarterly 전환 동작. 브라우저 네트워크에 `lib/dart` 직접호출 없음.

---

### Phase 4 · nav 정리 + 이름

**목표**: nav에서 `재무제표`·`리포트` 제거, `종목 분석`(→`/stock`) 추가.

**Red**
- new `__tests__/nav-links.spec.ts`(또는 기존 nav 테스트 있으면 보강): nav에 `종목 분석` 존재, `재무제표`·`리포트` 부재.

**Green**
- `components/common/nav.tsx` `links`: `재무제표`·`리포트` 항목 삭제, `{ href:"/stock", label:"종목 분석" }` 추가. 유튜브 유지.

**DoD**: 스펙 green. nav 항목 10→9.

---

### Phase 5 · 레거시 라우트 유지 + 리다이렉트

**목표**: `/financial`·`/research` **라우트 파일은 유지**하되 내용을 `/stock` 리다이렉트로 교체(북마크·외부 링크 무손상). "라우트 제거" 아님.

**Red**
- new `__tests__/legacy-redirect.e2e.spec.ts`: `/financial`·`/research` 방문 → `/stock`으로 리다이렉트.

**Green**
- `app/financial/page.tsx`·`app/research/page.tsx` 내용을 `redirect("/stock")` (Next `redirect`)로 교체(**파일 존치**). 이관 끝난 컴포넌트(`components/financial/*` 중 CompareSearchBar/CompareTable은 탭·랜딩이 재사용 → 유지).
- `research-search-ux.spec.ts` 삭제. `research-selective-download.spec.ts`는 Phase 1에서 경로 갱신됨.

**DoD**: 리다이렉트 스펙 green. 전체 `npm test` green. 죽은 import 없음.

---

### Phase 6 · 언급 통합 탭 (텔레그램 + 유튜브) — 후속 분리

> **범위 주의**: 핵심 일원화(Phase 1~5)보다 범위 큼(탭 통합·레거시 딥링크·실패정책). **Phase 1~5 완료·머지 후 별도 작업**으로 진행. 아래는 설계만 확정.

**목표**: 종목별 텔레그램·유튜브 언급을 **한 타임라인**으로. 개별 탭 2개 → `언급` 1개. 데이터·API 신규 없음(기존 두 쿼리 합쳐 정렬만).

**근거**: 두 탭이 이미 같은 행 구조(날짜·요약·소스뱃지·원문링크), code 키 동일. 텔레그램 추가분(session·change_type·themes·테마피어), 유튜브 추가분(video_ids)만 소스별로 붙임.

**Red**
- new `__tests__/mentions-tab.e2e.spec.ts`: `getTelegramMentions`·`getYoutubeMentions` mock → `/stock/005930?tab=mentions` → 두 소스 행이 **날짜 desc 한 목록**에 섞여 표시, 각 행 소스 뱃지(텔레그램/유튜브) 검증. 소스 필터 토글 = 유튜브 → 텔레그램 행 숨김 검증. **한쪽 쿼리 실패(reject) mock → 나머지 소스 행은 그대로 표시** 검증.
- `__tests__/stock-hub.spec.ts` 보강: `STOCK_TABS`에 `mentions` 포함, `telegram`/`youtube` 제거. **레거시 호환**: `resolveTab("telegram")==="mentions"`, `resolveTab("youtube")==="mentions"`.

**Green**
- new `components/stock/mentions-tab.tsx`: `getTelegramMentions(code,{session})` + `getYoutubeMentions(code)`를 **`Promise.allSettled`**로 병렬 → 각 소스 `rejected`면 그 소스만 빈 배열(전체 탭 실패 아님), 둘 다 실패 시에만 에러 상태. 공통 행 `{ date_kst, source:"tg"|"yt", summary, badges, links, meta }`로 정규화 → 정렬.
  - **정렬**: 1차 `date_kst` desc. 2차(동일 날짜) 소스 우선순위 고정 `tg` → `yt`(테스트 안정). 세션 있으면 3차로 session 순.
- 기존 `telegram-tab.tsx`·`youtube-tab.tsx`의 렌더 로직 재사용(행 렌더 함수로 분리), 쿼리는 그대로. 소스 필터(전체/텔레그램/유튜브) 토글(client). 텔레그램 "같은 테마 종목" 블록은 하단 유지.
- `lib/stock-hub.ts`:
  - `STOCK_TABS`: `"telegram"`,`"youtube"` → `"mentions"`로 교체.
  - `resolveTab`: **레거시 호환** — 입력이 `"telegram"`/`"youtube"`면 `"mentions"` 반환. 북마크·외부 링크가 `overview`로 안 떨어지게(내부 호출부 수정과 별개로 방어).
- `app/stock/[code]/page.tsx`: `TAB_LABEL.mentions = "언급"`, 분기 교체(`<MentionsTab code={code} session={tg_session} />`). 기존 telegram/youtube 분기·import 제거.

**주의**
- 내부 호출부(`telegram-tab.tsx:109` 피어 링크, `summary-table.tsx:34` 유튜브표)의 `tab: "telegram"|"youtube"` → `"mentions"`로 갱신(깔끔). 외부·북마크는 위 `resolveTab` 호환이 방어.
- `telegram-filter.tsx`(session 필터)는 텔레그램 소스 한정 → 소스 필터가 유튜브일 때 비활성/숨김.

**DoD**: `mentions-tab.e2e`·`stock-hub` green. 허브 `언급` 탭에서 텔레그램·유튜브 섞인 타임라인 + 소스 필터 동작. 죽은 import(telegram-tab/youtube-tab 미사용 시) 정리.

---

## 리스크 / 주의

- **client/server 경계**: 허브 `page.tsx`는 서버컴포넌트. `ResearchTab`(state·poll)는 client. `FinancialTab`은 **서버 유지**(토글은 URL 파라미터 재렌더, 데이터는 서버 `fetchCompare` — 브라우저에서 `lib/dart` 호출 금지). `MentionsTab`은 서버 쿼리 + client 필터 토글 소컴포넌트.
- **research-tab 크기**: `/research` page가 커서(약 460줄) 통째 이관 시 탭 컴포넌트도 큼. 검색 제거분만 덜어냄. 리팩터링은 이관 후 별건.
- **corps 미등록 종목**: 검색에 안 뜸(위 식별자 결론). 필요 시 fallback은 후순위.
- **기존 허브 진입 무손상**: watchlist·홈·유튜브표의 `stockHubHref`는 변경 없음. Phase 4/5는 nav·구페이지만 건드림.

## 완료 정의(전체)

- nav = `종목 분석` 단일 진입, `재무제표`·`리포트` nav 항목 소멸.
- `/stock` 검색 → 선택 시 **`?tab=financial`로 진입**(검색 경로 한정). 허브의 **기본 탭은 여전히 `overview`**(전역 기본 변경 아님). 재무·리포트·언급·차트·노트 한 곳.
- `/financial`·`/research` 라우트 **유지 + `/stock` 리다이렉트**(제거 아님).
- (후속 Phase 6) 종목별 텔레그램·유튜브 언급이 `언급` 탭 한 타임라인에서 소스 뱃지 구분·필터, 레거시 `?tab=telegram|youtube`는 `resolveTab` 호환.
- `npm test` 전부 green.
