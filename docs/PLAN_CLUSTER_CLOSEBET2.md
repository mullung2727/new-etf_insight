# 정량 필터 추가 백테스트 설계

## 1. 목적

기존 클러스터 기반 종가베팅 전략의 백테스트 결과가 유효하지 않았으므로, 전략 전체를 복잡하게 확장하기보다 **가격 기반 핵심 필터 3개만 추가하여 익일 성과 개선 여부를 검증**한다.

이번 단계에서는 다음을 사용하지 않는다.

* 뉴스
* 웹검색
* LLM
* LangGraph
* 테마명 분석
* 정성적 재료 판단

검증 대상은 오직 정량 데이터이다.

---

# 2. 기존 Baseline 구조

기존 전략 구조는 유지한다.

```text
전 종목
→ 과거 수익률 기반 Clustering
→ 당일 거래대금 상위 종목 추출
→ 강한 Cluster 선정
→ Cluster 내 매수 후보 선정
→ 종가 부근 진입
→ 익일 성과 측정
```

이번 실험에서는 이 Baseline 뒤에 필터를 추가한다.

```text
Baseline Candidate
        ↓
Quant Filter
        ↓
최종 매수 후보
```

---

# 3. 실험 원칙

필터는 한 번에 여러 개 적용하지 않는다.

먼저 각각 독립적으로 검증한다.

```text
Baseline

vs

Baseline + Late Momentum

vs

Baseline + Close Strength

vs

Baseline + High Proximity
```

개별 실험에서 성과 개선이 확인된 필터만 다음 단계에서 조합한다.

예:

```text
Baseline
+ Late Momentum
+ Close Strength
```

이후 필요할 경우:

```text
Baseline
+ Late Momentum
+ Close Strength
+ High Proximity
```

까지 확장한다.

처음부터 3개를 동시에 넣지 않는다.

---

# 4. Filter 1 — Late Momentum

## 4.1 목적

종가 직전까지 상승 흐름이 유지되는 종목만 선택한다.

장중 한때 강했던 종목이 아니라 **장 후반에도 실제 매수세가 유지되고 있는지** 확인한다.

---

## 4.2 기본 Feature

우선 다음 구간 수익률을 계산한다.

```text
ret_1430_1500
ret_1500_1515
ret_1515_1520
ret_1430_1520
```

예:

```text
ret_1430_1520
=
Price(15:20) / Price(14:30) - 1
```

---

## 4.3 기본 실험 조건

### 조건 A

```text
Price(15:20) > Price(15:00)
```

가장 단순한 형태.

---

### 조건 B

```text
Price(15:20) > Price(15:00) > Price(14:30)
```

장 후반 지속적인 상승 여부 확인.

---

### 조건 C

```text
ret_1430_1520 > 0
AND
ret_1500_1520 > 0
```

두 구간 모두 상승한 경우만 통과.

---

## 4.4 연속 Feature 분석

Threshold를 바로 고정하지 않고 `ret_1430_1520` 자체도 저장한다.

예:

```text
< 0%
0 ~ 0.5%
0.5 ~ 1%
1 ~ 2%
2% 이상
```

각 구간의 익일 성과를 확인한다.

---

## 4.5 우선 실험

처음에는 복잡한 VWAP이나 이동평균 조건을 넣지 않는다.

다음 세 가지 정도만 비교한다.

```text
LM0 : 필터 없음

LM1 :
Price(15:20) > Price(15:00)

LM2 :
Price(15:20) > Price(15:00) > Price(14:30)
```

---

# 5. Filter 2 — Close Strength

## 5.1 목적

당일 고가 근처에서 마감하는 종목을 선별한다.

장중 상승 후 크게 밀린 종목을 제외하고, **장 마감까지 가격이 강하게 유지된 종목**을 찾는다.

---

## 5.2 정의

```text
close_position
=
(Close - Low)
/
(High - Low)
```

범위:

```text
0 ~ 1
```

해석:

```text
1에 가까움
→ 당일 고가 근처 마감

0에 가까움
→ 당일 저가 근처 마감
```

---

## 5.3 예외 처리

```text
High == Low
```

