# 호가 스냅샷 수집기 — 개발 명세

작성: 2026-09-11
상태: 구현 대상 명세. 이 문서의 코드는 아직 구현되지 않았음.
대상 파일: `docs/SPEC_ORDERBOOK_SNAPSHOT_RECORDER.md`

## 1. 구현 범위

- 키움 기존 WebSocket 연결에서 `0D`를 구독한다.
- broker는 모든 0D를 SSE로 전달한다. 사전 샘플링은 하지 않는다.
- ETL 워커가 KST 벽시계 **1초 격자**로 10단 호가를 SQLite에 저장한다.
- 오전 `[08:45:00, 09:15:00)`, 오후 `[당일 후보 완료 확인 시각, 15:30:00)`.
- 오후 후보 확인은 15:00부터 시작한다. 마지막 저장 가능 격자는 15:29:59다.
- 거래소는 KRX. 실시간 체결 `0B`, 전체 장 수집, 틱 배치, 주문 실행은 구현 범위 밖이다.
- 기존 매매 설정, 주문·청산 로직, REST 유량 제한을 변경하지 않는다.
- 새 코드·설정은 아래 경로에 구현한다. 이 명세 작성 작업에서는 해당 실행 파일을 만들지 않는다.

## 2. 파일과 실행 환경

모든 상대 경로의 기준은 프로젝트 루트다.

| 파일 | 구현 내용 |
|---|---|
| `broker/kiwoom/ws/channels.py` | REAL payload에 item 추가 |
| `broker/kiwoom/ws/manager.py` | 수신 시각, 소켓 참조, 구독 목록, REG/REMOVE 응답 대기, 재접속 복구 |
| `broker/kiwoom/ws/event_bus.py` | 기존 비차단 발행 유지, 유실 계측 |
| `broker/routers/events.py` | SSE 채널 필터, 큐 상한 |
| `broker/routers/realtime.py` | 신규 GET/POST/DELETE 구독 제어 |
| `broker/main.py` | 라우터 등록 |
| `etl/scripts/run_orderbook_recorder.py` | 신규 워커·SSE 수신·1초 저장·생명주기 |
| `etl/scripts/orderbook_symbols.py` | 오전 대상 및 오후 완료 산출물 검증 |
| `etl/scripts/orderbook_store.py` | FID 정규화·스키마·배치 저장·실행 기록 |
| `etl/scripts/orderbook_recorder_config.py` | 설정 로더·검증 |
| `etl/scripts/orderbook_recorder.json` | 아래 설정 |
| `ops/scheduled-tasks/run-orderbook-recorder.ps1` | 신규 런처 |
| `ops/scheduled-tasks/orderbook-recorder.xml` | 신규 오전·오후 트리거 |
| `etl/tests/test_orderbook_*.py` | 설정·대상·격자·저장·워커 테스트 |
| `broker/test_orderbook_*.py` | WS·HTTP·SSE 테스트 |

- Python 선언: ETL/broker 모두 `>=3.12`. 각 프로젝트의 기존 venv와 lock을 사용한다.
- broker 기존 의존성: FastAPI, Pydantic 2, websockets, sse-starlette, httpx.
- ETL 기존 의존성: requests, python-dotenv. SQLite·threading·queue·msvcrt는 표준 라이브러리.
- ETL SSE 수신은 requests 스트리밍 전용 스레드, 격자/SQLite 쓰기는 메인 스레드로 구현한다.
- 수신 스레드는 공유 슬롯을 lock으로 갱신한다. DB 연결을 스레드 사이에 공유하지 않는다.
- broker 주소는 로컬 전용이다. API(:8000)로 라우팅하지 않는다.
- 입력 DB는 읽기 전용. `wl_sqlite.connect_ro`를 사용하고 조회 전에 파일 존재를 확인한다.
- 수집 DB writer는 `wl_sqlite.connect_rw`의 WAL·commit·close 규약을 사용한다.

## 3. 설정 계약

`etl/scripts/orderbook_recorder.json`:

