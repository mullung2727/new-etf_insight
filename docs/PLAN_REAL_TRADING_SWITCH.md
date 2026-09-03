# PLAN — 실전(real) 전환 안전 패치

## 요약

모의투자(`KIWOOM_ENV=paper`)에서 실전(`real`)으로 넘어가기 전, **키 교체만으로는 막히지 않는
4개 구멍**을 먼저 막는다. 구멍은 (1) 시장가 주문 금액 무제한, (2) 런타임 env 전환 경로
(웹 버튼 + MCP 툴), (3) 두 매수 전략의 15:19 동시 실행·예수금 경쟁, (4) 깨진 테스트 11개.
패치 후 전량 정지 상태에서 조회만으로 실전 접속을 검증하고, 소액으로 재개한다.

확정 결정(사용자):
- **두 전략 모두 유지**, **실행시각은 둘 다 15:19 그대로**(아래 "시각 분리 철회" 참조).
- **실전 초기 종목당 예산 10만원**. 파생: 눌림목 `budget_per_stock=100000`(×3=30만),
  종가베팅 `budget_by_count={1:100000, 2:100000, 3:100000}`(최대 30만),
  **1일 최대 노출 60만**, `MAX_ORDER_AMOUNT=150000`.
- 모의 미청산 종가베팅 `050110` 11주 = **모의에서 시장가 청산 후** 전환(장중 실행).
- env는 **프로세스 시작 시 고정**. 런타임 전환 경로는 제거한다.
- 자격증명은 **env 별 이름(`KIWOOM_PAPER_*` / `KIWOOM_REAL_*`)으로 분리**해
  `KIWOOM_ENV` 한 줄이 주소·키·계좌를 함께 바꾸게 한다.

## 진행 상태

- 작업 1 (시장가 금액 가드) — **완료**
- 작업 2 (런타임 env 전환 제거 + env 별 자격증명 분리) — **완료**
- 작업 3 (예산 축소) — **완료**. 시각 분리는 철회
- 작업 4 (테스트) — 신규 테스트는 작업 1·2 에서 완료. `test_broker_client.py` 수리 **남음**
- 런북 — 미실행

---

## 배경 — 실측으로 확인한 현재 상태

| 항목 | 실측 | 근거 |
|---|---|---|
| 시장가 금액 가드 | **없음**. `if not market:` 블록 안에만 금액 검사 | `broker/kiwoom/guards.py:21-29` |
| 15:19 동시 실행 | 눌림목·종가베팅 트리거 둘 다 `15:19:00` | `ops/scheduled-tasks/trading-order.xml`, `close-bet-order.xml` `<StartBoundary>` |
| 루트 `.env` | `KIWOOM_ENV` **키 자체가 없음** → default `paper` | `broker/kiwoom/config.py:79` |
| `broker/.env` | **존재하지 않음** → 루트 `.env` 한 곳이 broker·ETL 공통 소스 | `config.py:20-21` fallback |
| DB env 컬럼 | `kiwoom_trade_history`·`pullback_orders`·`close_bet_orders` 모두 없음 | `broker/notes/db.py:59` |
| 실제 보유 미청산 | 종가베팅 `050110` 11주 @1,150 (7/15) 1건. 나머지 `rejected`/`failed`는 실체 없음 | `watchlist.sqlite3` 조회 |
| 모의 누적 실현손익 | 눌림목 +68,248원(24건) / 종가베팅 -1,013,944원(31건) | `sell_pl_won` 합계 |
| `test_broker_client.py` | 11개 전부 `AttributeError: module 'scripts.run_close_bet' has no attribute 'requests'` | 실행 확인 |

---

## 작업 1 — 시장가 주문 금액 가드

### 문제

`check_order`가 `market=True`면 `qty > 0`만 보고 통과한다. 시장가는 `price=0`이라
notional 계산이 불가하다는 이유로 검사 자체를 건너뛴다. 실전에서 수량 오입력 시
**예수금 전액까지** 주문이 나간다(무한대는 아님 — 키움이 예수금 부족으로 거부).
종가베팅·눌림목 청산·웹 수동주문이 전부 시장가라 전 경로가 노출된다.

### 결정 — 매수만 가드, 매도는 면제

