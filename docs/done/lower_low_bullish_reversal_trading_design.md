# lower_low_bullish_reversal 실매매 설계

## 1. 목적

- watchlist 편입 종목에서 `lower_low_bullish_reversal` 신호를 찾는다.
- 신호가 발생한 종목을 15:19에 시장가 매수한다.
- 실제 매수 체결가 대비 설정된 TP/SL과 최대 보유 거래일에 따라 청산한다.
- 이 전략의 주문임을 기존 투자노트 필드에 기록한다.
- 기존 종가베팅 주문·청산과 포지션을 섞지 않는다.

## 2. 확정된 운영값

| 항목 | 값 |
| --- | --- |
| 전략 ID | `lower_low_bullish_reversal` |
| 주문 시각 | 15:19:00~15:20:00 |
| 종목당 예산 | `pullback.json`의 `budget_per_stock`(초깃값 300,000원) |
| 일일 신규 매수 상한 | `pullback.json`의 `max_new_positions`(초깃값 3종목) |
| 익절 | `pullback.json`의 `tp`(초깃값 +3%) |
| 손절 | `pullback.json`의 `sl`(초깃값 -3%) |
| 가격 판단 | 최우선 매수호가(`buy_bid`) |
| 강제청산 | `pullback.json`의 `max_hold_days` 번째 거래일 15:19(초깃값 3) |
| 중복 매수 | 두 전략 중 하나라도 보유 중이면 제외 |
| 주문 환경 | 기존 `KIWOOM_ENV` 사용, 개발·테스트는 모의계좌와 mock만 사용 |

## 3. 신호 정의

### 3.1 후보 기간

- watchlist 편입 다음 거래일부터 최대 5거래일까지만 감시한다.
- 각 감시일은 바로 전 거래일 저가와 비교한다.
- 기간 계산은 달력 날짜가 아니라 KRX OHLCV에 존재하는 거래일로 한다.

### 3.2 15:19 매수 신호

아래 조건을 모두 만족해야 한다.

1. 당일 저가가 직전 거래일 저가보다 낮다.
2. 15:19 현재가가 당일 시가보다 높다.
3. D+1~D+5 중 위 두 조건을 동시에 처음 만족한 날이다.
4. 기존 종가베팅 또는 이 전략으로 해당 종목을 보유하고 있지 않다.
5. 같은 watchlist 편입 건으로 이미 주문하지 않았다.

lower-low가 발생했더라도 15:19 기준 양봉이 아니면 그날은 주문하지 않고, 다음 거래일에
직전 거래일 저가를 새 기준으로 다시 평가한다. D+5까지 매일 반복하며 `저가 이탈 + 양봉`
을 동시에 처음 만족한 날만 진입한다. 이는 phase7의 `lower_low_bullish_reversal`과
`avg_wait_days=2.8986`을 산출한 탐색 방식에 맞춘 것이다.

phase8·9의 분봉 변환 코드는 최초 lower-low 날짜를 먼저 고정해 그날만 `close_confirm`을
검사하므로 phase7과 의미가 달라졌다. 실매매 정의는 원래 phase7을 따르며, 15:19 근사까지
포함한 동일 조건의 추가 백테스트가 필요하다.

### 3.3 백테스트와 실매매의 차이

- 연구는 15:30 마지막 1분봉으로 양봉 마감을 확정하고 그 종가에 체결됐다고 가정했다.
- 실매매는 15:19 현재가로 양봉 상태를 판단해 종가 동시호가 시장가 주문을 낸다.
- 실매매의 당일 저가는 15:19까지의 장중 저가다. 15:19 이후 신저가가 발생하면 연구의
  확정 일저가 조건과 달라질 수 있다.
- 15:19 이후 가격 하락으로 최종 음봉이 되거나 체결가가 달라질 수 있다.
- 연구 청산은 일봉·분봉 OHLC의 TP/SL 터치와 동일 봉 SL 우선 규칙을 사용했지만,
  실매매는 3초 간격 최우선 매수호가만 관측한다. 관측 사이에 TP와 SL을 모두 통과한
  순서는 복원할 수 없으므로 실제 먼저 관측된 조건으로 청산한다.
- 실매매 성과는 반드시 별도로 집계하며 연구 성과와 합치지 않는다.

## 4. 기존 코드 재사용 범위

