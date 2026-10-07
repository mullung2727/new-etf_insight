# PLAN — 추격 매매 엔진 공용화 (매매 코드는 git, 전략만 private)

한 줄: Jev·유무상증자가 따로 가진 추격 매매 코드(호가 지정가 → 10초 정정 → 마감 시장가)를 git 관리 공용 모듈 하나로 합치고, 종목·시각·금액·원장 기록 같은 전략 부분만 `research/private` 에 남긴다.

> ## ⚠️ 작업 시작 조건 — 반드시 지킬 것
> - **2026-10-09(한글날 휴장) 이후, 장 운영 중이 아닐 때만 작업한다.** 그 전 거래일(10-08) 엔 손대지 않는다.
> - 스케줄 작업은 **이 작업 폴더의 코드를 그대로 실행**한다. 파일을 고치는 순간 실매매 코드가 바뀐다 (브랜치 체크아웃도 마찬가지).
> - 작업 가능 시간: **휴장일 전체**, 또는 거래일 **20:00 이후 ~ 다음 거래일 08:00 전**.
>   거래일 08:00~20:00 엔 08:50 주문 배치·09:00 매도 추격·14:58~15:31 오후 배치·18:00 감지·19:00 배치가 돈다.
> - 각 단계는 **전 테스트 통과 상태에서만 멈춘다.** 반쯤 고친 상태로 다음 거래일 08:00 을 넘기지 않는다. 못 끝내면 0단계 백업으로 되돌린다.

## 진행

