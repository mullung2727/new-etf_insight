# close_bet 체결 대조 계획

## 한 줄 요약

`close_bet_orders`에 기록된 주문을 키움 `kt00007`로 조회해 실제 체결 여부와 단가를 대조하고, 결과를 DB에 기록 후 Discord로 보고한다.

## 목적

`run_close_bet.py`가 주문을 넣고 `close_bet_orders`에 `order_no`를 기록하지만, 키움이 실제로 체결했는지는 주문 응답만으로 확정할 수 없다. 장 마감 후 `kt00007`로 당일 체결내역을 조회해 `order_no` 기준으로 대조하면:

- 체결 확정: `cntr_qty > 0`, 실제 체결단가(`cntr_uv`) 기록
- 미체결/거절: `close_bet_orders.status` 업데이트, Discord 알림
- 불일치(주문 기록 있는데 체결내역 없음): 명시적 경고

## TR 스펙

### kt00007 계좌별주문체결내역상세요청 — `/api/dostk/acnt`

bulk 대조용 (당일 매수 체결 전체 조회):

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| `ord_dt` | `YYYYMMDD` | 대상일 |
| `qry_tp` | `4` | 체결내역만 |
| `stk_bond_tp` | `1` | 주식 |
| `sell_tp` | `2` | 매수 |
| `stk_cd` | `''` | 전종목 |
| `fr_ord_no` | `''` | 전체 |
| `dmst_stex_tp` | `%` | 전체 |

응답 `acnt_ord_cntr_prps_dtl[]`의 `ord_no` ↔ `close_bet_orders.order_no` 매칭.

핵심 필드: `ord_no`, `stk_cd`, `cntr_qty`(체결수량), `cntr_uv`(체결단가), `ord_remnq`(잔량)

### ka10076 체결요청 — `/api/dostk/acnt`

단건 확인용 (`ord_stt` 직접 확인 필요 시):

| 파라미터 | 값 |
|----------|----|
| `stk_cd` | 종목코드 |
| `qry_tp` | `1` |
| `sell_tp` | `2` |
| `stex_tp` | `0` |

## close_bet_orders 스키마 변경

기존 컬럼에 대조 결과 컬럼 추가:

```sql
ALTER TABLE close_bet_orders ADD COLUMN cntr_price   INTEGER;
ALTER TABLE close_bet_orders ADD COLUMN cntr_qty     INTEGER;
ALTER TABLE close_bet_orders ADD COLUMN verified_at  TIMESTAMP;
```

또는 `CREATE TABLE IF NOT EXISTS`에 포함해 초기부터 생성.

## 실행 방식

`run_close_bet.py`에 `--verify` 모드로 분리:

```powershell
# 주문 실행 (15:19 배치)
.\.venv\Scripts\python.exe scripts\run_close_bet.py --date 20260615

# 체결 대조 (장 마감 후, 예: 16:00 별도 배치 또는 수동)
.\.venv\Scripts\python.exe scripts\run_close_bet.py --date 20260615 --verify
```

`--verify` 모드 흐름:
1. `close_bet_orders WHERE date=? AND status='submitted'` 조회
2. `kt00007`로 당일 매수 체결내역 전체 조회
3. `order_no` 기준 매칭
4. 매칭 성공: `cntr_price`, `cntr_qty`, `verified_at`, `status='confirmed'` upsert
5. 매칭 실패: `status='unconfirmed'` + Discord 경고
6. Discord에 대조 결과 리포트

## ops 배치

별도 cron 또는 `daily-close-bet-order.md`에 verify 단계 추가 — 실행 시점 결정 필요 (16:00 또는 수동).

## 구현 순서

1. `close_bet_orders` DDL에 `cntr_price`, `cntr_qty`, `verified_at` 추가
2. `kt00007` 래퍼 함수 작성 (`broker/kiwoom/` 또는 `etl/scripts/`)
3. `--verify` 모드 구현 + 테스트
4. ops 배치 파일 및 cron 등록

## 전제

- `close_bet_orders.order_no`가 키움 `ord_no` 7자리와 동일한 포맷으로 저장되어야 함
- `kt00007`는 당일(same `ord_dt`)만 조회 가능 — 다음날 대조는 불가
- 모의투자(`mockapi`)에서도 동작 확인 필요

## 범위 밖

- 투자노트 이벤트 자동 연결 (`project_kiwoom_trade_history_todo.md` 별도 관리)
- 실전 전환 시 추가 가드 (별도 승인 필요)