```json
{
  "enabled": true,
  "broker_url": "http://127.0.0.1:8001",
  "windows": {
    "morning": {"start": "08:45:00", "end": "09:15:00"},
    "afternoon": {"start": "15:00:00", "end": "15:30:00"}
  },
  "snapshot_sec": 1,
  "max_stale_sec": 30,
  "venue": "KRX",
  "reg_timeout_sec": 5,
  "reg_retry": 3,
  "symbols": {
    "static": [],
    "max": 20,
    "max_lookback_days": 5
  },
  "scoring_result_path": "../../reports/recent_3day_probability_scores.json"
}
```

- 파일 없음·정의된 키 누락: 위 기본값. windows의 중첩 키도 해당 기본값으로 보완한다.
- 알 수 없는 키: 오류. 원안의 단일 `window_start/window_end`, `symbols.mode`는 사용하지 않는다.
- `snapshot_sec`는 이번 구현에서 정수 1만 허용한다.
- 시각은 HH:MM:SS, 각 start < end, 오전 종료 <= 오후 시작.
- max·reg_retry는 양의 정수, max_stale_sec는 양수, max_lookback_days는 0 이상 정수.
- reg_timeout_sec는 이번 구현에서 5만 허용한다. broker 제어 ACK timeout도 5초다.
- venue는 KRX만 허용. static은 중복 제거 가능한 6자리 종목코드 목록.
- broker_url은 로컬 broker 주소. 기존 프로젝트의 인증 설정이 있으면 같은 방식을 사용한다.
- scoring_result_path는 프로젝트 루트 기준 상대 경로 또는 절대 경로.
- 설정은 프로세스 시작 시 한 번 읽는다. 실행 중 변경은 다음 기동부터 적용한다.
- 잘못된 설정은 키와 사유를 로그에 남기고 종료 코드 1로 끝낸다.
- 이번 명세는 두 구간 설정을 위 형태로 구체화한다. 다른 config 형식을 추가하지 않는다.

## 4. 대상 종목과 오후 완료 확인

### 4.1 공통

- 함수 계약: `resolve_symbols(cfg, date, window) -> dict`.
- date는 KST YYYYMMDD, window는 morning 또는 afternoon.
- 반환: `status`(ready/wait/empty), `source_date`, `symbols`, `excluded`, `source_meta`.
- 합집합은 최초 출현 순서로 중복 제거 후 max개를 선택한다.
- SQL 목록은 ticker 오름차순으로 정렬한다. static은 설정 순서다.
- 제외 종목과 사유 `symbol_limit`을 실행기록 note에 남긴다.
- 빈 집합은 구독하지 않는다. 오전은 empty로 종료, 오후는 새 완료 산출물이 생길 수 있으므로 종료 시각까지 확인한다.

### 4.2 오전

입력: `etl/db/watchlist.sqlite3`.

미청산 목록:

```sql
SELECT DISTINCT ticker
FROM close_bet_orders
WHERE status = 'confirmed' AND sell_status IS NULL AND date < ?
ORDER BY ticker;
```

직전 후보일 및 후보 목록:

```sql
SELECT MAX(date) FROM llm_scores WHERE date < ?;
SELECT DISTINCT ticker FROM llm_scores WHERE date = ? ORDER BY ticker;
```

- 순서: **미청산 → 직전 후보 → static**.
- 직전 후보일과 수집일의 달력 날짜 차이가 max_lookback_days를 넘으면 후보 목록만 제외한다.
- 후보가 없거나 오래돼도 미청산·static은 수집한다. 사유를 note에 남긴다.
- source_date는 사용한 후보일, 후보를 사용하지 않았으면 NULL.
- 시총·거래대금·점수 필터를 추가하지 않는다.
- 미청산 조건은 `run_close_bet_exit.load_unsold_positions`의 SQL 조건과 일치시킨다.
  해당 함수는 스키마 변경을 수행하므로 수집기에서 직접 호출하지 않는다.

### 4.3 오후 완료 산출물

기존 생산자:
`research/watchlist_expected_return/watchlist_probability_langgraph.py`.

생산 순서:
`ensure_complete_scores → persist_scoring_results/commit → operational report → recent_3day_probability_scores.json`.