| 기능 | 재사용 기준 |
| --- | --- |
| 주문 시간창 | `run_close_bet.py`의 15:19~15:20 가드 |
| 현재가·예수금 조회 | 기존 broker REST 호출 함수 패턴 |
| 시장가 주문 | broker `POST /orders` 단일 경로 |
| 체결 대조 | `run_verify.py`의 주문번호 정규화·체결 조회 패턴 |
| 청산 감시 | `run_close_bet_exit.py`의 3초 폴링·`buy_bid` 판단 패턴 |
| 잔고 대조 | 기존 잔고·미체결·DB 3중 대조 패턴 |
| 알림 | 기존 `send_discord` 호출 패턴 |

기존 close-bet 파일을 직접 확장하지 않는다. 전략별 상태와 강제청산일이 달라 회귀 위험이
크므로 공통 주문 경로만 호출하고 실행 파일과 테이블은 분리한다.

## 5. 파일 구성

| 파일 | 책임 |
| --- | --- |
| `etl/scripts/run_pullback_order.py` | 후보 선정, 15:19 신호 확인, 예산 계산, 시장가 매수 |
| `etl/scripts/run_pullback_verify.py` | 매수·매도 체결 대조, 체결가·수량 확정, 투자노트 기록 |
| `etl/scripts/run_pullback_exit.py` | 최우선 매수호가 기준 TP/SL 감시, 만기 강제청산 |
| `etl/scripts/pullback.json` | 눌림목 전략 운영값의 단일 저장 원본 |
| `etl/scripts/pullback_config.py` | JSON 로드와 배치 실행 전 값 검증 |
| `etl/tests/test_pullback_order.py` | 신호·중복·예산·주문 시간 테스트 |
| `etl/tests/test_pullback_verify.py` | 체결 대조·투자노트 멱등성 테스트 |
| `etl/tests/test_pullback_exit.py` | TP/SL·거래일 만기·재주문 방지 테스트 |
| `broker-web/lib/pullback-config.ts` | 웹 설정 타입·검증·JSON 읽기·쓰기 |
| `broker-web/app/api/pullback-config/route.ts` | 설정 GET·PUT API |
| `broker-web/components/admin/pullback-panel.tsx` | `/admin/settings`의 `눌림목 매매` 탭 UI |
| `ops/scheduled-tasks/*.xml` | 매수·검증·청산 Windows 작업 정의 |
| `ops/scheduled-tasks/run-pullback-*.ps1` | 작업 실행과 UTF-8 로그 저장 |

## 6. 전략 설정

### 6.1 JSON 스키마

`etl/scripts/pullback.json`을 배치와 관리 화면이 공유하는 단일 원본으로 사용한다.

```json
{
  "budget_per_stock": 300000,
  "max_new_positions": 3,
  "tp": 0.03,
  "sl": 0.03,
  "max_wait_days": 5,
  "max_hold_days": 3
}
```

| 키 | 의미 | 검증 |
| --- | --- | --- |
| `budget_per_stock` | 종목당 주문 예산 | 1원 이상 정수 |
| `max_new_positions` | 하루 신규 매수 상한 | 1~3 정수 |
| `tp` | 실제 체결가 기준 익절률 | 0 초과 1 이하 |
| `sl` | 실제 체결가 기준 손절률 | 0 초과 1 이하 |
| `max_wait_days` | watchlist 편입 후 감시 거래일 | 1~5 정수 |
| `max_hold_days` | 매수 후 강제청산까지 거래일 | 1~5 정수 |

- `budget_per_stock`을 포함한 운영값을 주문 코드에 하드코딩하지 않는다.
- JSON 파일이 없거나 필수 키가 누락되거나 값이 유효하지 않으면 배치를 즉시 중단한다.
- 잘못된 설정으로 실주문하지 않도록 Python 로더와 TypeScript 저장 API에서 같은 범위를
  각각 검증한다.
- 웹 저장 시 기존 파일을 `pullback.json.bak`으로 백업한 뒤 UTF-8 JSON으로 저장한다.
- 배치는 실행 시작 시 한 번 설정을 읽고, 실행 중 파일이 바뀌어도 해당 실행에는 반영하지 않는다.

### 6.2 관리 화면

- `/admin/settings`에 기존 `종가베팅`과 나란히 `눌림목 매매` 탭을 추가한다.
- 탭은 `GET /api/pullback-config`로 현재 값을 읽고 `PUT /api/pullback-config`로 저장한다.
- 금액은 원 단위 정수, TP·SL은 화면에서 %로 표시하고 JSON에는 0~1 소수로 저장한다.
- 감시일과 보유일은 거래일 단위 정수로 표시한다.
- 저장 성공·실패 메시지와 입력 중 변경사항 폐기 경고는 기존 종가베팅 패널 패턴을 따른다.
- 설정 화면은 JSON만 변경하며 주문이나 배치를 직접 실행하지 않는다.

