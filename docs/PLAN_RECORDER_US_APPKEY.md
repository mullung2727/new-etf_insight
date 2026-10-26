# PLAN_RECORDER_US_APPKEY — 리서치 녹화기를 :8003(미국 계좌) 앱키로 옮긴다

한 줄: 키움은 앱키당 웹소켓 1개만 살려서 리서치 녹화기(:8001 토큰)가 broker :8001 재접속에 매일 08:30:06 밀려난다(10-21~ 녹화 0건) → 녹화기는 실시간을 안 쓰는 :8003 앱키 토큰을 쓰고, :8003 broker 는 웹소켓 자동 연결을 끈다.

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 — `scripts/restart_all_servers.ps1` 로 broker 3개 재기동 (한국 시각 20:00~08:00 만)
- [ ] 첫 실가동 확인 (날짜) — 녹화 trade·book 행 > 0, 끊김 `stop_kicked` 없음
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 0. 근거 (실측)

| 사실 | 근거 |
|---|---|
| 앱키당 웹소켓 1개, 마지막 로그인만 생존 | 2026-10-26 21:04 2번째 접속 → `SYSTEM R10001 "동일한 App key로 접속이 되었습니다. 기존 세션은 종료가 됩니다"` + `1000 Bye` |
| broker 는 끊기면 5초 뒤 무조건 재접속 | `broker/kiwoom/ws/manager.py:91` `_run_loop`, 21:04:21.097 재접속 로그 |
| 녹화기는 :8001 토큰 사용 | `etl/scripts/run_orderbook_research_recorder.py:174` `broker/.token_cache.json` |
| 10-21~ 녹화 0건 | `E:\new-etf_insight_data\orderbook\2026\10\trade_*`: 10-20 97.8만 → 10-21 334 → 10-22 19 → 10-26 29 |
| :8003 은 국내 실시간이 필요 없다 | `broker/kiwoom/us.py` 웹소켓 없음, :8003 의 `/realtime`·`/events` 사용처 grep 0건 |
| :8003 도 같은 코드라 웹소켓을 연다 | `broker/main.py:96` lifespan `_ws_manager.start()` 무조건, broker-us 로그 `WS connected (0D 0)` |

**미확인 (사용자 결정으로 생략, 2026-10-26)**: :8003 앱키로 국내 0B/0D 가 실제로 오는지. 21:15 장외 시험은 LOGIN·REG 성공, REAL 0건(거래 없음). → 첫 실가동에서 확인, 0건이면 B안(앱키 추가 발급)으로.

## 1. 변경

| # | 파일:함수 | 변경 | 누가 |
|---|---|---|---|
| 1 | `broker/main.py:lifespan` | env `KIWOOM_WS_ENABLED`(기본 `1`)가 `0` 이면 `_ws_manager.start()`·`stop()` 건너뜀 | 내 판단 |
| 2 | `scripts/restart_all_servers.ps1` `$usEnv` | `set "KIWOOM_WS_ENABLED=0"` 추가 (:8001·:8002 는 그대로) | 내 판단 |
| 3 | `run_orderbook_research_recorder.py:load_token` | 읽는 파일 `broker/.token_cache.json` → `broker/.token_cache.us.json`. 모듈 상수 `TOKEN_CACHE` 로 | 사용자(:8003 사용) |
| 4 | 같은 파일 `run_recording` 재접속 직전 | `load_token` 이 `expired` 면 `GET http://127.0.0.1:8003/us/deposit` 1회(broker-us 가 토큰 재발급) 후 다시 읽기. 그래도 실패면 기존대로 종료 | 내 판단 (US 토큰은 broker-us REST 호출 때만 갱신, 10-26 기준 만료 12:40 → 장중 재접속 때 만료 가능) |
| 5 | 같은 파일 docstring 10줄 | ":8001 토큰" → ":8003 토큰" | — |
| 6 | `etl/tests/test_realtime_users.py` | (a) `restart_all_servers.ps1` 의 `$usEnv` 에 `KIWOOM_WS_ENABLED=0` 있는지 (b) 미국 실시간 타입 `"F4"`·`"F5"`·`"FE"`·`"FT"`·`usa20280~usa20291` 이 `broker`·`etl/scripts`·`research/private` 에 나오면 실패 | 사용자(미국 겹침 검토) |

손 안 대는 것: `BrokerWatch`(:8001 SSE 끊김 횟수만 셈, 녹화는 안 멈춤) — 이제 녹화기가 :8001 을 안 밀어내므로 값은 대개 0. 지우지 않는다.

## 2. 미국 쪽 겹침 (사용자 요청 검토)

- 지금 겹침 없음: 미국 주문·조회는 전부 REST(`broker/kiwoom/us.py`), 체결통보 `00` 구독(`broker/main.py:_fill_sync_loop`)은 국내 체결용.
- 생길 수 있는 경우: :8003 에서 미국 실시간(주문확인 F4·체결 F5·체결가 FE·10호가 FT)이나 미국 조건검색(usa20280~91)을 쓰기 시작할 때. 녹화 시간(08:30~20:00 KST)과 미국 프리마켓·주간거래가 겹칠 수 있다.
- 막는 장치: 1절 #6 테스트. 그 기능을 붙이는 순간 실패해서 이 충돌을 다시 보게 한다.

## 3. 요구사항 → 테스트

| 요구 | 테스트 |
|---|---|
| :8003 은 웹소켓을 안 연다 | `KIWOOM_WS_ENABLED=0` 에서 lifespan 이 `start` 를 안 부름 (broker 테스트, 가짜 manager) |
| :8001·:8002 는 그대로 연다 | env 없을 때 `start` 호출됨 |
| 녹화기는 US 캐시를 읽는다 | `load_token` 이 `.token_cache.us.json` 만 읽음 (임시 폴더) |
| 만료 시 1회 갱신 요청 | `expired` → 갱신 함수 1회 호출 → 재시도. 두 번째도 `expired` 면 종료 |
| 미국 실시간 사용 시 경고 | #6 (b): 가짜 파일에 `"F5"` 넣으면 실패 |
| 운영 env 누락 차단 | #6 (a) |

## 4. 운영

- 재기동은 `scripts/restart_all_servers.ps1` 로 broker 3개 같이, **한국 시각 20:00~08:00 만** (PowerShell `Get-Date` 로 확인). 녹화 스케줄 08:20 전에 끝낼 것.
- 녹화 스케줄 작업(`orderbook-research-recorder`, 08:20)·registry 변경 없음.
- 10-21~ 빠진 틱은 틱차트 `ka10079` 로 메울 수 있다(약 1달 보관 실측). 호가는 복구 불가. 메우기는 이 PLAN 범위 밖.

## 구현 차이
없음