현재 운영 런처:
`ops/scheduled-tasks/run-watchlist-intraday.ps1`은
`--reports-dir`, `--output-dir`를 모두 workspace의 reports 디렉터리로 전달한다.
완료 확인에는 config의 scoring_result_path만 사용한다. 로그 문자열이나 스케줄러 상태를 파싱하지 않는다.

15:00부터 1초마다 파일을 확인하고 다음 조건을 모두 만족할 때만 ready로 처리한다.

1. UTF-8 JSON을 완전히 읽을 수 있다. 파일 없음·읽는 중 변경·불완전 JSON은 wait.
2. `db_write == true`.
3. `generated_at`은 timezone 포함 ISO 시각이며 KST 당일 15:00 이상, 현재 시각 이하.
4. `results`에서 `date == 수집일`인 항목이 정확히 하나다.
5. 해당 항목의 `candidate_count == scored_count == len(scores)`.
6. scores의 ticker가 유효하고 중복되지 않으며 각 score.date가 수집일이다.
7. 같은 읽기 트랜잭션에서 해당 ticker들이 llm_scores의 당일 행으로 모두 존재하고,
   DB score가 산출물의 probability_score와 일치한다.

- 파일 읽기 전후 크기·mtime이 다르면 다시 읽는다. 검증 통과한 파일 바이트의 SHA-256을 source_meta에 저장한다.
- 후보 목록은 완료 산출물 scores의 ticker를 사용한다. DB에 남아 있는 과거 재실행 잔여 행은 추가하지 않는다.
- 오후 순서: 당일 후보(ticker 오름차순) → static.
- source_date는 수집일. `source_meta.generated_at`과 실제 `ready_observed_at`을 별도로 기록한다.
- generated_at은 완료 산출물 시각이며 정확한 DB commit 시각으로 표기하지 않는다.
- 0건 완료도 유효하다. static이 있으면 static만 수집한다.
- 준비되지 않은 동안 0D를 등록하지 않는다. 15:30까지 미완료면 `candidate_not_ready`로 종료한다.
- 동일 hash는 다시 등록하지 않는다. 같은 날 새 유효 결과는 갱신 이력으로 남긴다.
- 구간 도중에는 신규 종목만 추가한다. 이미 수집한 종목은 구간 종료까지 유지한다.
  기존 등록 수를 포함해 max를 적용하고 초과 신규 종목은 excluded로 기록한다.
- 새 파일의 검증 실패는 기존 유효 구독을 해제하지 않는다.

## 5. broker 구독 제어 API

신규 라우터: `broker/routers/realtime.py`.
종목 입력은 접두사 없는 6자리 코드이며, broker가 KRX 접두사를 붙인다.
모든 제어 요청은 하나의 lock으로 직렬화한다. HTTP 처리 중 ws.recv()를 직접 호출하지 않는다.

| 메서드/경로 | 요청 | 성공 응답 |
|---|---|---|
| GET /realtime/orderbook | 없음 | 아래 상태 |
| POST /realtime/orderbook | `{"codes":["005930","000660"]}` | ACK 성공 후 아래 상태 |
| DELETE /realtime/orderbook | 없음 | 해제 완료 후 codes 빈 상태 |

상태 응답:

```json
{"connected": true, "venue": "KRX", "codes": ["000660", "005930"]}
```

- codes는 broker가 유지하는 확정 구독 목록, 정렬·중복 제거.
- POST는 추가/멱등 등록이다. 전체 교체가 아니다.
- 기존 코드만 전달되면 재등록하지 않고 성공 응답한다.
- 새 구독은 ACK 성공 후에만 확정 목록에 합친다.
- DELETE는 수집기 소유 group 2의 0D만 해제한다. 00/group 1은 유지한다.
- 소켓 미연결: HTTP 503. REG/REMOVE 거부: 502. reg_timeout_sec(기본 5초) 초과: 504.
- 입력 오류: 422. POST codes는 비어 있으면 오류.
- 오류 detail에 단계·키움 return_code/return_msg를 포함하되 token·계좌번호는 넣지 않는다.
- ACK 성공은 첫 REAL 수신을 의미하지 않는다. 첫 수신 시각은 ETL에서 별도 기록한다.
- ACK timeout은 결과 불명으로 취급한다. 지연 ACK를 다음 요청 성공으로 사용하지 않는다.
  timeout이 발생한 제어 세션은 종료하고 재접속 후 마지막 확정 목록을 복구한다.

