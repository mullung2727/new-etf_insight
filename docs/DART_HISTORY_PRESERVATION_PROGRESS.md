# DART ETF 공시 이력·최초 수집정보 보존 — 진행 (Muse 작성, 2026-10-04)

> 최종 상태: 구현·Codex 직접 검수·109 tests·실제 DB 적용 완료. 아래 초안 설명과 보완 기록은 작성 순서의 이력이며 최종 판정은 맨 아래 「Codex 최종 검수·운영 DB 적용」을 기준으로 한다. 코드·테스트는 모두 Muse 작성, Codex의 직접 코드 수정 없음.

## 범위·역할

- 대상 4파일만 수정: `etl/src/new_etf_insight/daily_pipeline.py`, `etl/scripts/build_db.py`,
  `etl/tests/test_pipeline_modules.py`, `etl/tests/test_build_db.py`.
- 명령 실행 금지 준수: 파일 읽기·편집만 수행. 테스트 실행·검증은 Codex 담당.
- `daily_pipeline.py`·`build_db.py`는 통째로 다시 씀 (아래 “작업 메모” 참고).
  테스트 2파일은 기존 케이스 무수정 + 새 클래스 append만.

## 문제별 코드 근거 (수정 전)

1. 당일 records만 조회 → 전날 정정 유실
   - 근거: `run_daily_pipeline`이 `record_path = records_dir / f"{etf_key}.json"` 단일 경로만 보고,
     정정인데 그 경로가 없으면 무조건 `correction_without_existing_record` skip.
2. overwrite + INSERT OR REPLACE로 최신만 남음
   - 근거: `_save_correction_update`가 같은 경로 JSON 덮어쓰기, `sync_to_db`가 etf별 1행 REPLACE.
3. `_load_records` 최신 rcept_dt만 남김 → 공시번호·버전 소실
   - 근거: `best[etf_key]` 1개만 유지, 구 JSON snapshot 버려짐.
4. first 규약 있으나 실제 수집시각·최초 공시번호 없음
   - 근거: JSON/DB에 `first_rcept_dt`·`revision_count`만 있고 수집시각 필드 전무.
5. 후보 순서 의존 → 정정이 원본보다 먼저 처리될 수 있음
   - 근거: `collect_candidates` 반환 순서 그대로 루프, 정렬 없음.

## 구현 요약

### `etl/scripts/build_db.py`

- `etf_records`에 `first_rcept_no TEXT`, `first_collected_at TEXT` 추가 (CREATE + ALTER migration).
- `etf_filing_history(etf_key, rcept_no PK, rcept_dt, first_collected_at, action, reason, filing_json, record_json)` 추가.
- `_upsert_history_preserve`: 존재 행은 snapshot·최초시각 보존. 유일한 전이는 failed→created/updated(성공 재시도).
- `_load_all_records` + `_backfill_history_from_json`: dedup 전 모든 날짜 JSON을 이력으로 보존.
  수집시각 모르면 NULL (rcept_dt·mtime 위조 금지).
- `_resolve_first_meta`: latest JSON → 기존 DB → earliest JSON 순으로 first 보완.
- 공개 헬퍼: `get_history_entry`, `write_history_observation`, `ensure_previous_snapshot`.
- `sync_to_db` 반환값·기존 dedup 로직(`_load_records`) 그대로 유지.

### `etl/src/new_etf_insight/daily_pipeline.py`

- 후보를 `(rcept_dt, rcept_no)` 정렬 후 처리.
- `_scan_etf_records`로 runs 전체 + 당일에서 ETF JSON 전수 조회, 최신/최초 판별.
- 동일 rcept_no JSON 존재 → `existing_record` skip, LLM 호출 없음.
- history에 non-failed 행 존재 시: (a) 성공 행인데 JSON 없음 → 재구축 허용,
  (b) `correction_without_existing_record`였는데 기록 생김 → 재처리 허용,
  (c) 그 외 → skip (skipped 행은 기존 reason 그대로 반환, LLM 없음).
- 정정 + 기록 없음 → 관측 이력 쓰고 `correction_without_existing_record`.
- 60일 규칙 유지 + 관측 이력. stale(최신보다 오래된 공시) → `stale_filing` skip + 관측 이력, 최신 불변.
- review/update 예외도 잡아서 `correction_review_failed`·`correction_update_failed` + 실패 이력
  (기존엔 정정 경로 예외가 파이프라인 전체를 죽였음).
