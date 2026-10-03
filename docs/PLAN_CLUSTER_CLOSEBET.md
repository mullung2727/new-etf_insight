# 클러스터 기반 종가베팅 시스템

## 1단계 — Quant Backtest 설계

## 1. 목적

과거 가격 및 거래대금 데이터를 이용하여 다음 가설을 검증한다.

> **평소 주가 움직임이 유사한 종목들이 당일 거래대금 상위권에 동시에 등장할 경우, 해당 종목군의 주도주는 익일에도 상승 흐름이 이어지는가?**

1단계에서는 전략의 정량적 유효성만 검증한다.

제외:

* LangGraph
* LLM
* 웹검색
* 뉴스
* 테마명 판별
* 뉴스 지속성 판단

해당 기능은 Quant 전략의 유효성이 확인된 이후 2단계에서 추가한다.

# 2. 핵심 전략

```text
전 종목 과거 1년 가격
        ↓
주간 수익률 계산
        ↓
K-Means Clustering
        ↓
종목 ↔ Cluster 생성
        ↓
당일 거래대금 Top 30
        ↓
Top 30의 Cluster 분포 확인
        ↓
강한 Cluster 선정
        ↓
Cluster 내 거래대금 상위 2~3종목 선정
        ↓
당일 종가 매수 가정
        ↓
익일 성과 측정
```

# 3. Universe

기본 Universe:

```text
KOSPI + KOSDAQ 보통주
```

제외 대상:

* ETF
* ETN
* 우선주
* SPAC
* 거래정지 종목
* 관리종목
* 가격 데이터가 부족한 종목

Clustering을 위해 최소 1년 수준의 가격 이력이 존재하는 종목을 기본 대상으로 한다.

확정: Universe(t)는 매매일 당시 실제 상장/거래 가능 종목이다. 신규상장은 52주 확보 전 제외한다.

# 4. Clustering

## 4.1 목적

기존 산업분류나 테마 DB를 사용하지 않고 실제 주가 움직임이 유사한 종목을 하나의 그룹으로 묶는다.

예:

```text
Cluster 17

삼성전자
SK하이닉스
한미반도체
HPSP
테크윙
...
```

Cluster에 `반도체`, `방산` 등의 이름을 부여할 필요는 없다.

중요한 것은 종목들의 실제 가격 동조성이다.

## 4.2 기본 데이터

최근 1년의 **주간 수익률(수정주가 기준)**을 사용한다.

확정: 52주 중 결측 20% 초과 종목은 제외한다.

```text
약 52주 × 전 종목
```

예:

```text
             W1      W2      W3   ...   W52

삼성전자     1.2%   -0.5%    2.1%       ...
SK하이닉스   2.0%   -0.8%    3.2%       ...
현대차      -0.3%    1.4%   -0.2%       ...
```

일별 수익률보다 주간 수익률을 우선 사용하는 이유:

* 일별 노이즈 감소
* 일시적인 동조현상 완화
* 중기적으로 같이 움직이는 종목군 탐지
* 1년 데이터 사용 시 약 52차원으로 축소

## 4.3 표준화

종목별 주간 수익률을 표준화한다. std=0 종목은 제외한다.

```text
Z(i,t)

= (Return(i,t) - Mean(i))
  / Std(i)
```

목적:

* 종목별 변동성 차이 완화
* 절대 상승폭보다 움직임의 패턴에 집중

## 4.4 Clustering 갱신

실전에서는 일정 주기로 Cluster를 재계산하는 구조를 가정한다.

기본안:

```text
매주 1회 재계산
```

백테스트에서도 동일한 방식으로 당시 이용 가능한 과거 데이터만 사용한다.

예:

```text
2025-06-09 Cluster 생성

사용 데이터:
2024-06 ~ 2025-06-06

해당 Cluster:
2025-06-09 ~ 2025-06-13 사용
```

# 5. K 값

확정: Euclidean + k-means++ + n_init=20 + fixed seed=42.

K-Means의 Cluster 개수는 사전에 하나로 확정하지 않는다.

초기 후보:

```text
K = 30
K = 50
K = 75
K = 100
K = 150
```

