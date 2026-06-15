# 주문 원장 + 체결 대조 계획 (PLAN_ORDER_LEDGER)

## 핵심 사상

> **Kiwoom API는 무조건 broker를 통해서만 호출한다.**
> 주문 기록도 broker에서 발생하므로, 거래 원장도 broker가 관리한다.

## 구현 완료 (2026-06-15)

4개 스테이지 전부 구현·테스트 완료 (broker 9 + ETL 37 통과).

- **Stage 1**: `kiwoom_trade_history`를 broker `notes.db`로 이전. `OrderRequest.source`로
  주문 출처(close_bet/manual) 구분. 모든 주문이 broker `POST /orders`에서 기록됨
  (ETL 배치 + broker-web 수동 주문 모두). ETL의 옛 trade_history 코드 제거.
- **Stage 2**: broker `kt00007` 래퍼 + `GET /orders/history` (정규화 응답).
- **Stage 3**: ETL `run_verify.py` — 체결 대조 후 `close_bet_orders` 업데이트.
- **Stage 4**: `close-bet-verify.xml`(평일 16:00) + `daily-close-bet-verify.md` 등록.

남은 실측 확인: `kt10000` 응답 `ord_no`와 `kt00007` `ord_no` 포맷 일치 여부
(첫 실거래 후 1회 — `normalize_order_no`로 1차 방어 중).

> 이하는 구현 당시 설계 기록이다.

---

## 아키텍처 목표

```
[ETL 배치]     → POST /orders (source="close_bet") → broker → Kiwoom
[broker-web]   → POST /orders (source="manual")    → broker → Kiwoom
                                  │
                          orders.py에서 기록
                                  │
                         broker/notes.db
                         (notes / note_events / kiwoom_trade_history)
```

> **DB 위치**: 기존 `broker/notes.db` 단일 파일에 `kiwoom_trade_history`
> **새 테이블**을 추가한다. 기존 `notes` 테이블은 건드리지 않으며, 같은
> 연결(`notes/db.py`의 lazy-init 단일 connection)을 공유한다. 별도 db 파일을
> 만들지 않는다(두 번째 연결 관리 회피).