- **시장가 매수**: 주문 직전 현재가를 조회해 `est_price × qty`를 `MAX_ORDER_AMOUNT`와 비교.
  현재가 조회 실패 시 **거부**(fail-closed).
- **시장가 매도**: 금액 가드 **면제**, `qty > 0`만 검사.
  - 근거: 매도 수량 초과는 키움이 거부한다(에러코드 `800033`, `broker/routers/orders.py:31`에
    안내문구까지 존재). 반대로 가드가 매도를 막으면 **보유 포지션이 청산 불가로 갇힌다**
    (예: 10만에 산 종목이 급등해 평가액이 상한을 넘으면 손절·강제청산이 전부 거부됨).
    금액 상한의 목적은 "의도보다 큰 신규 노출 방지"이므로 노출을 줄이는 매도에는 적용하지 않는다.

### 변경

**`broker/kiwoom/guards.py`** — `check_order`에 `side: str`, `est_price: int | None = None` 추가:

```
qty <= 0                            → OrderRejected                     (기존)
지정가 & price <= 0                 → OrderRejected                     (기존)
지정가 & qty*price > 상한           → OrderRejected                     (기존)
시장가 & side == sell               → 통과                              (신규·명시)
시장가 & side == buy & est_price 없음 → OrderRejected("현재가 조회 실패")  (신규)
시장가 & side == buy & qty*est > 상한 → OrderRejected                    (신규)
```

**`broker/kiwoom/orders.py::place_order`** — `check_order` 호출 전, `market and side == buy`일 때만
`quotes.get_quote(req.symbol).price`로 `est_price` 조회.

- 순환 import 없음: `kiwoom/quotes.py`는 `tr`·`client`·`models`만 import (확인함).
- 조회 실패(예외/None) → `est_price=None`으로 넘겨 `check_order`가 거부. 예외를 삼키지 않는다.
- 추가 REST 1콜. 시장가 **매수** 시에만 발생.

### 강제 경로 확인 (plan-writing 1번)

`check_order` 호출부는 `broker/kiwoom/orders.py:31` **1곳**. 이 프로세스에서 주문을 내보내는
경로는 전부 `orders.place_order`를 지난다:

- HTTP `POST /orders` → `routers/orders.py:87`
- MCP `place_order` 툴 → 같은 라우트 (FastApiMCP가 라우트에서 생성, `broker/main.py:145`)
- ETL 배치 → `trading_batch_common.market_order` → `POST /orders` (`trading_batch_common.py:87`)
- 웹 수동주문 → `broker-web/lib/broker-client.ts` → `POST /orders`

**우회 경로 없음**을 위 4개로 확인. 신규 진입점이 생기면 이 목록을 갱신한다.

---

## 작업 2 — 런타임 env 전환 경로 제거

### 문제

`POST /settings`가 `set_runtime_env`로 호스트만 바꾼다(`broker/routers/settings.py:32`).

1. appkey/secret/account_no는 `.env` 그대로라 **자격증명과 호스트가 어긋난다**.
2. `_CFG_MODULES = [orders, account, conditions]`에 **WS 매니저가 없다**
   (`broker/kiwoom/ws/manager.py:34` `self._cfg` 캐시 유지) → 토글해도 체결 실시간 피드는
   **옛 호스트에 그대로 붙어있다**.
3. `FastApiMCP(app)`가 전 라우트를 노출하므로 **MCP 툴 `update_settings`로 LLM 에이전트도
   env를 뒤집을 수 있다**. 웹 버튼보다 이쪽이 더 위험하다.

### 변경

- `broker/routers/settings.py` — **POST 라우트 삭제**. GET만 남기고 응답에 `account_tail`
  (계좌번호 뒤 4자리) 추가. → MCP 툴 `update_settings`도 함께 소멸(라우트 기반 생성).
- `broker/kiwoom/config.py` — `_runtime_env` 전역과 `set_runtime_env` **삭제**.
  `get_current_env()`는 `os.getenv("KIWOOM_ENV", "paper")`만 본다.
  → WS `_cfg` stale 버그는 **원인 제거로 소멸**(코드 추가 아님).
- `broker/kiwoom/auth.py`, `broker/kiwoom/client.py` — `clear_cache()` 삭제.
  유일한 호출부가 settings POST 였다(변경으로 생긴 고아 제거).
