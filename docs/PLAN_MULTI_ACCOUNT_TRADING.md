# PLAN — 계좌별 전략 운영 (다계좌 broker)

한 줄: broker 를 **계좌마다 1개씩** 띄우고(기존 :8001, 신규 :8002), 전략은 **자기 계좌 broker 로만** 주문하도록
양쪽에서 막는다. 첫 적용은 비공개 high52 전략이고, 배치는 전부 이 저장소 레지스트리(`ops/batches`)에 등록한다.
세 번째 계좌부터는 `.env` 키 3줄과 인스턴스 표 1줄만 추가하면 된다.

작성 2026-09-13, 개정 2026-09-13 (리뷰 반영). 구현 전 설계.

> 이 문서는 공개 저장소에 있다. **전략 규칙·파라미터·전략별 주문 절차는 쓰지 않는다.**
> 전략 쪽 설계는 `research/private/high52_quiet_breakout/PLAN_LIVE.md`, 규칙 원본은 같은 폴더 `FINANCIAL_FILTER.md` §1 (둘 다 gitignore).

## 구현 전 결정할 것

| # | 결정 | 추천 | 이유 |
|---|---|---|---|
| D1 | 계좌 분리 방식 | **broker 프로세스를 계좌별로 1개씩** | `load_config()` 가 프로세스당 앱키·계좌 1개로 굳어 있고, 라우터·MCP·WS·노트 DB 가 전부 단일 계좌 전제다. 한 broker 에서 여러 계좌를 다루려면 모든 라우터를 고쳐야 한다. 프로세스 분리는 config 한 곳과 기동 스크립트만 고치면 된다 |
| D2 | 신규 계좌 env 이름 | `KIWOOM_PROFILE=HIGH52` → `KIWOOM_HIGH52_REAL_APPKEY / _SECRETKEY / _ACCOUNT_NO` | 기존 `KIWOOM_REAL_*` 과 이름이 겹치지 않는다. profile 이 있으면 **폴백 없음**: 키가 없으면 기동 실패 (기존 키로 조용히 떨어지는 사고 차단) |
| D3 | 비공개 전략 코드 위치·버전관리 | `research/private/high52_quiet_breakout/live/` + **`research/private/` 를 별도 로컬 git 저장소로** | 실매매 코드가 git 이력 없이 돌면 되돌리기·백업이 안 된다. 상위 저장소는 이미 `research/private/` 를 무시한다 |
| D4 | 기존 미등록 매매 배치 | **이번에 레지스트리에 같이 편입**한다 (레지스트리·README 만, XML·러너 수정 없음) | §0 의 미등록 10개. "모든 배치는 이 프로젝트에서 관리"한다는 전제에 비해 레지스트리가 불완전하다 |

---

## 0. 확인된 사실 (2026-09-13 코드 기준)

| 항목 | 내용 | 위치 |
|---|---|---|
| broker 계좌 | 프로세스당 앱키·계좌 1개. `KIWOOM_ENV` 가 주소와 키 묶음을 같이 고른다 | `broker/kiwoom/config.py` `load_config` |
| env 우선순위 | `load_dotenv` 가 기존 값을 덮어쓰지 않는다 → 기동 시 프로세스 env 로 준 값이 `.env` 보다 우선 | `config.py:21-22` |
| 인스턴스별 상태 파일 | 토큰 캐시 `TOKEN_CACHE_PATH`, 노트 DB `NOTES_DB_PATH` 둘 다 env 로 경로를 바꿀 수 있다 | `config.py:81`, `broker/notes/db.py:19` |
| gitignore 구멍 | `broker/.gitignore` 가 `.token_cache.json`, `notes.db` 를 **정확한 파일명**으로만 무시한다 → 새 파일명은 커밋될 수 있다 | `broker/.gitignore` |
| broker 백그라운드 작업 | WS, 체결→노트 동기화, idea 알림 루프가 인스턴스마다 돈다. 새 인스턴스는 자기 노트 DB 만 본다 | `broker/main.py` `lifespan` |
| 전략 주문 경로 | 전략 주문은 전부 `POST /orders/strategy` 로 들어간다 → 계좌·전략 바인딩을 강제할 지점 | `broker/routers/orders.py:127` `place_strategy_order` |
| 주문 거래소 | 모든 주문이 `dmst_stex_tp = "SOR"` 고정. 신규 전략도 15:19 접속매매 주문이라 **그대로 쓴다** (동시호가 주문 없음 → KRX 지정 불필요) | `broker/kiwoom/orders.py:56` |
| 러너 → broker | 러너는 `--broker-url` 로 broker 를 고른다 (기본 `BROKER_API_URL` 또는 :8001). 기존 러너 ps1 은 :8001 을 명시한다 | `run_pullback_order.py:491`, `run-trading-order.ps1` |
| 일괄시세 | ka10095, 50종목씩 나눠 호출, 호출 간격 0.3초. 호출 한도는 앱키별이라 신규 인스턴스는 기존 전략과 한도를 나눠 쓰지 않는다 | `broker/kiwoom/quotes.py:25`, `client.py:31` |
| 레지스트리 미등록 작업 (XML 만 있음) | `trading-exit/order/verify`, `close-bet-order/exit/force-exit/verify`, `notes-sync`, `financial-indicators`, `orderbook-recorder` (10개) | `ops/scheduled-tasks/*.xml` vs registry |

