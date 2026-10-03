# PLAN — 종가베팅 청산 반반 분할 (동시호가 vs 매도1호가 추격)

**한 줄 요약** — 청산 워커가 08:50 기동 때 보유 수량을 두 줄로 쪼개, 절반은 08:55 KRX 동시호가 시장가,
절반은 09:00:30부터 10초마다 `매도1호가 − 1틱` 지정가 추격, 09:01에 두 줄의 남은 수량을 시장가로 정리한다.
체결된 것만 새 테이블 `close_bet_sell_fills` 에 남긴다.

선행 PLAN = `docs/PLAN_CLOSE_BET_AUCTION_EXIT.md` (동시호가 청산, 미커밋·미배포). 이 PLAN 은 그 위에 얹는다.
그 PLAN Q3 "매도1호가 − 1틱 추격 = 제외" 결정을 **추격분 절반에 한해 뒤집는다**.

목적: 같은 종목·같은 날 수량을 반씩 나눠 동시호가 청산과 추격 청산을 직접 비교한다.
수치는 저장소 공개라 옮기지 않는다.

---

## 0. 사용자 확정 사항 (2026-09-17)

| # | 질문 | 결정 |
|---|---|---|
| Q1 | 원장 기록 | 한 종목을 **두 줄**로 (동시호가분 / 추격분) |
| Q2 | 쪼개는 시점 | **청산 워커 기동 때** (T+1 08:50). 매수 배치·16:00 체결대조는 그대로 한 줄 |
| Q3 | 홀수·1주 | 남는 1주는 **동시호가분**. 1주 종목은 동시호가분만 (추격분 줄 없음) |
| Q4 | 추격 종료 | **09:01:00** 에 남은 지정가 취소 → 남은 수량 시장가. 추격은 09:00:30 / :40 / :50 **3회** |
| Q5 | 동시호가 미체결분 | 09:00:30 에 추격에 합류 (같은 규칙으로 추격 → 09:01 시장가) |
| Q6 | 매도 주문 기록 | **체결된 것만** 새 테이블에 1건 1줄. 체결 안 된 주문(정정 전·취소)은 안 남김. 진행 중 주문번호 1개만 기존 `sell_order_no` 에 덮어씀 |
| Q7 | 줄별 순손익 | 키움 ka10077 종목 합계 수수료·세금을 **매도금액 비율로 배분** |

---

## 1. 흐름 (T+1)

```
08:50     워커 기동 → 잔고대조 → 미분할 줄(leg='single')을 auction/chase 두 줄로 분할
08:55     auction 줄: 전량 시장가 매도 (KRX)                       ← 선행 PLAN 그대로
09:00:30  chase 1회: 매도1호가 − 1틱 지정가 신규 매도 (SOR)
          auction 줄 미체결 잔량 있으면: 동시호가 주문 취소 → 같은 규칙으로 지정가 신규
09:00:40  chase 2회: 잔량 있고 목표가가 현재 주문가와 다르면 정정. 같으면 건너뜀
09:00:50  chase 3회: 동일
09:01:00  두 줄의 걸린 지정가 취소 → 남은 수량 시장가 (SOR)
09:01:06  백스톱(09:01 스케줄 + 러너 5초 대기): sell_status NULL 줄만 시장가 — 워커 정상이면 무동작
~09:10    체결 기록·줄별 손익 확정. 잔존 시 디스코드 알림 (기존)
```

## 2. 스키마

### 2-1. `close_bet_orders` — `leg` 추가, 기본키 변경

| 컬럼 | 값 |
|---|---|
| `leg TEXT NOT NULL DEFAULT 'single'` | `single`(미분할·과거 행) / `auction` / `chase` |
| PK | `(date, ticker)` → **`(date, ticker, leg)`** |

- SQLite 는 PK 변경 불가 → **테이블 재생성** 마이그레이션 (새 테이블 생성 → 복사 → DROP → RENAME, 한 트랜잭션)
- 멱등 판정: `PRAGMA table_info` 에 `leg` 가 PK 로 있으면 무동작
- 위치: `etl/scripts/run_close_bet.py` `create_close_bet_orders_table` DDL 수정 + `ensure_exit_columns` 에서 재생성 호출
  (`ensure_exit_columns` 는 청산 워커·백스톱·매수 확인 경로가 공통으로 부름)

