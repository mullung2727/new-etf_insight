# PLAN_CCC_DELTA

한 줄: CCC 수준 대신 전년 같은 분기 대비 CCC 변화(ΔCCC, 개선=감소)로 월별 10분위 정렬해 D1(개선 최대)−D10(악화 최대) 스프레드를 PLAN_CCC_REPLICATION 과 같은 틀로 측정한다.

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음
- [x] 첫 실가동 확인 (날짜) — 해당 없음
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

## 배경

- 사용자 가설: CCC 수준이 아니라 **개선**이 주가에 영향.
- Wang(2019)은 ΔCCC 를 신호로 안 봄(재고·매출채권·매입채무 변화는 통제변수로만, Table 8). 관련 선행: 재고 증가 → 미래 수익 음(Thomas·Zhang 2002, Belo·Lin 2012).
- 이후 할 일(사용자 지시): 저CCC 기업의 이익 서프라이즈 지속 여부, 논문의 위험(금융경색) 설명 확인.

## 사양

| 항목 | 값 | 출처 |
|---|---|---|
| 입력 | 월별 재현 캐시 `research/private/ccc/cache/cs_48.pkl` (cik, qend, ccc, ind, ret, me, microcap, ym) | 기존 산출물 재사용 |
| ΔCCC | ccc(qend) − ccc(같은 cik, qend − 350~380일) | 내 판단 (전년 동기: 계절성 제거) |
| 전년 값 | 같은 (cik, qend) 가 캐시 여러 달에 나오면 최초 등장 값 | 내 판단 |
| 시점 | 전년 분기는 당분기보다 먼저 공시됨 → 월 m 표본의 공시일 규칙 그대로 | PLAN_CCC_REPLICATION |
| 신호 | `dccc`, `dccc_adj`(월·FF48 업종 중앙값 차감), 월별 1% 윈저 | 월별 재현과 같은 규칙 |
| 정렬·통계 | `run._decile_panel`·`run._panel_md` 재사용 (ALL/exMIC × EW/VW, NW t, CAPM·FF3·FF5+Mom 알파) | 기존 코드 |
| 방향 | D1 = ΔCCC 최소(개선 최대). 가설이 맞으면 D1−D10 > 0 | 사용자 가설 |
| 기간 | 전체 / 2010-07~2015-12 / 2016~ | 월별 재현과 같음 |
| 저장 | 코드 `research/ccc/delta.py`(공개), 결과 `research/private/ccc/DELTA_RESULTS.md` | 사용자 (코드 공개·결과 비공개) |

## 단계

1. `research/ccc/delta.py`: `add_delta(cs)` → `main()` (캐시 읽기, 팩터 `factors.load_factors`, 기간별 패널, md 저장).
2. 테스트 `research/ccc/tests/test_delta.py`.
3. 실행 → 결과 비공개 저장.

## 테스트

- D1 전년 매칭: qend 2020-03-31 의 전년 2019-03-31 값과 차이. 2019-06-30(90일 전)은 매칭 안 됨.
- D2 전년 없음 → 행 제외.
- D3 최초 등장 값: 같은 (cik, qend) 가 두 달에 다른 ccc 로 나오면 앞 달 값.
- D4 dccc_adj: 같은 달·같은 업종 중앙값 차감 수기값 일치.

## 한계

- 월별 재현과 같음(생존편향·배당 미포함·현재 SIC·월평균 ~760종목).
- 전년 분기가 표본에 있어야 해서 첫 해(2010-07~2011-06)는 표본이 거의 없음.

## 구현 차이

- 없음 (전년 매칭률 86%, 수익 월 2011-04~)
- 결과: research/private/ccc/DELTA_RESULTS.md (비공개, 2026-10-12 실행)