```
[verify 배치 16:00]
  ETL close_bet_orders (order_no 목록)
        │
        └→ broker GET /orders/history (내부 kt00007) → 당일 체결내역
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

**broker/kiwoom/models.py**
- `OrderRequest`에 `source: str = "manual"` 선택 필드 추가
  - ETL 배치 → `"close_bet"`, broker-web 수동 주문 → 기본값 `"manual"`
  - broker가 주문 맥락을 알 수 있는 유일한 통로

**broker/notes/db.py** (기존 `notes.db` 단일 연결 재사용)
- `_SCHEMA`에 `kiwoom_trade_history` 테이블 DDL 추가 (새 테이블, 기존 테이블 불변)

```sql
CREATE TABLE IF NOT EXISTS kiwoom_trade_history (
    order_no    TEXT PRIMARY KEY,
    date        TEXT,
    ticker      TEXT,
    side        TEXT,
    order_type  TEXT,
    qty         INTEGER,
    price       INTEGER,   -- 주문단가(시장가면 0). 실제 체결가는 close_bet_orders.cntr_price가 보유
    status      TEXT,      -- broker 관점: 'submitted' | 'failed'
    source      TEXT,      -- OrderRequest.source ('close_bet' | 'manual')
    raw         TEXT,
    created_at  TEXT       -- ISO8601 문자열 (notes 테이블 created_at 패턴과 동일)
);
```

**broker/routers/orders.py**
- `place_order()` 성공 후 `kiwoom_trade_history`에 기록
  - `status`: `accepted=True` → `'submitted'`, 가드/Kiwoom 거부(예외) → `'failed'`
  - `price`: 시장가 주문은 `req.price`(=0) 그대로 기록 (조회 현재가 안 넣음)
  - `date`: broker 서버의 Asia/Seoul 당일 (`order_no`가 비면 기록 skip)
  - `ticker`: `req.symbol`

**ETL run_close_bet.py**
- `place_order_via_broker`가 POST body에 `"source": "close_bet"` 추가
- `upsert_trade_history()` 호출 제거
- `create_kiwoom_trade_history_table()` / `upsert_trade_history()` 함수 제거
- `fetch_price_via_broker`의 `cur_prc`는 더 이상 trade_history에 안 쓰임 — skip 판정용으로만 유지
- `close_bet_orders` 기록은 유지 (배팅 의도 원장)

**ETL 테스트**
- `test_trade_history.py` 제거 (broker로 이전)
- `test_close_bet_integration.py` — `kiwoom_trade_history` 관련 helper/seed/assert 제거
  (`create_kiwoom_trade_history_table`, `_trade_rows`, `TestTradeHistoryIntegration`)

**기존 데이터**
- 현재 `watchlist.duckdb`의 `kiwoom_trade_history` 행은 마이그레이션하지 않고 폐기
  (테스트성 소량, 실거래 이력 아님). 옮길 가치 있는 행 발견 시 그때 수동 이관.

### TDD
- broker `test_orders.py`: `place_order()` 후 `kiwoom_trade_history` 기록 검증
  - `source` 전달값이 그대로 저장되는지
  - 가드 거부(422) 시 `'failed'`로 기록되는지 (또는 기록 skip 정책 — 구현 시 확정)
- dry_run 없음 — broker는 실제 호출만 처리

---

## Stage 2 — broker에 kt00007 래퍼 추가

**목표**: 체결내역 조회도 broker 경유.

**broker/kiwoom/tr.py**
- `TR_CNTR_HIST = "kt00007"` 추가

**broker/kiwoom/orders.py** (또는 별도 `account.py`)
- `get_order_history(date: str) -> list[dict]`
  - `kt00007` 호출 (POST, `EP_ACNT = /api/dostk/acnt`):
    ```
    ord_dt=date          # YYYYMMDD
    qry_tp="4"           # 체결내역만
    stk_bond_tp="1"      # 주식
    sell_tp="2"          # 매수
    stk_cd=""            # 전체 종목
    fr_ord_no=""         # 전체
    dmst_stex_tp="%"     # 전체 거래소 (Required=Y — 누락 주의)
    ```
  - 응답 `acnt_ord_cntr_prps_dtl[]` 반환
  - `ord_no`는 0-padding 7자리 문자열("0000050")

**broker/routers/orders.py** (또는 `account.py`)
- `GET /orders/history?date=YYYYMMDD` 엔드포인트 추가
  - 반환: `[{order_no, ticker(=stk_cd), cntr_qty, cntr_uv, ord_remnq, ...}]`

### TDD
- broker `test_orders.py`: `GET /orders/history` mock 응답 검증

---

## Stage 3 — ETL verify 배치 구현

**목표**: 16:00에 체결 대조 후 `close_bet_orders` 업데이트 + Discord 보고.

**etl/scripts/run_verify.py** (별도 스크립트)

흐름:
1. `close_bet_orders WHERE date=? AND status IN ('submitted','unconfirmed')` → order_no 목록
   - `dry_run`/`skipped`/`failed`는 체결 대상 아님 → 제외
   - `unconfirmed`를 포함해야 당일 재실행 시 멱등 재시도 가능
2. `GET {broker_url}/orders/history?date=?` → 당일 체결내역
   - broker 연결 실패/HTTP 오류: 업데이트 중단 + Discord에 "broker 조회 실패" 보고 후 종료
3. `order_no` 매칭 — 양쪽 모두 `.lstrip("0")` 정규화 후 비교
   (키움 응답은 0-padding 7자리, broker 저장값은 padding 보장 안 됨)
   - 매칭 성공: `cntr_price=cntr_uv`, `cntr_qty`, `verified_at=now`, `status='confirmed'`
   - 매칭 실패: `status='unconfirmed'` + WARN 로그
4. `close_bet_orders` 업데이트
5. 구조화된 로그 출력 (`[verify] ... → confirmed/unconfirmed`, 합계)

> **Discord 보고는 스크립트가 직접 하지 않는다.** 기존 `daily-close-bet-order`
> 아키텍처와 동일하게, `run_verify.py`는 실행·로그·DB 업데이트만 담당하고
> Discord 보고는 OpenClaw 배치(`daily-close-bet-verify.md`, 16:02)가 로그/DB를
> 읽어 수행한다.

**전제 / 확인 사항**:
- 매수주문(`kt10000`) 응답 `ord_no`와 `kt00007`의 `ord_no` 포맷 일치 여부는
  **실주문 1건으로 실측 확인 필요** (현재 미검증 — `lstrip("0")` 정규화로 1차 방어)
- `kt00007`는 당일만 조회 가능 — 다음날 재대조 불가 (재시도 윈도우 = 당일)
- 부분체결: 시장가 1주라 `ord_remnq`는 0 전제. >0이면 그대로 기록만 하고 경고
- 모의투자(`mockapi`)에서도 동작 확인 필요
- `run_verify.py`는 시간창 가드 없음 (당일 내 수동 재실행 허용)

### TDD
- `test_verify.py`: broker mock → close_bet_orders 업데이트 검증

---

## Stage 4 — ops 배치 등록

**목표**: 16:00 자동 실행.

> **왜 16:00**: 종가 주문은 15:19~15:20 제출 → 종가 단일가 체결은 15:30 확정.
> 16:00이면 체결이 모두 확정된 뒤이므로 1회 조회로 대조 가능.

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
