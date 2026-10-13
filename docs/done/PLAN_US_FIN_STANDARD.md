# PLAN_US_FIN_STANDARD — 미국 XBRL 표준 재무제표

한 줄: 회사마다 다른 XBRL 태그를 표준 항목 약 50개로 묶고(동의 태그 우선순위), 해당 없는 항목은 0, 총계 결측은 계산으로 채워 시점(PIT) 기준 잔액·TTM 표를 만드는 공용 모듈. 첫 사용처는 AFA 재실행.

## 진행
- [x] 구현·테스트 머지 (PR #79, 2026-10-13)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음
- [x] 첫 실가동 확인 (날짜) — 해당 없음
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

## 왜

2026-10-12 진단(2019 연간, 비금융 상장 2,940사):

| 원인 | 예 | 해결 |
|---|---|---|
| 같은 항목이 태그 여러 개로 갈림 | 매출 태그1개 55% → 묶음 90%, 판관비 50→91%, 감가상각 65→92% | 동의 태그 묶음 |
| 해당 없는 항목은 아예 안 씀 | 우선주 36%, 비지배지분 29%, 배당 34%, 연구개발 53% | 재무제표 낸 회사의 결측 = 0 |
| AFA 항목 선택 규칙(전부 보유 60%) | 30개 묶음 전부 보유 0.9% → 매달 K≈11 에서 멈춤 | 표준 항목 고정, 교집합 규칙 폐지 |
| TTM 재구성 손실 | 순이익 분기값 있는데 TTM 없음 3~8% | 범위 밖(작음), LEADS |

## 사양

| 항목 | 값 | 누가 |
|---|---|---|
| 범위 | EdgarTools 동의 묶음(약 55) + 논문 28항목용 추가분 | 사용자(A, 2026-10-12) |
| 출처 | EdgarTools `edgar/standardization/synonym_groups.py` (MIT) 태그 목록을 복사, 저작권 표기 | 내 판단 |
| 정제 | 범위가 다른 태그 제거(아래 표). 같은 정의의 이름 변경·폐지 태그만 동의어 | 내 판단 |
| 제외 묶음 | 비율·주당·지표(EPS, EBITDA, FCF, BVPS, ROE, ROA), 주식수, 리스 지급예정표 | 내 판단 |
| 태그 선택 | (cik, 항목, start, end)마다 우선순위 첫 태그 값 1개. 출처 태그 열 보존 | 내 판단 |
| 시점 | `first_reported` (최초 공시값), avail=filed | 기존 AFA |
| 잔액 | start 없는 값, 형성일 t 기준 end ≥ t−200일 중 최신 | 기존 AFA |
| TTM | 연간 직접값 우선, 없으면 전년 연간 + 당해 YTD − 전년 YTD (±10일). 항목 단위(태그 섞여도 됨) | 기존 AFA `ttm_obs` |
| 0 채움 | 아래 "0 채움 항목"만. 같은 형성일 그 회사의 총자산(잔액)·순이익(TTM)이 있을 때만 | 내 판단 |
| 계산 채움 | 총부채 결측 = 부채와자본총계 − 자본(비지배 포함) − 임시자본(없으면 0) | 내 판단 |
| 저장 | 새 저장소 없음. 함수가 DataFrame 반환, 캐시는 호출자(AFA cs.pkl) | 내 판단 |

### 정제: 빼는 태그 (범위가 다름)

| 묶음 | 뺄 태그 | 이유 |
|---|---|---|
| 현금 | CashCashEquivalentsAndShortTermInvestments | 단기투자 포함 → `cash_and_sti` 묶음으로 |
| 유형자산 | PropertyPlantAndEquipmentGross | 감가상각 전 |
| 무형자산 | IntangibleAssetsNetIncludingGoodwill | 영업권 포함 |
| 재고 | InventoryGross, InventoryFinishedGoods | 총액·일부 |
| 매출채권 | AccountsReceivableGross | 충당금 전 |
| 장기차입금 | LongTermDebt | 유동분 포함 → 유동분 있으면 빼서 사용(계산 채움) |
| 단기차입금 | LongTermDebtCurrent | 일부 → 합산 아님, 첫 태그만 |
| 이연수익 | DeferredRevenueNoncurrent | 범위 다름 |
| 이자비용 | InterestIncomeExpenseNet | 순액(부호 다름) |
| 법인세 | IncomeTaxesPaidNet | 현금 지급액 |
| 순이익 | IncomeLossFromContinuingOperations | 중단영업 전 → `income_before_xo` 묶음으로 |
| 감가상각 | AmortizationOfIntangibleAssets | 일부 |
| 자사주 매입 | StockRepurchasedDuringPeriodValue | 자본변동표 값 |
| 자본 | StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest | 비지배 포함 → `total_equity` 묶음으로 |

구현 시 남은 묶음 전부를 같은 기준(이름 변경·폐지만 동의어)으로 다시 본다. 판단 근거는 `mapping.py` 묶음 옆 주석 한 줄.

### 추가 묶음 (논문 28항목용)

`preferred_stock`(PreferredStockValue, PreferredStockValueOutstanding), `redeemable_preferred`(TemporaryEquityCarryingAmountAttributableToParent, RedeemablePreferredStockCarryingAmount),
`minority_interest`(MinorityInterest), `total_equity`(StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest),
`liabilities_and_equity`(LiabilitiesAndStockholdersEquity), `income_before_xo`(IncomeLossFromContinuingOperations),
`discontinued_ops`(IncomeLossFromDiscontinuedOperationsNetOfTax, …AttributableToReportingEntity), `nonoperating_income`(NonoperatingIncomeExpense, OtherNonoperatingIncomeExpense),
`preferred_dividends`(DividendsPreferredStock, PreferredStockDividendsIncomeStatementImpact), `cash_and_sti`(CashCashEquivalentsAndShortTermInvestments).

### 0 채움 항목

우선주, 상환우선주, 비지배지분, 영업권, 무형자산, 재고, 단기투자, 단기차입금, 장기차입금, 연구개발비, 배당, 우선주배당, 중단영업손익, 영업외손익, 자사주매입, 이연수익, 이연법인세 자산·부채.
총계·핵심 항목(총자산, 유동자산·부채, 자본, 매출, 순이익, 영업현금흐름 등)은 0 채움 금지 — 없으면 결측.

## 논문 28항목 대응 (AFA)

| Compustat | 표준 항목 / 계산 |
|---|---|
| ATQ 총자산 | total_assets |
| LTQ 총부채 | total_liabilities (계산 채움 포함) |
| SEQQ·TEQQ 자본 | stockholders_equity · total_equity |
| CEQQ 보통주자본 | stockholders_equity − preferred_stock |
| PSTKQ·PSTKRQ 우선주 | preferred_stock · redeemable_preferred |
| PPENTQ 유형자산 | property_plant_equipment |
| DLTTQ 장기차입금 | long_term_debt |
| CHEQ 현금·단기투자 | cash_and_sti, 없으면 cash + short_term_investments |
| APQ 매입채무 | accounts_payable |
| ACOQ 기타유동자산 | 유동자산 − 현금·단기투자 − 매출채권 − 재고 |
| AOQ 기타자산 | 총자산 − 유동자산 − 유형자산 − 영업권 − 무형자산 − 장기투자 |
| LCOQ 기타유동부채 | 유동부채 − 매입채무 − 단기차입금 |
| LOQ 기타부채 | 총부채 − 유동부채 − 장기차입금 − 이연법인세부채 |
| ICAPTQ 투하자본 | long_term_debt + stockholders_equity + minority_interest |
| SALEQ 매출 | revenue |
| IBQ·IBADJQ 계속영업이익 | income_before_xo (IBADJ 는 같은 값) |
| IBCOMQ 보통주 귀속 | income_before_xo − preferred_dividends |
| NIQ 순이익 | net_income |
| XIDOQ·DOQ 중단영업 | discontinued_ops |
| PIQ 세전이익 | income_before_tax |
| TXTQ 법인세 | income_tax_expense |
| NOPIQ 영업외손익 | nonoperating_income |
| DVPQ 우선주배당 | preferred_dividends |
| DVQ 배당 | dividends_paid |

기타(ACO·AO·LCO·LO)는 Compustat 정의와 정확히 같지 않음(근사) — 한계에 적는다.

## 구조

- `research/us_fin_std/mapping.py` — 묶음 dict(항목 → 우선순위 태그 목록, 종류 bal/flow, 0채움 여부). EdgarTools 출처·MIT 표기.
- `research/us_fin_std/build.py`
  - `load_std(db_path, items=None)` — 묶음 태그를 chunk 로 `load_facts` → `first_reported` → 우선순위 첫 태그 선택 → bal: `bal_obs` / flow: `ttm_obs`. 반환 long(cik, item, end, avail, val, src_tag).
  - `std_snapshot(bal, ttm, t, items)` — 형성일 표(index=cik) + 0 채움 + 계산 채움.
- AFA: `research/afa/run.py` 가 `load_std`·`std_snapshot` 사용, 28항목 고정, `select_items` 미사용. 결측 남은 회사만 제외.
- 재사용: `research/ccc/panel.load_facts`, `research/ccc/event.first_reported`, `research/afa/items.bal_obs·ttm_obs`.

## 테스트 (요구 → 케이스)

- S1 우선순위: 같은 (cik,기간)에 두 태그 → 첫 태그 값, src_tag 기록
- S2 태그 전환: 2017 SalesRevenueNet, 2019 RevenueFromContract… → 둘 다 revenue, TTM 이 태그 경계 넘어 계산
- S3 0 채움: 총자산 있는 회사의 우선주 결측 → 0 / 총자산 없는 회사 → 결측 유지
- S4 0 채움 금지: 매출 결측 → 결측 유지
- S5 계산 채움: Liabilities 없음, LiabilitiesAndStockholdersEquity·자본 있음 → 차이
- S6 정제: 빼는 태그(예 InventoryGross)만 있는 회사 → inventory 는 0 채움 대상이지 Gross 값 아님
- S7 시점: avail > t 값은 안 보임
- 검증 실행: 2019 연간 보유율 표(진단 스크립트) 재측정 — 매출·판관비·감가상각 ≥ 90%

## 판정 (AFA 재실행)

- 기존 PLAN_AFA_REPLICATION 판정 그대로: 2010~12 동일가중 Q5−Q1 양수면 코드 정상 신호, 2018~ 업종조정 알파 t ≥ 2 & VW 양수면 유지, 아니면 기각.

## 한계

- 기타 4항목은 근사. 금융업 제외라 영향 작음.
- 회사 확장 태그(custom)는 안 씀 → 일부 결측 남음.
- TTM 재구성 손실(3~8%)은 손대지 않음.

## 구현 차이

- 추가: 200일 창에 없으면 400일 창 값으로 채움 (`mapping.FALLBACK_DAYS`, `std_snapshot` combine_first). 세전이익 등을 10-K 에만 싣는 회사가 많아 결산 7개월 뒤부터 결측이 됐음
- 추가: 유형자산에 `PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization` (2019 리스회계 후 태그 전환), 항목 `ap_and_accrued`
- 추가 계산 채움: 세전이익 = 계속영업이익 + 법인세, 법인세 = 세전이익 − 계속영업이익
- 원래 `load_facts` 재사용 → 실제 `build._read_tags` 자체 쿼리. `load_facts` 는 CCC 기본 태그 20개(336만 행)를 묶음마다 같이 읽어 6묶음이면 2,000만 행 낭비(실행 14.5→9.8분)
- 원래 태그 단위 묶음 → 실제 항목 단위 묶음 (한 항목 태그가 한 묶음에 있어야 우선순위 선택이 맞음, 메모리)
- 동의어 예외: `net_income` 의 `ProfitLoss` 는 비지배지분 포함이라 "같은 정의만 동의어" 원칙의 예외. `NetIncomeLoss` 없을 때만 쓰는 대체값, 비지배지분 없는 회사는 같은 값 (PR 리뷰 지적, 동작 유지·주석)
- 남은 손실: 분기값은 있는데 TTM 이 안 만들어지는 3~8% (결산월 변경·과도기 등) — 손대지 않음