### 2-2. 신규 `close_bet_sell_fills` — 체결된 매도만

| 컬럼 | 설명 |
|---|---|
| `date, ticker, leg` | `close_bet_orders` 줄 참조 |
| `order_no TEXT` | 체결이 발생한 주문번호 (정정 전 번호에서 부분체결된 것도 그 번호로). **PK = (date, ticker, leg, order_no)** — 키움 주문번호가 일자별로 재사용될 수 있어 단독 PK 안 씀 |
| `kind TEXT` | `auction`(08:55 시장가) / `chase`(지정가 추격) / `market_0901`(09:01 시장가) / `backstop` |
| `round INTEGER` | chase 회차 1~3. 그 외 NULL |
| `price INTEGER, qty INTEGER` | kt00007 `cntr_uv`, `cntr_qty` |
| `recorded_at TEXT` | 기록 시각 |

- `cntr_qty > 0` 인 주문번호만 INSERT. 같은 번호 재기록은 `INSERT OR REPLACE` (부분체결 누적 반영)

### 2-3. 분할 규칙 (`split_positions`, `run_close_bet_exit.py` 신규 함수)

- 대상: `status='confirmed' AND sell_status IS NULL AND leg='single' AND date < today`
- 원장 기준 수량 `base = 기록 qty` (매수 수량). 잔고 매도가능(`qty_eff`)은 원장에 쓰지 않는다 —
  미체결 매도가 물량을 묶으면 보유분보다 작게 잡히는데, 그 값을 원장에 덮으면 차액이 매수 기록에서
  사라져 다음 실행이 존재를 모른 채 영영 청산 대상에서 빠진다
- `auction = ceil(base/2)`, `chase = floor(base/2)`
- 원래 줄: `leg='auction'`, `qty`·`cntr_qty` 각자 ceil 분할. 새 줄: 매수 컬럼 복사, `leg='chase'`,
  각자 floor 분할 (부분체결이면 `qty != cntr_qty` 라 같은 값으로 쓰면 안 된다)
- 이번 실행에 낼 주문 수량만 `qty_eff` 로 자른다 (auction 부터 채우고 남은 만큼 chase)
- `chase == 0` (1주): 원래 줄 `leg='auction'` 만, 새 줄 없음
- 한 트랜잭션. 재기동 시 `leg='single'` 이 없으므로 재분할 안 됨

## 3. 확정 결정 — 구현 위치

