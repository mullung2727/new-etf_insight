# PLAN — 당일 LLM 재료 분류·생존성 분석

## 1. 배경과 현재 판단

- 현재 검증 기준을 통과한 종가배팅·눌림목 운영 전략은 0개다.
- 기존 LLM 점수는 `D+1 시가 > D 종가`를 직접 예측하지만, 현재 표본에서 점수의 구분력이 확인되지 않았다.
- LLM의 역할을 다음 날 가격 예측에서 **재료 식별, 재료 종류 분류, 재료 생존 여부와 예상 지속기간 판단**으로 옮긴다.
- 가격·거래량·차트 위치·상한가 진입 가능 여부는 기존 정량 로직과 주문 가드가 담당한다.
- 새 분석은 먼저 shadow로 저장하며, 검증 전에는 기존 `llm_scores.score`·종가배팅 주문 조건을 바꾸지 않는다.

### 구현 상태

- 완료: 같은 LLM 응답의 주재료·보조재료 구조, 자유 카테고리, 상태·기간·1~5점 schema
- 완료: 시점 고정 근거 참조 검증, 운영 JSON 보존, 신규 `llm_catalyst_assessments` 멱등 저장
- 완료: 기존 `llm_scores`와 신규 catalyst 행의 단일 트랜잭션 저장·실패 시 전체 롤백
- 완료: 기존 D+1 점수 및 종가배팅 주문 회귀 검증
- 대기: 표본 축적 후 taxonomy 정규화, paired backtest, 운영 적용 판정

## 2. 목표

- 매 거래일 15:00 LLM 분석에서 종목별 **주재료 1개와 보조재료 0개 이상**을 한 번에 추출한다.
- 주재료와 모든 보조재료에 대해 자유 카테고리, 생존 상태, 예상 지속기간, 1~5점 생존 점수를 저장한다.
- 재료 종류를 고정 목록으로 제한하지 않는다. LLM이 당일 근거에 맞는 `category_raw`를 직접 생성한다.
- 원본 재료 설명과 당일 자유 카테고리를 보존해, 표본이 쌓인 뒤 별도 taxonomy로 재분류할 수 있게 한다.
- 동일 정량 신호에 LLM 재료 필터를 적용한 경우와 적용하지 않은 경우를 paired backtest로 비교한다.

## 3. 범위

### 포함

- 15:00 기준 뉴스·텔레그램 근거를 이용한 당일 재료 분석
- 주재료와 보조재료 동시 추출
- 각 재료의 자유 카테고리와 생존성 판단
- 원본 분석의 시점 고정 저장
- 사후 표준 카테고리 재분류가 가능한 저장 구조
- 종가배팅·눌림목 정량 신호와의 paired backtest

### 제외

- 새 점수를 즉시 실주문 기준으로 사용하는 것
- `alive_score`에 가격·거래량·기술지표를 포함하는 것
- 재료별 세부 가점·감점 합산표를 만드는 것
- 처음부터 고정 카테고리 목록으로 출력을 제한하는 것
- 과거 자료를 현재 뉴스 검색 결과로 재생성해 시점 안전 표본으로 간주하는 것

## 4. 확정 판단 구조

### 4.1 종목 단위

- `primary_catalyst`: 당일 종목 움직임과 향후 관심 지속을 가장 잘 설명하는 주재료 1개
- `secondary_catalysts`: 주재료와 구별되는 보조재료 전체. 없으면 빈 배열
- 같은 사실의 표현만 다른 항목은 별도 보조재료로 중복 생성하지 않는다.
- 보조재료도 주재료와 동일한 필드를 모두 판정한다.
- 종목 전체의 임의 가중평균 점수는 만들지 않는다. 백테스트에서는 주재료 기준과 “주·보조 중 최고 생존점수” 기준을 별도 규칙으로 비교한다.

### 4.2 재료 단위 필드