이면 해당 값은 계산할 수 없으므로:

```text
close_position = NaN
```

처리하고 필터 실험에서는 제외한다.

---

## 5.4 Threshold 후보

처음에는 너무 세밀하게 탐색하지 않는다.

다음 네 개 정도만 비교한다.

```text
CP0 : 필터 없음
CP1 : close_position >= 0.6
CP2 : close_position >= 0.7
CP3 : close_position >= 0.8
```

필요한 경우에만 이후 0.9를 추가한다.

---

## 5.5 윗꼬리 필터는 별도 추가하지 않음

Upper Wick Ratio는 Close Position과 상당 부분 중복된다.

따라서 이번 단계에서는:

```text
Close Position만 사용
```

한다.

Close Position 효과가 확인된 이후에도 필요성이 있을 때만 Upper Wick을 별도 실험한다.

---

# 6. Filter 3 — High Proximity / Breakout

## 6.1 목적

최근 고점이나 전고점 부근까지 상승한 종목의 익일 지속성을 검증한다.

핵심 질문:

> 최근 가격 저항대에 가까운 종목이 익일에도 강한가?

---

## 6.2 60일 최고가 기준

기본 Feature:

```text
distance_high_60
=
Close / PriorMaxHigh60 - 1
```

여기서:

```text
PriorMaxHigh60
=
매매일 당일을 제외한
이전 60거래일의 최고가
```

당일 고가는 계산에서 제외한다.

---

## 6.3 52주 최고가 기준

추가 Feature:

```text
distance_high_252
=
Close / PriorMaxHigh252 - 1
```

```text
PriorMaxHigh252
=
이전 252거래일 최고가
```

---

## 6.4 Breakout 여부

```text
breakout_60
=
Close >= PriorMaxHigh60
```

```text
breakout_252
=
Close >= PriorMaxHigh252
```

---

## 6.5 Threshold 후보

60일 기준:

```text
HP0 : 필터 없음

HP1 :
distance_high_60 >= -0.10

HP2 :
distance_high_60 >= -0.05

HP3 :
distance_high_60 >= -0.03

HP4 :
breakout_60 == True
```

처음에는 60일 기준을 우선 검증한다.

52주 기준은 60일 결과가 의미 있을 경우 추가한다.

---

# 7. 세 필터의 역할 구분

각 필터는 서로 다른 정보를 측정한다.

```text
Late Momentum
→ 장 후반에도 상승 흐름이 유지되는가?

Close Strength
→ 하루 전체 기준으로 고가 근처에서 마감했는가?

High Proximity
→ 최근 주요 가격 저항대에 가까운가?
```

따라서 세 필터는 완전히 동일한 정보는 아니다.

다만 어느 정도 상관관계가 있을 수 있으므로 개별 검증을 먼저 수행한다.

---

# 8. 실험 순서

## Phase 1 — Baseline 재확인

기존 Baseline 결과를 동일 조건으로 다시 저장한다.

```text
Baseline
```

모든 비교의 기준이 된다.

---

## Phase 2 — Late Momentum

```text
Baseline
vs
LM1
vs
LM2
```

검증.

---

## Phase 3 — Close Strength

```text
Baseline
vs
CP1
vs
CP2
vs
CP3
```

검증.

---

## Phase 4 — High Proximity

```text
Baseline
vs
HP1
vs
HP2
vs
HP3
vs
HP4
```

검증.

---

# 9. 개별 필터 기각 기준

필터가 다음 중 하나에 해당하면 기각한다.

```text
평균수익률 개선 없음
```

또는

```text
t-stat 개선 없음
```

또는

```text
승률 개선만 있고 평균수익률 악화
```

또는

```text
거래 건수가 지나치게 감소
```

또는

```text
Train에서는 개선되지만
Validation/OOS에서는 사라짐
```

단순히 한 지표만 좋아졌다고 채택하지 않는다.

---

# 10. 성과지표

각 실험마다 최소 다음 지표를 출력한다.

```text
Trades
Average Return
Median Return
Win Rate
Average Win
Average Loss
Profit Factor
t-stat
Cumulative Return
MDD
```