- 덮어쓰기 전 `ensure_previous_snapshot`, 성공 후 즉시 `write_history_observation`.
- `_first_meta`로 first 3종 + `collected_at`을 코드에서 확정 후 기록 (`**output` 뒤에 덮어써 LLM 간섭 차단).
- `_save_*`는 기존 시그니처 유지 + `previous_record`·`earliest_record`·`collected_at` 옵션 추가, 저장 record 반환.
- 일반 공시는 당일 파일 존재 또는 runs에 기존 기록 있으면 `existing_record` skip (신규 생성만).

### 케이스별 동작

- 원본 신규: created, first=자신, snapshot 저장.
- 정정(정상): cross-day여도 latest 찾아 review→updated, first 불변, revision+1, 이전 snapshot 보존.
- 동일 공시 재실행: JSON 또는 history 적중 → skip, revision·first·snapshot 불변, LLM 없음.
- 역순 도착: 정렬로 정순 처리. 뒤늦은 구 공시는 stale skip, 최신 후퇴 없음.
- no-update/60일/기록없음: 요약 유지 + 관측 이력(record NULL).
- 실패→성공: 같은 행 failed→created/updated, 최초 관측시각 보존, JSON collected_at도 최초시각.
- legacy JSON: snapshot·식별 가능한 first 보존, 수집시각 NULL.
- rcept_no 없는 깨진 JSON: 이력 skip, sync 크래시 없음.

## 확정 설계와 차이 (승인 요청 아님, 기록용; 최종 구현 기준 정정)

1. DB 이력 fallback — 1차에서 구현함 (`get_db_latest_snapshot`).
   - runs JSON 전수 + DB 최신 성공 snapshot을 `(rcept_dt, rcept_no)`로 비교해 최신을 previous로 선택.
   - 최종 검수에서 DB 최초번호 NULL을 snapshot의 잘못된 정정번호로 보완하지 않도록 확정
     (DB NULL이면 NULL 유지; `first_rcept_no`·`first_collected_at` 포함).
   - 공유 DB 경로 테스트 간섭은 최종 1항의 `DailyPipelineTest` temp 격리로 해소.
2. 새 skip reason `stale_filing` 추가 (spec에 이름 없음).
   - 최신보다 오래된 신규 공시를 `existing_record`과 구분하기 위함. 요약 미갱신은 동일.
3. 성공 이력 + JSON 분실 시 — 1차부터 재분석 대신 DB snapshot 복구 (LLM/PDF 없음).
   - snapshot 있으면 JSON 복구하고 skipped. DB에 더 최신이 있으면 복구 없이 skipped.
   - snapshot 없으면만 rebuild 허용. 최종 5항에서 복구 `write_text` 실패는 silent skip 대신
     `failed`/`snapshot_restore_failed`로 명확히 실패시키고 성공 이력 snapshot 불변.

## 테스트 추가

### `etl/tests/test_pipeline_modules.py` → `FilingHistoryPreservationTest`

- test_cross_day_correction_preserves_first_and_two_snapshots (완료기준 1)
- test_same_day_reversed_candidates_process_in_filing_order (완료기준 2·5)
- test_same_rcept_no_rerun_skips_without_llm_and_preserves_history (완료기준 3)
- test_skipped_no_update_correction_leaves_observation_history (완료기준 4)
- test_skipped_60_day_correction_leaves_observation_history (완료기준 4)
- test_skipped_correction_without_record_leaves_observation_history (완료기준 4)
- test_stale_filing_does_not_regress_latest (완료기준 5)
- test_failed_then_success_reuses_same_history_row (완료기준 7)

### `etl/tests/test_build_db.py` → `BuildDbHistoryTest`

- test_history_preserves_every_dated_json_before_dedup (완료기준 6)
- test_first_meta_resolved_from_earliest_when_latest_missing (완료기준 6)
- test_sync_twice_keeps_history_first_and_snapshot_identical (완료기준 8)
- test_existing_db_first_preserved_when_latest_json_loses_meta (완료기준 8)
- test_new_format_times_flow_into_history
- test_backfill_skips_json_without_rcept_no

### 검수자 실행 (etl 디렉토리 기준)

