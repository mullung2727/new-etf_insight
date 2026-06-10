# 재무제표 다기간 비교 — v2 분기/반기 확장

## 한 줄 요약
v1(연간 5년 표, 커밋 6db2459) 완료. v2는 **연간/분기 토글** 추가 — 분기 모드 시 최근 N분기 표시.

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

## v2 핵심 결정 사항

### 1. 누적 vs 단독 분기 (IS 항목)
DART 분기 IS 값은 **기간 누적** (Q3 매출 = Q1+Q2+Q3). 선택지:
- **누적 그대로** 표시 — 호출 단순, 대신 분기 비교 왜곡
- **단독 환산** (당기 - 전기) — 호출 2배, 더 직관적

→ Step 0 확인 후 결정.

### 2. 비율 분기 지원 여부
`fnlttSinglIndx`가 11012/11013/11014 지원하는지 불확실. 안 되면:
- 분기 모드에서 비율 행 숨기거나 `—` 처리

### 3. 표시 범위
- 기본: 최근 8분기 (약 2년)
- period 포맷: `"2024Q1"`, `"2024Q3"`, `"2024H1"` 등 문자열로 통일

---

## v2 단계별 진행 (TDD)

| 단계 | 내용 | 산출물 |
|------|------|--------|
| **0** | probe 스크립트 — 삼성전자 2024 Q1/Q3 `fnlttSinglAcntAll` + `fnlttSinglIndx` 실호출 → 비율 지원 여부·누적값 확인 | 픽스처 JSON + 결정 확정 |
| **1** | 타입 확장 — `periods: string[]`, `mode: "annual"\|"quarterly"`, reprt_code 유틸 | `dart-compare-keys.ts`, `types/dart.ts` |
| **2** | `fetchCompare` 확장 — quarterly 모드, fs_div 탐색 분기 적용 | `lib/dart.ts` |
| **3** | API route `?mode=quarterly` 지원 + Playwright API 테스트 (Red→Green) | `api/financial/compare/route.ts` + spec |
| **4** | UI 토글 + CompareTable string periods 대응 + e2e 테스트 (Red→Green) | 컴포넌트 + spec |

각 단계 완료 후 결과 보고 → 확인 → 다음. 독립 커밋.

---

## 추정 호출 수 (quarterly 8분기)

| 시나리오 | 호출 수 |
|----------|---------|
| 금액만 (비율 미지원) | 8회 |
| 금액 + 비율 지원 | 8 + 8×3 = 32회 |
| 단독 환산 (전기 차감) | 위 ×2 |

→ 비율 미지원 or 연간만 비율 표시가 현실적.

---

## 다음 단계 후보 (v2 이후)

- DuckDB lazy 캐시 (`rcept_no`로 정정 추적)
- 호출 수 축소: `fnlttSinglAcntAll` 응답에 당기/전기/전전기 3년치 포함 → 2회로 5년 커버
- 공시목록 `list.json` 사전조회로 유효 기간 확정
- 추세 차트, 비율 4분류 전체, 금액 항목 커스텀