## 7. 데이터 모델

`watchlist.sqlite3`에 기존 테이블과 분리된 `pullback_orders`를 둔다.

```sql
CREATE TABLE IF NOT EXISTS pullback_orders (
    watchlist_date TEXT NOT NULL,
    signal_date    TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    strategy       TEXT NOT NULL,
    prior_low      INTEGER NOT NULL,
    day_open       INTEGER NOT NULL,
    signal_price   INTEGER NOT NULL,
    qty            INTEGER NOT NULL,
    status         TEXT NOT NULL,
    buy_order_no   TEXT,
    buy_price      INTEGER,
    buy_qty        INTEGER,
    bought_at      TEXT,
    remaining_hold_days INTEGER,
    last_hold_count_date TEXT,
    expiry_date    TEXT,
    sell_order_no  TEXT,
    sell_status    TEXT,
    sell_price     INTEGER,
    sell_qty       INTEGER,
    sold_at        TEXT,
    exit_reason    TEXT,
    pnl_pct        REAL,
    note_uid       TEXT,
    message        TEXT,
    raw            TEXT,
    created_at     TEXT NOT NULL,
    verified_at    TEXT,
    PRIMARY KEY (watchlist_date, ticker)
);
```

### 7.1 상태값

```text
candidate -> submitted -> confirmed -> sell_ordered -> closed
          -> dry_run
          -> skipped
          -> rejected / failed / unconfirmed
```

- `(watchlist_date, ticker)` 기본키로 동일 편입 건의 재주문을 막는다.
- `buy_order_no`, `sell_order_no`로 체결 대조를 멱등 처리한다.
- 주문 성공 후 프로세스가 종료돼도 DB 상태와 키움 체결내역으로 복구한다.
- 매수 체결 시 `remaining_hold_days=max_hold_days`를 저장한다. 실제 장이 열린 거래일마다
  감소시키고 0이 되는 날 `expiry_date`를 확정한다. 미래 휴장일을 평일로 추정하지 않는다.
- `last_hold_count_date`로 같은 날 워커 재시작 시 중복 감소를 막는다. 당일
  `intraday_ranking`이 정상 생성된 경우에만 장이 열린 거래일로 인정한다.
- 단순히 당일 신호가 없다는 이유로 `pullback_orders` 행을 만들지 않는다. 다음 거래일에
  같은 watchlist 편입 건을 다시 평가해야 하기 때문이다.
- `skipped`는 신호가 확정됐지만 수량 0, 중복 포지션 등으로 해당 편입 건의 주문을
  최종 포기한 경우에만 저장하며 이후 재평가를 차단한다.
- D+5까지 신호가 없으면 행을 만들지 않고 감시 대상에서 자연스럽게 제외한다.

## 8. 투자노트 기록

기존 투자노트 스키마를 유지한다.

| 필드 | 기록값 |
| --- | --- |
| `symbol` | 종목코드 |
| `target_price` | 실제 체결가 기준 설정 TP를 유효 호가단위로 내림한 값 |
| `holding_period` | `매수 다음 max_hold_days 번째 거래일까지` |
| `buy_reason` | `[lower_low_bullish_reversal] 전일 저가 이탈 후 15:19 양봉 반전` |
| `memo` | watchlist일, 신호일, 전일 저가, 당일 시가, 신호가격, 주문번호 |

- 주문 제출만으로 노트를 만들지 않는다.
- 매수 체결이 확인된 뒤 노트를 생성·갱신하고 `note_uid`를 저장한다.
- 같은 `buy_order_no`를 다시 검증해도 노트 문구와 이벤트를 중복 생성하지 않는다.
- 기존 열린 노트가 있는 종목은 매수 대상에서 제외한다.
- 과거에 닫힌 노트가 있으면 현재 자동연결 정책에 따라 같은 종목 노트를 재사용하되,
  전략명과 주문번호를 memo에 추가한다.
- 실제 매수·매도 이벤트는 기존 `notes/sync-trades`의 주문번호 기반 동기화를 사용한다.
- 투자노트의 보유기간은 설정값을 기록하며, 정확한 만기 날짜는 거래일 카운터가 0이 된
  날 memo와 `pullback_orders.expiry_date`에 추가한다.

## 9. 주문 흐름

### 9.1 매수