---

## 1. 범위

**한다**

A. broker 다계좌 (공개 코드)
- `broker/kiwoom/config.py`: `KIWOOM_PROFILE` 이 있으면 `KIWOOM_<P>_<ENV>_*` 만 읽는다 (폴백 없음)
- `broker/main.py` `/health`: `profile` 필드 추가
- `broker/routers/orders.py` `place_strategy_order`: env `ALLOWED_ORDER_SOURCES` 가 있으면 목록 밖 source 는 422 로 거부. 없으면 지금처럼 동작 (:8001 불변)
- `broker/.gitignore`: `.token_cache*.json`, `notes*.db` 패턴으로 변경
- `broker/.env.example`: `KIWOOM_HIGH52_REAL_*` 예시 (값 없이)
- `scripts/restart_all_servers.ps1`: 인스턴스 표에 high52(:8002) 추가, 인스턴스별 env 주입, 헬스체크

B. 러너 공용 가드 (공개 코드)
- `etl/scripts/trading_batch_common.py`: `require_profile(broker_url, expected)` 추가. `/health` 의 profile 이 다르면 False → 러너는 주문 없이 종료하고 알린다

C. 비공개 전략 러너 (order / exit / verify) — 설계는 `PLAN_LIVE.md`

D. 배치 (공개 `ops/`)
- registry 에 `high52-order`, `high52-exit`, `high52-verify` 등록 (`windowsTask.taskPath = \new-etf_insight\`)
- `ops/scheduled-tasks/high52-*.xml`, `run-high52-*.ps1`, `ops/batches/high52-trading.md`
- `ops/batches/README.md` 전략·작업 매핑표에 high52 추가 (전략명과 작업 시각만 적는다)

E. (D4) 기존 미등록 10개 작업을 registry 에 편입

**안 한다**
- 한 broker 에서 여러 계좌 다루기 (D1)
- broker-web UI 와 Hermes MCP 에 2번째 계좌 연결 → 필요할 때 별도로
- `daily-trading-result` 통합 보고에 high52 넣기 → high52 는 자체 보고
- 공용 `_ADJ` 수정 (별도 미결 건)

---

## 2. 설계

### 2-1. 계좌 인스턴스

| 인스턴스 | 포트 | `KIWOOM_PROFILE` | `TOKEN_CACHE_PATH` | `NOTES_DB_PATH` | `ALLOWED_ORDER_SOURCES` | `MAX_ORDER_AMOUNT` |
|---|---|---|---|---|---|---|
| main (기존) | 8001 | 없음 | `.token_cache.json` | `notes.db` | 없음 (제한 없음, 기존 동작) | `.env` 값 |
| high52 | 8002 | `HIGH52` | `.token_cache.high52.json` | `notes.high52.db` | `high52_order,high52_exit` | 비공개 config 의 1건 주문 금액 기준 |

- 비밀값(앱키·시크릿·계좌번호)은 루트 `.env` 에만 둔다. 사용자가 직접 입력하고, 에이전트는 값을 읽거나 출력하지 않는다
- 기동 스크립트는 비밀이 아닌 값(위 표)만 프로세스 env 로 주입한다
- `KIWOOM_ENV` 는 공용이다 (둘 다 real). 신규 계좌 검증은 모의투자 대신 dry-run 으로 한다

### 2-2. 계좌·전략 바인딩: 두 방향 모두 막는다

| 사고 | 막는 곳 |
|---|---|
| 기존 전략(눌림목·종가베팅)이 실수로 :8002 를 부름 | broker :8002 의 `ALLOWED_ORDER_SOURCES` → 422 |
| high52 러너가 실수로 :8001(종가베팅 계좌)을 부름 | 러너의 `require_profile(url, "HIGH52")` → 주문 전 종료 |
| profile 키 누락으로 기존 키가 쓰임 | config 폴백 없음 → :8002 기동 실패, 헬스체크 실패 |

- 수동 주문(`POST /orders`, MCP)은 제한하지 않는다. 사용자가 직접 개입할 수 있어야 한다

### 2-3. high52 배치

| 작업 | 시각 | 비고 |
|---|---|---|
| `high52-exit` | 평일 08:50 기동 | 장중 청산 워커. 종료 시각은 PLAN_LIVE (order 작업과 주문 시간이 겹치지 않게) |
| `high52-order` | 평일 15:10 기동, 15:19 주문 | 신규 매수·교체·만기 매도 |
| `high52-verify` | 평일 16:00 | 체결 확정, 원장 갱신 |

- 선행 배치: `krx-ohlcv`(08:00), `financial-indicators`(19:00) — 기존 그대로
- 원장은 `etl/db/watchlist.sqlite3` 안 전략 전용 테이블 (구조는 PLAN_LIVE)

---

## 3. 단계

| 단계 | 내용 | 검증 |
|---|---|---|
| P0 (사용자) | 신규 계좌 앱키 발급, 허용 IP 등록, 루트 `.env` 에 `KIWOOM_HIGH52_REAL_*` 3줄 입력 | 입력 완료 알림만 받는다 (값 공유 금지) |
| P1 | 공개 코드 A·B + 테스트 | 단위 테스트 통과. 실행 전 확인을 받고 :8002 기동 → `/health` profile=HIGH52·신규 계좌, `/account/deposit` 조회, :8001 응답 불변 |
| P2 | 비공개 C (PLAN_LIVE 의 L 단계) | PLAN_LIVE 기준 |
| P3 | 배치 D 등록, dry-run | `Test-OpenClawBatchRegistry.ps1` 통과. dry-run 합격 기준은 PLAN_LIVE |
| P4 | 실주문 전환 (`--dry-run false`) | **사용자 승인 후** |
| P5 | (D4) 기존 10개 작업 registry 편입 | 레지스트리 검증 통과, 실제 작업 스케줄러 등록값과 대조 |

---

## 4. 요구사항 → 테스트

| 요구사항 | 테스트 |
|---|---|
| 신규 계좌는 종가베팅 계좌와 겹치지 않는다 | ① `ALLOWED_ORDER_SOURCES` 설정 인스턴스에 `close_bet`·`pullback_order` source 주문 → 422, 키움 호출 0 ② high52 러너가 profile 이 다른 broker 를 받으면 주문 0건으로 종료 ③ profile 설정 + `KIWOOM_HIGH52_*` 누락 → `load_config` RuntimeError (기존 `KIWOOM_REAL_*` 가 있어도) |
| 기존 :8001 동작 불변 | ④ profile 미설정 시 기존 env 이름 그대로 로드 (기존 `broker/test_orders.py` config 테스트 통과) ⑤ `ALLOWED_ORDER_SOURCES` 미설정 → 모든 source 허용 |
| 모든 배치는 이 프로젝트에서 관리 | ⑥ 신규 3작업의 registry·xml·runner 가 서로 가리키는지 레지스트리 검증 스크립트로 확인 |
| 전략 비공개 | ⑦ `git check-ignore` 로 live 모듈·config·`.token_cache.high52.json`·`notes.high52.db` 가 전부 무시되는지 확인 ⑧ 공개 파일 diff 에 비공개 config 의 값이 0건 (grep) |

전략 동작 테스트는 PLAN_LIVE §6.

---

## 5. 위험·한계

- broker 프로세스가 하나 늘어 PC 재부팅 뒤 `restart_all_servers.ps1` 에 의존하는 대상이 늘어난다
- D3 을 안 하면 비공개 실매매 코드의 이력과 백업이 없다
- 전략 쪽 위험은 PLAN_LIVE §7
