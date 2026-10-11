# PLAN_CCC_EARNINGS

한 줄: Wang(2019) Table 12(식 5)를 간소화 재현해, 저CCC·고CCC 가 과거 이익을 통제하고도 다음 분기 이익(순이익/자산)을 예측하는지 2016년 전후로 비교한다. 예측력이 남았으면 "성과 차이는 그대로, 주가만 선반영", 사라졌으면 "관계 자체 소멸".

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음
- [x] 첫 실가동 확인 (날짜) — 해당 없음
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 배경

- CCC 수준·ΔCCC 스프레드 모두 2016년 이후 소멸 (PLAN_CCC_REPLICATION, PLAN_CCC_DELTA).
- 논문의 오가격 근거: CCC 가 과거 이익 통제 후에도 미래 이익 예측(Table 12, β_Low>0·β_High<0), 투자자가 이를 덜 반영.

## 사양

| 항목 | 값 | 출처 |
|---|---|---|
| 식 | E_t = α + β_Low·Low_{t−1} + β_High·High_{t−1} + β1·lnAT_{t−1} + β4·E_{t−1} + β5·NegE_{t−1} + ε | 논문 식 5 |
| 생략 통제 | Dividend, DDiv, Accruals | 내 판단 (태그 추가 수집 필요, 1차는 생략·한계 명시) |
| E | 분기 NetIncomeLoss / 같은 분기말 직전 분기 Assets | 논문은 CBOP, "IB 로도 비슷" → 순이익 사용 (내 판단) |
| 분기 순이익 | 80~100일 직접값, Q4 = 연간 − 같은 시작 9개월 (음수 허용) | 월별 재현 규칙 |
| 시점 | 최초 공시값 (`event.first_reported`) | PLAN_CCC_EVENT_STUDY |
| Low/High | `event.build_events` 의 공시분기별 ccc_adj 10분위 dec==1 / dec==10 (t−1 분기) | 기존 코드 |
| t 매칭 | 같은 cik, qend_t − qend_{t−1} = 80~100일 | 내 판단 |
| 추정 | qend_t 달력분기별 OLS → 계수 평균, NW t(lags=4) (Fama-MacBeth) | 논문 |
| 윈저 | E_t, E_{t−1} 분기별 1% | 논문 관행 |
| 기간 | 전체 / qend_t 2010~2015 / 2016~ | 앞 분석과 같음 |
| 저장 | 코드 `research/ccc/earn.py`(공개), 결과 `research/private/ccc/EARN_RESULTS.md` | 사용자 (코드 공개·결과 비공개) |

## 단계

1. `panel.py` 최소 수정: `load_facts(extra_tags=())` 로 순이익 태그 추가 로드, `quarter_flow(f, tags, allow_negative)` 일반화 함수 추가(기존 `_flows_with_filed` 는 그대로).
2. `research/ccc/earn.py`: 분기 E 패널 → build_events 와 결합 → 분기별 OLS → 요약 md.
3. 테스트 `research/ccc/tests/test_earn.py`.
4. 실행 → 비공개 저장.

## 테스트

- R1 `quarter_flow`: Q4 = 연간 − 9개월, 음수 결과(손실 분기) 유지.
- R2 E_t 분모 = 직전 분기말 Assets.
- R3 t 매칭: 90일 뒤 분기만, 180일 뒤는 제외.
- R4 분기별 OLS: 합성 데이터에서 Low 더미 계수 = 수기 평균차.
- R5 기존 테스트 전부 통과(`_flows_with_filed` 무변경).

## 한계

- 통제변수 3개 생략 → 계수 크기는 논문과 직접 비교 불가, 2016 전후 **변화**만 본다.
- 생존편향·현재 SIC (앞 분석과 같음).

## 구현 차이

없음