| # | 결정 | 구현 위치 |
|---|---|---|
| D1 | 워커 상태 키 `ticker` → `(ticker, leg)` — `watch`, `pending` | `run_close_bet_exit.py` `run_loop`, `build_watch_set`, `load_ordered_pending` |
| D2 | DB 갱신 WHERE 에 `leg` 추가 | 같은 파일 `mark_ordered`, `mark_filled`, `mark_missing_positions` |
| D3 | in-flight = 이 줄이 pending OR **그 종목에 추적 안 되는** 미체결 매도주문. 형제 줄의 추적 중 주문은 막지 않음(기존 중복매도 가드 유지). 체결확인도 주문번호 기준 | `is_in_flight`, `fetch_unfilled_tickers` → `fetch_unfilled_orders` `{정규화 주문번호: {ticker, oso_qty}}` ✅ |
| D4 | auction 줄 = 선행 PLAN 강제청산 경로 그대로 (08:55 KRX 시장가) | `run_loop` |
| D5 | chase 목표가 = `sel_bid − tick(sel_bid − 1)`, 하한가 미만이면 하한가 | 신규 `chase_price`. 호가단위는 `run_pullback_order._fallback_tick_size` 재사용 (공용 모듈로 이동 안 함, import) |
| D6 | chase 회차 시각 상수 `CHASE_ROUNDS = ("09:00:30","09:00:40","09:00:50")`, 종료 `CHASE_END = "09:01:00"`. config 추가 안 함 | `run_close_bet_exit.py` 상수 |
| D7 | 1회차: 지정가 신규(SOR). 2·3회차: 잔량>0 이고 목표가 ≠ 현재 주문가면 정정(kt10002), 같으면 건너뜀(시간우선 유지) | `run_chase` ✅ |
| D8 | 정정·취소 시 옛 번호를 pending `history`(메모리)에 보관 → `sell_order_no` 를 새 번호로 덮어씀. 체결분 기록은 매 폴링 `settle_pending` 에서 history+현재 번호 전체를 `record_fills` 로 멱등 기록 (구현 중 변경 — 정정 직전 1회 기록보다 체결 지연에 강함) | `_replace_order`, `record_fills` ✅ |
| D9 | 09:00:30 auction 줄 잔량 > 0 → 동시호가 주문 취소 → chase 규칙 합류 (fills `leg='auction'`, `kind='chase'`) | `run_chase` ✅ |
| D10 | 09:01:00 두 줄 걸린 지정가 취소 → 잔량 시장가 SOR (`kind='market_0901'`). 취소 후 재주문 실패는 `orphan` → 다음 폴링 시장가 재시도 | `run_chase` ✅ |
| D11a | ✅ 연결 완료(실모드만). 분할 호출은 **마지막 단계에서 연결** — 스케줄 작업이 작업트리 코드를 즉시 실행하므로 미완성 상태 실계좌 반영 방지 (`build_watch_set` 주석) | `run_close_bet_exit.py` `build_watch_set` |
| D11 | 줄 완료 = fills 수량 합 == 줄 qty → `sell_status='filled'`, `sell_price`=가중평균, `sell_qty`=합 | `settle_pending` ✅ |
| D12 | 순손익: 종목의 모든 줄이 filled 된 뒤 ka10077 1회 → 수수료+세금을 줄별 매도금액 비율로 배분. `sell_pl_won = 매도금액 − 매수금액 − 배분비용`, `pnl_pct = sell_pl_won / 매수금액 × 100`. 반올림 잔차는 auction 줄에 | `allocate_costs` (settle_pending 에서 호출) ✅ |
| D13 | ka10077 실패 시 기존처럼 gross 폴백, 비용 컬럼 NULL | 같은 곳 |
| D14 | `exit_reason` 은 두 줄 모두 `forced` (구분은 `leg`·fills `kind`) | — |
| D15 | 백스톱: 줄 단위 매도 ✅. **fills `kind='backstop'` 기록·체결확정은 미구현** — §9 F2 | `run_close_bet_force_exit.py`, `execute_sell` |
| D16 | broker 조회 API 응답에 `leg` 포함 ✅ | `broker/routers/close_bet.py` SELECT 2곳 |
| D17 | 취소·정정에 `exchange`(KRX/NXT/SOR, 기본 SOR) 추가 — 원주문 거래소를 넘김. 동시호가 주문 취소 = KRX, 추격 정정·취소 = SOR | `broker/kiwoom/orders.py` `modify_order`/`cancel_order`, `broker/routers/orders.py` DELETE 쿼리·PATCH body |

## 4. 내가 정한 기본값 — 확인 필요

1. **추격 거래소 SOR** — 선행 PLAN D4 "장중은 SOR" 규칙 따름. `/quotes`(ka10095) 매도1호가가 KRX 단독인지 통합인지는 모름
2. **분할 스위치 없음** — 롤백 = 코드 revert. 실매매라 `close_bet.json` 에 `split_exit: true/false` 를 두는 게 안전하면 추가
3. **broker-web 화면 (확정 2026-09-17)**: `app/close-bet/page.tsx` 행 key 에 `leg` 포함(현재 `${date}-${ticker}` 중복) + 줄마다 `동시호가`/`추격` 배지. 종목 합산 표시는 안 함
4. **일일 결과 리포트 건수**: 줄 수로 셈 (종목당 2건). 금액 합계는 `cntr_qty` 를 나눠 저장해 그대로 맞음
5. **호가 조회 실패 회차**: 그 회차 건너뜀 (주문 유지). 3회 전부 실패해도 09:01 시장가로 정리

## 5. 체크리스트

### A. 요구사항

