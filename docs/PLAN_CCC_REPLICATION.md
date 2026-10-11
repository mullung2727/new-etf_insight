# PLAN_CCC_REPLICATION — 현금전환주기(CCC) 스프레드 미국 재현

한 줄: Wang(2019, JFE 133) "The cash conversion cycle spread" 방법을 2010~2026 미국(us_facts + us_ohlcv)에 그대로 적용해 저CCC−고CCC 월수익·알파가 아직 있는지 확인한다. 매매 전략은 이 결과를 보고 따로 정한다.

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 — 해당 없음(리서치)
- [ ] 첫 실가동 확인 — 해당 없음(리서치)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 0. 사양 (누가 정했나)

| 항목 | 값 | 출처 |
|---|---|---|
| 목표 | 논문 재현 먼저, 매매화는 결과 후 | 사용자 |
| CCC | 365 × (평균 재고/매출원가 + 평균 매출채권/매출 − 평균 매입채무/매출원가), 분기, 평균=분기 초·말 잔액 평균 | 논문 |
| 업종 조정 | CCC − 같은 분기 FF48 업종 중앙값 | 논문 |
| 제외 | 금융(SIC 6000~6999), 분기매출/전분기 총자산 < 2.5% | 논문 |
| 극단값 | CCC 분기별 양쪽 1% winsorize | 논문 |
| 사용 시점 | 분기 끝 +1분기 건너뜀(1~3월 자료 → 7~9월 수익) **+ 포트 구성일(전월 말)까지 공시(filed)된 값만**, 정정공시는 그 시점 최신값 | 사용자 |
| 포트 | 매월 10분위, 동일가중(EW)·가치가중(VW), 롱 저CCC(1분위) − 숏 고CCC(10분위) | 논문 |
| 표본 | 전 종목 / 초소형주 제외(전월 말 시총 < 거래소 NYSE 종목 20백분위) | 논문 |
| 시가총액 | 전월 말 종가 × SEC 공시 주식수(dei EntityCommonStockSharesOutstanding, 그 시점까지 공시된 최신값) | 사용자 |
| 알파 | CAPM, FF3, FF5+모멘텀 (Ken French 월별 팩터) | 사용자 |
| 기간 | 수익 2010-07 ~ 최신 월 (일봉 2010-01-04~, 재무 2009~) | 내 판단(데이터 한계) |
| 수익률 | 월말 종가(분할 조정, **배당 없음**) 대비 | 내 판단(데이터 한계) |
| 상장폐지 −30% | 해당 없음 — 현재 상장 종목만 있음(생존편향, 결과는 상한) | 데이터 한계, 사용자 수용(PLAN_US_OHLCV_BACKFILL D3) |
| 장부가/시가 필수 | 논문 표본조건(BM 비결측) 따름: 자본총계 StockholdersEquity > 0 | 내 판단 |
| 저장 | 코드 `research/ccc/`(커밋), 숫자 결과·캐시 `research/private/ccc/`(gitignore), 팩터 원본 `D:/ken_french/` | 사용자 |

## 1. 데이터 변환 (내 판단, 근거 포함)

- **CIK↔티커**: `us_facts.companies.ticker`(현재값) ↔ `us_ohlcv.ohlcv.ticker`. 티커 변경 종목은 과거 누락 → 매핑 실패 수를 출력.
- **업종**: `companies.sic`(현재 SIC) → `build_us_facts.ff_industry(con, 48, sic)`. 과거 SIC 변경 미반영(사용자 결정, 과거 이력은 요청 시).
- **태그 대체 순서** (앞이 우선, 같은 분기 하나만 사용):
  - 매출: `Revenues` → `RevenueFromContractWithCustomerExcludingAssessedTax` → `SalesRevenueNet`
  - 매출원가: `CostOfGoodsAndServicesSold` → `CostOfRevenue` → `CostOfGoodsSold`
  - 재고 `InventoryNet`, 매출채권 `AccountsReceivableNetCurrent`, 매입채무 `AccountsPayableCurrent`, 총자산 `Assets`, 자본 `StockholdersEquity` (us-gaap)
  - 재고 없음 → 0 으로 보지 않고 결측(논문 Compustat 관행 확인 불가 → 보수적으로 제외). 실측 후 제외 비율 출력.
- **분기 흐름값(매출·원가)**: 기간 80~100일 값 우선. 4분기는 10-K 연간(350~380일) − 같은 회계연도 9개월 누적(또는 1~3분기 합). 음수 나오면 그 분기 제외.
- **시점 규칙**: 값마다 `filed` 를 가짐. 구성월 m(전월 말 D) 에 쓰는 값 = `end` 가 [m−6, m−4]개월 분기 끝 중 최신, 그리고 `filed ≤ D` 인 버전 중 최신.

## 2. 단계

1. `research/ccc/factors.py` — Ken French FF5(2x3)·모멘텀 월별 zip 받아 `D:/ken_french/` 저장 + 파싱 → verify: 2015-01 Mkt-RF 값 사이트와 일치
2. `research/ccc/panel.py` — 분기 CCC 패널(cik, 분기끝, filed, CCC, 업종조정 CCC, 매출/자산) → verify: AAPL·WMT CCC 를 10-Q 숫자로 손계산 대조
3. `research/ccc/monthly.py` — 월말 종가 → 월수익, 전월 말 시총, 초소형 구분 → verify: AAPL 2020-08 분할 달 수익 정상
4. `research/ccc/sort.py` — 월별 10분위 EW/VW, 스프레드 평균·t(Newey-West 6), 알파 3종, 원·업종조정 × 전종목·초소형제외 → `research/private/ccc/RESULTS.md`
5. 견고성(1~4 결과 본 뒤): FF5 업종 조정, NYSE 사이즈 5분위 × CCC 이중정렬

## 3. 테스트 (`research/ccc/tests/`, 가짜 데이터)

| # | 요구 | 검증 |
|---|---|---|
| C1 | CCC 공식 | 손계산 값과 일치 |
| C2 | 4분기 = 연간 − 9개월 | 일치, 음수면 제외 |
| C3 | 금융 SIC 제외 | 6000~6999 행 없음 |
| C4 | 매출/전분기 자산 < 2.5% 제외 | 해당 분기 없음 |
| C5 | winsorize 1% | 분기별 상하단 클립 |
| C6 | 한 분기 건너뜀 | 3월 분기 → 7·8·9월만 사용 |
| C7 | **미공시값 거부** | filed > 구성일인 값·정정값은 그 달에 안 씀 |
| C8 | VW 가중 = 전월 말 시총 | 당월 시총 아님 |
| C9 | 업종 중앙값 조정 | 같은 분기·업종 중앙값 차감 |

## 4. 알려진 한계 (결과 해석 시 같이 적음)

- 생존편향(상폐 종목 없음) → 특히 고CCC(부실) 쪽 수익이 과대 → 스프레드 **과소** 가능, 방향 섞임.
- 배당 미포함, 현재 SIC·현재 티커 기준.
- 2010~ 16년 ≈ 190개월(논문 474개월) → t값 낮게 나올 수 있음.

## 구현 차이

(구현 후 기록)