특히 중요하게 볼 항목:

```text
Average Return
t-stat
Trades
MDD
```

---

# 11. 거래 수 감소 확인

필터를 추가하면 당연히 거래 수가 줄어든다.

따라서 반드시 다음을 같이 저장한다.

```text
Baseline Trades

Filtered Trades

Retention Ratio
=
Filtered Trades / Baseline Trades
```

예:

```text
Baseline Trades = 1,000
Filtered Trades = 120

Retention Ratio = 12%
```

이 경우 성과 개선이 있어도 지나치게 강한 필터일 수 있으므로 별도로 확인한다.

---

# 12. 필터 조합 조건

개별 실험에서 살아남은 필터만 조합한다.

예:

```text
Late Momentum → 통과

Close Strength → 통과

High Proximity → 기각
```

이라면:

```text
Baseline
+ Late Momentum
+ Close Strength
```

만 테스트한다.

기각된 High Proximity를 다시 넣지 않는다.

---

# 13. 2개 필터 조합

두 개 이상 필터가 개별적으로 살아남은 경우에만 조합한다.

예:

```text
Combination A

Late Momentum
+
Close Strength
```

또는:

```text
Combination B

Late Momentum
+
High Proximity
```

또는:

```text
Combination C

Close Strength
+
High Proximity
```

모든 조합을 무조건 테스트할 필요는 없다.

개별 OOS 성과가 가장 안정적인 필터부터 조합한다.

---

# 14. 3개 필터 조합

세 필터 모두 독립적으로 효과가 확인된 경우에만 테스트한다.

```text
Late Momentum
+
Close Strength
+
High Proximity
```

세 필터 동시 적용은 마지막 단계이다.

---

# 15. No-Trade 규칙

필터를 통과한 종목이 없으면 거래하지 않는다.

```text
0개
→ NO TRADE
```

1개라면:

```text
1종목만 매수
```

2개라면:

```text
2종목만 매수
```

3개 이상이면:

```text
기존 우선순위 기준으로
최대 3종목
```

조건 미달 종목을 추가하여 억지로 3종목을 채우지 않는다.

---

# 16. Threshold 과적합 방지

전체 기간에서 가장 높은 수익률이 나온 Threshold를 선택하지 않는다.

예:

```text
close_position

0.6
0.7
0.8
```

전체 백테스트에서 0.8이 가장 좋았다는 이유만으로 최종 선택하면 안 된다.

기본 구조:

```text
Train
→ Threshold 후보 탐색

Validation
→ 후보 선택

Test
→ 고정된 Threshold 평가
```

또는 Walk-Forward를 사용한다.

---

# 17. Walk-Forward 적용

각 시점에서 미래 데이터를 사용하지 않는다.

예:

```text
과거 구간
→ Filter Threshold 선택

다음 기간
→ 고정 적용

기간 이동

다시 과거 구간
→ Threshold 재선택

다음 기간
→ 검증
```

필터뿐 아니라 기존 Clustering 파라미터도 동일한 원칙을 적용한다.

---

# 18. Look-Ahead 방지

장후반 Momentum을 사용할 경우 매수시점보다 이후 가격을 사용해서는 안 된다.

예:

```text
후보 판단 시각 = 15:20
```

이라면:

```text
15:20 이후 데이터 사용 금지
```

진입가격은 최소:

```text
15:20 이후 실제 체결 가능한 가격
```

을 사용해야 한다.

---

# 19. Late Momentum과 진입가격

Late Momentum이 15:20 가격을 사용하는 경우:

```text
15:20 데이터를 보고
15:20 가격에 체결
```

하는 것은 비현실적일 수 있다.

따라서 실제 백테스트에서는:

```text
15:20까지 Feature 계산

→ 15:21 이후 가격
또는
→ 동시호가
```

---

# 20. 실험 결과 — 3개 필터 모두 기각 (2026-09-23)

요약: 종가위치(CP)는 Train·Validation 통과했으나 OOS에서 개선 소멸, 고점근접(HP)은 Train 탈락, 장후반모멘텀(LM)은 선택구간 탈락. §9 기각 기준 적용 → 조합 단계(§12~14) 미진행.