```text
watchlist D+1~D+5 후보
  -> 직전 거래일 OHLCV 조회
  -> 15:19 현재 시세 조회
  -> lower-low + 현재 양봉 확인
  -> 기존 두 전략 미청산 포지션 확인
  -> 가용예수금 확인
  -> min(budget_per_stock, 남은 예수금 배분) // 현재가
  -> qty 0이면 skipped
  -> broker POST /orders, source=pullback_order
  -> pullback_orders에 주문번호와 submitted 저장
```

- D+1~D+5의 각 날짜에 과거 일봉을 순서대로 검사하고, 이전 날짜에 조건을 동시에 만족한
  기록이 없는 편입 건만 오늘 신호 후보가 될 수 있다.
- 후보가 `max_new_positions`를 넘으면 `watchlist_date`, `ticker` 오름차순으로
  결정적으로 설정값만큼 선택한다.
- 별도의 성과 점수가 없으므로 임의 점수나 시가총액 순위를 추가하지 않는다.
- 주문별 금액은 broker의 `MAX_ORDER_AMOUNT` 가드를 반드시 통과한다.

### 9.2 매도

```text
confirmed 포지션 + 계좌 잔고 대조
  -> 3초마다 최우선 매수호가 조회
  -> buy_bid / buy_price - 1 계산
  -> +tp 이상: tp 시장가 매도
  -> -sl 이하: sl 시장가 매도
  -> max_hold_days 만기일 15:19: forced 시장가 매도
  -> 미체결 조회로 중복 주문 차단
  -> 체결 확인 후 closed 저장
```

- TP와 SL은 실제 매수 체결가를 기준으로 계산한다.
- 호가가 기준선을 건너뛰면 실제 체결수익은 설정 TP보다 높거나 설정 SL보다 낮을 수 있다.
- 매도 판단은 매도호가가 아니라 즉시 매도 가능한 최우선 매수호가를 사용한다.
- 연구의 동일 봉 TP·SL 동시도달 시 SL 우선 규칙은 3초 호가 폴링에 그대로 적용할 수
  없다. 실매매에서는 먼저 관측된 조건을 사용하고 연구 대비 차이를 별도 집계한다.
- 보유수량과 DB 수량 중 작은 수량만 매도해 다른 포지션 물량 침범을 막는다.

## 10. 중복·충돌 방지

- `close_bet_orders`에서 매수 체결 후 미청산인 종목은 pullback 매수에서 제외한다.
- `pullback_orders`에서 미청산인 종목은 기존 close-bet 후보에서도 제외해야 한다.
- 첫 배포 단계에서 기존 close-bet 코드에 허용되는 변경은 위 조회 조건 추가뿐이다.
- 두 전략이 같은 종목을 동시에 보유하는 상태는 허용하지 않는다.
- 매수·매도 미체결 주문이 있으면 같은 방향 주문을 다시 내지 않는다.

## 11. 스케줄

| 작업 | 시각 | 역할 |
| --- | --- | --- |
| `trading-exit` | 평일 08:50 | 눌림목 청산 워커 시작 |
| `trading-order` | 평일 15:19 | 눌림목 신호 확인 및 신규 매수 |
| `trading-verify` | 평일 16:00 | 눌림목 체결 대조 및 노트 기록 |

- 청산 워커는 15:25에 종료한다.
- 만기 강제청산은 워커 내부 독립 조건으로 15:19에 실행한다.
- 기존 종가배팅 실행 작업은 비활성화하고 눌림목 전략만 실행한다.
- OpenClaw 보고 작업은 주문을 실행하거나 재시도하지 않는다.

## 12. 단계별 TDD 개발 계획

### 1단계: 신호와 거래일 계산

테스트를 먼저 작성한다.

- D+1~D+5만 후보가 되는지
- 휴일을 제외하고 거래일을 계산하는지
- 당일 저가 `<` 전일 저가, 현재가 `>` 시가를 모두 요구하는지
- lower-low가 음봉이면 다음 거래일에 직전 저가를 갱신해 다시 검사하는지
- lower-low와 양봉을 서로 다른 날짜에서 조합하지 않고 같은 날 동시에 요구하는지
- D+5 이후 후보가 제거되는지

테스트 실패를 확인한 뒤 최소 신호 함수만 구현한다.

완료 기준:

- 외부 API 없이 고정 OHLCV fixture로 모든 경계 테스트 통과
- 연구 정의와 실매매 15:19 근사의 차이가 테스트명에 드러남

### 2단계: 설정·주문 상태와 후보 중복 방지