REG:

```json
{
  "trnm": "REG",
  "grp_no": "2",
  "refresh": "1",
  "data": [{"item": ["KRX:005930"], "type": ["0D"]}]
}
```

REMOVE:

```json
{
  "trnm": "REMOVE",
  "grp_no": "2",
  "data": [{"item": ["KRX:005930"], "type": ["0D"]}]
}
```

- REMOVE item은 관리 중인 전체 0D 코드 목록. 빈 관리 목록이면 전송하지 않는다.
- refresh는 REG에서 **1 = 기존 유지**, 0 = 기존 해지. REMOVE에는 넣지 않는다.
- 현재 00 등록은 group 1, refresh 1, item [""]을 유지한다.
- 키움 응답 return_code 0 또는 "0"만 성공으로 처리한다.
- ACK에 요청 식별자가 없으므로 동시에 여러 REG/REMOVE 응답을 대기하지 않는다.
- receive loop가 LOGIN/PING/REG/REMOVE/REAL을 분기한다.
- LOGIN 후 등록 ACK를 기다리는 태스크와 receive loop를 분리하여 ACK 처리가 막히지 않게 한다.
- PING은 원문을 즉시 echo한다.
- 연결 단절 시 pending 제어 요청을 실패시키고 현재 소켓 참조를 무효화한다.
- 재접속 후 00과 확정 0D 목록을 복구한다. 복구 실패를 성공 connected로 보고하지 않는다.
- 공식 규약: [키움 실시간 명세](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom_docs/실시간시세.md).

## 6. REAL payload와 SSE

`parse_message`는 values의 기존 FID 키를 유지하고 item을 추가한다.
manager는 REAL 프레임을 받는 즉시 KST millisecond ISO 시각을 생성해 각 payload에 넣는다.

```json
{
  "channel": "0D",
  "payload": {
    "item": "KRX:005930",
    "_recv_ts": "2026-09-11T09:01:00.123+09:00",
    "21": "090100",
    "41": "+70000",
    "61": "1200"
  }
}
```

- SSE: `GET /events?ch=0D,system`.
- ch 생략: `system,00`. 기본 브라우저에 0D를 보내지 않는다.
- 허용 채널: system, 00, 0D. 중복 제거. 빈 값·알 수 없는 값·*는 HTTP 422.
- SSE 연결당 큐 maxsize=2000. EventBus publish는 put_nowait를 유지한다.
- 큐 포화 시 해당 이벤트를 버리고 채널별 유실 횟수를 계측한다. 키움 receive loop를 대기시키지 않는다.
- SSE data 필드는 위 event 객체 JSON. heartbeat는 15초, 주석 프레임으로 전송한다.
- ETL은 UTF-8 SSE를 빈 줄 단위로 조립한다. 여러 data: 줄을 개행으로 합쳐 JSON 파싱한다.
  heartbeat 주석·data 없는 프레임은 무시한다.
- ETL requests: connect timeout 5초, read timeout 30초. 실패 후 2초 간격으로 구간 종료까지 재연결.
- system connected/disconnected의 기존 payload.type 규약을 유지한다.
- 00 소비자의 기존 FID 값과 판정을 변경하지 않는다.

## 7. 구간별 워커 생명주기

추가 CLI 옵션 없이 `run_orderbook_recorder.py`를 실행한다.
현재 시각이 오전 종료 전이면 오전 구간, 그 이후 오후 종료 전이면 오후 구간을 선택한다.
구간 시작 전 기동은 시작까지 대기한다. 15:30 이후 기동은 구독 없이 종료한다.

1. 설정 로드. enabled=false면 즉시 종료.
2. `etl/db/orderbook.lock`의 첫 바이트를 msvcrt 비차단 잠금으로 획득한다.
   파일 크기 최소 1바이트, seek(0) 후 잠근다. 핸들은 프로세스 종료까지 유지한다.
3. 잠금 실패는 중복 기동으로 종료 코드 0. 실행기록과 DELETE를 수행하지 않는다.
4. 구간을 선택하고 orderbook_run을 1행 만든다.
5. 기존 `check_krx_trading_day.trading_day_status`로 휴장 여부를 확인한다.
   휴장이면 note에 사유를 기록하고 종료한다.
