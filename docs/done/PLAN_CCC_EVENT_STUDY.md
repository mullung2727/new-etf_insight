# PLAN_CCC_EVENT_STUDY

한 줄: CCC 공시일(10-Q/10-K) 전후 −5~+60거래일 저CCC(D1)·고CCC(D10) 시장대비 누적초과수익(CAR)을 비교해, 월별 재현에서 사라진 CCC 효과가 공시 직후 단기로 옮겨갔는지 확인한다.

## 진행
- [x] 구현·테스트 머지 (PR #75, 2026-10-11)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음
- [x] 첫 실가동 확인 (날짜) — 해당 없음
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

## 배경

- PLAN_CCC_REPLICATION: 월별 D1−D10 2010~2015 EW +0.62%/월(논문과 일치), 2016~ 소멸.
- 월별 설계는 공시 1~5개월 뒤 달에 매수 → 공시 직후 반영분은 측정 밖.
- 가설(사용자): CCC 반영이 빨라져 공시 직후 단기 수익으로 옮겨갔다.
  - 맞으면: D1−D10 CAR 이 day 0~+5 에 몰리고 2016~ 에도 남음.
  - 틀리면: 전 구간 0 근처.

## 사양

| 항목 | 값 | 출처 |
|---|---|---|
| CCC 정의·필터 | `panel.ccc_table` 그대로 (비금융, 매출/전기자산 2.5%, 대체 태그) | PLAN_CCC_REPLICATION |
| 시점 | 각 (cik,tag,start,end) **최초 공시값**만 → `ccc_table` → `avail`(구성항목 최대 filed) = 공시일 | 내 판단 (룩어헤드 없음) |
| 이벤트 | (cik, qend) 1건, `avail − qend ≤ 120일`(늦은 정정 제외) | 내 판단 |
| day 0 | `avail` 이후(포함) 첫 거래일 | 내 판단 (EDGAR filed 는 날짜만, 장후 공시 → +1 에 반응) |
| 분위 | `avail` 달력분기별 횡단면: 1% 윈저 → FF48 업종 중앙값 차감(ccc_adj) → 10분위 | 월별 재현과 같은 규칙, 단위만 분기 |
| 일수익 | `ohlcv.close` 전일대비(분할조정, 배당 없음), [−50%, +100%] 클립 | 내 판단 (데이터 오류 꼬리) |
| 시장 | 같은 날 ohlcv 전 종목 EW 평균(같은 클립) | 내 판단 |
| AR·CAR | AR = 종목 − 시장, CAR(a,b) = 합 | 표준 |
| 창 | −5 … +60 거래일. 창 일부 결측 종목은 있는 날만 합(결측일 AR=0 아님, 평균에서 빠짐) | 내 판단 |
| 통계 | 분기별 D1 평균 CAR − D10 평균 CAR → 분기 시계열(~65개) 평균·t (이벤트 군집 상관 회피) | 내 판단 |
| 구간 | (−5,−1) (0,1) (2,5) (6,20) (21,60) (0,60) | 내 판단 |
| 기간 | 전체 / 2010~2015 / 2016~ (avail 기준) | 월별 재현과 같음 |
| 저장 | 코드 `research/ccc/event.py`(공개), 결과 `research/private/ccc/EVENT_RESULTS.md` | 사용자 (코드 공개·결과 비공개) |

## 단계

1. `research/ccc/event.py`
   - `first_reported(facts)`: (cik,tag,start,end)별 filed 최소 행.
   - `build_events(facts, companies, ind_map)`: `ccc_table(first_reported(facts))` → 120일 필터 → 비금융·업종 → 분기 윈저·ccc_adj·10분위. 컬럼 cik, ticker(ticker_key), qend, avail, aq(YYYYQ), ccc_adj, dec.
   - `event_windows(events, con, lo=-5, hi=60)`: DuckDB 에서 티커별 거래일 순번·일수익·시장 EW → events 등록 조인 → (event_id, k, ar).
   - `summarize(ev, ar)`: 구간별 D1·D10·차이, 분기 시계열 t, 기간 분할. k별 평균 CAR 곡선(D1, D10, 차이).
   - `main`: run.py 의 `_load_companies`·`_load_ind_map` 재사용, RESULTS 파일 작성.
2. 테스트 `research/ccc/tests/test_event.py` → 3절.
3. 실행·손검증(이벤트 1건 AR 수기 대조) → 결과 비공개 저장.

## 테스트

- E1 `first_reported`: 같은 키 2개 filed 중 이른 값만 남는다.
- E2 day 0: avail 이 휴일(데이터 없는 날)이면 다음 거래일이 k=0.
- E3 AR: 합성 2종목·3일, 종목 AR = 종목수익 − EW 시장 수기값과 일치.
- E4 클립: +300% 일수익이 +100% 로 잘려 종목·시장 모두 반영.
- E5 120일 필터: avail − qend = 150 이벤트 제외.
- E6 분위: 분기별로 따로 10분위(다른 분기 값이 섞이지 않음).
- E7 통계: 분기 시계열 t 가 `sort.nw_tstat` 아닌 단순 t 와 수기값 일치(분기 3개 합성).

## 한계

- 생존편향·배당 미포함·현재 SIC (월별 재현과 같음).
- 이익 서프라이즈 미통제: 공시 직후 반응은 대부분 실적 서프라이즈 → D1−D10 차이가 나와도 CCC 고유 효과인지 미확정(나오면 다음 단계에서 SUE 통제).
- filed 는 날짜만 → day 0·+1 구분 불가, (0,1) 로 묶어 본다.

## 구현 차이

- 원래 E7 "t 가 단순 t 와 수기값 일치" → 실제 spread 평균·분기수·이벤트수만 검증, t 는 `sort.nw_tstat(lags=4)` 재사용 (검증된 함수)
- 손검증: AAPL 2023-08-04 10-Q, k=-1·0·+1 AR 이 수기(종목수익 − EW 시장) 와 일치. 공시일이 실적발표 다음 날이라 day 0 반응 = 실적 반응
- 결과: research/private/ccc/EVENT_RESULTS.md (비공개, 2026-10-12 실행), 판정은 research/private/rejected/REJECTED.md