- [x] A1 10주 → auction 5 / chase 5, 9주 → 5 / 4, 1주 → auction 1 만 — T1
- [x] A2 재기동 시 재분할 안 함 — T2
- [x] A3 auction 줄 08:55 KRX 시장가, chase 줄은 08:55 에 주문 안 냄 — T3
- [x] A4 chase 09:00:30 에 `sel_bid − 1틱` 지정가 신규 — T4
- [x] A5 호가단위 경계 (매도1호가 2,000 → 1,999 / 5,000 → 4,995) — T5
- [x] A6 2·3회차 목표가 같으면 정정 안 함, 다르면 정정 — T6
- [x] A7 09:00:30 auction 잔량 > 0 → 취소 후 추격 합류, 잔량 0 → 무동작 — T7
- [x] A8 09:01 남은 지정가 취소 → 잔량 시장가 — T8
- [x] A9 체결 안 된 주문번호는 fills 에 없음, 부분체결 번호는 체결 수량만 — T9
- [x] A10 같은 종목 auction 주문 미체결이 chase 주문을 막지 않음 (주문번호 기준 in-flight) — T10
- [x] A11 순손익 배분 합 == ka10077 종목 합계 — T11
- [x] A12 워커 없이 백스톱만 돌 때: 미분할 1줄 전량 / 분할 후 NULL 두 줄 각각 매도 — T12
- [x] A13 마이그레이션 멱등 + 기존 행 `leg='single'` 보존 — T13 (실 DB 복사본 리허설: 89행·수량·금액·손익 합 동일, integrity ok)

### B. 주의사항

- [x] B0 스펙 확인(2026-09-17): kt10002·kt10003 body `dmst_stex_tp` 필수(KRX/NXT/SOR). 원주문과 달라도 되는지는 **문서에 없음**
  → **D17** 로 해결: 원주문 거래소를 그대로 넘긴다
- [ ] B1 배포 순서 (장 마감 후): ① ✅ 마이그레이션 수동 1회(`ensure_exit_columns`, 2026-09-17 21:11, 백업 `etl/db/watchlist.sqlite3.bak-before-leg-20260917211139`) → ② ✅ broker 재기동(`restart_all_servers.ps1`, 21:1x, :8001·:8002 positions 200·leg 응답 확인) → ③ ✅ broker-web prod 배포(빌드 성공, :3100/close-bet 200) → ④ ✅ `exit_time=08:55:00`(2026-09-17 저녁) → ⑤ ✅ 백스톱 재등록(러너 5초 대기, 다음 실행 09/18 09:01:01+5s) → ⑥ ✅ `build_watch_set` 분할 호출 연결(D11a, 2026-09-17 저녁 — 9/18 08:50 워커부터 적용)
  - ①이 ②보다 먼저: 새 broker `/close-bet/positions` 는 `leg` 컬럼을 SELECT → 미마이그레이션 DB 면 500
- [x] B2 kt10002 응답 `ord_no` = "새 주문번호" (스펙 명시). D8 대로 옛 번호 체결분 먼저 기록
- [x] B3 **틀린 가정이었음** (러너 ps1 Start-Sleep 5 로 해결, 스케줄러 초 단위 무시 실측) — chase 줄이 09:00:30~:50 동안 호가 없음으로 주문 못 내면 `sell_status` NULL 인 채 09:01:00 을 맞아 워커·백스톱이 같은 초에 매도 → §9 F1
- [x] B4 `mark_missing_positions` 는 분할 전에 돈다 — `build_watch_set` 순서 확인

## 6. 테스트

| ID | 검증 | 위치 |
|---|---|---|
| T1 | `split_positions` 수량 규칙 | `etl/tests/test_close_bet_exit.py` |
| T2 | leg 가 이미 auction/chase 면 무동작 | 같은 파일 |
| T3 | 08:55 auction 만 KRX 매도 | 같은 파일 |
| T4 | 09:00:30 chase 지정가 가격·수량 | 같은 파일 |
| T5 | `chase_price` 호가단위 경계·하한가 클램프 | 같은 파일 |
| T6 | 정정 호출 여부 (같은 가격 skip) | 같은 파일 |
| T7 | auction 잔량 취소·합류 | 같은 파일 |
| T8 | 09:01 취소 → 시장가 | 같은 파일 |
| T9 | `record_fills` 체결분만 INSERT | 같은 파일 |
| T10 | 주문번호 기준 in-flight | 같은 파일 |
| T11 | 비용 배분 합계·반올림 잔차 | 같은 파일 |
| T12 | 백스톱 줄 단위 매도 | `etl/tests/test_close_bet_force_exit.py` `test_split_legs_each_residual` |
| T13 | PK 재생성 마이그레이션 멱등 | `etl/tests/test_close_bet_exit_migration.py` |
| T14 | broker 응답에 `leg` | `broker/test_close_bet_router.py` |
| T15 | 취소·정정 `exchange` 전달(기본 SOR, KRX 지정) — wrapper body + 라우트 위임 ✅ | `broker/test_orders.py` `TestModifyWrapper`/`TestCancelWrapper`/`TestModifyRoute` |

