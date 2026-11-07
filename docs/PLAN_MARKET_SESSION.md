# PLAN_MARKET_SESSION: 장 시간이 바뀌는 날(수능·연초)을 공용 시계 하나로 처리

한 줄: 특별한 날을 `market_sessions.json` 에 적어 둔다. 공용 `market_session.at(날짜, "평소 시각")` 이 그날 시각으로 바꿔 준다. 정오 전 시각은 개장 기준, 정오 후 시각은 마감 기준으로 민다. 전략마다 복사돼 있는 `at`/`wait_until` 12벌을 이 함수로 바꾸고, 러너 공용 가드가 작업 시작을 밀린 만큼 늦춘다.

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업 변경 없음 — 러너 공용 가드만 바뀜. `market_sessions.json` 에 2026-11-19 등록)
- [ ] 첫 실가동 확인 (2026-11-19 수능일)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 배경

- 2026-11-06 되돌림이 시가 늦은 종목(VI)을 놓쳤다. 이 건을 고치다가 사용자가 "수능일도 개장 시간이 다르다"고 짚었다.
- 국내 장 시간이 바뀌는 날:

  | 날 | 개장 | 마감 |
  |---|---|---|
  | 평소 | 09:00 | 15:30 |
  | 연초 첫 거래일 | 10:00 | 15:30 |
  | 수능일 | 10:00 | 16:30 |

- 지금 코드는 모든 시각을 평소 기준 문자열로 고정해 두었다. 수능일에 생기는 일:
  - 되돌림 15:19:20 종가 청산이 장중 가격 청산이 된다.
  - high52 15:10 주문이 장중 주문이 된다.
  - msig 09:25 실행은 장 시작 전에 돈다.
  - 16:00 체결 확인 작업들은 장 마감 전에 돈다.
- 사용자 지시(11-06): 날마다 주먹구구로 고치지 말고, 이벤트 날만 기록해 두고 그날은 정해진 시각으로 돌게 한다. 리팩토링을 먼저 고민한다.

## 2. 측정 (2026-11-06, 현행 코드)

- 시각 계산 함수 복사본이 12벌 있다. 모두 `"HH:MM:SS"` 를 오늘 날짜에 그대로 붙인다.
  - `at`/`wait_until`:
    - `research/private/intraday_rebound/live/order.py:48,53`
    - `research/private/envelope/live/order.py:59,64`
    - `research/private/high52_quiet_breakout/live/order.py:228,233`
    - `research/private/rights_issue/live/order.py:59,64`
    - `research/private/swing_trade/live/order.py:51,56`
  - `_at`:
    - `research/private/intraday_rebound/live/exit.py:51`
    - `research/private/swing_trade/live/exit.py:56`
    - `research/private/rights_issue/live/chase.py:58`
    - `etl/scripts/run_close_bet_exit.py:98`
    - `etl/scripts/run_orderbook_recorder.py:219`
  - `at`: `etl/scripts/chase_engine.py:96` (공용 추격 엔진)
  - `wait_until`: `research/private/us_oas/live/order.py:87` (미국, 대상 아님)
- 시각 상수는 약 45개, 실매매 파일 약 29개에 있다. 대표 예:
  - 되돌림: `T_QUOTE 09:00:20`, `T_BUY_CANCEL 15:15`, `T_CLOSE 15:18`
  - 유증: `WINDOW 08:45~09:00`, `chase T_FIRST 15:00 / T_MARKET 15:19:50`, `free INTRA_CUT 14:40`
  - 스윙: `T_PREOPEN 08:30:05`, `T_EXPIRY 15:20:30`
  - high52: `T_ORDER 15:19:10`
  - msig: `exec_batch last_cut 15:10`
  - 공용: `sliced_exec slices 09:30·11:00·13:00·14:30`, `funding_engine PREOPEN_FILL_AT 09:00`
  - CLI 기본값: `--stop-time 15:25`, `--force-exit-time 15:19` 등
- 분봉 시각: `etl/scripts/build_krx_from_minute.py:58,62` 가 `153000` 을 종가 봉으로 쓴다. 수능일엔 `163000` 이 종가 봉이다.
- 러너: `ops/scheduled-tasks/run-*.ps1` 55개 중 22개가 `lib_trading_day_guard.ps1` 의 `Exit-IfNonTradingDay` 를 이미 거친다. 이 함수가 한 곳에서 고칠 수 있는 지점이다.

## 3. 확정 결정