K가 너무 작으면:

```text
서로 관계없는 종목까지
같은 Cluster에 포함
```

K가 너무 크면:

```text
실제로 같이 움직이는 종목들이
여러 Cluster로 과도하게 분리
```

될 수 있다.

# 6. K 검증

Silhouette Score만으로 K를 결정하지 않는다.

최종 목적은 예쁜 Cluster를 만드는 것이 아니라 **종가베팅에 유용한 Cluster를 만드는 것**이다.

따라서 각 K에 대해 실제 매매성과를 비교한다.

확정: walk-forward로 한다.
IS(2022-08~2024-12): K·N 후보 탐색 및 1차 선택.
Validation(2025-01~12): 상위 1~2개 조합 중 최종 사양 확정.
Final OOS(2026-01~현재): 파라미터 조정 없이 최종 성과 측정.
운영 이후: 연 1회, 직전 2.5년 rolling window로 재선택.

예:

```text
K       익일 평균수익률

30          ...
50          ...
75          ...
100         ...
150         ...
```

추가로 확인:

* Cluster 평균 크기
* Top30 내 Cluster 집중도
* 기간별 성과 안정성
* 선택 종목 수
* 거래 발생 빈도

# 7. 거래대금 Top 30

매매일 당일 거래대금 기준 상위 30개 종목을 추출한다.

예:

```text
Rank    Stock        Cluster

1       A              17
2       B              42
3       C              17
4       D              17
5       E               8
6       F              42
...
```

목적:

> 현재 시장에서 실제 자금이 집중되고 있는 종목 중 평소에도 같이 움직이는 종목이 동시에 등장하는지 탐지한다.

# 8. Cluster Strength

가장 단순한 Baseline에서는 Top30에 같은 Cluster가 얼마나 많이 등장하는지를 사용한다.

```text
Cluster 17 → 6종목
Cluster 42 → 4종목
Cluster 8  → 2종목
```

이 경우 Cluster 17을 당일 주도 Cluster 후보로 선정한다.

단, Cluster마다 전체 종목 수가 다르므로 단순 출현 개수만 사용하는 방법에는 문제가 있을 수 있다.

# 9. Cluster Strength 개선 후보

Baseline 이후 다음 방법들을 비교한다.

## 9.1 Count

```text
Top30 내 동일 Cluster 종목 수
```

가장 단순한 방법.

## 9.2 Concentration

```text
Top30 포함 종목 수
------------------
Cluster 전체 종목 수
```

예:

```text
Cluster A

전체 100종목
Top30 = 6종목

→ 6%


Cluster B

전체 10종목
Top30 = 4종목

→ 40%
```

Cluster B가 훨씬 강하게 움직이는 종목군일 가능성이 있다.

## 9.3 Turnover Rank Weight

Top30에 들어왔다는 사실뿐 아니라 거래대금 순위도 고려한다.

예:

```text
Cluster A
1위 / 3위 / 7위 / 18위

Cluster B
11위 / 16위 / 23위 / 29위
```

같은 4종목이라도 Cluster A에 더 높은 점수를 부여한다.

## 9.4 Composite Score

최종적으로 다음 요소를 조합하는 방법을 검토한다.

```text
Cluster Strength

= Count
+ Concentration
+ Turnover Rank Weight
```

정확한 가중치는 사전에 임의로 최적화하지 않고 백테스트 결과를 통해 검토한다.

확정: Baseline은 Composite 없이 Count 하나로만 시작한다.

# 10. 주도 Cluster 선정

매매일마다 Cluster Strength가 가장 높은 Cluster를 선택한다.

확정: No-trade — Top30에서 동일 Cluster 최소 개수 미만이면 거래하지 않는다. N=3,5,7로 비교한다.

초기 Baseline:

```text
Top Cluster = 1개
```

추후 다음 방식도 비교할 수 있다.

```text
Top 1 Cluster

vs

Top 2 Clusters

vs

Strength Threshold 이상 모든 Cluster
```

# 11. 종목 선정

선택된 Cluster 중 당일 거래대금이 가장 큰 종목을 매수 후보로 한다.

