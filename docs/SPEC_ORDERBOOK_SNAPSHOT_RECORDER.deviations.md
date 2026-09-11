# 호가 수집기 — SPEC 해석·변경 기록

SPEC 원문(`SPEC_ORDERBOOK_SNAPSHOT_RECORDER.md`)과 다르게 구현했거나, SPEC 에 없어서 정한 부분과 그 근거를 모은다.
SPEC 원문은 수정하지 않는다. 단계가 진행되면 이 문서에 이어서 적는다.

## 1. SPEC 과 다르게 구현한 부분

### 1.1 종목코드는 영숫자 6자리 (§3, §5, §9)

- SPEC: "6자리 종목코드", "접두사 없는 6자리 코드".
- 구현: `^[0-9A-Z]{6}$`. 앞에 `A` 가 붙은 코드는 7자리일 때만 `A` 를 뗀다.
- 근거: KRX 신규 코드는 영문자를 포함한다. 예: `0197V0` 은 2026-09-10 `llm_scores` 후보다.
  숫자만 받으면 이런 종목이 수집에서 빠진다.
- 결정: 사용자 선택 (2026-09-11).

### 1.2 연결이 끊긴 동안의 DELETE 는 목록만 비우고 200 (§5)

- SPEC: "소켓 미연결: HTTP 503".
- 구현: DELETE 를 받았을 때 키움 연결이 없으면 broker 의 확정 목록만 비우고 200 을 준다.
  REMOVE 도중 연결이 끊기거나(503) ACK 가 5초 안에 오지 않아도(504) 목록을 비운다.
  키움이 해제를 거부(502)했을 때만 목록을 남긴다.
- 근거:
  - 키움 실시간 등록은 소켓에 묶여 있다. 소켓이 끊기면 서버 쪽 등록도 사라진다.
  - SPEC 대로 503 을 주면 확정 목록이 남는다. broker 는 재접속 때 그 목록을 복구한다(§5 "재접속 후 00과 확정 0D 목록을 복구").
  - 예: 09:15 수집기 종료 직전 DELETE 순간에 연결이 끊겨 있으면, 이미 꺼진 수집기의 20종목 호가를
    broker 가 하루 종일 다시 받는다. §11 의 "다음 실행의 최초 DELETE 에서 정리" 까지 부하가 남는다.
  - ACK 시간초과(504)는 세션을 끊으므로 같은 이유로 목록을 비운다.
- 결정: 사용자 승인 (2026-09-11, 추천안).
- 코드: `broker/kiwoom/ws/manager.py` `remove_orderbook`.

### 1.3 오전 구간 종료를 10:00 으로 확대 (§1, §3, §11)

- SPEC: 오전 `[08:45:00, 09:15:00)`.
- 구현: `[08:45:00, 10:00:00)`. 설정 파일(`orderbook_recorder.json`)과 로더 기본값(`DEFAULTS`) 둘 다 바꿨다.
- 근거: 사용자 요청 (2026-09-11). 장 초반 체결이 몰리는 구간을 더 길게 담는다.
- 영향:
  - 오전 실행 시간이 08:44 기동 기준 약 76분이 된다. SPEC §11 의 작업 실행 제한 1시간을 넘으므로,
    7단계 스케줄 XML 에서 실행 제한을 늘려야 한다(1시간 30분 예정).
  - 0D 수신량이 오전에 약 2.5배(30분 → 75분)로 는다.

### 1.4 broker 의 0D 복구 실패는 00 연결을 끊지 않는다 (§5) — 리뷰 P1 반영

- SPEC: "재접속 후 00과 확정 0D 목록을 복구한다. 복구 실패를 성공 connected로 보고하지 않는다."
- 구현: 00 등록 실패만 세션을 끊는다. 0D 복구가 거부(502)되면 broker 의 0D 목록을 버리고 00 만으로 `connected` 를 알린다.
  0D 복구 ACK 시간초과(504)는 세션이 이미 닫혔으므로 목록을 버리고 끝낸다 — 다음 세션은 00 만 복구한다.