6. 시작까지 대기, 대상 목록 해결. 오후 미완료는 §4.3에 따라 대기한다.
7. SSE를 먼저 연결하고 HTTP 스트림 성립을 확인한다.
8. 새 프로세스의 최초 등록에 한해 GET → DELETE(기존 0D 정리) → POST.
   복구·장중 추가에는 DELETE를 실행하지 않는다.
9. REG는 최초 시도 + 최대 reg_retry회 재시도, 재시도 간격 2초.
   모든 HTTP 대기는 구간 종료 시각을 넘기지 않도록 취소한다.
10. 수집 목록을 확정하고 1초 격자 저장 시작. 등록 직후 이미 수신한 대상 데이터도 사용 가능하다.
11. 첫 대상의 첫 0D와 기존 GET /quotes/{code}/orderbook을 실행당 최대 1회 대조한다.
    불일치·REST 오류는 경고만 기록한다. 정기 REST 호가 폴링은 하지 않는다.
12. system disconnected 또는 SSE 단절 시 슬롯을 비우고 저장을 중지한다.
    connected/새 SSE 연결 후 POST로 현재 전체 목록을 확인·복구하고 새 수신부터 재개한다.
    연결 직후 sticky connected와 복구 요청은 중복 실행되지 않도록 합친다.
13. 종료 시각에 신규 등록·저장을 중단한다. finally에서 DELETE를 best effort로 실행한다.
14. 실행 통계와 종료 사유·ended_at을 commit하고 스트림·DB·잠금 핸들을 닫는다.
15. 구간 정상 종료·휴장·빈 목록·비활성은 코드 0, 설정·DB·등록 재시도 소진은 코드 1.
    수집 실패는 주문이나 다른 매매 프로세스를 종료시키지 않는다.

## 8. 1초 격자와 슬롯

- 격자 t는 KST 정수 초. 시작은 대상 등록 후 현재 시각 이상의 첫 정수 초.
- t < window_end만 기록한다. 신규 종목은 등록 전 격자를 생성하지 않는다.
- 원본 시각 r = payload._recv_ts. ETL 수신 시각으로 바꾸지 않는다.
- 동일 r의 여러 이벤트는 SSE 수신 순서상 마지막 값을 사용한다.
- 종목별 cur/nxt 슬롯과 다음 격자 t를 하나의 lock으로 보호한다.

수신:

```text
미등록/다른 거래소/잘못된 item 또는 시각 → 제외하고 카운트
이미 확정·종료한 격자 이하의 늦은 수신 → 과거 행을 수정하지 않고 late_events 카운트
r <= t → cur을 더 최신인 (r, 수신순서) 값으로 갱신
r >  t → nxt를 더 최신인 (r, 수신순서) 값으로 갱신
```

flush:

```text
실행 시각이 t+1 이상 → t 및 지나간 격자 생성을 건너뛰고 skipped_grids 기록
                    → 현재 이후 첫 격자로 진행; 그 격자 이하의 보유 값 중 최신값 사용
그 외 → cur이 있고 r <= t이고 0 <= t-r <= max_stale_sec이면 1행 후보
       → 같은 회차 모든 종목 행을 한 트랜잭션으로 INSERT OR REPLACE
다음 t로 진행; nxt.r <= 다음 t이면 cur로 옮기고 nxt를 비움
```

- lock 안에서 행 후보를 복사하고 슬롯/격자를 전진시킨다. SQLite 쓰기는 lock 밖에서 수행한다.
- flush 지연 회차를 저장 시각만 바꿔 소급 채우지 않는다.
- 데이터가 한 번도 없으면 행을 만들지 않는다.
- 갱신이 없어도 마지막 수신 후 30초 이하이면 반복 저장한다. 30초 초과는 행을 생략한다.
- 단절을 감지한 즉시 슬롯을 지운다. 30초까지 오래된 데이터를 유지하지 않는다.
- 값이 도착하지 않은 것과 정상 0 잔량을 구분한다.
- SSE 전달이 늦어 이미 확정한 격자의 마지막 이벤트를 놓친 경우 늦은 이벤트를 집계한다.
- ETL 수신 스레드와 DB 쓰기 사이에 원본 이벤트 무제한 큐를 추가하지 않는다.