비교 대상:

```text
Top 1
Top 2
Top 3
```

초기 기준:

```text
거래대금 Top 3
```

예:

```text
Selected Cluster = 17

거래대금

A    1.2조
B    8,000억
C    5,500억
D    2,100억
E    1,300억

→ A / B / C 선택
```

# 12. Baseline 진입

초기 백테스트에서는 진입 로직을 최대한 단순화한다.

```text
Entry = 당일 종가
```

목적은 정확한 체결전략을 검증하는 것이 아니라:

> Cluster + 거래대금으로 선택한 종목에 익일 알파가 존재하는가?

를 먼저 확인하는 것이다.

# 13. Baseline 청산

초기 청산:

```text
Exit = 익일 시가
```

수익률:

```text
Return

= Next Day Open
  ---------------
  Entry Close

  - 1
```

초기 단계에서는 다음을 적용하지 않는다.

* +3% 익절
* -3% 손절
* 장초반 추세
* 09:05 청산
* 09:30 청산
* 시간외 단일가

# 14. Baseline 전략

최초 백테스트 전략은 다음과 같다.

```text
매주
│
├─ 전 종목 최근 52주 주간수익률
│
├─ 표준화
│
└─ K-Means
        ↓

매일
│
├─ 거래대금 Top30
│
├─ Cluster별 출현 횟수
│
├─ 가장 많이 출현한 Cluster 선정
│
├─ 해당 Cluster의 Top30 종목 중
│  거래대금 상위 3종목 선정
│
├─ 당일 종가 매수
│
└─ 익일 시가 매도
```

이 전략을 모든 개선안의 Benchmark로 사용한다.

# 15. 핵심 성과지표

단순 누적수익률만 보지 않는다.

최소 다음 지표를 계산한다.

```text
거래 횟수

평균 거래 수익률

중앙값 수익률

승률

평균 이익

평균 손실

Profit Factor

누적수익률

MDD
```

추가로 분포 확인:

```text
+3% 이상 비율
+2% 이상 비율
+1% 이상 비율

0% 미만 비율

-1% 이하 비율
-2% 이하 비율
-3% 이하 비율
```

# 16. Benchmark

전략 자체의 효과를 확인하기 위해 비교군이 필요하다.

예:

### Strategy

```text
강한 Cluster
→ 거래대금 Top3
```

### Benchmark A

```text
전체 시장 거래대금 Top3
```

### Benchmark B

```text
거래대금 Top30 중
Random 3
```

이를 통해 단순히 거래대금이 큰 종목을 산 효과인지, **Cluster 집중도를 사용한 추가적인 효과인지** 구분한다.

# 17. Look-Ahead Bias 방지

백테스트에서 가장 중요한 원칙이다.

매매일 `t`에서 사용하는 모든 데이터는 당시 실제 이용 가능했던 정보만 사용한다.

## Clustering

예:

```text
매매일:
2025-06-10

Clustering 데이터:
2024-06 ~ 2025-06-09 이전
```

## 거래대금

종가 진입을 가정하는 Baseline에서는 당일 최종 거래대금을 사용한다.

단, 실제 종가 이전 주문에서는 최종 거래대금을 알 수 없으므로 이는 추후 실제 체결전략 단계에서 수정해야 한다.

# 18. 종가 거래대금 Look-Ahead 문제

Baseline에서는:

```text
당일 최종 거래대금
+
당일 종가 진입
```

을 사용하면 실제로는 완전히 동시에 알 수 없는 정보라는 문제가 존재한다.

따라서 Baseline 결과가 확인되면 실제 실행 가능한 형태로 변경한다.

예:

```text
15:15 기준 누적 거래대금
        ↓
Top30 선정
        ↓
15:20 이후 진입
```

이 단계부터 분봉 데이터가 필요하다.

따라서 최종 전략 판단은 반드시 **실행 가능한 시점 기준 데이터**로 다시 검증한다.

확정: 최종 거래대금/종가를 동시에 쓰지 않는다. 15:15 후보선정 → 15:19가 진입 + 왕복 0.6% 비용으로 검증한다.