## 적용 사양 (설계문서 미정의분은 Claude 결정)

| 항목 | 값 | 누가 정했나 |
|---|---|---|
| Baseline | PLAN_CLUSTER_CLOSEBET.md 구조 그대로. K 20·30·50 × N 3·5 = 6셀 | Claude |
| 필터 적용 위치 | 선정 Cluster ∩ Top30 후보에 필터 → 통과분 중 거래대금 상위 최대 3 (§15) | 설계 |
| 진입 | 종가 동시호가(=일봉 종가) | 설계 §19 |
| 청산 | open1 익일시가(주지표), h1 익일 TP/SL ±3% | Claude |
| 평가 | 일별 가중 평균 + t-stat + retention | 설계 §10·§11 |
| 기간 | Train 20220801~20241231 / Validation 2025년 / OOS 20260101~20260918 | Claude |
| LM 기간 | 분봉 하한 때문에 Train 없음. 선택 20250901~20251231, OOS 2026년 | Claude |
| LM 가격 | p1520 = 151900 봉 종가(봉 시각은 시작시각), p1500·p1430 동일 방식 | Claude |
| 60일 고점 | ms 기준 직전 60거래일, 관측 40일 미만이면 결측 처리 | Claude |
| 채택 규칙 | Train: 평균·t 개선 5/6셀 이상 + retention≥20% / Validation: 평균 개선 4/6 이상 / OOS: 보고만 | Claude |

## 결과 (open1 기준, 일별 평균)

Baseline: Train −0.37~−0.52%, Validation −0.15~−0.41%, OOS +0.02~+0.15%

| 필터 | Train 평균개선 | Validation | OOS | retention | 판정 |
|---|---|---|---|---|---|
| CP1 (≥0.6) | 6/6 | 4/6 | 0/6 | 53~73% | 기각(OOS 소멸) |
| CP2 (≥0.7) | 6/6 | 6/6 | 0/6 | 43~61% | 기각(OOS 소멸) |
| CP3 (≥0.8) | 4/6 | 5/6 | 2/6 | 30~44% | 기각(Train) |
| HP1 (≥−10%) | 4/6 | 2/6 | 0/6 | 47~90% | 기각(Train) |
| HP2 (≥−5%) | 4/6 | 2/6 | 0/6 | 38~76% | 기각(Train) |
| HP3 (≥−3%) | 0/6 | 0/6 | 2/6 | 32~67% | 기각(Train) |
| HP4 (60일 돌파) | 2/6 | 1/6 | 3/6 | 22~50% | 기각(Train) |
| LM1 (15:20>15:00) | — | 2/6 (선택구간) | 0/6 | 68~84% | 기각(선택구간) |
| LM2 (15:20>15:00>14:30) | — | 2/6 (선택구간) | 5/6 | 31~45% | 기각(선택구간) |

## 관찰 (채택 아님)

- LM2는 OOS(2026년) open1에서 6셀 중 5셀 개선, 일별 평균 중앙값 +0.22%, retention 35~45%. 단 선택구간(2025-09~12, 80거래일)에서는 2/6이라 사전 규칙상 채택 불가. OOS만 보고 고르면 과최적화.
- OOS 구간은 Baseline 자체가 양수(+0.02~+0.15%)라 시장 국면 효과가 섞여 있다. LM2도 6셀 중 2셀만 Benchmark A를 넘었다.
- CP는 종가를 보고 종가에 진입하는 구조라 미세한 look-ahead가 있다. 기각됐으므로 분봉(15:20 가격) 재검증은 미실행.
- 무작위 필터 placebo는 채택 후보가 없어 미실행.

## 산출물

- 코드: `research/cluster_closebet/` (필터 변형 `--variants`, 분봉 특징 `minute_features.py`)
- 결과: `results/f_train.json`, `f_val.json`, `f_oos.json`, `lmsel.json`, `lmoos.json`, `lm_features.json`(7,695건 / 257거래일 / 461종목)

---

# 21. 추가 필터 실험 — 매물대 · Cluster 단위 (2026-09-23)