- 근거: 목록을 남긴 채 세션을 끊으면 재접속마다 같은 거부가 반복돼 체결통보(00)까지 계속 끊긴다(리뷰어가 가짜 ACK 로 재현).
  0D 재등록은 `connected` 를 받은 수집기가 POST 로 하고, 실패하면 수집기 재시도 규칙으로 끝낸다.
- 결과: `connected` 는 "00 정상" 을 뜻한다. 0D 복구 여부는 `GET /realtime/orderbook` 의 `codes` 로 본다.
- SPEC 작성자(리뷰어)가 SPEC 보완 예정.
- 코드: `broker/kiwoom/ws/manager.py` `_after_login`.

## 2. SPEC 대로지만 기존 동작이 바뀐 부분

### 2.1 `system connected` 는 00 등록 ACK 이후에 발행 (§5)

- 기존: LOGIN 성공 후 00 REG 를 보내자마자 `connected` 를 발행했다. ACK 는 확인하지 않았다.
- 구현: 00 REG ACK, 이어서 확정 0D 목록 복구 ACK 를 받은 뒤에 `connected` 를 발행한다.
  하나라도 실패하면 소켓을 닫고 5초 뒤 재접속한다.
- 근거: SPEC §5 "복구 실패를 성공 connected로 보고하지 않는다", "LOGIN 후 등록 ACK를 기다리는 태스크와 receive loop를 분리".
  REG 응답 형식 `{'trnm': 'REG', 'return_code': 0, 'return_msg': ''}` 은 `kiwoom_api.xlsx` 의 `주식호가잔량(0D)` 시트에서 확인했다.
- 위험: 키움이 00 REG 에 ACK 를 보내지 않으면 재접속을 반복하고, 체결통보 기반 노트 동기화가 멈춘다.
- 상태: broker 재시작 때 `connected` 가 정상으로 뜨는지 실측해야 한다.

## 3. SPEC 에 없어서 정한 부분