# 19. 2차 Quant Feature

Baseline에서 효과가 확인된 후 다음 Feature를 하나씩 추가한다.

한 번에 모두 추가하지 않는다.

## 19.1 Closing Strength

```text
Close - Low
-----------
High - Low
```

## 19.2 Breakout

* 전고점 돌파
* 최근 N일 최고가
* 52주 신고가

## 19.3 Candle

* 양봉 여부
* 몸통 크기
* 윗꼬리 비율

## 19.4 Cluster Return Strength

```text
Cluster 구성 종목
당일 평균수익률
```

## 19.5 Cluster Turnover Increase

```text
당일 Cluster 거래대금
---------------------
최근 평균 Cluster 거래대금
```

# 20. 분봉 확장

일봉 Baseline이 유효할 경우 분봉 백테스트로 확장한다. 분봉은 전 시간대 봉을 사용한다(15:30 이후 시간외 포함).

추가 검증:

```text
15:00 이후 Momentum

15:15 거래대금 Top30

15:20 진입

당일 저점 유지

장후반 주도주 이탈 여부
```

# 21. 익일 청산 확장

종목선정 로직의 유효성을 확인한 후 청산 전략을 별도로 최적화한다.

비교:

```text
익일 시가

09:05

09:30

10:00

TP +3% / SL -3%

TP +2% / SL -2%

시간 기반 강제청산
```

이 단계에서는 분봉 데이터가 필요하다.

# 22. Backtest 진행 순서

## Step 1 — Baseline

```text
52주 주간수익률
K-Means
Top30
Cluster Count
Top Cluster 1개
거래대금 Top3
종가 매수
익일 시가 매도
```

## Step 2 — K 비교

```text
30
50
75
100
150
```

## Step 3 — Clustering Window 비교

```text
13주
26주
52주
```

필요 시 일별 수익률 방식과도 비교한다.

## Step 4 — Cluster Strength 비교

```text
Count

Concentration

Rank Weight

Composite
```

## Step 5 — Quant Feature 추가

```text
Closing Strength
Breakout
Candle
Cluster Return
Cluster Turnover
```

## Step 6 — 분봉 적용

```text
실제 매매 가능 시점의
거래대금 / 가격 데이터 사용
```

## Step 7 — 청산전략 검증

```text
시가매도
vs
장초반 매도
vs
TP / SL
```

# 23. 1단계 완료 조건

다음 질문에 답할 수 있으면 Quant Backtest 단계를 완료한 것으로 본다.

### Q1

과거 주가 수익률을 이용한 Clustering이 종가베팅에 유용한가?

### Q2

거래대금 Top30에 특정 Cluster가 집중될 경우 익일 성과가 개선되는가?

### Q3

어떤 K와 Clustering Window가 가장 안정적인가?

### Q4

단순 거래대금 Top 종목 매수보다 Cluster 정보를 추가했을 때 성과가 개선되는가?

### Q5

Cluster Strength를 어떤 방식으로 정의하는 것이 효과적인가?

### Q6

신고가, 종가강도, 장후반 Momentum 등의 추가 Feature가 성과를 개선하는가?

### Q7

익일 시가 청산과 장초반 청산 중 어떤 방식이 적합한가?

# 24. 2단계로 넘길 내용

1단계에서는 구현하지 않는다.

```text
웹검색
뉴스 수집
공시 분석
LLM
LangGraph
Theme 이름 생성
상승 원인 분석
재료 지속성 판단
```

1단계 Quant 전략의 성과를 Baseline으로 저장한다.

이후 2단계에서는:

```text
Quant Strategy
        ↓
후보 Cluster
        ↓
Web Search
        ↓
LLM Theme Analysis
        ↓
재료 지속성 판단
        ↓
최종 종목 선정
```

을 추가하고,

```text
Quant Only

vs

Quant + LLM
```

성과를 Forward Test로 비교한다.

# 25. 1단계 핵심 원칙