### P0 백업
- [ ] 구현·테스트 머지 (PR #) — 해당 없음 (파일 복사만)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜) — 해당 없음
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P1 호가·주문 API 공용화 (`BrokerApi`)
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음 (import 경로만 바뀜)
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P2a 추격 엔진 공용화 — 유무상증자 먼저
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P2b 추격 엔진 공용화 — Jev 이전
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 왜

| 문제 | 근거 |
|---|---|
| 추격 엔진이 2벌 | Jev `research/private/jev_quant_live/buy_ah.py`(682줄)·유무상증자 `research/private/rights_issue/live/chase.py`(341줄)가 같은 패턴을 각자 구현 |
| 전략끼리 엉킴 | 유무상증자 `live/expire.py:44~46` 이 Jev 폴더의 `buy_ah.BrokerApi`·`BID_MAX_AGE_S`·`SOURCES` 를 import. Jev 폴더를 바꾸면 유무상증자 매매가 깨질 수 있음 |
| 이력 없음 | `research/private` 는 gitignore. 2026-10-07 가격 규칙 변경(호가 간격 3틱 이상이면 반대쪽 호가 ∓1틱)도 커밋·되돌리기 불가 |
| 공개해도 되는 코드 | 호가 지정가·정정·시장가 전환은 일반 매매 기법. 숨길 것은 종목 선정·시각·금액(전략) |

## 2. 확정 결정

| # | 항목 | 값 | 누가 |
|---|---|---|---|
| D1 | 원칙 | 매매 코드는 git 관리 공용 모듈, 전략(선정·시각·금액·원장 기록)은 private | 사용자 |
| D2 | 범위 | 호가 가격 계산뿐 아니라 **추격 정정 엔진까지** 공용화 | 사용자 |
| D3 | 순서 | P1 `BrokerApi` → P2a 유무상증자 엔진 → P2b Jev 이전. 단계마다 기존 테스트 전부 통과 후 다음 | Claude 제안 · 사용자 승인 |
| D4 | 시작 조건 | 2026-10-09 이후, 장 운영 중 아닐 때 (위 ⚠️ 박스) | 사용자 |
| D5 | 공용 모듈 위치 | `etl/scripts/chase_engine.py` (`scripts.trading_batch_common` 과 같은 import 규약) | Claude 제안 · 사용자 승인 |
| D6 | 공용 테스트 위치 | `etl/tests/test_chase_engine.py` (unittest, `cd etl && PYTHONPATH=.. uv run python -m unittest`) | Claude 제안 · 사용자 승인 |
| D7 | 엔진 기준 | 유무상증자 `chase.py` 를 기준으로 삼음 — 이미 매도·매수 겸용, state 별 시각(`start_at`·`market_at`), 지연 수량(`sizer`) 지원 | Claude 판단 |
| D8 | 동작 변경 | **없음.** 순수 이동·구조 변경. 가격·시각·정정 간격·거절 한도 등 실매매 동작은 그대로 | Claude 판단 |

## 3. 단계별 내용

### P0 백업 (작업 첫 단계)
- `research/private/jev_quant_live/`·`research/private/rights_issue/live/` 를 `research/private/_backup_<YYYYMMDD>/` 로 통째 복사 (git 이력이 없어 되돌릴 유일한 수단)
- 되돌리기 = 백업 폴더를 원래 자리로 복사 + 공용 모듈 커밋 revert

### P1 `BrokerApi` 공용화
- 옮길 것 (`buy_ah.py` → `etl/scripts/chase_engine.py`): `class BrokerApi` 전체(`limit_price`·`_place`·`place_limit`·`place_market`·`modify`·`cancel`·`unfilled`·`history`), `_bid_fresh`, `BID_MAX_AGE_S`, `REQUEST_TIMEOUT`, `EXCHANGE`
- 주문 `source` 문자열은 전략마다 다름 → `BrokerApi(broker_url, sources={"buy": ..., "sell": ...}, dry_run=...)` 로 주입. Jev 의 `SOURCES` 값은 Jev 쪽에 남김 (broker 로그·체결 매칭이 이 값을 씀 — 값 변경 금지)
- 로그 접두어 `[jevq-buy-ah]` → 주입 가능한 `tag` 인자 (기본값은 기존 문자열 유지)
- 고칠 import: `buy_ah.py`(자기 정의 → 공용 import), `rights_issue/live/expire.py:44~46`, `jev_quant_live/exit.py:302`(지연 import, `BrokerApi` 부분)
- 테스트: 기존 `jev_quant_live/tests/test_buy_ah.py` 의 `LimitPriceTest`(10-07 추가 9건 포함)를 공용 테스트로 **복사**, 원래 테스트는 import 만 바꿔 그대로 통과
- 첫 커밋 = 현재 코드 그대로(10-07 가격 규칙 포함). 리팩터링과 같은 커밋에 섞지 않음

### P2a 추격 엔진 공용화 (유무상증자 먼저)
- `chase.py` 의 엔진 부분(`run_chase`·`_try_first_order`·`_chase_step`·`_market_step`·`_bounds`·`_entry`·`_fills`·`Clock`·`_at`)을 공용 모듈로 이동
- **원장 기록 분리**: 지금 `chase.py:89·92·118·121·201·204·278·281` 이 `ledger.record_order`·`ledger.update_position` 을 직접 호출 → 엔진은 `on_event(state, kind, **fields)` 콜백만 부르고, 유무상증자 쪽 콜백이 원장에 씀. `kind` 는 지금 기록 지점 그대로: 첫 주문 접수·결과 불명·정정·시장가 전환·체결
- 그대로 둘 것 (private): 상태 목록 만들기·수량 결정(`sizer`, 1건 상한 분할)·무상 우선 취소·갈아타기 시장가 시각 — 전부 `expire.py` 쪽
- 유지할 불변식 (공용 테스트로 고정): 조회 실패·결과 불명이면 주문 안 함·재주문 금지(fail closed), 지정가 명시 거절 연속 3회면 지정가 포기, 가격 같으면 정정 안 함, 시장가 마감 이후 시장가 금지, state 별 이른 시장가 시각
- 유무상증자 테스트 382개 전부 통과 + 공용 엔진 단위 테스트

### P2b Jev 이전
- `buy_ah.py` 의 자체 루프(`_try_first_order`·`_step`·`_market_step`·`run_chase`)를 공용 엔진 호출로 교체. DB 기록(`_save`, `live_orders` 상태 `pending_ah`/`working_ah`/`unknown_ah`/`failed_ah`)은 Jev 쪽 `on_event` 콜백으로
- 두 엔진 차이 — 이전 전에 공용 엔진에 맞출지 결정 필요:
  - 체결 확정: Jev 는 마감 뒤 `_finalize` 가 체결내역을 30초 간격 3회 재조회해 체결가 기록(`FINALIZE_RETRIES`) / 유무상증자는 엔진 안에서 체결 수량만 셈
  - 상태 값: Jev 는 DB 문자열 상태, 유무상증자는 state dict 의 `status`
- Jev 테스트 101개 전부 통과

## 4. 검증

| 요구 | 테스트 |
|---|---|
| 동작 변경 없음 (D8) | 단계마다 Jev 101개·유무상증자 382개 기존 테스트 전부 통과 (기대값 수정 금지 — 수정이 필요하면 동작이 바뀐 것) |
| 가격 규칙 유지 | `LimitPriceTest` 21건 공용 테스트에서 통과 |
| fail closed | 조회 실패·결과 불명 → 주문 0건 테스트 (공용) |
| 전략 정보 비공개 | 공용 모듈에 종목 선정·금액·전략 시각 상수 없음 — PR 전 `grep -nE "15:1\|09:0\|budget\|acptno\|jev" etl/scripts/chase_engine.py` 결과 확인 |
| 작업 시간 (D4) | PR·작업 기록에 작업 시각 적기 |
| 첫 실가동 | 각 단계 머지 후 다음 거래일 추격 매매 로그(`etl/logs/jevq-*`, 유무상증자 오후 배치 로그)에서 정상 주문·체결 확인 |

## 5. 운영 체크
- 스케줄 작업·config 변경 없음 (import 경로만 바뀜)
- 같은 계좌 타 전략 보유분 매도 금지: 엔진은 넘겨받은 state 만 주문 — 기존과 동일, 공용 테스트로 고정
- 공개 저장소: 공용 모듈 주석에 private 설계 문서 번호(R·F 항목)·수익 수치 적지 않음

## 구현 차이
없음