```json
{
  "description": "미국 완성차 업체와 차세대 배터리 공급 협의가 진행 중이며 구체적 계약 발표 가능성이 남아 있음",
  "label": "해외 배터리 공급계약 기대",
  "category_raw": "해외 고객사 신규 공급계약",
  "status": "alive",
  "expected_duration": "two_to_five_trading_days",
  "alive_score": 4,
  "reason": "공급 협의가 진행 중이고 구체적인 계약 결과가 아직 발표되지 않았다.",
  "invalidation": "협상 중단 또는 공급계약 부인",
  "evidence_refs": ["원문 URL 또는 저장된 게시물 참조"]
}
```

### 4.3 상태

- `alive`: 후속 사실·일정·확산 가능성이 남아 있음
- `uncertain`: 재료는 있으나 사실성 또는 지속 여부가 불명확함
- `exhausted`: 이미 종료·무효화됐거나 후속 동력이 사실상 소멸함

### 4.4 예상 지속기간

- `intraday`: 당일성
- `two_to_five_trading_days`: 2~5거래일
- `one_week_or_more`: 1주 이상
- `unknown`: 근거 부족

### 4.5 생존 점수

- `1`: 소멸·무효화된 재료
- `2`: 대부분 반영됐거나 후속 동력이 약한 재료
- `3`: 살아 있으나 단기성 또는 지속 여부가 불확실한 재료
- `4`: 살아 있고 후속 일정·확산 가능성이 확인되는 재료
- `5`: 공식 확인된 강한 재료이며 중장기 후속 전개가 남은 재료

점수는 세부 항목의 산술합으로 만들지 않는다. LLM이 위 앵커를 기준으로 1~5점 중 하나를 선택한다.

## 5. 카테고리 운영 원칙

### 당일 분석

- 모든 주·보조재료에 `category_raw`를 필수 생성한다.
- 카테고리는 자유 텍스트이며 기존 목록에 맞추기 위해 재료 의미를 왜곡하지 않는다.
- `description`, `label`, `category_raw`는 당일 원본으로 보존하고 사후 작업에서 덮어쓰지 않는다.

### 사후 표준화

- 표본이 쌓인 뒤 `description + label + category_raw`를 사용해 유사 재료를 묶는다.
- 표준 분류는 `category_normalized`, `taxonomy_version`, `classified_at`으로 별도 저장한다.
- taxonomy가 바뀌면 새 버전으로 다시 분류하고, 당일 원본과 이전 분류 버전은 유지한다.
- 표준 카테고리는 분석 편의를 위한 파생값이며 당일 LLM 출력 제한 목록으로 사용하지 않는다.

예시:

```json
{
  "category_raw": "미국 기업 납품 추진",
  "category_normalized": "수주·공급계약",
  "taxonomy_version": "v1"
}
```

## 6. 당일 실행 설계

현재 운영 경로는 다음과 같다.

```text
14:59 watchlist 생성
  -> 15:00 시장 스냅샷
  -> watchlist_probability_langgraph.py 실행
  -> llm_scores 및 운영 리포트 저장
  -> 15:19 종가배팅 주문 판단
```

새 재료 분석은 `watchlist_probability_langgraph.py`의 기존 후보별 LLM 호출에 포함한다.

- 후보별 LLM 호출 횟수를 추가하지 않고 **현재 한 번의 구조화 출력에 재료 필드를 추가**한다.
- shadow 기간에는 기존 D+1 확률 출력도 호환 목적으로 유지한다.
- 재료 생존 판단에는 `collected_news`와 `telegram_history`만 사용한다.
- `market_snapshot`은 기존 D+1 확률 계산에만 사용하며, 재료의 `status`, `expected_duration`, `alive_score`, `category_raw` 판단 근거로 사용하지 않는다.
- 입력 `as_of` 이후 기사·게시물·가격은 사용하지 않는다.
- 후보 하나의 분석 실패가 발생하면 기존처럼 전체 점수 완전성 검사를 통과하지 못하게 하고 DB write를 중단한다.

## 7. 저장 설계

기존 `llm_scores`는 종가배팅 호환용으로 유지한다. 새 원본은 신규 테이블에 별도로 저장한다.