## 9. 종목·FID 정규화

- 등록은 KRX:6자리. REAL item이 KRX:6자리이면 접두사를 제거한다.
- REAL item이 6자리이면 해당 세션에서 ACK된 KRX 등록 목록과 대조한다.
- A+6자리는 A 제거 후 목록과 대조한다. NXT/SOR 또는 _NX/_AL 응답은 KRX로 재라벨링하지 않는다.
- 가격은 abs(int(value)), 수량은 int(value). 정상 0은 0으로 보존한다.
- 누락·빈 문자열은 NULL. 숫자 파싱 실패는 해당 필드를 NULL로 저장하고 카운트한다.
- quote_tm은 FID 21 원문 HHmmss. 신선도 판정에는 사용하지 않는다.
- 예상체결 두 쌍을 서로 덮어쓰거나 합치지 않는다.

| FID | 컬럼 |
|---|---|
| 41~50 | ask1_px~ask10_px |
| 61~70 | ask1_qty~ask10_qty |
| 51~60 | bid1_px~bid10_px |
| 71~80 | bid1_qty~bid10_qty |
| 121 / 125 | ask_total_qty / bid_total_qty |
| 23 / 24 | exp_px / exp_qty |
| 291 / 292 | exp_px_ca / exp_qty_ca |

## 10. SQLite 스키마

경로: `etl/db/orderbook.sqlite3`. journal_mode=WAL.
PK 충돌은 INSERT OR REPLACE. 실행기록은 덮어쓰지 않는다.

```sql
CREATE TABLE IF NOT EXISTS orderbook_snapshot (
  date TEXT NOT NULL,
  ticker TEXT NOT NULL,
  venue TEXT NOT NULL,
  ts TEXT NOT NULL,
  recv_ts TEXT NOT NULL,
  quote_tm TEXT,
  ask1_px INTEGER,
  ask2_px INTEGER,
  ask3_px INTEGER,
  ask4_px INTEGER,
  ask5_px INTEGER,
  ask6_px INTEGER,
  ask7_px INTEGER,
  ask8_px INTEGER,
  ask9_px INTEGER,
  ask10_px INTEGER,
  ask1_qty INTEGER,
  ask2_qty INTEGER,
  ask3_qty INTEGER,
  ask4_qty INTEGER,
  ask5_qty INTEGER,
  ask6_qty INTEGER,
  ask7_qty INTEGER,
  ask8_qty INTEGER,
  ask9_qty INTEGER,
  ask10_qty INTEGER,
  bid1_px INTEGER,
  bid2_px INTEGER,
  bid3_px INTEGER,
  bid4_px INTEGER,
  bid5_px INTEGER,
  bid6_px INTEGER,
  bid7_px INTEGER,
  bid8_px INTEGER,
  bid9_px INTEGER,
  bid10_px INTEGER,
  bid1_qty INTEGER,
  bid2_qty INTEGER,
  bid3_qty INTEGER,
  bid4_qty INTEGER,
  bid5_qty INTEGER,
  bid6_qty INTEGER,
  bid7_qty INTEGER,
  bid8_qty INTEGER,
  bid9_qty INTEGER,
  bid10_qty INTEGER,
  ask_total_qty INTEGER,
  bid_total_qty INTEGER,
  exp_px INTEGER,
  exp_qty INTEGER,
  exp_px_ca INTEGER,
  exp_qty_ca INTEGER,
  PRIMARY KEY (date, ticker, venue, ts)
);

CREATE TABLE IF NOT EXISTS orderbook_run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  mode TEXT NOT NULL,
  source_date TEXT,
  venue TEXT,
  symbols TEXT NOT NULL,
  rows_written INTEGER,
  note TEXT
);
CREATE INDEX IF NOT EXISTS ix_orderbook_run_date ON orderbook_run(date);
```