- `.\.venv\Scripts\python.exe -m unittest tests/test_pipeline_modules.py -v`
- `.\.venv\Scripts\python.exe -m unittest tests/test_build_db.py -v`
- 새 클래스만: `... -m unittest tests.test_pipeline_modules.FilingHistoryPreservationTest -v`,
  `... -m unittest tests.test_build_db.BuildDbHistoryTest -v`
- import 확인: `daily_pipeline`, `build_db` import + `dump_db_schema.py`로 DB_SCHEMA 갱신은 검수자 담당.

## 작업 메모

- 편집 도구가 CRLF 파일에서 멀티라인 find를 매치하지 못해 (`build_db.py`·`daily_pipeline.py`가 CRLF),
  두 파일은 전체 write로 교체함. 미변경 헬퍼(`_load_records`, `_days_between`, `_iter_dates`,
  `_build_source`, `_read_json`, `run_period_as_daily_runs` 등) 내용은 그대로 옮김.
  줄바꿈이 LF로 바뀌어 git diff가 크게 보일 수 있음. 기능 diff는 CR 무시 비교 권장.
- 기존 `DailyPipelineTest`·`BuildDbSqliteTest` 케이스는 한 줄도 안 고침.
  새 파이프라인의 history-aware skip이 기존 reason(stderr 포함)을 그대로 반환하도록 설계해 회귀를 피함.
- `.env`·실제 DB·runs 미접촉. 요청 외 파일 생성 없음.

## 1차 보완 (Codex 직접 검수 지적, Muse 작성, 2026-10-04)

- 범위: 같은 4파일만 surgical edit. 새 CLI/API/파일/추상화 없음.
  `get_history_entry`에 snapshot(`record`) 반환 추가와 `get_db_latest_snapshot` 1개는
  기존 helper 패턴 안의 최소 확장으로 처리함. 기존 82 tests 무수정.

### Codex 발견 1 → history 전이·snapshot 누락

- 지적: failed→created/updated만 허용이라 skipped 재처리의 성공 snapshot 영구 유실,
  failed→skipped가 failed로 남아 매번 LLM 재호출, 성공 이력+JSON 유실 시 rebuild로 LLM 재호출,
  JSON 성공인데 history failed/skipped면 sync가 성공 snapshot을 못 채움.
- 처리: `_upsert_history_preserve`를 snapshot-less row 한정 전이로 확장.
  성공 snapshot 있는 row는 절대 불변. failed/사정성 skipped(`correction_without_existing_record`)가
  실제 성공 시 같은 row의 action/reason/record_json만 채움. failed→skipped와
  사정성 skipped→terminal skipped도 전이. first_collected_at/filing_json/rcept_dt는 보존,
  NULL legacy 시각을 재시도 시각으로 위조하지 않음. 최종 sync backfill이 같은 로직으로 채움.
- 파이프라인: 성공 이력인데 JSON 전무면 DB snapshot으로 JSON 복구하고 skipped, LLM/PDF 미호출.
  DB에 더 최신이 있으면 복구 없이 skipped. snapshot 없으면만 기존 rebuild 허용.

### Codex 발견 2 → DB sync 최신성·최초정보 보존

- 지적: `_load_records`가 rcept_dt만 비교, `_upsert_record`이 오래된 JSON으로 최신 DB를 덮음,
  `_resolve_first_meta`가 최신 JSON 값을 우선해 보존된 최초정보를 덮고 레거시 earliest를 놓침.
- 처리: `_load_records`를 `(rcept_dt, rcept_no)` 튜플 비교로 변경해 당일 다건·입력순서 무관.
  `_upsert_record`는 DB tuple이 더 최신이면 current row·holdings를 건드리지 않고 return,
  history backfill은 그대로 보존. `_resolve_first_meta`는 DB 쌍 우선, 쌍(pair)으로만 해석,
  earliest 증거·latest의 더 이른 기억 순으로 결정. 최초일<source일인데 번호 모르면 NULL 유지,
  수집시각 모르면 NULL 유지.

### Codex 발견 3 → 기존 기록 조회의 DB fallback

- 지적: json_records만 current라 DB 최신 snapshot이 있어도 without_record 또는 오래된 요약으로 정정.
- 처리: `get_db_latest_snapshot`으로 DB 최신 성공 snapshot을 current 후보에 포함,
  `(rcept_dt, rcept_no)`로 JSON 최신과 비교해 최신을 previous로 선택. 동일 번호는 수동 재분석일 수 있어
  JSON을 우선하고 첫 메타만 DB 값으로 반영. DB 선택 시에도 첫 메타는 DB 해결값을 적용.
  동일 번호 재실행은 발견 1의 snapshot 복구로 LLM/PDF 없이 처리.