테스트를 먼저 작성한다.

- `pullback.json` 정상 로드와 모든 필수 키 검증
- 설정 파일 없음·키 누락·범위 오류 시 fail-closed
- Python과 TypeScript 검증 범위 일치
- `/admin/settings`의 `눌림목 매매` 탭 표시와 GET·PUT API
- 저장 실패 시 기존 JSON 파일 불변, 성공 시 `.bak` 생성
- `pullback_orders` DDL과 마이그레이션 멱등성
- 동일 `(watchlist_date, ticker)` 재실행 시 주문 1회
- 기존 close-bet 미청산 종목 제외
- pullback 미청산 종목이 기존 close-bet에서도 제외
- `max_new_positions` 상한과 `budget_per_stock` 기준 수량 계산
- 예수금 부족·현재가가 종목당 예산을 초과할 때 안전하게 skip

완료 기준:

- 임시 SQLite와 mock broker만 사용한 테스트 통과
- 임시 JSON 경로를 사용해 실제 `pullback.json`을 변경하지 않음
- 실제 주문 HTTP 호출 없음

### 3단계: 15:19 매수 배치

테스트를 먼저 작성한다.

- 15:19 이전과 15:20 이후 주문 차단
- `dry_run=true`에서 주문 호출 0회
- 모의 응답의 주문번호와 상태 저장
- 종목 하나 실패가 다른 종목 주문을 막지 않음
- broker 422 거부와 네트워크 실패 상태 구분

완료 기준:

- `run_pullback_order.py` 통합 테스트 통과
- 모의계좌 수동 실행은 별도 승인 후 1회 수행

### 4단계: 체결검증과 투자노트

테스트를 먼저 작성한다.

- 주문번호 앞자리 0 정규화 후 체결 매칭
- 부분체결에서 누적 체결수량·평균 체결가 반영
- 체결 확인 전 투자노트 미생성
- 체결 확인 후 전략명·watchlist일·신호일 기록
- 같은 주문번호 재검증 시 노트와 이벤트 중복 없음
- 투자노트 API 실패 시 주문 상태를 훼손하지 않고 재시도 가능

완료 기준:

- `run_pullback_verify.py` 테스트 통과
- `notes/sync-trades`와 함께 실행해도 주문번호당 이벤트 1개 유지

### 5단계: TP/SL·만기 청산

테스트를 먼저 작성한다.

- 최우선 매수호가가 설정 TP에 도달하면 `tp`
- 최우선 매수호가가 설정 SL에 도달하면 `sl`
- 경계 미도달 시 주문 없음
- 매수 다음 `max_hold_days` 번째 거래일 15:19에 `forced`
- 실제 장이 열린 날에만 `remaining_hold_days`를 감소시키는지
- 주말·휴일이 보유일 계산에서 제외
- 미체결 매도나 `sell_ordered` 상태에서 재주문 없음
- 잔고보다 많은 수량을 매도하지 않음
- 갭상승·갭하락은 실제 호가로 판단

완료 기준:

- `run_pullback_exit.py` 단위·통합 테스트 통과
- 기존 close-bet exit 테스트 전체 통과

### 6단계: 스케줄과 모의계좌 검증

테스트를 먼저 작성한다.

- PowerShell wrapper 인자와 Python CLI 계약 일치
- 모든 신규 CLI의 기본값은 `dry_run=true`
- XML 작업 시각과 문서 시각 일치
- 스케줄이 눌림목 실행 파일만 호출함

검증 순서:

1. fixture 기반 전체 테스트
2. 실제 시세를 사용하는 dry-run
3. 모의계좌에서 `pullback.json` 설정값으로 주문
4. 체결·투자노트·청산 상태 대조
5. 최소 5거래일 관찰 후 실계좌 전환 여부 별도 결정

## 13. 전체 완료 조건

- 신규 및 기존 close-bet 테스트가 모두 통과한다.
- 배치와 관리 화면이 동일한 `pullback.json`을 사용하며 금액 하드코딩이 없다.
- 실주문 경로는 broker `POST /orders`만 사용한다.
- 신규 주문은 기본 dry-run이며 모의계좌 외 실제 주문을 테스트하지 않는다.
- 주문·체결·청산·투자노트가 주문번호 기준으로 멱등이다.
- 두 전략이 같은 종목을 동시에 보유하지 않는다.
- 15:19 신호값과 실제 체결값을 분리 저장해 실매매 오차를 측정할 수 있다.
- 연구 결과와 실매매 결과를 별도로 보고한다.