요약: §20 이후 사용자 요청으로 필터 2계열을 추가 검증. 매물대 4종은 Train/Validation 탈락, Cluster 단위 9종은 Train에서 강하게 나왔으나 플라시보 검정에서 Validation/OOS 전부 소멸 → 전부 기각. 종가베팅 클러스터 전략 계열 종료.

## 추가 사양 (Claude 결정)

| 항목 | 값 |
|---|---|
| 매물대 | 직전 120거래일(ms 기준, 관측 80일 미만 제외) 종가·거래량을 40개 등폭 빈에 적재. 머리 위 매물 비중 = 당일 종가보다 위 빈 거래량 / 전체 |
| 매물대 변형 | VP05/VP10/VP20 = 머리 위 비중 ≤5·10·20%, VPB = 전일 종가 < 최대빈 상단 ≤ 당일 종가(당일 돌파) |
| Cluster 게이트 | 선정 Cluster 구성종목 중 당일 행 + ms-1 직전행이 있는 종목만 집계. 개별 수익률은 ±30% 클립 |
| Cluster 변형 | CSR50~80 = 상승 종목 비율, CSN05/10 = 상승 종목 수, CSM1~3 = 평균 수익률 ≥1·2·3%, CSS30/50 = 3% 이상 상승 종목 비율 |
| 게이트 동작 | 조건 미달이면 그날 전략 매매 없음. Benchmark는 게이트 영향 없음 |
| 플라시보 | 필터 선정일 수 N과 같은 수를 전체 매매일에서 무작위 추출(1만회), 필터 평균의 단측 p값. 시드 42 |

## 결과 (open1, 일별 평균, 6셀)

매물대 — Train 평균개선 / Validation / OOS / retention

| 변형 | Train | Val | OOS | retention | 판정 |
|---|---|---|---|---|---|
| VP05 | 6/6 | 0/6 | 0/6 | 45~78% | 기각(Val) |
| VP10 | 4/6 | 2/6 | 0/6 | 53~86% | 기각(Train) |
| VP20 | 5/6 | 2/6 | 0/6 | 63~95% | 기각(Val) |
| VPB | 1/6 | 2/6 | 0/6 | 7~17% | 기각(Train) |

Cluster 단위 — 플라시보 p<0.05 셀 수

| 변형 | Train | Val | OOS | retention | 판정 |
|---|---|---|---|---|---|
| CSR70 | 0/6 | 0/6 | 0/6 | 15~26% | 기각 |
| CSR75 | 3/6 | 0/6 | 0/6 | 9~34% | 기각 |
| CSR80 | 2/6 | 0/6 | 0/6 | 6~21% | 기각 |
| CSS50 | 6/6 | 2/6 (n=3일) | 0/6 | 1~15% | 기각 |
| CSR50/60, CSN05/10, CSM1~3, CSS30 | 채택규칙 단계에서 탈락 | | | | 기각 |

## 관찰

- 승률·PF는 Cluster 게이트에서 세 구간 모두 개선됐다(예 CSR70: 승률 +3~5%p, PF 0.57→0.70 / 0.70→0.75 / 1.07→1.15). 그러나 일별 평균은 비용 차감 후 0 부근이고 t는 전부 |t|<1.5.
- "Benchmark A를 6/6으로 이김"(CSR70~80, OOS)은 매매일 17~42일 표본. 플라시보에서 p 0.23~0.83으로 무작위 추출과 구별되지 않았다.
- Train에서 6/6으로 깨끗했던 조건이 네 번(CP2, VP05, CSR75, CSS50) 나왔고 모두 이후 구간에서 소멸. 변형 수가 많을 때의 전형적 허위신호.
- 매수 횟수 감소 목표 자체는 달성(최대 1/30). 감소분만큼의 기대값 개선은 없었다.

## 산출물 추가

- 결과: `results/vp_{train,val,oos}.json`, `cs_{train,val,oos}.json`, `cs2_{train,val,oos}.json`, `d_{train,val,oos}{,_daily}.json`
- 플라시보: `research/cluster_closebet/placebo.py` (`--dump-daily` 산출물 필요)