- date/source_date: YYYYMMDD.
- ts: HH:MM:SS, recv_ts: HH:MM:SS.mmm. 수신 날짜는 date와 일치해야 한다.
- started_at/ended_at: KST offset을 포함한 millisecond ISO 시각.
- mode: morning / afternoon.
- symbols: ACK가 성공한 대상의 콤마 구분 목록. 추가 등록 후 갱신한다.
- venue: 첫 ACK 성공 전에는 NULL, 성공 후에는 KRX.
- rows_written: 해당 실행에서 commit한 행 수. 이전 실행의 누적값을 포함하지 않는다.
- DB 오류 시 해당 회차를 rollback하고 워커를 종료한다. DB 쓰기가 가능할 때만 note에 기록한다.
- 스키마 생성은 orderbook DB에만 한다. watchlist DB에 테이블·컬럼을 추가하지 않는다.
- 구현 후 `etl/scripts/dump_db_schema.py`로 스키마 카탈로그를 갱신한다.

note는 JSON 문자열이다. 별도 이력 테이블은 만들지 않는다.
시작·준비 완료·구독 변경·종료에 업데이트한다. 이벤트마다 DB에 로그를 쓰지 않는다.

first_recv_ts는 종목별 최초 수신 때 메모리에 표시하고 다음 격자 저장 트랜잭션에서 note에 반영한다.

```json
{
  "window": {"start": "08:45:00", "end": "09:15:00"},
  "reason": "window_end",
  "source_updates": [
    {
      "generated_at": null,
      "ready_observed_at": "2026-09-11T08:45:00.000+09:00",
      "sha256": null,
      "selected": ["005930"],
      "excluded": []
    }
  ],
  "subscriptions": [
    {
      "ticker": "005930",
      "registered_at": "2026-09-11T08:45:00.200+09:00",
      "first_recv_ts": "2026-09-11T08:45:00.250+09:00",
      "ended_at": "2026-09-11T09:15:00.000+09:00",
      "end_reason": "window_end"
    }
  ],
  "stats": {
    "skipped_grids": 0,
    "late_events": 0,
    "invalid_events": 0,
    "invalid_fields": 0
  }
}
```

- excluded 요소: `{"ticker":"000660","reason":"symbol_limit"}`.
- 재연결마다 subscriptions에 새 구독 구간을 추가한다. 첫 수신이 없으면 first_recv_ts=NULL.
- 오전 generated_at/sha256은 NULL. 오후는 완료 산출물 값과 hash.
- 진행 중 reason·종료 시각은 NULL. 비정상 프로세스 사망은 ended_at=NULL로 남을 수 있다.
- DB가 쓰기 불가이면 파일 로그에 오류를 남긴다. 모든 실패가 DB에 기록된다고 보장하지 않는다.

## 11. 스케줄과 로그

- 신규 작업: `\OpenClaw\orderbook-recorder`.
- 평일 트리거 두 개: 08:44, 14:59. 하나의 XML과 런처를 사용한다.
- MultipleInstancesPolicy=IgnoreNew, 실행 제한 1시간.
- 런처 working directory는 etl, 실행은 `.venv\Scripts\python.exe scripts\run_orderbook_recorder.py`.
- PowerShell 파일은 UTF-8 BOM. Python 출력은 UTF-8.
- 로그: `etl/logs/orderbook-recorder-YYYYMMDD.log`.
- 스케줄 등록 자체는 구현·검증 후 수행한다.
- 기존 매매 작업을 변경하지 않는다. 작업 활성 상태는 실제 Task Scheduler에서 별도 확인한다.
- enabled=false는 다음 기동을 막는다. 이미 실행 중인 프로세스에는 자동 적용되지 않는다.
- 강제 종료 후 broker에 남은 구독은 다음 실행의 최초 DELETE에서 정리된다.

## 12. 검증 조건

