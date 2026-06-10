# 금융업 재무제표 연도 백필 (방안 1)

## 배경

DART `fnlttSinglAcntAll.json`은 금융지주 2022 이전 데이터를 미지원 (status=013).  
단, 2023 연간보고서 응답에는 `frmtrm_amount`(전기=2022), `bfefrmtrm_amount`(전전기=2021)가 포함됨.  
2024 보고서 + 2023 보고서 → 2021~2024 커버 가능.

## 목표

금융지주 종목도 DART에 데이터가 존재하는 연도(2021~)는 `—` 없이 표시.

## 선택 근거 (방안 1 채택)

| | 방안 1 (백필) | 방안 2 (보고서 중심 재설계) |
|---|---|---|
| 추가 API 호출 | 0회 (응답 재활용) | 감소 (5→2~3회) |
| 수정 범위 | extract 단계만 | fetchCompare 전면 재작성 |
| 테스트 회귀 위험 | 낮음 | 높음 |
| 커버리지 | 동일 (2021까지) | 동일 |
| 비율(ROE 등) | 백필 불가 (금액만) | 동일 |

## 구현 계획

### Step 1 — 실패 테스트 작성

`broker-web/__tests__/api-compare.spec.ts`에 추가:
- 신한지주 2021/2022 opProfit not null
- 신한지주 2021/2022 netIncome not null
- 신한지주 2021/2022 totalAssets not null

### Step 2 — 응답 구조 파악

`fnlttSinglAcntAll.json` 응답 항목:
- `thstrm_amount` — 당기 (해당 연도)
- `frmtrm_amount` — 전기 (year-1)
- `bfefrmtrm_amount` — 전전기 (year-2)

year=2024 보고서 → thstrm=2024, frmtrm=2023, bfefrmtrm=2022  
year=2023 보고서 → thstrm=2023, frmtrm=2022, bfefrmtrm=2021

### Step 3 — dart.ts 수정

**`extractAmount` 시그니처 변경:**
```ts
function extractAmount(list, accountId, sjDiv, field: "thstrm"|"frmtrm"|"bfefrmtrm" = "thstrm")
```

**`fetchCompare` 백필 로직:**
```
baseYear 확인 (예: 2024)
periods = [2020, 2021, 2022, 2023, 2024]

각 연도 acntResult fetch:
  year=2024 → thstrm (기존)
  year=2023 → thstrm (기존)
  year=2022 → status=013이면 year=2023 응답의 frmtrm 사용
  year=2021 → status=013이면 year=2023 응답의 bfefrmtrm 사용
  year=2020 → status=013이면 year=2024 응답의 bfefrmtrm... 불가 (2020은 커버 범위 밖)
```

**불확실 사항 (해소됨, 2026-06-10):**
- [x] restated 여부 — 신한지주 2024 보고서 frmtrm/bfefrmtrm이 2023 보고서 값과 일치, 불일치 없음
- [x] 백필 적용 범위 — 전체 적용 채택. M83(2024 상장)도 2022/2023 백필됨 (의도된 동작)

### Step 4 — 테스트 통과 확인

전체 22개 회귀 포함.

## 미결 결정 (해소됨)

- [x] `bfefrmtrm_amount` restated 여부 — 교차검증 결과 일치, 그대로 사용
- [x] 비율 행(ROE, 매출증가율 등) 백필 — `fnlttSinglIndx`는 연도 직접 호출만 지원하므로 백필 불가. 금액만 백필하고 비율은 `—` 유지.

## 완료 (2026-06-10)

- Step 1: 실패 테스트 3개 작성 (red 확인) — 신한지주 2021/2022 실값 검증 + M83 2022/2023 백필
- Step 2: frmtrm/bfefrmtrm 실값 확인 (`temp/probe-backfill-fields.mjs`)
- Step 3: `extractAmount`에 field 파라미터, `fetchCompare`에 donor 백필 (year+1 frmtrm → year+2 bfefrmtrm)
- Step 4: 전체 25/25 통과, tsc 클린