| # | 결정 | 누가 정했나 | 구현 위치 |
|---|---|---|---|
| S1 | 특별한 날은 파일 하나에 적는다. `{"YYYYMMDD": {"open": "HH:MM", "close": "HH:MM", "why": "..."}}`. 없는 날 = 09:00/15:30 | 사용자(이벤트 날 기록) + 내 판단(형식) | `etl/scripts/market_sessions.json` (새 파일) |
| S2 | 공용 모듈 1개. `session(day) -> (open, close)`, `shift(day, hms) -> 초`, `at(day, hms) -> datetime`, `wait_until(day, hms)` | 사용자(리팩토링 먼저) + 내 판단(이름) | `etl/scripts/market_session.py` (새 파일) |
| S3 | 미는 규칙: 평소 시각 `< 12:00:00` 은 개장 차이(open − 09:00)만큼, `>= 12:00:00` 은 마감 차이(close − 15:30)만큼 민다. 연초: 오전 +1h, 오후 0. 수능: 둘 다 +1h | 내 판단 | `market_session.shift` |
| S4 | 전략 상수는 평소 날 시각 그대로 둔다(값·의미·백테스트 대응 불변). 바꾸는 건 그 상수를 datetime 으로 바꾸는 함수뿐이다 | 내 판단 | §2 의 12개 함수 → `market_session.at` 호출로 교체 |
| S5 | 정오 규칙이 안 맞는 상수는 그 자리에서 기준을 명시한다: `at(day, hms, anchor="open"|"close")`. §4 대조표에서 예외로 나온 상수만 | 내 판단 | 해당 호출부 |
| S6 | 러너 작업 시작 지연: `Exit-IfNonTradingDay` 가 통과한 뒤 `market_session.py --shift <작업 예정 HH:MM>` 으로 초를 받아 그만큼 `Start-Sleep` 한다. 국내 장과 무관한 작업(미국 등)은 `-NoSessionShift` 를 넘긴다 | 내 판단 | `ops/scheduled-tasks/lib_trading_day_guard.ps1` |
| S7 | 시간표 파일 오류(형식·시각 역전)는 실패로 처리한다. 가드는 거래 안 함 + 알림(기존 fail closed 와 같음) | 내 판단 | `market_session.load` + 가드 |
| S8 | 분봉 종가 봉은 `153000` 대신 `session(day).close` 를 쓴다 | 내 판단 | `build_krx_from_minute.py:aggregate_day` |
| S9 | 미국 전략·리서치 백테스트는 손대지 않는다 | 내 판단 | — |
| S10 | 되돌림 늦은 시가 주문(`SPEC_LATE_OPEN.md`, 마감 10:30)은 이 PLAN과 별개로 먼저 반영한다. 그 마감도 이 PLAN 뒤에는 `at(day, "10:30:00")` 으로 민다 | 사용자(되돌림 먼저) | `intraday_rebound/live/order.py` |

## 4. 상수 대조표 (구현 첫 단계에서 채움 — 정오 규칙 맞는지)

예외 판단 기준: "그 시각이 개장에 묶인 일인가, 마감에 묶인 일인가". P3(11-06)에서 켜진 전략의 상수를 대조했다. **예외(anchor 명시)는 0개다.**

| 묶음 | 상수 (평소) | 연초 | 수능 | 기준 |
|---|---|---|---|---|
| 장전·개장 | 되돌림 `09:00:05·09:00:20·09:00:50·09:01·09:05`, 유증 `08:45·08:50·09:00·09:05`, 스윙 `08:30:05·08:59:30·09:00:05·09:00:30·09:01`, high52 exit `--window-start 09:00`, 자금 `PREOPEN_FILL_AT 09:00` | +1h | +1h | 정오 전 → 개장 |
| 장중 오전 | 되돌림 `T_LATE_END 10:30`, msig `slices 09:30·11:00` | +1h | +1h | 개장 |
| 장중 오후 | msig `slices 13:00·14:30` | 0 | +1h | 마감 |
| 마감 직전 | 되돌림 `15:14·15:15·15:18·15:20·15:20:10`, high52 `15:18~15:20`·exit `--stop-time 15:18:30`, 유증 chase `15:00·15:19:50·15:20`·expire `15:17·15:18`, 스윙 `15:20·15:20:30·15:29:30`, msig `15:10·15:15·15:20` | 0 | +1h | 마감 |
| 마감 후 | 되돌림·스윙 `15:31`, 자금 `--confirm-at 15:31` | 0 | +1h | 마감 |