```sql
CREATE TABLE IF NOT EXISTS llm_catalyst_assessments (
    date                  TEXT NOT NULL,
    ticker                TEXT NOT NULL,
    as_of                 TEXT NOT NULL,
    primary_category_raw  TEXT NOT NULL,
    primary_status        TEXT NOT NULL,
    primary_duration      TEXT NOT NULL,
    primary_alive_score   INTEGER NOT NULL,
    max_alive_score       INTEGER NOT NULL,
    assessment_json       TEXT NOT NULL,
    prompt_version        TEXT NOT NULL,
    model                 TEXT,
    generated_at          TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
);
```

- `assessment_json`에 주재료와 모든 보조재료, 근거 참조를 손실 없이 저장한다.
- 조회가 잦은 주재료 필드와 주·보조 최고점만 별도 컬럼으로 둔다.
- `max_alive_score`는 저장 편의를 위한 최댓값이며 가중 종합점수가 아니다.
- 동일 날짜·종목 재실행은 upsert하되, 운영 리포트에도 전체 구조화 결과를 남긴다.
- 과거 시점의 재현성을 위해 `as_of`, `prompt_version`, `model`, `generated_at`, `evidence_refs`를 저장한다.
- 사후 표준 카테고리는 이 테이블의 원본 JSON을 수정하지 않고 별도 파생 테이블 또는 버전 파일에 저장한다.

## 8. 구현 대상

### 수정

- `research/watchlist_expected_return/prompts/watchlist_probability.md`
  - 주재료·보조재료 추출 규칙과 1~5점 앵커 추가
  - 재료 생존 판단에서 가격 입력 사용 금지 명시
- `research/watchlist_expected_return/watchlist_scoring_schema.json`
  - `primary_catalyst`, `secondary_catalysts` 구조 추가
- `research/watchlist_expected_return/watchlist_probability_langgraph.py`
  - 재료 결과 보존, 검증, 신규 테이블 upsert, 운영 리포트 출력 추가
- `research/watchlist_expected_return/tests/test_watchlist_probability_langgraph.py`
  - 구조·저장·시점·호환성 테스트 추가

### 유지

- `llm_scores.score`의 현재 의미와 컬럼
- `etl/scripts/run_close_bet.py`의 점수 임계값·주문 대상 선정
- 종가배팅·눌림목 진입 및 청산 조건
- 상한가 체결 불가 하드 규칙

## 9. 요구사항과 테스트 매핑

1. 주재료와 보조재료를 당일 모두 분석한다.
   - 한 후보의 단일 LLM 응답에 주재료 1개와 보조재료 2개가 들어오는 fixture를 검증한다.
   - LLM 호출 mock count가 후보당 1회인지 검증한다.

2. 보조재료가 없는 종목도 처리한다.
   - `secondary_catalysts=[]`가 스키마와 DB 저장을 통과하는지 검증한다.

3. 각 재료를 당일 자유 카테고리로 분류한다.
   - 주·보조 모든 재료의 `category_raw`가 비어 있으면 거부되는지 검증한다.
   - 사전 목록에 없는 자유 카테고리 문자열이 정상 저장되는지 검증한다.

4. 점수를 과도하게 세분화하지 않는다.
   - 각 재료의 점수가 정수 1~5만 허용되는지 검증한다.
   - 세부 가점·감점 필드가 신규 catalyst schema에 없는지 검증한다.

5. 원본을 나중에 다시 분류할 수 있어야 한다.
   - `description`, `label`, `category_raw`, `evidence_refs`가 `assessment_json`과 리포트에 모두 보존되는지 검증한다.
   - 사후 `category_normalized` 생성 시 원본 JSON을 변경하지 않는지 별도 taxonomy 구현 단계에서 검증한다.

6. 가격 판단과 정성 판단을 분리한다.
   - 같은 텍스트 근거에 서로 다른 `market_snapshot`을 넣어도 catalyst 필드가 동일하도록 mock 기반 회귀 테스트를 둔다.
   - 프롬프트에 catalyst 판정에서 `market_snapshot` 사용 금지 문구가 있는지 검증한다.

