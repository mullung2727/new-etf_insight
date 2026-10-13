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
- [ ] 구현·테스트 머지 (PR #) — 해당 없음 (파일 복사만)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜) — 해당 없음
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P1 공용 시작 절차 `run_live_batch` + 결과 불명 규칙
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음 (전략 전환 단계에서 적용)
- [ ] 첫 실가동 확인 (날짜) — 해당 없음 (전략 전환 단계에서 확인)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P2 주문 실행 엔진 `chase_engine`
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜) — 해당 없음 (P3 에서 확인)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P3 무상증자(rights) 이전 — 엔진 기준 코드의 원래 주인
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음 (import 만 바뀜)
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P4 되돌림(rebound) 이전
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

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
- P5 jevq 의 두 엔진 차이(체결 확정을 마감 뒤 재조회 `FINALIZE_RETRIES` vs 엔진 안 체결 수량, DB 문자열 상태 vs state dict)는 착수 전 사용자와 결정.
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

## 7. 운영 체크
- 스케줄 작업·config 변경 없음(import·진입 함수만 바뀜). 러너 종료 코드 규약(2 = 알림 완료)은 P1 반영 시 러너와 대조.
- 공개 저장소: 공용 모듈 주석·테스트에 private 설계 번호·수익 수치·전략 시각 값 금지(테스트 시각은 임의 값).
- 같은 계좌 타 전략: 예약금·가용현금 규칙은 전략 쪽 그대로, 1회 주문 한도 분할은 전략 sizer.

## 구현 차이
없음