### Codex 발견 4 → 새 일반 공시도 관측 이력

- 지적: 같은 ETF 기존 기록 있는 일반 공시가 `existing_record`로 끝나 history가 없음.
- 처리: 일반 공시 skip 분기에 `skipped`/`existing_record` 관측 이력 쓰기만 추가.
  요약·JSON 불변, 기존 skip 동작·사유 유지. `_upsert_history_preserve`가 멱등이라 rerun 중복 0.

### 추가 테스트 (같은 기존 클래스에 append)

- `FilingHistoryPreservationTest`:
  - test_absent_parent_correction_retry_succeeds_and_fills_snapshot
  - test_failed_then_no_update_skipped_avoids_llm_on_rerun
  - test_lost_success_json_rerun_restores_snapshot_without_llm
  - test_db_only_previous_enables_correction_update
  - test_old_json_newer_db_correction_uses_db_snapshot
  - test_db_only_same_rcept_correction_rerun_restores_without_llm
  - test_new_general_filing_leaves_observation_history_without_summary_change
- `BuildDbHistoryTest`:
  - test_new_db_legacy_first_dt_self_dates_preserves_earliest
  - test_existing_db_first_meta_wins_over_conflicting_latest_json
  - test_newer_db_ignores_older_only_json_for_current_row
  - test_same_date_multiple_rcept_no_keeps_largest_regardless_of_order
  - test_legacy_missing_first_no_with_earlier_first_dt_keeps_null
- 총 12개 추가, 기존 82개 무수정. 실행·검증은 Codex 담당.

## 최종 검수 보완 (Codex 최종 리뷰, Muse 작성, 2026-10-04)

- 범위: 같은 4파일 + 본 문서만. 명령 실행 금지 준수. 실 DB·runs·비밀 미접촉.
- 코드 2파일은 LF 유지 (1차에서 CRLF→LF로 전체 write済). 테스트 2파일은 surgical edit만.

### 1. 기존 테스트 경로 격리

- 원인: `DailyPipelineTest` 8개가 `base_dir/records`라 `records_dir.parent.parent`가 temp 밖 공유 DB를 가리킴.
- 처리: `DailyPipelineTest.setUp/tearDown`에서 `tempfile.TemporaryDirectory`를 `runs/<날짜>`로 리다이렉트.
  CRLF 반복 fixture 8행을 직접 고치는 대신 한 곳에서 동등 격리 (60일 케이스만 `20260531`, 나머지 `20260429`).
  결과 레이아웃은 요청과 동일 (`base_dir/runs/<날짜>/records` + 같은 날짜 pdfs, db는 temp 안).
  공유 DB 삭제 없음, 제품 DB fallback 유지. 기존 assert·동작 기대 무수정.

### 2. 최초 수집시각 추정 금지 및 최초번호 일치

- `_resolve_first_meta` 단순화: DB 쌍 우선 → 날짜 일치 원본번호 매칭 → latest의 더 이른 기억 → earliest.
  최초시각 근거는 일치 원본 JSON의 `collected_at` 또는 명시적 `first_collected_at`만.
  DB 기지 시각은 JSON 시각으로 덮지 않음. earliest 정정 시각을 원본 시각으로 사용 금지.
- `daily_pipeline._first_meta`도 원본번호 없는데 `first_rcept_dt` < earliest source 날짜면 정정번호 부착 금지.
  일치 원본·명시적 보존만 근거. self-date (`first_dt` > earliest)는 earliest로 교정.
- `get_db_latest_snapshot`은 DB 최초값(NULL 포함)을 그대로 적용해 snapshot의 잘못된 정정번호·시각을 유지하지 않음.

### 3. 재시도 공시별 시각 일치

- history 있으면 action 무관하게 `first_collected_at` 재사용. NULL은 now로 위조 금지.
- `_save_*`는 `_AUTO_COLLECTED_AT` sentinel로 생략(→now)과 명시적 NULL(→NULL 유지) 구분. 직접 호출 기본값 호환 유지.

### 4. 이력 성공 snapshot reconciliation