| 항목 | 정한 값 | 근거 |
|---|---|---|
| WS `close_timeout` | 2초 (websockets 기본 10초) | ACK 시간초과 때 세션을 닫는 동안 HTTP 504 응답이 늦어지지 않게 한다 |
| 큐 유실 로그 | 채널별 첫 유실과 이후 1000건마다 | 0D 가 넘치면 유실마다 로그를 쓰는 것만으로 로그가 커진다. 건수는 `EventBus.dropped` 에 전부 남는다 |
| 유실 계측 범위 | 버스 전역, 채널별 | SPEC §6 "채널별 유실 횟수". 연결별로 나누지 않았다 |
| 새 API 의 MCP 노출 | 유지 (kiwoom-broker MCP 도구로 보임) | 사용자 결정 (2026-09-11) |
| 설정 파일이 깨진 JSON | `ConfigError("<file>")` → 종료 코드 1 | §3 은 "파일 없음 = 기본값" 만 정했다. 깨진 파일을 기본값으로 덮으면 사용자가 바꾼 값이 조용히 무시된다 |
| `broker_url` 검증 | `http` 이고 호스트가 `127.0.0.1`·`localhost` 일 때만 허용 | §3 "로컬 broker 주소". broker 에는 인증 설정이 없어 인증 헤더는 붙이지 않는다 |
| `scoring_result_path` | 로드 때 프로젝트 루트 기준 절대 경로(Path)로 바꿔 둔다 | §3 "프로젝트 루트 기준 상대 경로 또는 절대 경로". 워커 실행 위치(etl/)와 무관하게 같은 파일을 보게 한다 |
| `orderbook_run` 첫 행 | `symbols=''`, `rows_written=0` | `symbols` 는 NOT NULL 인데 첫 ACK 전에는 대상이 없다. `rows_written` 은 "이번 실행 commit 행 수" 라 시작값 0 |
| `resolve_symbols` 인자 | SPEC 3개 인자 뒤에 키워드 전용 `watchlist_db`, `now` 추가(기본값 = 운영 DB·현재 시각) | 테스트에서 임시 DB·고정 시각을 넣기 위해. SPEC 호출 형태 `resolve_symbols(cfg, date, window)` 는 그대로 동작 |
| `source_meta` 내용 | 오전: `generated_at`/`sha256`=NULL, `notes`(사유), `unsold`, `candidates`. 오후 ready: `generated_at`, `sha256`, `candidates`. wait: `reason` | §4.1 은 키 이름만 정했다. §4.2 "사유를 note 에 남긴다" 를 `notes` 로 담는다 |
| wait 사유 코드 | `file_missing`, `file_changing`, `invalid_json`, `db_write_false`, `generated_at_invalid`/`_stale`/`_future`, `no_result_for_date`, `results_not_unique`, `count_mismatch`, `scores_invalid`, `db_score_mismatch`, `watchlist_db_missing` | §4.3 7개 조건마다 어느 조건에서 막혔는지 로그로 구분하려고 |
| 오전 사유 코드 | `watchlist_db_missing`, `no_prior_candidates`, `candidates_stale` | §4.2 "후보가 없거나 오래돼도 … 사유를 note 에 남긴다" |
| `generated_at` 하한 | 설정의 `windows.afternoon.start`(기본 15:00:00) | §4.3 "당일 15:00 이상". 오후 시작 시각과 같은 값이라 설정 하나로 맞춘다 |
| 읽는 중 변경 | 크기·mtime 이 같을 때까지 최대 3번 다시 읽고, 그래도 바뀌면 `file_changing` 으로 wait | §4.3 "다시 읽는다" 의 횟수를 정했다. 다음 1초 확인에서 다시 시도한다 |
| 오후 0건 + static 없음 | `status=empty`, `source_date`=수집일 | §4.1 "오후는 종료 시각까지 확인". empty 는 구독 없이 계속 확인하라는 뜻 |
| 조건 7 의 "같은 읽기 트랜잭션" | `WHERE date=? AND ticker IN (...)` 한 번의 SELECT | SQLite 는 한 문장이 한 읽기 스냅샷이라 모든 ticker 를 같은 시점으로 대조한다 |
| 늦은 수신의 이후 사용 | 확정 격자 이하 `r` 은 `late_events` 로 세고 과거 행은 고치지 않는다. 다만 보유 값보다 최신이면 다음 격자에 쓴다 | §8 은 "과거 행을 수정하지 않고 카운트" 만 정했다. 그 시점까지 받은 값 중 최신 상태라 다음 격자 값으로는 맞다 |
| 등록과 격자 시작 분리 | POST 전에 `register`(수신만 받음), ACK 후 `activate`(그 시각 이상 첫 정수 초부터 행 생성). 실패하면 `forget` | §7-10 "등록 직후 이미 수신한 대상 데이터도 사용 가능" + §8 "신규 종목은 등록 전 격자를 생성하지 않는다" 를 둘 다 지키려고 |
| item 대조 범위 | `KRX:` 접두사 코드도 ACK 된 목록에 있어야 인정. 없으면 `invalid_events` | §9 는 맨 6자리만 ACK 목록과 대조하라고 했다. §8 "미등록 … 제외하고 카운트" 에 따라 접두사 여부와 무관하게 미등록은 뺀다 |
| 건너뛴 격자와 늦은 수신 | 늦은 flush 로 건너뛴 격자도 "확정·종료한 격자" 로 본다. 그 이하 `r` 은 `late_events` | §8 flush 규칙의 "지나간 격자" 를 종료로 취급 |
| 이른 flush | `now < t` 이면 아무것도 하지 않는다 | 격자 t 의 상태가 아직 끝나지 않았다 |
| `invalid_fields` 집계 | 종목·시각이 유효한 이벤트의 필드만 센다 | 버린 이벤트의 필드까지 세면 두 지표가 겹친다 |
| 수신 날짜 불일치 | `_recv_ts` 날짜 ≠ 수집일, tz 없음, 형식 오류 → `invalid_events` | §10 "수신 날짜는 date 와 일치해야 한다" |
| 첫 수신 시각 | 종목별로 구독 구간마다 한 번. 단절(`clear`) 후 다시 첫 수신을 기록 | §10 "재연결마다 subscriptions 에 새 구독 구간을 추가" |
| 격자 확정 시각 | 격자 t 를 t+0.2초에 확정(`FLUSH_DELAY`) | §8 은 flush 시각을 정하지 않았다. broker 가 t 직전에 찍은 이벤트가 SSE 로 조금 늦게 와도 그 격자에 담기게 한다. t+1 이전이라 건너뛰기 규칙과 겹치지 않는다 |
| 복구 POST 계기 | `system connected` 를 받을 때만 복구 POST. SSE 스트림이 새로 열린 것만으로는 보내지 않는다 | §7-12 "connected/새 SSE 연결 후 POST … 중복 실행되지 않도록 합친다". 새 SSE 에는 broker 가 sticky connected 를 바로 재생하므로 둘이 한 계기로 합쳐진다. broker 재시작 직후처럼 키움 연결 전이면 connected 가 올 때까지 기다려 503 재시도를 태우지 않는다 |
| 15:30 이후 기동 | 실행기록 행 없이 종료 코드 0 | §7-4 는 구간을 고른 뒤 행을 만든다. 고를 구간이 없고 `mode` 가 NOT NULL 이다 |
| broker 가 구간 내내 미연결 | 사유 `broker_not_connected`, 종료 코드 1 | §7-15 에 없는 경우. 수집 실패를 스케줄러 결과로 드러내려고 등록 실패(1)와 같게 둔다 |
| 종료 사유 코드 | `window_end`, `holiday`, `empty`, `candidate_not_ready`, `reg_failed`, `db_error`, `broker_not_connected`, `broker_error`(시작 시 GET/DELETE 정리 실패 등, 코드 1), `error`(예상 밖 예외) | §7·§10 이 예시한 값에 빈 경우를 채웠다. 휴장이면 note 에 `holiday` 사유 문구도 남긴다. `broker_error` 는 CodeRabbit 리뷰 반영 |
| 오후 빈 목록 | 0건 완료만 보고 끝나면 `empty`, 끝까지 준비 안 되면 `candidate_not_ready` | §4.3 / §4.1 |
| 재시도 범위 | 최초 등록·재접속 복구·구간 중 추가 모두 "최초 + reg_retry 회, 2초 간격". 소진하면 구간 전체를 끝내고 코드 1. 최초 등록을 못 한 채 구간이 끝나도 `reg_failed`·코드 1 | §7-9 를 모든 POST 에 적용. 구간 중 추가 실패로 기존 종목 수집까지 멈추는 점은 실측 후 다시 볼 대상. 최초 등록 미완 종료는 리뷰 P1(무한 루프) 반영 |
| 복구·추가 등록 스레드 | 최초 등록은 메인 스레드에서 동기(격자 시작 전이라 잃는 격자 없음). 복구·추가 등록 POST 는 전용 단일 작업 스레드에서 수행한다. 메인 루프는 매 회차 결과만 확인하고, `grid.activate/forget`·note·DB 변경은 메인 스레드에서만 한다. 등록 요청은 한 번에 하나, 재시도 간격 2초·`reg_retry` 정책은 그대로. 대기 중 새로 붙은 코드는 앞 POST 가 끝난 뒤 이어서 보낸다. 종료 때는 진행 중 POST 가 끝난 뒤 DELETE | 리뷰 P2(재리뷰) 반영: 1초 수집 중 POST 가 메인 루프를 최대 15초 막으면 안 된다. HTTP 세션은 스레드별로 따로 쓴다 |
| REST 대조 | 첫 수신 종목의 ka10004 `sel_fpr_bid`/`buy_fpr_bid`(최우선 매도/매수호가, 절댓값)를 0D `ask1_px`/`bid1_px` 와 비교. 별도 daemon 스레드에서 돌리고 로그만 남긴다 | §7-11 은 대조 필드를 정하지 않았다. 시점이 달라 불일치할 수 있어 경고만. REST 대기가 격자 저장을 막지 않게(리뷰 P2) |
| system 알림 보존 | SSE 큐가 가득 차면 system 알림은 버리지 않고 가장 오래된 이벤트를 버린 뒤 넣는다. 버린 건 그 채널 유실로 센다 | 리뷰 P2 반영. 구독 중인 연결에는 sticky 재생이 없어서, connected/disconnected 를 잃으면 슬롯 비우기·복구 POST 를 놓친다 |
| 활성화 전 격자 종료 | 한 종목도 활성화 못 한 격자도 종료 시각이 지나면 `flush` 가 None(끝) | 리뷰 P1 반영. 전에는 빈 목록만 돌려 저장 루프가 끝나지 않았다 |
| HTTP 대기 | 연결 5초, 읽기 min(15초, 구간 남은 시간). 종료 DELETE 는 5초 | §7-9 "구간 종료를 넘기지 않도록" |
| SSE 스레드 종료 | 멈춤 요청 후 막힌 read 는 다음 heartbeat(≤15초)까지 남는다. daemon 스레드라 프로세스 종료를 막지 않는다 | 스트림을 다른 스레드에서 강제로 닫는 복잡도를 피했다 |
| 로그 | Python 은 stdout 으로만 쓴다. 파일(`etl/logs/orderbook-recorder-YYYYMMDD.log`)은 7단계 런처가 리다이렉트한다 | 기존 러너들과 같은 방식 |
| 스키마 카탈로그 갱신 범위 | `etl/docs/DB_SCHEMA.md` 에 `orderbook.sqlite3` 절만 추가(`dump_db_schema.render` 로 생성해 끼움) | §10 "dump_db_schema.py 로 갱신". 전체 재생성하면 다른 작업(조기신호·리포트 지표·텔레그램·유튜브·분봉)의 미반영 스키마 약 440줄이 이 브랜치에 섞인다. 전체 재생성은 별도 작업으로 남긴다 |
| 런처 stderr 처리 | Python 호출 구간만 `$ErrorActionPreference="Continue"`, `2>&1 \| Tee-Object -Append` | 오전·오후가 같은 날짜 로그를 쓰므로 Append. PS 5.1 은 EAP=Stop 에서 native stderr 첫 줄에 러너를 죽인다(텔레그램 세션 러너 사고와 같은 원인) |
| 스케줄 재시작 정책 | `RestartOnFailure` 없음 | §11 에 없다. 강제 종료 후 남은 구독은 다음 실행의 최초 DELETE 가 정리한다 |
| 스케줄 시작일 | 두 트리거 모두 2026-09-14(월)부터 | 등록은 실측 검증 후(§11) |
| ops 문서 | `ops/batches/*.md`, `openclaw-cron.registry.json` 항목은 만들지 않았다 | §2 파일 목록에 없다. 등록할 때 같이 추가할지 정한다 |
| 회차 저장 트랜잭션 | 스냅샷 행 + `orderbook_run.rows_written`(+바뀐 경우 note) 를 한 트랜잭션으로 | §8 "같은 회차 모든 종목 행을 한 트랜잭션", §10 "rows_written: commit 한 행 수", first_recv_ts 는 "다음 격자 저장 트랜잭션에서 note 에 반영". 실패하면 셋 다 rollback |
