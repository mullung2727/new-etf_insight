# PLAN — 실매매 공용 매매 엔진 (모든 전략: broker 호출 → 주문 실행 엔진 → 전략)

한 줄: 실매매 전략 7개가 각자 짠 broker 호출·추격 지정가·마감 시장가·시작 절차를 git 관리 공용 3층
(`BrokerClient` → `chase_engine` → 전략)으로 모은다. 전략 차이는 공용 함수의 파라미터·콜백으로 넘기고,
종목 선정·금액·시각 값·원장 기록만 `research/private` 에 남긴다.
(2026-10-13 `PLAN_LIVE_BATCH_COMMON` 을 이 문서로 합치고 삭제)

> ## ⚠️ 작업·반영 조건 — 반드시 지킬 것
> - 스케줄 작업은 **메인 작업 폴더의 코드를 그대로 실행**한다. 메인 폴더 파일을 고치는 순간 실매매 코드가 바뀐다(브랜치 체크아웃도 마찬가지).
> - **작업**은 언제든: git 코드는 worktree `.claude/worktrees/shared-engine`(브랜치 `feat/shared-trading-engine`),
>   비공개 코드 수정본은 `research/private/_staging/<전략>/` 에 만든다. 메인 폴더 원본은 건드리지 않는다.
> - **반영**(머지·스테이징 복사)은 거래일 **20:00 이후 ~ 다음 거래일 08:00 전** 또는 휴장일에만.
>   거래일 08:00~20:00 엔 08:50 주문·09:00 매도·14:58~15:31 오후 배치·18:00 감지·19:00 배치가 돈다.
> - 반영 직후 그 전략의 기존 테스트 + 공용 테스트를 메인 폴더에서 전부 다시 돌린다. 하나라도 실패하면 P0 백업으로 되돌린다.
> - 한 번 반영에 전략 하나. 다음 거래일 실로그 확인 후 다음 전략.

## 진행