## 7. 손 안 대는 것 (커버 재확인 완료)

| 코드 | 안 건드려도 되는 근거 (grep 확인) |
|---|---|
| `run_close_bet.py` 후보 중복 가드 `NOT EXISTS (date,ticker)` | 분할은 T+1 — 매수일 D 행은 한 줄. 두 줄이어도 EXISTS 판정 동일 |
| `run_close_bet.py` `upsert_order_result` COUNT (date,ticker) | 매수 시점엔 한 줄. 분할 후 같은 날짜 재매수 없음 |
| `run_verify.py` | `status IN (submitted, unconfirmed)` 만 대조 — 분할 대상은 `confirmed` |
| `run_pullback_order.load_open_close_bet_tickers` | `DISTINCT ticker` |
| `orderbook_symbols.py` | `DISTINCT ticker` |
| `report_daily_trading_result.py` | 줄 단위 나열·합산. `cntr_qty` 분할 저장으로 금액 합 보존 (건수만 줄 수 — §4-4) |
| `analyze_listing_market_cap_impact.py` | 줄 단위 오프라인 분석, 운영 경로 아님 |
| 매수 배치·체결대조 스케줄 XML | 변경 없음 |

## 8. 미검증

- **재기동 오분류**: `load_ordered_pending` 은 kind·거래소를 leg 로 추정. 09:00:30~09:01 사이 재기동 시 auction 잔량 주문(실제 SOR 지정가)을 KRX 로 취소 시도할 수 있음 → 취소 실패 시 잔존 알림
- **재기동 시 정정 전 주문번호 유실**: history 는 메모리. 재기동 전 부분체결된 옛 번호는 fills 에 안 남아 분할 줄이 수량 미달로 확정 안 될 수 있음 → stop-time 잔존 알림
- **취소 후 재주문 실패**: `orphan` 으로 표시하고 다음 폴링에 시장가 재시도 (테스트 있음)

- 동시호가 시장가가 09:00 에 부분체결로 남는 실제 빈도 (하한가 잠김·VI 외엔 드묾 — 선행 PLAN Q3)
- ka10095 `sel_bid` 가 09:00:30 시점에 갱신돼 있는지 (TTL 캐시)

## 9. 최종 대조 결과 (2026-09-17)

- 요구사항 A1~A13 ↔ 테스트 T1~T15 매핑 확인. 전체 etl 1084 / broker 186 통과, broker-web tsc 0
- **PLAN 기준 완료 ≠ 요구사항 기준 완료** — 분할 호출 미연결(D11a)이라 실운영에선 아직 반반 분할 안 됨

| # | 발견 | 영향 | 제안 |
|---|---|---|---|
| F1 ✅ 해결(러너 ps1 5초 대기 — 스케줄러는 분 단위라 XML 09:01:30 이 09:01:01 에 실행됨) | B3 경합: chase 줄 미주문 상태로 09:01:00 도달 시 워커 시장가와 백스톱(09:01:00)이 동시 매도 | 2차 주문은 `execute_sell` 잔고 재조회로 대부분 `no_qty` 거부되나 같은 초 경합은 보장 못 함. 워커는 이후 매 폴링 `no_qty` 실패 메시지 반복 | 백스톱 XML 시각을 09:01:30 으로 (선행 PLAN 원래 값) |
| F2 | 백스톱이 분할 줄을 팔면 `ordered` 만 남고 fills 기록·체결확정 경로 없음 | 기존 single 도 같은 구멍(다음날 워커는 `fetch_sell_fills(today)` 라 전날 체결 못 찾음). 워커 크래시 날에만 발생 | 이번 범위 밖. 필요 시 별도 PLAN |