```text
Clustering
→ 무엇이 평소 같이 움직이는가?

거래대금
→ 오늘 어디에 돈이 몰렸는가?

Cluster Strength
→ 특정 종목군으로 돈이 집중됐는가?

가격 Feature
→ 장 마감까지 힘이 유지되고 있는가?

익일 가격
→ 그 움직임이 실제로 지속됐는가?
```

1단계에서는 **테마를 설명하려 하지 않는다.**

`Cluster 17이 반도체인지`, `Cluster 32가 방산인지`는 백테스트에 필요하지 않다.

검증할 핵심은 하나다.

> **과거에 함께 움직였던 종목들이 오늘 동시에 거래대금 상위권으로 올라왔을 때, 그 종목군의 주도주를 종가에 매수하는 전략에 익일 알파가 존재하는가?**

# 26. IS 결과 — 기각 (2026-09-23)

요약: IS(2022-08~2024-12, 592거래일)에서 K 7개 × N 3개 × 청산 3종 모두 비용 차감 후 음수. Cluster 정보는 전체 거래대금 Top3(Benchmark A)를 개선하지 못함 → Q4 기각, Validation/OOS 진행 안 함.

## 적용 사양

| 항목 | 값 | 누가 정했나 |
|---|---|---|
| Universe | 보통주(티커 끝자리 0), 스팩 제외, 0값 행 제외. ETF/ETN은 DB에 없음. 관리종목 미적용(소스 없음) | 설계 + Claude |
| Clustering | 52주 주간수익률, 결측 20% 초과 제외, 표준화, 매주 재계산(직전 주까지 데이터) | 설계 |
| 선정 | 거래대금 Top30 → Count 최다 Cluster(동일 Cluster N개 미만이면 거래 안 함) → Top3 | 설계 |
| 진입 | 당일 종가(일봉 대리) | 설계 |
| 청산 | open1 익일 시가 / h1 D+1 TP·SL / h2 D+1~D+2 TP·SL, 미도달 시 마지막날 종가 | 사용자 |
| TP/SL | +3% / −3%(진입가 기준 가격), 동일 봉 양쪽 도달 시 SL 우선, 시가 갭은 시가 청산 | 사용자 + Claude |
| 비용 | 왕복 0.6% | 설계 |
| 가드 | 상한가 진입 제외, ±31% 갭 제외, 거래정지 갭(ms 불연속) 제외 | Claude |
| 평가 | 일별 가중(하루 평균 후 집계), Benchmark는 전략 매매일 동일 조건 | Claude |

## 결과 (일별 평균 순수익률)

| 청산 | 전략 범위 (K 20~150 × N 3·5·7) | Benchmark A 전체 | Benchmark B 전체 |
|---|---|---|---|
| open1 | −0.31 ~ −0.59% (K100_N7 +0.25%, K150_N7 +0.37%) | −0.31% | −0.64% |
| h1 | −0.55 ~ −0.91% (K100_N7 +0.07%) | −0.57% | −0.99% |
| h2 | −0.58 ~ −0.94% (K100_N7 +0.11%) | −0.59% | −1.02% |

- 같은 날 Benchmark A 대비 전략이 열위인 조합이 대부분(open1 K30~150 기준 11/15, h1·h2 13/15)
- 전략은 Benchmark B(Top30 무작위 3)보다는 우위 → 효과는 "거래대금 상위"에서 오며 Cluster 추가분은 없음
- K100_N7(48일)만 양수였으나 같은 날 A·B도 양수 → 기간 효과. 여러 칸 중 한 칸 선택은 과최적화라 채택 안 함
- K를 섹터 수준(20·25)으로 줄여도 동일 결론
- h1·h2는 일봉 판정(SL 우선)이라 실제보다 보수적. 단 Benchmark도 같은 규칙이라 상대 결론은 불변
- 분봉 대조(2025-08 이후)는 기각으로 미실행

## 산출물

- 코드: `research/cluster_closebet/` (실행: `etl\.venv\Scripts\python.exe -m research.cluster_closebet.backtest --trade-start 20220801 --trade-end 20241231 --ks ... --ns 3,5,7 --exits open1,h1,h2 --out ...`)
- 결과: `research/cluster_closebet/results/is_baseline.json`, `is_tpsl.json`, `is_smallk.json`
