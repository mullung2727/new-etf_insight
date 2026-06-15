# watchlist 종가배팅 자동화 계획

## 목표

- 15:10 장중 watchlist 배치를 당일 주문 판단의 원본으로 사용한다.
- 기존 watchlist 후보에 대해 기존 LLM scoring을 실행한다.
- 15:19 주문 배치가 `score >= 80` 종목을 전부 키움 모의투자 시장가 1주로 매수한다.
- 시간, 점수 기준, 수량, 테스트 우회 옵션은 함수 파라미터로 둔다.

## 확정된 전제

- 후보 산출 기준은 기존 watchlist 로직 그대로 사용한다.
  - 당일 키움 `ka10030` 거래량 상위 스냅샷
  - 직전 60거래일 일별 거래량 top30 합집합 제외
  - 동전주 컷은 15:10 배치(`build_intraday_ranking.py`)에서 이미 적용됨 — 주문 배치에서 별도 확인 불필요
- 15:10 배치 결과를 그날의 주문 기준 원본으로 고정한다.
- 장 종료 후 확정 거래량으로 후보를 재계산하더라도 당일 주문 판단은 바꾸지 않는다.
- LLM scoring은 기존 `run_watchlist_research.py`의 프롬프트와 점수 체계를 유지한다.
- 주문은 15:20 동시호가 전에 시장가로 넣는다.
- 상한가, 거래정지, API 거절, 증거금 부족 등으로 매수가 실패하는 경우는 실패로 기록한다.
- 1차 검증은 키움 모의투자로 진행한다.

## 전체 흐름

```text
15:10
  -> 키움 ka10030 호출
  -> intraday_ranking(date) 저장
  -> watchlist(date) 후보 생성
  -> 후보별 기존 LLM scoring 실행 (순차)
     - 종목 1개 scoring 완료 시마다 llm_scores 즉시 upsert
       (현재 run_watchlist_research.py는 일괄 저장 방식 → 즉시 upsert 구조로 수정 필요)
     - scoring_deadline_time(15:17) 도달 시 새 호출 발행 금지, 진행 중 종목은 완료 대기
     - 종목당 scoring_timeout_per_symbol 초과 시 해당 종목 skip, 다음 종목 진행

15:19 (별도 배치 — daily-close-bet-order)
  -> precondition: 오늘 llm_scores 행 수 확인 → 0이면 즉시 abort + Discord 알림
  -> llm_scores에서 오늘 score >= score_threshold 조회, score 내림차순 정렬
  -> max_order_count 개수 제한 적용
  -> 대상 종목 전부 ka10001로 현재가(cur_prc) 조회
  -> 현재가 조회 성공 + cur_prc > 0이면 시장가 1주 매수
  -> 루프 내 now >= order_deadline_time 체크 — 마감 초과 시 남은 종목 즉시 중단
  -> 종목별 주문 결과 close_bet_orders upsert

15:20 이후
  -> 운영 모드에서는 주문 금지 (동시호가 15:20~15:30 배제 — 체결 방식이 달라 시장가 슬리피지 예측 불가)
  -> 테스트 모드에서는 시간 제한 우회 가능
```

## 파라미터

| 이름 | 기본값 | 설명 |
|---|---:|---|
| `target_date` | 오늘 | 대상 날짜, `YYYYMMDD` |
| `scoring_start_time` | `15:10:00` | watchlist + scoring 시작 기준 시각 (15:10 배치) |
| `scoring_deadline_time` | `15:17:00` | 이 시각 이후 새 LLM 호출 발행 금지. 진행 중 종목은 완료 대기 |
| `scoring_timeout_per_symbol` | `120` | 종목당 LLM 응답 최대 대기 시간 (초). 초과 시 skip |
| `order_time` | `15:19:00` | 주문 실행 가능 시작 시각 (15:19 배치) |
| `order_deadline_time` | `15:20:00` | 주문 실행 마감 시각. 루프 내에서 실시간 체크 |
| `score_threshold` | `80` | 매수 기준 점수 |
| `max_order_count` | `5` | score 내림차순 상위 N개 주문 상한. 후보명 대안: `order_cap`, `top_n_orders` |
| `qty_per_symbol` | `1` | 종목당 매수 수량 |
| `allow_order_outside_close_window` | `false` | 테스트용 시간창 우회 |
| `dry_run` | `true` | 주문 전송 없이 대상 선정/기록만 수행 |
| `kiwoom_env` | `paper` | 키움 환경, `paper` 또는 `real` |
| `order_interval_sec` | `0.5` | 종목 간 주문 간격 |

## DB 기준

- 기존 `etl/db/watchlist.duckdb`를 메인 DB로 사용한다.
- 기존 테이블
  - `intraday_ranking`: 15:10 키움 장중 거래량 스냅샷 저장
  - `watchlist`: 15:10 스냅샷 기반 후보 저장
  - `llm_scores`: 기존 LLM scoring 결과 저장
- 신규 테이블 후보
  - `close_bet_orders`

```sql
CREATE TABLE IF NOT EXISTS close_bet_orders (
    date        VARCHAR,
    ticker      VARCHAR,
    score       INTEGER,
    qty         INTEGER,
    order_type  VARCHAR,
    status      VARCHAR,
    order_no    VARCHAR,
    message     TEXT,
    raw         TEXT,
    created_at  TIMESTAMP,
    PRIMARY KEY (date, ticker)
);
```