- msig `slices` 의 다음 분할 마감(`next − 1분`)은 이제 평소 시각 문자열로 계산한 뒤 `at` 에 넣는다. 예전처럼 밀린 datetime 을 문자열로 되돌리면 두 번 밀린다.
- 대조에서 뺀 것:
  - `free INTRA_CUT 14:40`·`time_class`: 공시 시각 분류에 쓰는 값이라 시계가 아니다. 수능일 15:30~16:30 공시가 "장후"로 분류될 수 있다. 1년에 하루뿐이고 그날 무상 공시가 있어야 문제가 되므로 §10 R4 로 둔다.
  - `_quote_fresh`(유증 호가 신선도): 호가가 찍힌 실제 시각을 비교한다. 그대로 둔다.
  - 엔벌로프: 11-06 중지. `order.py` 는 손대지 않았다.
  - `run_close_bet*`·`run_pullback_order`: 작업이 꺼져 있다. 공용 함수(`_at`·`is_exit_window_started`·`in_order_window`)만 바뀌어 따라온다.

## 5. 하루 흐름 (수능일 2026-11-19 예)

| 평소 | 수능일 | 하는 일 |
|---|---|---|
| 08:40 작업 시작 | 09:40 (가드가 60분 대기) | 되돌림·유증·스윙·msig 선정 |
| 09:00:20 시세 | 10:00:20 | 되돌림 시가 조회 |
| 15:10 high52 주문 | 16:10 | 가드 대기 → 코드 내부 15:19:10 → 16:19:10 |
| 15:19:20 종가 청산 | 16:19:20 | 되돌림·유증·스윙 |
| 16:00 확인 | 17:00 | 체결 확인 |

## 6. 손 안 대는 것 — 요구사항 커버 확인

- Windows 작업 트리거 시각: 가드 대기로 해결한다. 단 **가드를 안 거치는 러너 33개** 중 국내 장 시각에 묶인 것이 있으면 구멍이다. P1 에서 목록을 대조해 가드를 넣거나 무관함을 표시한다.
- 장 시각과 무관하게 "지금 시각"만 쓰는 코드(로그·타임스탬프)는 그대로 둔다.
- 백테스트(`research/backtest_*`)는 일봉·분봉 DB 를 읽는다. 수능일 분봉 시각은 DB 에 실제로 찍힌 대로라 손대지 않는다. 다만 `090000`/`153000` 고정 비교가 있는 백테스트 코드는 수능일 1~2건이 틀릴 수 있다. 영향이 작아 이번 범위 밖이고, LEADS 에 남긴다.

## 7. 테스트 (요구사항 → 테스트)

| 요구사항 | 테스트 |
|---|---|
| 평소 날 불변 | 파일에 없는 날 `at(d, "15:19:20")` == 그날 15:19:20 |
| 수능 오전·오후 +1h | 20261119 `at("09:00:20")` → 10:00:20, `at("15:19:20")` → 16:19:20 |
| 연초 오전만 | 20270104 `at("09:00:20")` → 10:00:20, `at("15:19:20")` → 15:19:20 |
| anchor 명시 | 연초 `at("13:00", anchor="open")` → 14:00 |
| 파일 오류 거부 | open ≥ close, 형식 틀림 → 예외 (가드 fail closed) |
| 러너 대기 | `--shift 15:10` 수능 → 3600, 평소 → 0. `-NoSessionShift` → 0 |
| 12개 함수 교체 후 동작 불변 | 각 전략 기존 테스트 전부 통과 (평소 날) |
| 분봉 종가 봉 | 수능일 163000 봉이 종가, 153000 무시 안 함(장중 봉) |

`etl/tests/test_market_session.py` (unittest), 전략 테스트는 각 `live/tests/`.

## 8. 운영 체크

- 휴장일: 기존 `check_krx_trading_day` 를 그대로 쓴다. 시간표 파일은 개장일의 시각만 다룬다.
- 시간표 갱신: 매년 수능일과 연초 첫 거래일을 사람이 적는다. 다음 연도 연초 날짜가 파일에 없으면 12월에 알림을 보낸다(P2, `daily-trading-result` 나 가드 한 줄).
- Windows: `.ps1` 은 UTF-8 BOM 을 유지한다.
- 실매매 코드(`research/private/*/live`) 반영은 20시 이후에 하고, 원본 백업부터 한다 (장중 muse 금지 규칙).

## 9. 단계

- P1: `market_session.py` + json + 테스트, §4 대조표 작성, 가드 없는 러너 대조
- P2: 가드 대기(`lib_trading_day_guard.ps1`)
- P3: 12개 함수 교체 (전략별, 각자 테스트). 공용(`chase_engine`·`sliced_exec`·`funding_engine`·`run_close_bet*`·`run_pullback*`) 먼저, 그다음 live 5개
- P4: `build_krx_from_minute` 종가 봉, 연초 알림
- 마감: 11-18(수) 20시까지 반영. 11-19 첫 실가동.

## 10. 열린 항목