| ID | 입력/행동 | 기대 결과 |
|---|---|---|
| T01 | 2종목 REAL | item 보존, 별도 종목 행 |
| T02 | 40개 FID에 서로 다른 값 | 10단 가격·수량 매핑 일치 |
| T03 | 음수 가격·0·빈 값 | 절댓값 가격, 0 유지, 빈 값 NULL |
| T04 | REG/REMOVE 전문 | group 2, REG refresh 1, 기존 00 유지 |
| T05 | 00 payload 확장 | 기존 체결 처리 테스트 통과 |
| T06 | ch 생략/0D,system/* | 기본에 0D 없음, 지정 필터 작동, *는 422 |
| T07 | 큐 2001건 | maxsize 2000, 비차단 유실 계측 |
| T08 | 1초에 100개 이벤트 | 종목당 격자 1행 |
| T09 | t-0.1 이벤트 후 t 이후 20개 | flush(t)는 t-0.1 값, flush(t+1)는 다음 값 |
| T10 | flush가 한 격자 이상 지연 | 소급 행 없음, skipped_grids 증가 |
| T11 | 30초/30초 초과 신선도 | 경계 포함 저장/초과 미저장 |
| T12 | SSE가 오래된 이벤트 전달 | ETL 수신 시각으로 신선도 갱신 안 함 |
| T13 | 데이터 0건·단절 | 미수신 행 없음, 단절 슬롯 제거 |
| T14 | 08:45:01.2에 준비 완료 | 첫 격자 08:45:02 |
| T15 | 오전/오후 종료 | 09:15:00/15:30:00 행 없음 |
| T16 | 오전 후보 오래됨+미청산 있음 | 미청산 수집, 오래된 후보만 제외 |
| T17 | 오전 합집합 max 초과 | 미청산 우선, 제외 이력 |
| T18 | 오후 파일 없음·부분 JSON·전날 결과 | 미등록 wait |
| T19 | 오후 db_write=false·점수 불일치·중복 ticker | 미등록 wait |
| T20 | 당일 완전 결과+DB 일치 | 후보+static 등록, 준비/등록/첫 수신 시각 기록 |
| T21 | 새 완료 결과에 신규 종목 | 기존 유지, 신규만 POST, REMOVE 없음 |
| T22 | ACK 거부/timeout | HTTP 오류, 확정 목록에 추가 안 함, timeout 후 ACK 혼동 없음 |
| T23 | reconnect | 00·확정 0D 복구, 과거 슬롯 재사용 없음 |
| T24 | broker 재시작 | ETL SSE 재연결+전체 목록 POST |
| T25 | ETL 중복 기동·강제 종료 후 기동 | 중복 차단, OS 잠금 자동 해제 |
| T26 | 동일 snapshot PK 두 번 삽입 | 1행, 실행기록은 실행별 별도 행 |
| T27 | 일부 배치 INSERT 실패 | 전체 회차 rollback, rows_written 증가 없음 |
| T28 | 빈 후보 완료/종료까지 미완료 | static 규칙 적용/미완료 사유 기록 |
| T29 | 15:30 이후·휴장·disabled | 신규 구독 없음 |
| T30 | 수집기 장애 | 주문 실행·매매 프로세스 종료 호출 없음 |

- broker 테스트는 broker venv, ETL 테스트는 etl venv에서 실행한다.
- 테스트 대상 모듈명은 위 test_orderbook_* 경로에 맞춰 unittest discovery로 수집한다.

프로젝트 루트에서 실행:

```powershell
$env:PYTHONPATH = "broker"
& .\broker\.venv\Scripts\python.exe -m unittest discover -s broker -p "test_orderbook_*.py"
$env:PYTHONPATH = "etl"
& .\etl\.venv\Scripts\python.exe -m unittest discover -s etl/tests -p "test_orderbook_*.py"
```

- 위 테스트 외에 기존 00 체결 처리·SSE 소비자 회귀 테스트를 실행한다.
- 실측은 약 5종목에서 시작해 운영 예정 규모를 별도로 확인한다.
- 기록: 종목별/전체 초당 REAL 건수·프레임 바이트, 루프 지연 p95/p99/max,
  SSE 큐 최대 깊이·유실, 수신→ETL 지연, 00 수신→내부 반영 지연.
- 거래소·서버 모드, 등록 코드·ACK·첫 수신, 해제·재접속 결과를 기록한다.
- 등록 성공만으로 모든 종목 수신이나 등록 최대 한도를 확인했다고 표시하지 않는다.
- 위 지표의 수치 합격 기준은 실측 전 운영 기준으로 별도 설정한다. 기능 구현 완료와
  실운영 부하 검증 완료를 구분한다.
