# 재무제표 다기간 비교 — v2 분기/반기 확장

## 한 줄 요약
v1(연간 5년 표, 커밋 6db2459) 완료. v2는 **연간/분기 토글** 추가 — 분기 모드 시 최근 N분기 표시.

**Step 0 정찰 완료 (2026-07-06, 삼성전자·바이브컴퍼니 실호출):** 두 리스크 다 해소 → 아래 §v2 핵심 결정 확정.

---

## API 기본 구조 (v1에서 확정)

| 용도 | API | fs_div | 비고 |
|------|-----|--------|------|
| 절대금액 | `fnlttSinglAcntAll` | 있음 (CFS/OFS) | account_id로 추출 |
| 비율 | `fnlttSinglIndx` | 없음 | **2023Q3+** 만 존재 |

**account_id 매핑 (확정):**

| key | account_id | sj_div |
|-----|------------|--------|
| revenue | `ifrs-full_Revenue` | IS (→CIS 폴백) |
| opProfit | `dart_OperatingIncomeLoss` | IS (→CIS 폴백) |
| netIncome | `ifrs-full_ProfitLoss` | IS (→CIS 폴백) |
| totalAssets | `ifrs-full_Assets` | BS |
| totalLiab | `ifrs-full_Liabilities` | BS |
| totalEquity | `ifrs-full_Equity` | BS |

**비율 idx 매핑 (확정, 2026-06-10 실호출):**

| key | idx_cl_code | idx_code |
|-----|-------------|----------|
| ROE | M210000 | M211550 |
| 부채비율 | M220000 | M221100 |
| 매출증가율 | M230000 | M231000 |
| 영업이익률 | — | 금액 계산 (opProfit÷revenue×100) |

**CIS 폴백:** IS 계정 없으면 CIS 재시도. M83(476080) 실호출 확인.

---

## DART reprt_code 체계

```
11011 = 사업보고서   (연간, 12월말)
11012 = 반기보고서   (H1,   6월말)
11013 = 1분기보고서  (Q1,   3월말)
11014 = 3분기보고서  (Q3,   9월말)
```

---

## v2 핵심 결정 사항 (Step 0 실호출로 확정)

### 1. 누적 vs 단독 분기 (IS 항목) → **단독. 환산 불필요**
분기보고서(11013/11012/11014) `thstrm_amount` = **해당 3개월 단독값** (누적 아님).
- 삼성 매출: Q1 71.9조 / Q2(H1보고서) 74.0조 / Q3 79.1조 → 합+Q4 = FY 300.9조 ✔
- 바이브: H1보고서(62.4억) > Q3(57.3억) 로 **감소** → 누적 불가능, 단독 확정.
- → 플랜 원안의 "단독 환산(전기 차감)" 로직 **전부 삭제**. 호출 ×2 없음.

**주의 1:** 사업보고서(11011) `thstrm_amount` = **연간 누적**(단독 Q4 아님).
→ **Q4 단독 = FY − (Q1+Q2+Q3)** 계산해야 함.

**주의 2 (2-3서 발견):** 차감은 **IS/CIS(기간·flow)에만** 적용. **BS는 시점(잔액·stock)** 이라
FY 연말잔액 = Q4 잔액 → **그대로 통과, 차감 금지.** `deriveQ4List`가 sj_div로 분기 처리.

### 2. 비율 분기 지원 여부 → **완전 지원**
`fnlttSinglIndx` 11013/11012/11014/11011 전부 `status=000, list=15` (삼성·바이브 공통).
→ 분기 모드에서도 비율 4행 그대로 표시. 숨김/`—` 처리 불요.

### 3. 표시 범위 (결정)
- 기본: 최근 8분기 (약 2년), **진짜 단독분기 Q1~Q4**
- 분기→reprt_code: Q1=11013, Q2=11012, Q3=11014, **Q4=11011(FY−Q1..Q3 계산)**
- period 라벨: `"2024Q1"`, `"2024Q2"`, `"2024Q3"`, `"2024Q4"` 문자열 통일

---

## 🎯 최종 목표 (변하지 않음)
재무제표 비교 뷰 상단에 **연간 ⇄ 분기 토글**. 분기 선택 시 최근 8분기를 **진짜 단독분기(Q1~Q4)** 로 금액+비율 표시. 연간 모드는 지금 동작 그대로.

## v2 단계별 진행 (TDD)

Step 2가 커서 **behavior-neutral 리팩터 → 순수 로직 → 배선** 순으로 분할.
2-1/2-2는 연간 출력 불변(기존 spec으로 회귀검증). 2-3은 순수함수 단위테스트. 실 분기 데이터는 2-4에서 처음 흐름.

| 단계 | 내용 | 산출물 | 검증 |
|------|------|--------|------|
| ~~**0**~~ ✅ | probe — 삼성·바이브 실호출, 비율지원O·thstrm 단독 확인 | `probe-dart-quarterly.mjs` | — |
| ~~**1**~~ ✅ | reprt 유틸 — `CompareMode`/`PeriodDesc`/`buildPeriods()` | `dart-periods.ts` | 유닛 4 green |
| ~~**2-1**~~ ✅ | `periods` number[]→**string[]** 마이그레이션 (연간 라벨 불변) | `types/dart.ts`, `lib/dart.ts`, `api-compare.spec.ts` | 기존 spec 24 green |
| ~~**2-2**~~ ✅ | `fetchAcntRaw`/`fetchIndxRaw` **reprtCode 파라미터화**(기본 11011) | `lib/dart.ts` | 기존 spec 24 green |
| ~~**2-3**~~ ✅ | **Q4 파생 순수함수** `deriveQ4List` — IS차감/BS통과 | `lib/dart-quarterly.ts` + 유닛 | 유닛 4 green |
| ~~**2-4**~~ ✅ | 분기 fetch 경로(`determineQuarterlyBase` probe + `fetchQuarterlyAcnt` 연도단위 4보고서 + `deriveQ4List`) + `fetchCompare(corp,count,mode)` 배선. 조립부는 `resolve/topNList/ratioByIndex/n` 인젝션으로 인라인 공유(별도 함수 추출 안 함) | `lib/dart.ts` | 연간 24 green, tsc 클린 |
| ~~**3**~~ ✅ | route `?mode=quarterly&count=` + 분기 API 통합테스트 | `route.ts` + `api-compare-quarterly.spec.ts` | 삼성·바이브 8 green |
| **4** | UI 연간/분기 토글 + `CompareTable` 모드 라벨(현재 "연간" 하드코딩) + e2e | 컴포넌트 + spec | e2e green |

각 단계 완료 후 결과 보고 → 확인 → 다음. 독립 커밋.

---

## 추정 호출 수 (quarterly 8분기)

| 시나리오 | 호출 수 |
|----------|---------|
| ~~금액만 (비율 미지원)~~ | ~~8회~~ |
| **금액 + 비율 (확정)** | **8 + 8×3 = 32회** |
| ~~단독 환산 (전기 차감)~~ | 불필요 (thstrm 이미 단독) |

→ **32회 확정.** ×2 폐기. Q4는 이미 받은 FY 응답 재사용해 계산(추가 호출 0).

---

## 다음 단계 후보 (v2 이후)

- DuckDB lazy 캐시 (`rcept_no`로 정정 추적)
- 호출 수 축소: `fnlttSinglAcntAll` 응답에 당기/전기/전전기 3년치 포함 → 2회로 5년 커버
- 공시목록 `list.json` 사전조회로 유효 기간 확정
- 추세 차트, 비율 4분류 전체, 금액 항목 커스텀
