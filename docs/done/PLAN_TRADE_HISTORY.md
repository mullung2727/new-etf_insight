# 거래 이력 통합 계획 (kiwoom_trade_history)

## 핵심 사상

> **Kiwoom API는 무조건 broker를 통해서만 호출한다.**
> ETL 스크립트가 Kiwoom HTTP를 직접 호출하는 코드는 허용하지 않는다.

## 배경

`run_close_bet.py`가 Kiwoom API를 직접 호출하는 코드를 자체 구현하고 있음:
- `get_token()` — 토큰 발급
- `fetch_current_price()` — ka10001 직접 호출
- `place_market_order()` — kt10000 직접 호출

broker는 이미 동일 기능을 REST API로 제공한다:
- `GET /quotes/{symbol}` → 현재가
- `POST /orders` → 주문

ETL 중복 구현을 제거하고 broker 경유로 통일한다.
성공한 모든 주문을 `kiwoom_trade_history`(범용 원장)에 기록한다.

## 최종 아키텍처

```
run_close_bet.py
  │
  ├─ GET  http://localhost:8001/quotes/{ticker}
  │       └─ 현재가 수신
  │
  └─ POST http://localhost:8001/orders
          body: {symbol, side:"buy", qty, order_type:"market"}
          └─ OrderResult(order_no, accepted)
                 │
                 ├─ close_bet_orders  upsert  (의도 기록 — 기존 유지)
                 └─ kiwoom_trade_history upsert  (범용 원장 — 신규)
```

broker 코드 변경 없음. 이미 필요한 엔드포인트 존재.

## kiwoom_trade_history 스키마

```sql
CREATE TABLE IF NOT EXISTS kiwoom_trade_history (
    order_no    VARCHAR PRIMARY KEY,
    date        VARCHAR,
    ticker      VARCHAR,
    side        VARCHAR,   -- 'buy' | 'sell'
    order_type  VARCHAR,   -- 'market' | 'limit'
    qty         INTEGER,
    price       INTEGER,   -- 주문 시점 현재가
    status      VARCHAR,   -- 'submitted' | 'failed' | 'dry_run'
    source      VARCHAR,   -- 'close_bet' | 향후 확장
    raw         TEXT,
    created_at  TIMESTAMP
)
```

dry_run 레코드는 `order_no = 'DRY_{ticker}_{YYYYMMDDHHMMSS}'`로 구분.

## 단계별 구현 계획

### Stage 1 — kiwoom_trade_history DDL + upsert
**목표**: DB 레이어 완성. broker/Kiwoom 무관하게 독립 검증 가능.

- `create_kiwoom_trade_history_table(con)` 함수 추가
- `upsert_trade_history(watchlist_db, row)` 함수 추가 (order_no PK 중복 시 skip)
- TDD: DDL 멱등, 삽입, PK 중복 skip, dry_run order_no 형식

### Stage 2 — fetch_price_via_broker() 구현
**목표**: 현재가 조회를 broker REST API 경유로 전환.

- `fetch_price_via_broker(broker_url, ticker) -> int | None`
  - `GET {broker_url}/quotes/{ticker}` → `response["price"]`
  - 실패/None → None 반환
- TDD: 정상 응답, price=None 응답, HTTP 에러, 연결 실패

### Stage 3 — place_order_via_broker() 구현
**목표**: 주문을 broker REST API 경유로 전환.

- `place_order_via_broker(broker_url, ticker, qty, dry_run) -> dict`
  - dry_run=True → HTTP 호출 없이 DRY_RUN dict 반환
  - `POST {broker_url}/orders` body: `{symbol, side:"buy", qty, order_type:"market"}`
  - 응답 `accepted=False` → status="failed"
  - HTTP 에러 → status="failed"
- TDD: dry_run, 성공, accepted=False, HTTP 에러, request body 검증

### Stage 4 — main() 연결 + 양쪽 테이블 기록
**목표**: 실제 흐름에서 broker 경유 주문 + 두 테이블 동시 기록.

- main()에서 `fetch_current_price` → `fetch_price_via_broker` 교체
- main()에서 `place_market_order` → `place_order_via_broker` 교체
- 주문 성공 후 `upsert_trade_history` 추가 호출
- `BROKER_API_URL` 파라미터(`--broker-url`, env: `BROKER_API_URL`) 추가
- TDD(통합): broker 엔드포인트 mock → close_bet_orders + kiwoom_trade_history 동시 기록 검증

### Stage 5 — 구코드 제거 + 테스트 정리
**목표**: 핵심 사상 위반 코드 완전 제거. 전체 테스트 GREEN.

제거 대상:
- `get_token()`, `TOKEN_CACHE_PATH`
- `fetch_current_price()`
- `place_market_order()`
- `_HOSTS`, `EP_STKINFO`, `EP_ORDR`, `TR_QUOTE`, `TR_BUY`
- `requests` Kiwoom 직접 호출 코드

테스트 업데이트:
- `test_close_bet.py`: 제거된 함수 관련 테스트 삭제/수정
- `test_close_bet_integration.py`: `get_token`/`fetch_current_price`/`place_market_order` mock → broker URL mock으로 교체

### Stage 6 — ops 업데이트
**목표**: 배치 실행 전 broker 기동 확인 precondition 추가.

- `ops/batches/daily-close-bet-order.md`에 broker health check 추가:
  ```powershell
  Invoke-RestMethod http://localhost:8001/health
  ```

## 제약

- broker(`http://localhost:8001`)가 15:10~15:20 중 반드시 기동되어 있어야 함
- dry_run=True 시 broker 호출 없음 → broker 미기동 상태에서도 dry_run 테스트 가능
- broker가 내려가면 run_close_bet.py abort

## 범위 밖

- broker-web 수동 주문 시 kiwoom_trade_history 기록 (broker 측 구현 필요)
- kiwoom_trade_history 조회 API / UI (PLAN_PENDING.md 거래내역 탭)
- 매도 주문
- PLAN_CLOSE_BET_VERIFY.md의 kt00007 대조 (이 플랜 완료 후 단순화 가능)