- R1: 2026 수능일 = 11-19(목)로 알고 있다. 확인 필요. 2027 첫 거래일 10:00 개장 날짜(01-04 추정)도 확인 필요.
- R2: 수능일 NXT(애프터마켓) 시간 변화는 모름. 지금 NXT 를 쓰는 전략은 없다.
- R3: 가드 없는 러너 33개 대조 (11-06). 켜져 있고 장 시각에 묶인 것은 아래 5개다. P2 에서 가드를 넣는다. 거래일 검사는 하지 않고 대기만 하게 `-ShiftOnly` 를 쓴다.
  - `daily-trading-result` 15:25
  - `orderbook-recorder` 08:44·14:59
  - `orderbook-research-recorder` 08:20
  - `watchlist-intraday` 14:59
  - `etf-holdings` 16:30 (장 마감 뒤 데이터라면 대상)

  나머지는 손대지 않는다.
- R4: 유증 무상 감지의 공시 시각 분류(`INTRA_CUT 14:40`, `research.rights_issue.analyze.time_class`)는 평소 장 시간 고정이다. 수능일 15:30~16:30 공시는 "장후"로 분류된다. 영향은 연 1일이고 미반영 상태다.
  - 꺼진 작업: `close-bet-*`, `trading-*`
  - 장 시각과 무관한 작업: 백업, 미국, 유튜브·텔레그램, `krx-ohlcv`(전날 데이터)

## 구현 차이
- P2: `-NoSessionShift` 는 만들지 않았다. `Exit-IfNonTradingDay` 를 쓰는 러너가 모두 국내 장 작업이다(미국 러너는 이 가드를 안 쓴다). 가드 없는 5개 러너는 `-ShiftOnly` 대신 `Wait-SessionShift` 를 직접 부른다.
- P3: 함수는 12벌이 아니라 13곳이다. `trading_batch_common.in_order_window`(유증 주문이 씀)가 하나 더 있었다. 문자열 시각 비교 3곳도 바꿨다: `run_pullback_exit.is_exit_window_started`, high52 exit `--stop-time`, 자금 엔진 대기 2곳.
  - 각 함수는 `market_session.at(...)` 결과에 기준 datetime 의 tzinfo 를 다시 붙인다. 예전 `.replace(hour=...)` 와 같은 시간대를 돌려줘야 naive 시계를 쓰는 테스트와 호출부가 그대로 돈다.
  - 엔벌로프는 중지 상태라 제외했다.
- P4: 연초 누락 알림은 만들지 않았다. 연초 첫 거래일은 `session()` 이 공휴일 달력으로 자동 판정해서 파일에 없어도 된다.
  - 대신 수능일 누락 알림을 넣었다: `market_session.csat_missing`.
    - 10월 1일부터 그해 마감이 15:30 이 아닌 줄이 파일에 없으면 15:25 정산 보고 끝에 ⚠ 한 줄을 붙인다.
    - 사용자 결정 11-07.
  - 분봉 일봉 집계(`build_krx_from_minute.aggregate_day`)는 정규장 범위와 종가 봉을 `session(date)` 로 자른다.
- PR #155 리뷰 반영 (11-07, CodeRabbit 지적 5건 전부 수정, 사용자 "다 고쳐"):
  - 시작 지연 대기(최대 1시간)가 작업 최대 실행 시간(10분~1시간)을 넘어 수능일에 작업이 시작 전에 꺼질 뻔했다. 가드를 쓰는 작업 27개의 `ExecutionTimeLimit` 을 XML·등록 작업 모두 +1시간.
  - `Wait-SessionShift` 는 지금 시각이 아니라 작업의 예정 시작 시각(`Get-ScheduledTask` 트리거) 기준으로 남은 시간만 기다린다. 늦게 시작(재시도·절전 복귀)해도 녹화 구간을 놓치지 않는다. 작업 이름이 Tag 와 다른 2개는 `-TaskName` 을 넘긴다.
  - `sliced_exec`: 다음 회차가 정오를 걸치면(새해 첫날 11:59→12:59) 정오 회차를 건너뛰던 것 — 마감 문자열을 이동 차이만큼 보정.
  - 수능 누락 경고는 보고 날짜가 아니라 오늘 날짜로 판정(과거 날짜 재실행 시 오경고 방지). `_first_trading_day` 는 연도별 캐시.
  - 재리뷰: msig-select 는 08:40·09:15(재시도) 두 번 뜨는데, 수능일엔 08:40 회차가 09:40 까지 대기 중이라 `IgnoreNew` 가 09:15 를 버렸다. `Queue` 로 바꿈(XML·등록 작업). 성공 뒤 두 번째 회차는 `ledger.has_run(formation, "select")` 로 건너뛴다. 같은 구조의 orderbook-recorder(08:44·14:59)는 첫 회차가 11:00 에 끝나 영향 없음.