- `_upsert_history_preserve` 성공 전이를 snapshot-less 전 행으로 확대 (failed·모든 skipped 포함).
  성공 snapshot 있으면 절대 불변. NULL 행 + 신뢰 성공 JSON이면 `created`/`updated`로 채우고 최초 filing·시각 보존.
  terminal skipped의 pipeline 재분석은 없음 (sync backfill만).

### 5. 성공 snapshot 복구 저장 실패

- 복구 `write_text`의 `except: pass` 제거. 실패 시 `failed`/`snapshot_restore_failed`(+`error_type`·`error`)로 명확히 실패.
  성공 이력 snapshot·history 미기록. 추가 복구 계층 없음.

### 추가 테스트 (9개)

- `FilingHistoryPreservationTest`:
  - test_legacy_correction_only_next_correction_keeps_first_nulls (2항 pipeline)
  - test_skipped_history_time_reused_for_retry_success (3항 T1 재사용)
  - test_failed_null_time_stays_null_on_success (3항 NULL 유지)
  - test_new_filing_gets_utc_collected_time (3항 신규 UTC)
  - test_snapshot_restore_failure_reports_failed_without_touching_history (5항)
- `BuildDbHistoryTest`:
  - test_correction_only_first_time_not_fabricated_with_db (2항 DB 유)
  - test_correction_only_first_time_not_fabricated_without_db (2항 DB 무)
  - test_get_db_latest_snapshot_clears_wrong_first_no_when_db_null (2항 snapshot)
  - test_skipped_no_update_success_json_reconciles_snapshot (4항)
- 기존 테스트 본문 무수정 (1항은 `setUp`/`tearDown` 추가만). 실행·검증은 Codex 담당.

## 테스트 경로 최종 수정 (Muse 작성, 2026-10-04)

- `DailyPipelineTest.setUp`의 `tempfile.TemporaryDirectory` 우회 전체 제거: `with`는 인스턴스 `__enter__`가 아니라 타입 special method를 조회해 효과가 없음.
- 8개 fixture를 `base_dir/runs/<날짜>/records`(+`pdfs`)로 직접 수정, 깊은 경로용 `mkdir(parents=True)` 추가. 60일 케이스만 `20260531`, 나머지 `20260429`. assert·mock·history 테스트 무수정. 실행·검증은 Codex 담당.

## Codex 최종 검수·운영 DB 적용 (2026-10-04)

### 결과

- 코드·테스트 4개 파일은 전부 Muse 작성. Codex는 설계·문서·diff 검수·테스트·DB 실행만 담당했으며 직접 코드 수정 없음.
- 최초 초안 82 tests 통과 뒤 직접 검수에서 발견한 DB fallback 미구현, 이력 상태 전이 누락, 최초 메타 우선순위 오류를 재작성 요청했다.
- 추가 검수에서 정정 수집시각을 원본 수집시각으로 잘못 채울 가능성, 재시도 NULL 시각 위조, 성공 JSON 이력 보완 누락, snapshot 복구 실패 은폐를 수정했다.
- 기존 DailyPipelineTest가 임시 폴더 밖 공유 DB를 사용해 회귀 4건 실패. Muse의 TemporaryDirectory 인스턴스 __enter__ 변경 우회도 효과가 없어 반려하고, 8개 fixture를 temp/runs/<날짜>/records로 직접 변경했다. 기존 assert·mock은 유지했다.
- 최종 실행: `PYTHONPATH=src .venv/Scripts/python.exe -m unittest tests.test_build_db tests.test_pipeline_modules tests.test_dart_client tests.test_llm_retry` — **109 tests, OK**. stderr `boom`과 transient retry 메시지는 기존 모의 실패 테스트의 정상 출력이다.
- 변경 모듈 import는 위 테스트에서 실제 확인했다. 관련 없는 실매매·서버·원격 DART 배치는 실행하지 않았다.

### 확정 설계 대조