## 주문 규칙

- `score >= score_threshold` 종목을 score 내림차순으로 정렬하여 최대 `max_order_count`개 매수한다.
- 테스트 단계에서는 각 종목 `1주`만 매수한다.
- 같은 날짜 같은 종목은 중복 주문하지 않는다.
- 주문 전 `ka10001`(주식기본정보요청)로 현재가(`cur_prc`) 조회를 수행한다.
  - `upl_pric`(상한가) 필드로 상한가 종목도 감지 가능.
  - 현재가 조회 실패 또는 `cur_prc <= 0`이면 해당 종목은 스킵한다.
- 주문 간격은 기본 `0.5초`로 둔다.
- 루프 내에서 매 종목 처리 전 `now >= order_deadline_time`을 체크한다. 초과 시 남은 종목 즉시 중단.
- 운영 모드에서는 `order_time <= now < order_deadline_time`일 때만 주문한다.
- 동시호가(15:20~15:30) 중 시장가 주문은 슬리피지 예측 불가로 배제한다.
- 테스트 모드에서는 `allow_order_outside_close_window=true`로 장중 아무 시간에 모의 주문을 검증할 수 있다.

## 실패 처리

- 15:19 배치 시작 시 오늘 `llm_scores` 행이 없으면 즉시 abort하고 Discord에 "scoring 배치 미실행" 알림을 보낸다.
- 15:19 시점까지 `llm_scores`에 저장된 종목만 주문 대상으로 본다. scoring 미완료 종목은 당일 주문에서 제외한다.
- `scoring_deadline_time` 이후 새 LLM 호출을 중단하여 발생한 미스코어링 종목은 skip 처리하고 Discord 리포트에 명시한다.
- `scoring_timeout_per_symbol` 초과로 skip된 종목도 Discord 리포트에 명시한다.
- 주문 실패는 자동 재시도하지 않는다. 실패 사유는 `close_bet_orders.status/message/raw`에 기록한다.
- 상한가로 인한 미체결 또는 주문 거절은 정상적인 실패 케이스로 취급한다.
- 수동 재시도: `close_bet_orders`에서 해당 `(date, ticker)` 행을 DELETE 후 `dry_run=false`로 재실행.

## 구현 순서

1. `run_watchlist_research.py`를 종목별 즉시 upsert 구조로 조정한다.
   - 현재: 전체 완료 후 `upsert_scores()`로 일괄 저장 (라인 744)
   - 변경: 루프 내 `research_one()` 완료 직후 단건 upsert
   - `scoring_deadline_time` 체크: 루프 진입 전 `now >= scoring_deadline_time`이면 break
   - `scoring_timeout_per_symbol` 체크: `research_one()`을 `concurrent.futures.ThreadPoolExecutor` + `timeout` 또는 `signal.alarm`으로 감쌈
   - verify: 후보 1개 scoring 완료 직후 `llm_scores`에 행이 생기는지 확인

2. `close_bet_orders` 테이블 생성/업서트 함수를 추가한다.
   - verify: 같은 날짜/종목 재실행 시 중복 행이 생기지 않는지 확인

3. 15:19 배치 스크립트(`run_close_bet.py`)를 작성한다.
   - 시작 시 precondition: `SELECT COUNT(*) FROM llm_scores WHERE date=?` — 0이면 abort
   - `score >= score_threshold` 조회, score 내림차순 정렬, `max_order_count` 슬라이싱
   - `ka10001`로 현재가 조회 (`cur_prc` 추출, `upl_pric` 상한가 감지)
   - 루프 내 `now >= order_deadline_time` 실시간 체크
   - verify: `dry_run=true`에서 주문 없이 대상과 예정 주문만 기록

4. 시간창, 테스트 우회, 모의투자 환경 파라미터를 적용한다.
   - verify: `allow_order_outside_close_window=true`일 때 현재 시간이 15:19가 아니어도 모의 주문 가능

5. 모의투자로 장중 테스트 주문을 실행한다.
   - verify: 키움 주문 응답과 `close_bet_orders` 기록이 일치

6. 운영 스케줄을 등록한다 (OpenClaw cron 2개).
   - `daily-etf-watchlist-intraday-kiwoom` (15:10): scoring 배치
   - `daily-close-bet-order` (15:19): 주문 배치
   - verify: 두 배치가 독립 실행되는지, 15:19 배치가 scoring 미완료 시 abort하는지 확인

## 남은 결정 사항

- 실제 운용 전 총 매수 금액 제한을 정해야 한다.
- 실제 운용 전 총 종목 수 제한을 정해야 한다.
- 시장가 주문의 예상 주문금액 가드를 보강해야 한다.
- `score`는 상승 확률이 아니라 원인 명확성 점수라는 점을 화면/리포트에 명확히 표시할지 정해야 한다.

## 주의

- 현재 프로젝트 SPEC의 초기 제외 범위에는 실시간 매매 기능이 포함되어 있다.
- 따라서 이 계획은 바로 실전 운용 기능으로 확장하지 않고, 모의투자 검증용 자동화로 먼저 제한한다.
- 실전 전환 시에는 별도 승인, 금액 제한, 환경 전환 가드, 주문 전 확인 로그를 추가해야 한다.