### P0 백업
- [x] 구현·테스트 머지 (PR #) — 해당 없음 (파일 복사만, `research/private/_backup_20261013`·`_backup_20261013_apply`)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음
- [x] 첫 실가동 확인 (날짜) — 해당 없음
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

### P1 공용 시작 절차 `run_live_batch` + 결과 불명 규칙
- [x] 구현·테스트 머지 (PR #83, 2026-10-13)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음 (전략 전환 단계에서 적용)
- [x] 첫 실가동 확인 (날짜) — 해당 없음 (전략 전환 단계에서 확인)
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

### P2 주문 실행 엔진 `chase_engine`
- [x] 구현·테스트 머지 (PR #83, 2026-10-13)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음
- [x] 첫 실가동 확인 (날짜) — 해당 없음 (P3 에서 확인)
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

### P3 무상증자(rights) 이전 — 엔진 기준 코드의 원래 주인
- [x] 구현·테스트 머지 (PR #) — 해당 없음 (비공개 코드, 2026-10-13 20시 메인 폴더 반영, 테스트 389 통과)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음 (import 만 바뀜)
- [ ] 첫 실가동 확인 (날짜)
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

### P4 되돌림(rebound) 이전 — 1차: 마감 시장가만 (P4b 남음)
- [x] 구현·테스트 머지 (PR #) — 해당 없음 (비공개 코드, 2026-10-13 20시 메인 폴더 반영, 테스트 46 통과)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜)
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

### P5 Jev 이전
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P6 high52 · 엔벌로프 이전
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P7 종가베팅·눌림목(`etl/scripts`) 이전
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 왜

| 사고·문제 | 근거 |
|---|---|
| 2026-10-05 대체공휴일에 jevq 청산이 돌아 broker 422 → 결과 불명(`unknown_ah`)을 "매도 완료"로 쳐 다음 거래일 청산 누락(3종목, 10-06 수동 복구). 같은 날 high52 확인이 휴장일에 보유일 +1 | 휴장일 가드·결과 불명 처리를 배치마다 따로 짬 (rights 는 있고 jevq 청산·rebound 는 없었음). 응급 조치: 러너 공통 사전검사 `ops/scheduled-tasks/lib_trading_day_guard.ps1` |
| 2026-10-13 rebound 마감 시장가가 계획보다 47초 늦은 15:20:07 접수 → 종가 동시호가로 넘어감 | `intraday_rebound/live/exit.py` `run_once`: 한 바퀴(broker 조회 5회 ≈ 12초 + 대기 5초)에 "목표가 취소"만 하고 시장가는 **다음 바퀴**. rights `chase._market_step` 은 취소 → 즉시 미체결 재조회 → 같은 바퀴 시장가 |
| broker 호출 코드가 전략마다 복사본 | 공용 `etl/scripts/broker_client.py` `BrokerClient`(PR #48) 가 있으나 **사용 전략 0개**. jevq `buy_ah.BrokerApi`·rebound `exit.BrokerIO`·envelope·high52 가 각자 호출 |
| 전략끼리 엉킴 | rights `live/expire.py:44~46` 이 Jev 폴더의 `buy_ah.BrokerApi`·`BID_MAX_AGE_S`·`SOURCES` 를 import |
| 이력 없음 | `research/private` 는 gitignore → 매매 로직 변경(예: 10-07 호가 간격 3틱 규칙)을 커밋·되돌리기 불가 |

## 2. 확인된 사실 (2026-10-13 코드 기준)

| 전략 | 진입점(주문 관련) | broker 호출 | 주문 방식(전략 값은 private 문서) |
|---|---|---|---|
| rights(무상·유상) | `rights_issue/live/expire.py`·`chase.py`·`order.py` | `etl/scripts/run_close_bet_exit` 함수 + Jev `BrokerApi` | 추격 지정가(10초 정정) → 시각 시장가, 매수·매도 겸용, state 별 시작·시장가 시각 |
| jevq | `jev_quant_live/buy_ah.py`·`add_ah.py`·`exit.py`·`order.py` | 자체 `BrokerApi` | 장후 추격 매수·추가 매수, 다음 날 매도 |
| rebound | `intraday_rebound/live/order.py`·`exit.py` | 자체 `BrokerIO` | 지정가 매수, 목표가 지정가 유지, 마감 시장가(SOR → KRX 재시도) |
| high52 | `high52_quiet_breakout/live/order.py`·`exit.py` | `trading_batch_common` 등 | 마감 전 매수, 장중 목표·손절 감시 매도 |
| envelope | `envelope/live/order.py`·`expire.py` | `trading_batch_common` | 지정가 매수 + 목표가, 마감 매도 |
| 종가베팅 | `etl/scripts/run_close_bet.py`·`run_close_bet_exit.py` | 자체 함수(`place_sell_via_broker` 등) | 마감 매수, 다음 날 추격 매도·강제 청산 |
| 눌림목 | `etl/scripts/run_pullback_order.py`·`run_pullback_exit.py` | 자체 함수 | 마감 지정가 매수, 목표·손절·강제 매도 |

- 기존 테스트: rights `live/tests`(chase·expire 등), jevq `tests`(buy_ah·add_ah·live_a~c), rebound·high52·envelope `live/tests`, `etl/tests/test_broker_client.py` 외 etl 테스트.
- 결과 불명 처리: rights 는 다음 실행에서 미체결로 재판정(`expire.recheck_unknown`), jevq 는 10-06 전까지 완료로 침, rebound·envelope·high52 는 확인 필요(P4·P6 착수 시 grep).
- `quote_fresh(base_tm, now, max_age_s)` 는 `HHMMSS` 만 비교 → 전일 같은 시각 시세가 통과. 날짜 검사를 대신 못 함.

## 3. 구조 — 3층

```text
전략 (research/private, 비공개)     종목 선정 · 금액 · 시각 값 · 가격 규칙 선택 · 원장 기록(콜백)
   │  OrderSpec(파라미터) + on_event(콜백)
주문 실행 엔진 etl/scripts/chase_engine.py   추격 지정가 · 정정 · 목표가 유지 · 마감 시장가 · 결과 불명 처리
   │
broker 호출   etl/scripts/broker_client.py BrokerClient   호가 · 지정가 · 시장가 · 정정 · 취소 · 미체결 · 체결내역
   +
시작 절차    etl/scripts/trading_batch_common.run_live_batch   거래일 · 계좌 · 예외 알림 · 종료 코드
```

### 3-1. `BrokerClient` (있음, 필요 시 확장)
- 모든 전략의 broker 호출을 이걸로. 전략 차이는 `sources`(주문 source 문자열 — broker 로그·체결 매칭 키라 **기존 값 그대로**)와 `log_tag` 뿐.
- 없는 호출(예: 잔고·주문가능, 거래소 지정 SOR/KRX)은 P2 착수 시 기존 전략 호출과 대조해 추가.

### 3-2. `chase_engine` — 공용 동작과 파라미터

| 공용 동작 | 파라미터(전략이 넘김) | 기준 코드 |
|---|---|---|
| 추격 지정가: 첫 주문 → N초마다 호가 따라 정정 | 시작 시각, 정정 간격, 가격 규칙(매수1호가+k틱 등 — 함수 인자), 지정가 거절 한도 | rights `chase._try_first_order`·`_chase_step` |
| 목표가 지정가 유지: 보유 수량에 목표가 매도가 떠 있게 | 목표가 계산 콜백, 수량 | rebound `exit.run_once` 4·5 단계 |
| 마감 시장가: **한 바퀴 안에서** 잔량 조회 → 취소 → 즉시 미체결 재조회로 취소 확인 → 시장가 | 시장가 시각, 시장가 금지 시각, 거래소(SOR/KRX), 실패 시 재시도 거래소 | rights `chase._market_step` |
| 수량 지연 확정 | `sizer` 콜백 | rights `chase` sizer |
| 원장 기록 | `on_event(state, kind, **fields)` — kind: 접수·불명·정정·시장가 전환·체결·취소 | rights `ledger.record_order`·`update_position` 호출 지점 |

- 엔진은 시각 **값**을 갖지 않는다(공개 저장소). 전략이 넘긴 값으로만 돈다.
- 불변식(공용 테스트로 고정): 조회 실패·결과 불명이면 주문 안 함·재주문 금지(fail closed) / 취소 미확인이면 시장가 안 냄(중복 매도 방지) /
  가격 같으면 정정 안 함 / 시장가 금지 시각 이후 시장가 금지 / 넘겨받은 state 만 주문(같은 계좌 타 전략 보유분 매도 금지) / 매도 수량 ≤ 실잔고.

### 3-3. 시작 절차 `run_live_batch(tag, body, *, broker_url, profile, notify_channel="batch", trading_day=True) -> int`
1. `trading_day` 이고 `is_krx_trading_day(오늘)` 거짓 → `"{tag} 거래일 아님 — 종료"` 출력, **0**(알림 없음)
2. `broker_url` 있으면 `require_profile` 거짓 → 알림, **1**
3. `body()` → 반환 `{"status", "lines"}` 의 lines 알림, **0**
4. 예외 → 트레이스백 + 알림 시도. 성공 **2** / 실패 **1** (러너는 2면 중복 알림 생략 — 스윙 배치 규약)
- 데이터 배치(jevq outcomes·report, rights detect)는 `broker_url=None`. 러너 사전검사(`lib_trading_day_guard.ps1`)는 이중 방어로 유지.

### 3-4. 결과 불명 공통 규칙
- `unknown` 은 원장에 그대로 남기고 완료로 치지 않는다. 다음 실행 시작에서 재판정: 미체결에 우리 주문번호가 있으면 유지(+수동 확인 알림), 없으면 체결내역 반영 후 재시도 대상.
- 재시도 수량 = `min(원장 수량, 실잔고)`.

### 3-5. 시세 날짜 (high52·envelope verify, P6)
- 보유 갱신(high52 verify `update_holdings`, envelope verify)은 시세 날짜가 오늘인지 먼저 확인. 없거나 이상하거나 오늘 아니면 그 종목 갱신 건너뜀 + 알림 1줄.
- P6 착수 전 broker `/quotes` 응답에 날짜 필드가 있는지 실측. 없으면 일봉(`ka10081`) 최신 날짜로 대체.

## 4. 단계 내용

### P0 백업
- `research/private/{rights_issue/live, jev_quant_live, intraday_rebound/live, high52_quiet_breakout/live, envelope/live}` 를
  `research/private/_backup_<YYYYMMDD>/` 로 통째 복사(비공개 코드의 유일한 되돌리기 수단). 단계마다 반영 직전 다시.

### P1 `run_live_batch` + 결과 불명 헬퍼 (git, worktree)
- `etl/scripts/trading_batch_common.py` 에 추가. 전략 전환은 각 전략 단계에서.

### P2 `chase_engine` (git, worktree)
- rights `chase.py` 의 엔진 부분(`run_chase`·`_try_first_order`·`_chase_step`·`_market_step`·`_bounds`·`_entry`·`_fills`·`Clock`·`_at`)을 일반화해 이동. broker 호출은 `BrokerClient`.
- 목표가 유지 동작(rebound)은 이 단계에서 같이 넣는다(P4 가 씀).
- 공용 테스트 `etl/tests/test_chase_engine.py`: rights `test_chase.py` 의 엔진 케이스를 복사·일반화 + 3-2 불변식 + "취소 → 같은 바퀴 시장가" 케이스.
- 첫 커밋은 기준 코드 그대로 옮긴 것, 일반화는 다음 커밋(리뷰에서 동작 변경 여부가 보이게).

### P3~P7 전략 이전 (private 스테이징 + 20:00 이후 반영)
- 각 전략: broker 호출 → `BrokerClient`, 추격·마감 → `chase_engine`, `main()` → `run_live_batch`, 원장 기록 → `on_event` 콜백.
- **동작 변경 없음**이 원칙. 예외는 이 문서에 적은 것만:
  - P4 rebound: 마감 시장가를 한 바퀴 처리로(10-13 지연 수정) + 시장가 금지 시각 추가.
  - P5 jevq: 매도 모드 `auction` 추가(아래). 기존 `chase` 모드는 동작 그대로.
- P5 jevq (2026-10-14 확정):
  - 진입 함수 `buy_ah.run_chase(con, cfg, today, api, clock, side)` 시그니처 유지, 안의 루프(`_step`·`_try_first_order`·`_market_step`)를 `chase_engine.run_chase` 로 교체.
    Jev `BrokerApi` 는 엔진 프로토콜 어댑터로 감싸 그대로 씀(호가 신선도 `BID_MAX_AGE_S` 포함). BrokerClient 교체·rights 의 Jev 의존 제거는 P5b.
  - 원장 상태·로그: 엔진 `on_event` 콜백에서 기존 `_save` 로 같은 상태값(`pending_ah/working_ah/failed_ah/unknown_ah`)·같은 로그 문구로 기록.
  - 재시작 이어받기(이미 낸 번호가 미체결이면 그 주문을 이어감): 엔진 `on_step` 훅에서.
  - 체결 확정: 엔진 뒤에 기존 `_finalize`(30초 간격 3회 재조회) 그대로 — 엔진 안 체결 수량은 쓰지 않음.
  - 추가 매수 `add_ah.py`(16:00 고정가 지정가, 추격 아님)는 이번 범위 밖.
- P5 매도 모드 `auction` (동작 변경, 사용자 2026-10-14). `config.json` `sell_mode` = `"chase"` | `"auction"` 로 갈아끼움:
  1. `exit.run_exit` 직후(개장 동시호가 시간), 매도 행마다 하한 지정가 1회 주문: 가격 = 기준가(전일 종가) × (1 − `floor_pct`) 를 호가단위로 **올림**. 값은 비공개 config `sell_auction`.
     시가 ≥ 하한이면 개장 동시호가에서 시가 체결(백테스트 가정과 같음), 시가 < 하한이면 미체결로 남음.
  2. 미체결 잔량: `sell_auction` 의 `start` 부터 기존 추격 지정가(정정) → `market_at` 시장가 → `end` 이후 시장가 금지 → `final_at` 체결 확정.
  3. 기준가 조회 실패 → 그 종목 하한 주문 안 함(행은 `pending_ah` 그대로) → 추격 시작 시각에 첫 주문.
  - 값·근거: `research/private/jev_quant_live/PLAN_AUCTION_SELL.md` (비공개).
  - 공용 추가: `chase_engine.place_first_orders`(시각 고정 첫 주문 1회 — 동시호가 주문용, 엔진 첫 주문과 같은 fail closed 규칙),
    `BrokerClient.base_price(t)`(`GET /quotes/{t}` 의 `base_pric`, 실패·0 이면 None). 하한 가격 계산(호가단위 올림)은 전략 쪽 `price_fn`.
  - jevq-report 는 그 시각 체결내역으로 확정하므로 시가 체결분은 그대로 보고, 추격 구간 체결분은 다음 보고에 반영.
- P7 은 git 코드라 worktree 브랜치로만 작업, 머지 = 반영.

## 5. 요구사항 → 테스트

| 요구사항 | 테스트 |
|---|---|
| 기존 동작 그대로 (전략별) | 단계마다 그 전략 기존 테스트 전부 통과 — 기대값 수정 금지(수정이 필요하면 동작이 바뀐 것, 4절 예외에 없으면 중단) |
| 마감 시장가 한 바퀴 처리 | 엔진: 시장가 시각 첫 바퀴에서 취소 → 재조회 → 시장가 주문 1건 (가짜 클라이언트 호출 순서 검증) |
| 취소 미확인이면 시장가 안 냄 | 재조회에 주문 잔류 → 시장가 0건 |
| 시장가 금지 시각 이후 금지 | 브로커 지연으로 금지 시각 넘김 → 시장가 0건 |
| 조회 실패·결과 불명이면 주문 안 함 | 미체결/체결 조회 None → 주문 0건, 불명 결과 → 재주문 0건 |
| 타 전략 보유분 매도 금지 | 잔고에 state 밖 종목 → 그 종목 주문 0건 |
| 매도 수량 ≤ 실잔고 | 잔고 < 원장 수량 → 잔고만큼만 |
| 휴장일이면 주문·알림·원장 0, 종료 0 | `run_live_batch` 휴장일 → body·notify 미호출, 0 |
| 계좌 불일치면 주문 0, 종료 1 | profile 거짓 → body 미호출, 1 |
| 예외 시 알림 성공 2 / 실패 1 | notify True → 2, False·예외 → 1 |
| 결과 불명은 완료 아님 | unknown 행 → 다음 실행 대상, 미체결 잔류면 제외+알림 |
| 오래된 시세로 보유 갱신 안 함 | 시세 날짜 전일 → update 0 + 알림, 날짜 없음 → update 0 |
| 공용 코드에 전략 정보 없음 | PR 전 `grep -nE "1[45]:[0-9]{2}\|budget\|acptno\|jev\|rebound" etl/scripts/chase_engine.py` 결과 0 |
| 작업·반영 시간 | PR·작업 기록에 반영 시각 기재 |
| P5 jevq `chase` 모드 동작 그대로 | jevq 기존 테스트 204개 기대값 수정 없이 통과 |
| P5 `auction`: 개장 전 하한 지정가 1회 | 기준가 × (1 − 하한 비율) 을 호가단위 올림한 지정가 1건 (경계값 단위 테스트) |
| P5 `auction`: 기준가 조회 실패 | base_price None → 하한 주문 0건, 행 `pending_ah` 유지, 추격 시작 시각 첫 주문 1건 |
| P5 `auction`: 시가 전량 체결 | 추격 구간 정정·시장가 0건, `_finalize` 가 체결가 기록 |
| P5 `auction`: 미체결·부분 체결 | 추격 정정은 미체결 잔량만, 시장가 = 남은 잔량 |
| `place_first_orders` fail closed | 미체결/체결 조회 None → 주문 0, 이미 전량 체결 → done·주문 0, 불명 → unknown·재주문 0 |
| `BrokerClient.base_price` | 정상 응답 → 부호 뗀 int, HTTP 실패·값 없음·0 → None |

## 6. 결정

| # | 결정 | 누가 |
|---|---|---|
| D1 | 매매 코드는 git 공용, 전략(선정·시각·금액·원장)은 private | 사용자 |
| D2 | 범위: 실매매 **전 전략**, 공용화 전략 → 세부 전략은 파라미터·커스터마이징 | 사용자 (2026-10-13) |
| D3 | `PLAN_LIVE_BATCH_COMMON` 을 이 문서로 합치고 삭제 | 사용자 (2026-10-13) |
| D4 | 작업은 브랜치·스테이징에서 언제든, 반영은 20:00 이후·휴장일 | 사용자 (2026-10-13) |
| D5 | 3층 구조, 엔진 위치 `etl/scripts/chase_engine.py`, 테스트 `etl/tests/test_chase_engine.py` | Claude 제안 · 사용자 승인 |
| D6 | 엔진 기준 코드 = rights `chase.py` (매수·매도 겸용, state 별 시각, sizer, 한 바퀴 시장가) | Claude 판단 |
| D7 | 순서 P3 rights → P4 rebound → P5 jevq → P6 high52·envelope → P7 종가베팅·눌림목. rebound 는 10-13 동시호가 체결로 급하지 않음 | Claude 제안 — 검토 필요 |
| D8 | 동작 변경 없음, 예외는 4절 명시분만 | Claude 판단 |
| D9 | `run_live_batch` 함수 1개, 종료 코드 0/1/2 | Claude 판단 (구 LIVE_BATCH_COMMON) |
| D10 | 시세 날짜 검증 포함 | Claude 판단 (10-05 high52 원인) |
| D11 | 한 전략만 쓰는 매매 동작도 공용 함수로 두고 전략은 갈아끼우기만 | 사용자 (2026-10-14) |
| D12 | P5 Jev 는 매수·매도 추격 함께 이전(같은 루프라 쪼개지 않음), 추가 매수는 범위 밖 | 사용자 (2026-10-14) |
| D13 | Jev 매도 `auction` 모드 채택 (하한·시각 값과 근거는 비공개 문서) | 사용자 (2026-10-14) |
| D14 | P5 체결 확정은 Jev `_finalize` 유지, 원장 기록은 `on_event`, 이어받기는 `on_step` | Claude 판단 (동작 변경 없음) |

## 7. 운영 체크
- 스케줄 작업·config 변경 없음(import·진입 함수만 바뀜). 러너 종료 코드 규약(2 = 알림 완료)은 P1 반영 시 러너와 대조.
- 공개 저장소: 공용 모듈 주석·테스트에 private 설계 번호·수익 수치·전략 시각 값 금지(테스트 시각은 임의 값).
- 같은 계좌 타 전략: 예약금·가용현금 규칙은 전략 쪽 그대로, 1회 주문 한도 분할은 전략 sizer.
- P5 auction 모드: `\OpenClaw\jevq-exit`(08:55 시작) 실행 제한이 10분(PT10M)이라 09:05 에 강제 종료됨 → 반영 시 `openclaw-cron.registry.json` 먼저 PT45M 으로 고치고 작업 재등록.

## 구현 차이
- P1 원래 `run_live_batch(..., notify_channel="batch")` → 실제 `notify_fn: Callable[[str], bool]`(기본 = batch 채널 notify) (전략마다 쓰던 알림 함수를 그대로 넘겨 전환 시 알림 채널이 안 바뀌게)
- P1 원래 결과 불명 "공통 규칙"만 → 실제 함수 `classify_unknown(order_no, 미체결, 체결내역) -> (lookup_failed|no_order_no|open|filled|not_found, 체결수량)` + `retry_qty` (전략 전환 단계에서 호출)
- P2 원래 "목표가 유지 동작"을 별도 기능으로 → 실제 state 의 `price_fn`(고정 가격, 같으면 정정 안 함) + `on_step(states, api, now)` 훅(스텝마다 state 추가·수량 조정)으로 일반화. rebound 가 어떻게 쓸지는 P4 에서 확정
- P2 broker 호출을 매도/매수 메서드 쌍 → side 인자 하나의 프로토콜(`price/place_limit/place_market/modify/cancel/unfilled/fills`) + `ClientEngineApi`(BrokerClient 어댑터). 무상증자의 "실패 → 불명" 변환은 엔진이 아니라 어댑터 책임(P3)
- P2 `dry_run` 결과도 접수로 취급 (BrokerClient 모의 모드)
- P3 원래 "broker 호출 → BrokerClient + 추격 → chase_engine" → 실제 추격 엔진만 교체(`chase.py` = 원장·문구·시각 상수 + `_EngineApi` 어댑터 래퍼). rights 의 broker 호출(`expire.BrokerApi`, Jev `BrokerApi` 의존)은 그대로 — `place_sell_via_broker` 의 "실패 = 불명" 의미·거래소 지정 등 HTTP 동작을 바꾸지 않으려고. BrokerClient 교체는 Jev 이전(P5) 때 Jev 의존 제거와 함께
- P3 state 처리 순서: 원래 매도 전부 → 매수 전부, 엔진은 states 목록 순서. `expire.py` 가 매도 → 매수 순으로 states 를 만들어 실동작 동일(382개 테스트 통과)
- 스테이징 위치: 원래 `research/private/_staging/<전략>/` → 실제 이 worktree 의 `research/private/`(gitignore) 사본. 테스트가 `research.private.*` 경로로 import 하므로 같은 경로 구조가 필요. worktree 에 `etl/db` 빈 폴더 필요(테스트가 sqlite 파일 생성)
- P4 원래 "broker 호출 → BrokerClient, 추격·마감 → chase_engine, main → run_live_batch" → 실제 1차는 **마감 시장가만** 엔진 `_market_step` 으로(`exit.run_once` closing 분기 + `_CloseApi` 어댑터, SOR 시장가 금지 시각 15:20:00 추가). rebound 청산은 장중 목표가 유지·2차 체결 시 재주문·15:15 매수 취소·KRX 재시도·carried 를 가진 상태 기계라 `run_chase` 모양이 아님. `BrokerIO`→BrokerClient·`run_live_batch` 전환은 P4b 로 미룸(이번 반영 범위 최소화)
- P4 SOR 시장가 실패(빈 주문번호) 처리: 원래 즉시 `close_ordered`(→ 15:20:10 KRX 재시도만) → 실제 원장 그대로 두고 다음 바퀴에 SOR 재시도, 15:20:00 넘으면 `close_ordered` 로 KRX 재시도에 넘김
- P4 마감 시작 시각: 원래 15:19:20 → 실제 15:18:00 (사용자 결정). 백테스트(77건, 300만) 손익 종가 가정 +124.8만 · 15:18 +117.8만 · 15:19 +120.0만 — 차이 작고, 15:18 은 실패해도 15:20:10 KRX 재시도가 남음
- 반영 단위: 원래 "한 번 반영에 전략 하나" → 실제 P3·P4 를 2026-10-13 같은 밤에 반영 (10-13 되돌림 마감 시장가가 동시호가로 넘어간 문제를 다음 거래일 전에 고치려고). 두 전략 첫 실가동을 10-14 함께 확인
- P5 엔진 이벤트: 원래 기존 kind·필드 그대로 → 실제 `first_rejected(status, message, rejects)` 추가, `first_unknown`·`market_unknown` 에 message, `modified` 에 old_price, `market_rejected` 에 status (Jev 로그 문구를 그대로 재현하려고). 엔진 테스트 4개는 이벤트 결과 비교라 새 필드만 기대값에 추가
- P5 부분 체결 뒤 첫 주문 수량: 원래(Jev) 원장 수량 전량 → 실제 엔진 규칙대로 잔량(qty − 체결)만 (초과 매도 방지 쪽, 기존 테스트 밖 모서리)
- P5 재시작 이어받기: on_step 훅에서 이어받은 스텝은 정정하지 않도록 어댑터가 그 스텝 가격을 미체결 주문가로 돌려줌 (기존 "이어받은 스텝은 저장만" 순서 유지)
- P5 대상 0건일 때: 엔진은 state 가 없으면 시장가 시각까지 안 기다림 → Jev `run_chase` 가 엔진 뒤에 market_at 대기를 직접 유지 (기존 시각 테스트 그대로)
