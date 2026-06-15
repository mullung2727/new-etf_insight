# 주문 원장 + 체결 대조 계획 (PLAN_ORDER_LEDGER)

## 핵심 사상

> **Kiwoom API는 무조건 broker를 통해서만 호출한다.**
> 주문 기록도 broker에서 발생하므로, 거래 원장도 broker가 관리한다.

## 배경 및 현재 상태

### 완료
- `run_close_bet.py` → broker `POST /orders` 경유 주문 (ETL이 Kiwoom 직접 호출 없음)
- `kiwoom_trade_history` 테이블 DDL + `upsert_trade_history()` 구현
- `close_bet_orders` DDL (`cntr_price`, `cntr_qty`, `verified_at` 컬럼 포함)
- ETL 배치 주문 시 `close_bet_orders` + `kiwoom_trade_history` 동시 기록

### 미완료 (이 플랜 범위)
- `kiwoom_trade_history`가 ETL `watchlist.duckdb`에 있음 → broker SQLite로 이전 필요
- broker-web 수동 주문은 아직 기록 안 됨
- 체결 대조(`kt00007`) 미구현

---

## 아키텍처 목표

```
[ETL 배치]     → POST /orders → broker → Kiwoom
[broker-web]   → POST /orders → broker → Kiwoom
                                  │
                          orders.py에서 기록
                                  │
                         broker/trades.db
                         kiwoom_trade_history
```

```
[verify 배치 16:00]
  ETL close_bet_orders (order_no 목록)
        │
        └→ broker GET /kt00007 → 당일 체결내역
                │
        order_no 매칭
                │
        close_bet_orders 업데이트 (cntr_price, cntr_qty, verified_at)
        Discord 보고
```

---

## Stage 1 — broker에 kiwoom_trade_history 이전

**목표**: 모든 주문 기록이 broker에서 발생하도록.

### 변경 내용

**broker/notes/db.py** (또는 별도 `broker/trades.py`)
- `kiwoom_trade_history` 테이블 DDL 추가 (SQLite)

```sql
CREATE TABLE IF NOT EXISTS kiwoom_trade_history (
    order_no    TEXT PRIMARY KEY,
    date        TEXT,
    ticker      TEXT,
    side        TEXT,
    order_type  TEXT,
    qty         INTEGER,
    price       INTEGER,
    status      TEXT,
    source      TEXT,
    raw         TEXT,
    created_at  TEXT
);
```

**broker/routers/orders.py**
- `place_order()` 성공 후 `kiwoom_trade_history`에 기록
- dry_run이 없는 broker 입장에서 `status = 'submitted'` or `'failed'`

**ETL run_close_bet.py**
- `upsert_trade_history()` 호출 제거
- `kiwoom_trade_history` DDL/upsert 함수 제거
- `close_bet_orders` 기록은 유지 (배팅 의도 원장)

**ETL 테스트**
- `test_trade_history.py` 제거 (broker로 이전)
- `test_close_bet_integration.py` — trade_history 관련 mock 제거

### TDD
- broker `test_orders.py`: `place_order()` 후 `kiwoom_trade_history` 기록 검증
- dry_run 없음 — broker는 실제 호출만 처리 (`accepted=True/False`로 status 결정)

---

## Stage 2 — broker에 kt00007 래퍼 추가

**목표**: 체결내역 조회도 broker 경유.

**broker/kiwoom/tr.py**
- `TR_CNTR_HIST = "kt00007"` 추가

**broker/kiwoom/orders.py** (또는 별도 `account.py`)
- `get_order_history(date: str) -> list[dict]`
  - `kt00007` 호출: `ord_dt=date, qry_tp=4, stk_bond_tp=1, sell_tp=2`
  - 응답 `acnt_ord_cntr_prps_dtl[]` 반환

**broker/routers/orders.py** (또는 `account.py`)
- `GET /orders/history?date=YYYYMMDD` 엔드포인트 추가
  - 반환: `[{order_no, ticker, cntr_qty, cntr_uv, ord_remnq, ...}]`

### TDD
- broker `test_orders.py`: `GET /orders/history` mock 응답 검증

---

## Stage 3 — ETL verify 배치 구현

**목표**: 16:00에 체결 대조 후 `close_bet_orders` 업데이트 + Discord 보고.

**etl/scripts/run_verify.py** (별도 스크립트)

흐름:
1. `close_bet_orders WHERE date=? AND status='submitted'` → order_no 목록
2. `GET {broker_url}/orders/history?date=?` → 당일 체결내역
3. `order_no` 매칭
   - 매칭 성공: `cntr_price=cntr_uv`, `cntr_qty`, `verified_at=now`, `status='confirmed'`
   - 매칭 실패: `status='unconfirmed'`, Discord 경고
4. `close_bet_orders` 업데이트
5. Discord 보고

**전제**:
- `close_bet_orders.order_no`가 키움 `ord_no` 7자리와 동일 포맷
- `kt00007`는 당일만 조회 가능 — 다음날 재대조 불가
- 모의투자(`mockapi`)에서도 동작 확인 필요

### TDD
- `test_verify.py`: broker mock → close_bet_orders 업데이트 검증

---

## Stage 4 — ops 배치 등록

**목표**: 16:00 자동 실행.

`ops/scheduled-tasks/close-bet-verify.xml` 생성 + 작업 스케줄러 등록:
- 실행: `run_verify.py --broker-url http://localhost:8001`
- 로그: `etl/logs/close-bet-verify-YYYYMMDD.log`

`ops/batches/daily-close-bet-verify.md` 생성:
- 결과 보고 지침 (DB 조회 + Discord)

---

## 범위 밖

- `kiwoom_trade_history` 조회 UI (broker-web 거래내역 탭) — PLAN_PENDING 연계
- 투자노트 이벤트 자동 연결
- 매도 주문 기록
- 실전 전환 시 추가 가드