7. 미래정보를 사용하지 않는다.
   - `as_of` 이후 뉴스와 당일 close/evening 텔레그램이 입력에서 제외되는지 검증한다.
   - 생성시각·근거 참조가 저장되는지 검증한다.

8. 기존 주문을 검증 전에 바꾸지 않는다.
   - shadow 실행 후 기존 `llm_scores.score` 저장과 `run_close_bet.py` 후보 선택 결과가 기존 fixture와 동일한지 검증한다.

9. 재실행 시 중복되지 않는다.
   - 동일 `(date, ticker)`를 두 번 저장해 신규 테이블 행이 1개인지 검증한다.

## 10. 검증 계획

### 비교군

- A: 정량 종가배팅 또는 눌림목 전략 단독
- B: A + `primary.alive_score >= 3`
- C: A + `primary.alive_score >= 4`
- D: A + 주·보조 중 하나라도 `alive_score >= 4`
- E: A에서 주재료 `status=exhausted`만 veto
- F: A 후보를 주재료 점수, 최고 재료 점수로 각각 재정렬

### 성과 지표

- 비용 차감 실제 TP/SL 손익
- D+1·D+3·D+5 코스피 대비 초과수익
- 승률, 평균·중앙값 수익률, MDD
- LLM이 제거한 손실과 함께 놓친 수익
- `category_raw` 및 사후 표준 카테고리별 표본 수와 성과
- 주재료 단독과 주·보조 포함 규칙의 증분 차이

### 판정 게이트

- 전체 paired 표본 30건 이상, 시간 후반부 10건 이상이 되기 전에는 연구 후보로도 확정하지 않는다.
- 전체와 시간 후반부 모두 비용 차감 평균수익이 양수이고, 후반부 코스피 초과수익이 양수여야 한다.
- MDD가 정량 기준군보다 악화되면 채택하지 않는다.
- 위 조건을 통과해도 운영 후보가 아니라 **독립 미래 기간 재검증 후보**로만 승격한다.
- 독립 미래 기간에서 최소 20건을 추가 검증한 뒤 주문 필터·재정렬·veto 적용 여부를 별도 승인한다.

## 11. 구현 순서

1. JSON schema와 프롬프트를 확장한다.
   - verify: 주재료 1개·보조재료 N개 구조와 자유 카테고리가 schema validation을 통과한다.
2. LangGraph 결과와 운영 리포트에 전체 catalyst 구조를 보존한다.
   - verify: 후보당 LLM 호출 1회, 리포트에 주·보조재료 누락 없음.
3. `llm_catalyst_assessments` 생성·upsert를 추가한다.
   - verify: 멱등 저장, 원본 JSON·시점·프롬프트 버전 보존.
4. 기존 `llm_scores`와 주문 경로 회귀 테스트를 실행한다.
   - verify: 기존 점수와 주문 후보 선정 결과 변화 없음.
5. 당일 스케줄에서 shadow 저장을 시작한다.
   - verify: 15:00 배치 결과에 당일 모든 후보의 catalyst assessment가 존재하고 실패 시 불완전 저장하지 않음.
6. 표본이 누적되면 사후 taxonomy 분류를 별도 단계로 구현한다.
   - verify: 원본을 덮어쓰지 않고 taxonomy version별 파생 분류 생성.
7. paired backtest를 실행하고 독립 기간 검증 전까지 주문 로직은 유지한다.

## 12. 완료 기준

- 당일 모든 후보에 주재료 1개와 보조재료 배열이 생성된다.
- 주·보조 모든 재료에 자유 카테고리·상태·지속기간·1~5점·근거·무효화 조건이 저장된다.
- 원본 설명과 자유 카테고리는 사후 표준화 후에도 보존된다.
- 후보당 LLM 호출은 1회이며 현재 배치 시간창을 늘리지 않는다.
- 기존 D+1 점수와 주문 로직은 shadow 검증 중 변경되지 않는다.
- 시점 차단, 저장 멱등성, schema, 운영 호환성 테스트가 통과한다.
- 표본 기준과 독립 기간 검증을 통과하기 전에는 새 점수를 실주문에 사용하지 않는다.