| 완료 기준 | 최종 구현 근거 | 실제 통과한 테스트 (함수명) | 판정 |
|---|---|---|---|
| 전날 원본에 다음날 정정 연결 | daily_pipeline.py:66, build_db.py:537 | test_cross_day_correction_preserves_first_and_two_snapshots; test_db_only_previous_enables_correction_update | 충족 |
| 같은 날 여러 공시 순서·snapshot | daily_pipeline.py:36, build_db.py:147 | test_same_day_reversed_candidates_process_in_filing_order | 충족 |
| 같은 번호 재실행 불변·LLM 미호출 | daily_pipeline.py:69, :103 | test_same_rcept_no_rerun_skips_without_llm_and_preserves_history; test_lost_success_json_rerun_restores_snapshot_without_llm | 충족 |
| no-update·60일·원본없음 관측 이력 | daily_pipeline.py:149 이후, build_db.py:586 | test_skipped_no_update_correction_leaves_observation_history; test_skipped_60_day_correction_leaves_observation_history; test_skipped_correction_without_record_leaves_observation_history | 충족 |
| 과거 공시로 최신 후퇴 방지 | daily_pipeline.py:161 이후, build_db.py:400 | test_stale_filing_does_not_regress_latest; test_newer_db_ignores_older_only_json_for_current_row; test_same_date_multiple_rcept_no_keeps_largest_regardless_of_order | 충족 |
| legacy 전 날짜 snapshot·최초 메타 | build_db.py:294, :644 | test_history_preserves_every_dated_json_before_dedup; test_new_db_legacy_first_dt_self_dates_preserves_earliest; test_correction_only_first_time_not_fabricated_with_db; test_correction_only_first_time_not_fabricated_without_db | 충족 |
| 실패·원본 미도착 재시도 첫 관측시각 | build_db.py:147, daily_pipeline.py:137 | test_failed_then_success_reuses_same_history_row; test_absent_parent_correction_retry_succeeds_and_fills_snapshot; test_skipped_history_time_reused_for_retry_success; test_failed_null_time_stays_null_on_success | 충족 |
| sync 멱등·DB 최초 정보 우선·성공 보완 | build_db.py:147, :294, :644 | test_sync_twice_keeps_history_first_and_snapshot_identical; test_existing_db_first_meta_wins_over_conflicting_latest_json; test_skipped_no_update_success_json_reconciles_snapshot | 충족 |
| 회귀·import·schema 생성 | tests 4 modules, scripts/dump_db_schema.py | 109 tests OK, 생성기 실행 성공 | 충족 |

### 실제 DB 적용 증거

- 적용 전 backup: `etl/runs/dart_history_migration_20261004/etf_insight.before.sqlite3` (기존 backup 유지).
- backup 복사본으로 먼저 2회 적재: 기존 컬럼 전체(db_updated_at 제외)·holdings 전체 동일, 이력 전체 동일, integrity_check=ok.
- 실제 `etl/db/etf_insight.sqlite3`도 같은 검증을 실행하고 2회 적재했다.
- `etf_records` 50행, `etf_holdings` 987행 그대로. 기존 source·summary·first_rcept_dt·revision_count 포함 기존 컬럼 전체 동일.
- `etf_filing_history` 50행. 기존 JSON 53개 중 중복 3개는 같은 접수번호 재분석이므로 별도 공시로 만들지 않았다.
- 최초 공시번호 50개 보완. 기존 실제 수집시각은 근거가 없어서 current/history 모두 NULL 50개 유지.
- 이력 50행 전체 (공시 메타·action/reason·snapshot·시각)는 2회 실행 전후 동일. `PRAGMA integrity_check` = `ok`.
- `etl/docs/DB_SCHEMA.md`는 기존 생성기로 재생성했다. 생성기가 모든 현지 DB를 스캔하므로 기존 다른 DB의 미반영 스키마도 함께 문서에 반영됐지만, 해당 DB나 생성기 코드는 수정하지 않았다.
- README·DAILY_PIPELINE_FLOW·OPENCLAW_PIPELINE_GUIDE·etl/CLAUDE·ETL reference skill에 최종 규약을 반영했다.

### 범위와 남은 한계

- 최신 요약 JSON·기존 DB 읽기 인터페이스는 유지하고 접수번호별 history를 추가했다. 서버 재시작·화면/API 추가·새 CLI·원격 전체 이력 소급 수집은 하지 않았다.
- 이미 덮어써져 JSON·DB 어디에도 남아 있지 않은 과거 원본 snapshot과 실제 과거 수집시각은 복원하지 않았다. 앞으로 관측하는 공시와 현재 보관된 JSON을 보존한다.
- 실 DART 정정 공시를 통한 운영 배치 검증은 미실행. 정정·실패·재실행·JSON 유실 시나리오는 외부 호출을 모의한 테스트로 검증했다.