- `broker-web/components/trading/account-panel.tsx` — 토글 버튼 → **읽기전용 배지**.
  `toggleEnv`·`envSwitching` 삭제. 배지는 유지하되:
  실전 = `status-loss` 경고색 채움 / 모의 = 중립 회색, 뒤에 `···{account_tail}` 병기.
  (기존 실전 = 초록(`status-profit`)은 "정상"으로 읽혀 경고 효과가 없어 교체.)
- `broker-web/lib/broker-client.ts` — `updateSettings` 삭제, `Settings`에 `account_tail` 추가.
- `broker/test_orders.py:145` — `patch("routers.orders.get_current_env", ...)`는 그대로 동작
  (함수는 남음). 수정 불필요.

### 자격증명 env 별 분리 (같이 처리)

`config.py::load_config`가 `KIWOOM_{ENV}_APPKEY` → `KIWOOM_APPKEY` → `KIWOON_MOCK_TR_APP_KEY`
순으로 조회한다(secret·account_no 동일).

- 접두사 없는 이름만 쓰면 실전 키를 `KIWOOM_APPKEY`에 넣은 뒤 `KIWOOM_ENV=paper`로 되돌렸을 때
  **모의 주소 + 실전 키** 혼합 상태가 된다. env 별 이름이 이 경로를 없앤다.
- 뒤의 두 이름은 기존 `.env` 호환용 폴백 — 지금 루트 `.env`(`KIWOON_MOCK_TR_*`만 존재)로도 그대로 뜬다.

env 변경은 이제 **루트 `.env`의 `KIWOOM_ENV` 한 줄 + broker 재기동**뿐이다.

---

## 작업 3 — 예산 축소 · 실행시각 분리

### 예산

| 파일 | 키 | 현재 | 변경 |
|---|---|---|---|
| `etl/scripts/pullback.json` | `budget_per_stock` | 300000 | **100000** |
| `etl/scripts/close_bet.json` | `budget_by_count` | 3000000 / 2000000 / 1666666 | **100000 / 100000 / 100000** |
| 루트 `.env` | `MAX_ORDER_AMOUNT` | 미설정 (기본 1000000) | **150000** |

- 두 config 모두 **웹 `/admin/settings`가 쓰고 배치가 읽는 단일 파일**
  (`etl/scripts/close_bet_config.py`, `broker-web/lib/pullback-config.ts`) — 코드 변경 없음.
- 10만은 `close_bet_config._validate`(양의 정수)·`pullback-config.ts` 검증(1 이상 정수) 통과.
- `MAX_ORDER_AMOUNT=150000`은 종목당 10만 + 시장가 슬리피지 여유. 상한이 예산보다 낮으면
  정상 주문이 거부되므로 반드시 예산보다 크게 잡는다.

### 실행시각 — 분리 철회 (둘 다 15:19 유지)

초안은 예수금 경쟁을 우려해 눌림목을 15:17로 옮기려 했으나, 근거를 다시 확인하고 철회한다.

두 배치 모두 예수금으로 한 번 더 깎는다:
- 눌림목 `run_pullback_order.py:150-153` → `min(budget_per_stock, cash // count)`
- 종가베팅 `run_close_bet.py:482` → `min(strat_budget, cash // n_sel)`

따라서 예수금이 총 필요액(60만)보다 넉넉하면 동시 실행해도 각자 10만씩 그대로 나간다.
예수금이 빠듯할 때만 두 배치의 합계가 잔액을 넘고, 그때 벌어지는 일은
**뒤에 나간 주문이 키움에서 예수금 부족으로 거부**되는 것이다 — 의도 초과 체결이 아니다.
돈이 새는 경로가 아니므로 분리할 이유가 되지 못한다.

반대로 옮기면 잃는 것:
- 눌림목 진입가 기준 시각이 바뀌어 **전략 조건 자체가 달라진다**(모의 이력 24건은 전부 15:19 기준).
- `ops/scheduled-tasks/trading-order.xml`의 `<StartBoundary>`와
  `run-trading-order.ps1:10-11`의 `--order-time`/`--order-deadline-time`을 **둘 다** 고쳐야 하고,
  한쪽만 고치면 `run_pullback_order.py:478,493`의 `in_order_window` 검사에 걸려 그날 전량 스킵된다.

→ **스케줄 파일은 손대지 않는다.** 실전 첫날 로그로 예수금 부족 거부가 실제로 나오는지만 확인하고,
나오면 그때 분리한다(런북 11단계).

---

## 작업 4 — 테스트 수리 + 신규 테스트

### 수리

`etl/tests/test_broker_client.py` 11개 — `market_order`가 `trading_batch_common`으로 이동했는데
mock 대상이 `scripts.run_close_bet.requests` 그대로다.
→ `scripts.trading_batch_common.requests`로 교체. 운영 코드는 손대지 않는다.

### 신규 (작업 1·2 대응)

`broker/test_orders.py`에 추가:

| # | 케이스 | 기대 |
|---|---|---|
| T1 | 시장가 매수, 현재가 10,000 × 20주 = 20만 > 상한 15만 | `OrderRejected` |
| T2 | 시장가 매수, 현재가 10,000 × 10주 = 10만 ≤ 상한 | 통과 |
| T3 | 시장가 매수, `get_quote`가 `price=None` | `OrderRejected` (현재가 조회 실패) |
| T4 | 시장가 매수, `get_quote`가 예외 | `OrderRejected` (예외 전파 아님) |
| T5 | 시장가 **매도**, 평가액이 상한 초과하는 수량 | **통과** (면제 확인) |
| T6 | 시장가 매도, `qty=0` | `OrderRejected` |
| T7 | 지정가 상한 초과 / 미만 | 기존 동작 유지 (회귀) |
| T8 | `POST /settings` 호출 | **405** (라우트 제거 확인) |
| T9 | OpenAPI 스키마에 `update_settings` operation_id 없음 | MCP 툴 미노출 확인 |
| T10 | `config` 모듈에 `set_runtime_env` 속성 | 없음 |
| T11 | `KIWOOM_ENV=real` + 양쪽 키 존재 | real 키·계좌·주소 선택 |
| T12 | `KIWOOM_ENV=paper` + 양쪽 키 존재 | paper 키·계좌·주소 선택 |
| T13 | `KIWOON_MOCK_TR_*` 만 있는 기존 `.env` | 정상 로드 |
| T14 | 해당 env 키 없음 | `RuntimeError` |

T8~T10은 "런타임 전환 불가"라는 **부정 요구를 차단 테스트로** 남긴 것 (plan-writing 3번).
구현 결과 `POST /settings`는 404가 아니라 **405**(경로는 GET으로 살아있음)로 확정.

---

## 요구사항 ↔ 테스트 매핑

| 요구사항 | 구현 | 검증 |
|---|---|---|
| 시장가 매수 금액 상한 적용 | `guards.check_order` + `orders.place_order` | T1, T2 |
| 현재가 미확인 시 주문 금지 | 위 (fail-closed) | T3, T4 |
| 청산(매도)은 상한에 막히지 않음 | `side == sell` 면제 | T5 |
| 수량 0/음수 거부 | 기존 `qty <= 0` | T6 |
| 지정가 기존 동작 불변 | 변경 없음 | T7 |
| 런타임 env 전환 불가 (웹) | 버튼 제거 + `updateSettings` 삭제 | 수동: 배지 클릭 불가 |
| 런타임 env 전환 불가 (API/MCP) | POST 라우트 삭제 | T8, T9 |
| 런타임 전환 함수 자체가 없음 | `set_runtime_env` 삭제 | T10 |
| WS가 옛 호스트에 남지 않음 | `set_runtime_env` 제거로 원인 소멸 | 런북 9단계 WS 로그 |
| 실전 주소 + 모의 키 혼합 불가 | `KIWOOM_{ENV}_*` 우선 조회 | T11, T12 |
| 기존 `.env`로도 기동 | 접두사 없는 이름 폴백 | T13 |
| 키 없으면 조용히 뜨지 않음 | `_require_first` | T14 |
| 실전/모의 계좌 오인 방지 | 배지에 계좌 뒷 4자리 + 실전 경고색 | 런북 9단계 육안 |
| 1일 최대 노출 60만 | 두 config + `MAX_ORDER_AMOUNT` | 런북 9단계 로그 |
| ETL·broker가 같은 env를 봄 | 루트 `.env` 단일 소스 | 런북 9단계 `/settings` + 배치 로그 |

---

## 범위 밖 (이번에 안 함)

- **DB env 컬럼 추가**: 실매매 안전에 필요한 건 컬럼이 아니라 **전환 시점 미청산 0**이다
  (런북 4~6단계). 컬럼은 과거 모의 이력과 실전 이력이 섞이는 **성과분석** 문제이므로 별건.
  - "손 안 댐"이 요구사항을 덮는지 재확인 (plan-writing 4번): 미청산이 0이면 청산 감시
    쿼리(`sold_at is null`)가 실전 포지션만 잡으므로 **실매매 경로는 커버된다**. 커버 안 되는 건
    누적 손익 집계뿐이며, 전환일 이후 `created_at`으로 분리 가능.
- **`kiwoom/tr.py` TR 코드 실전 재검증**: 실전도 코드가 동일하고 모의에서 이미 도는 TR이라
  런북 10단계 **1주 수동 주문**으로 대체한다.
- **MCP `place_order` 툴 노출**: Hermes 챗봇이 실전 주문을 낼 수 있다. 이번엔 `MAX_ORDER_AMOUNT`
  가드로만 제한하고 툴 제거는 하지 않는다. → 미결 항목.

---

## 전환 런북 (패치 완료 후 실행)

| # | 내용 | 주체 | 확인 |
|---|---|---|---|
| 1 | 주문·청산 스케줄 작업 전부 비활성화 (`trading-*`, `close-bet-*`) | 나 | `schtasks /query` 상태 Disabled |
| 2 | 실행 중인 눌림목/종가베팅 청산 워커 종료 | 나 | 프로세스 없음 |
| 3 | 예산 config 2개 적용 (시각은 변경 없음) | 나 | 파일 diff |
| 4 | 모의에서 `050110` 11주 시장가 청산 (**장중에만 가능**) | 나 (승인 후) | `close_bet_orders.sold_at` 기록 |
| 5 | 미청산 0 확인 | 나 | `sold_at is null` AND 실보유 0건 |
| 6 | 루트 `.env` 정리 — 아래 블록 참고 | **너** | broker 기동 성공 |
| 7 | `broker/.token_cache.json` 삭제 | 나 | 파일 없음 |
| 8 | broker 재기동 | 나 | 기동 로그 |
| 9 | **조회만**으로 실전 검증 | 나 | `GET /settings` env=real / 예수금·잔고 실데이터 / WS 로그가 `api.kiwoom.com` |
| 10 | 1주 수동 지정가 매수 → 체결 → 매도로 실전 TR 검증 | **너 승인** → 나 | 원장 기록·실현손익 조회 |
| 11 | 스케줄 재개 | **너 결정** | 첫날 로그로 15:17/15:19 순차 실행·`ord_alow_amt` 반영 확인 |

6단계 루트 `.env` 목표 형태:

```
KIWOOM_ENV=real
MAX_ORDER_AMOUNT=150000

KIWOOM_PAPER_APPKEY=<기존 KIWOON_MOCK_TR_APP_KEY 값>
KIWOOM_PAPER_SECRETKEY=<기존 KIWOON_MOCK_TR_APP_SECRET 값>
KIWOOM_PAPER_ACCOUNT_NO=<기존 KIWOON_MOCK_TR_ACCOUNT_NO 값>

KIWOOM_REAL_APPKEY=<실전 앱키>
KIWOOM_REAL_SECRETKEY=<실전 시크릿>
KIWOOM_REAL_ACCOUNT_NO=<실전 계좌번호>
```

- 기존 `KIWOON_MOCK_TR_*` 줄은 남겨둬도 무해하다(접두사 있는 이름이 우선).
  단 모의 값을 `KIWOOM_PAPER_*`로 옮겨두면 `KIWOOM_ENV` 한 줄로 왕복이 된다.
- `KIWOOM_APPKEY` / `KIWOOM_SECRETKEY` / `KIWOOM_ACCOUNT_NO`(접두사 없는 이름)에는
  **실전 값을 넣지 말 것** — env를 paper로 되돌려도 그 값이 이겨 혼합 상태가 된다.
- `broker/.env`는 **만들지 않는다**(이 저장소 규칙: .env는 루트만).

---

## 미결 항목

- MCP `place_order` 툴을 실전에서도 열어둘지 (Hermes 챗봇 주문 권한)
- DB env 컬럼 추가 시점
- 실전 성과 확인 후 예산 증액 기준·시점
