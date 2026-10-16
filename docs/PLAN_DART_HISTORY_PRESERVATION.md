# DART ETF 공시 이력·최초 수집정보 보존 — 2026-10-04

## 사용자 승인과 역할

- 사용자 요청: 문서·현행 코드 조사 후 덮어쓰기와 최초 정보 유실을 수정.
- 사용자 승인: 기존 SQLite에 공시별 이력 테이블과 최초 수집시각 추가(2026-10-04).
- Codex: 틀·명세, diff 검수, 테스트·실행. Muse: 상세 조사와 코드 작성.
- 명령 실행 금지, 파일만 작성. 테스트·실행·검증은 호출 에이전트가 담당한다.

## 확정 범위

- DART ETF 파이프라인 `daily_pipeline.py`와 SQLite 적재 `build_db.py`.
- 기존 최신 ETF 목록·요약·holdings는 유지한다. 신규 UI·API·배치·외부 API 추가 없음.
- 기존 날짜별 records 경로를 유지하고, 이력 원장은 SQLite로 추가한다.
- 웹(DART 원문)의 최초~정정 이력을 전수 재수집하는 별도 백필은 이번 범위가 아니다. 이미 보유한 JSON은 가능한 이력으로 보존한다.

## 현재 확인한 문제

1. `run_daily_pipeline`은 그날 `records_dir/{etf_key}.json`만 읽는다. 전날 공시 기록이 있어도 정정은 `correction_without_existing_record`로 빠진다.
2. 동일 경로 JSON은 overwrite, DB는 ETF별 INSERT OR REPLACE로 최신값만 남는다.
3. `_load_records`는 최신 rcept_dt만 남기므로 각 공시 원문 번호와 분석 버전이 소실된다.
4. 최초 공시일 first_rcept_dt와 revision_count 규약은 있지만 실제 수집시각과 처음 저장한 공시번호가 없다.
5. 후보 응답 순서에 의존하면 같은 조회 구간 내 최신 정정이 최초보다 먼저 처리될 수 있다.

## 최소 구현 설계

### DB

- 기존 `etf_records`에 nullable TEXT `first_rcept_no`, `first_collected_at` 추가.
- `etf_filing_history` 추가:
  - PK `(etf_key, rcept_no)`; ETF와 공시번호를 명확히 분리.
  - `rcept_dt TEXT`, `first_collected_at TEXT` nullable, `action TEXT`, `reason TEXT` nullable.
  - `filing_json TEXT`: 수집 공시 메타. `record_json TEXT` nullable: 해당 공시로 생성/갱신한 완전 분석 snapshot.
  - 실제 수집시각은 timezone-aware UTC ISO 문자열. 최초 공시일과 절대 혼용하지 않는다.
- 동일 공시 재실행은 이력 행·revision_count 증가 금지. 최초 snapshot/최초 수집시각 보존. 실패 후 성공은 같은 행의 상태를 성공으로 전이시키되 최초 시각 보존.
- 스킵한 정정도 공시 메타·사유로 남기지만 현 ETF 요약(source)은 갱신하지 않는다.
- 기존 JSON 백필 시 실제 수집시각이 없으면 NULL 유지. 공시일이나 파일 mtime을 실제 수집시각으로 위조하지 않는다.

### Pipeline

- runs 전체에서 ETF 최신 기존 기록을 찾아 정정을 연결한다. DB 이력 fallback이 필요하면 기존 helper 안에서 처리하되 최소화.
- 공시를 `(rcept_dt, rcept_no)` 오름차순 처리. 오래된 공시 재실행으로 최신 source/요약이 되돌아가면 안 된다.
- 덮어쓰기 전 이전 snapshot을 이력 원장에 보존한다. 성공 기록은 생성 후 즉시 이력 저장한다.
- 보유한 첫 공시번호·최초 공시일·실제 최초 수집시각을 기존 기록/DB에서 유지한다. 최초 공시 웹 이력 전수 복원을 했다고 주장하지 않는다.
- JSON 최신 레코드에도 first_rcept_no, first_collected_at, 해당 공시 collected_at를 코드에서 관리(LLM 출력으로 덮어쓰지 않음).
- 기존 60일 정정 분석 제한·needs_update 정책은 유지하되 해당 공시 자체는 이력에 남긴다.
- 에러 후 재실행, 같은 날 여러 정정, 날짜를 넘긴 정정에서 snapshot·중복·최신성 규칙을 만족한다.

### DB sync

- 기존 모든 날짜별 JSON을 최신값 dedup 전에 이력으로 보존한다.
- 최초 메타가 최신 JSON에서 누락돼도 DB/다른 보유 JSON의 기존값을 보존한다.
- sync를 두 번 실행해도 이력 수·최초시각·snapshot 동일.
- 기존 DB migration은 비파괴적 ALTER/CREATE로 수행한다. DB를 새로 생성하거나 삭제하지 않는다.

## Muse 허용 파일

읽기: README.md, AGENTS.md, etl/AGENTS.md, etl/CLAUDE.md, etl/DAILY_PIPELINE_FLOW.md, etl/OPENCLAW_PIPELINE_GUIDE.md, skills/new-etf-insight-etl-reference/SKILL.md, skills/new-etf-insight-batch/SKILL.md, 이 명세, 아래 코드·테스트.

코드 작성·수정은 아래 네 파일만:

- `etl/src/new_etf_insight/daily_pipeline.py`
- `etl/scripts/build_db.py`
- `etl/tests/test_pipeline_modules.py`
- `etl/tests/test_build_db.py`

조사·변경 설명 문서는 `docs/DART_HISTORY_PRESERVATION_PROGRESS.md`만 작성.

요청하지 않은 파일·경로 생성 금지. .env·실제 DB·runs 변경 금지. 셸·명령·테스트·코드 실행·외부 도구 호출 금지. 파일 읽기·편집만 허용.

## 조사 결과에 포함할 것

- 문제별 실제 코드 근거, 기존 코드에서 가장 작은 변경으로 해결했는지.
- 원본/정정/같은 공시 재처리/과거 역순 처리/no-update/60일 스킵/실패 재시도/legacy JSON 유실 위험.
- 확정 설계와 차이가 필요하면 문서에 명확히 표시하고, 임의로 새 API/파일/저장방식 도입하지 않기.
- 테스트 추가 사례와 검수자 실행에 필요한 unittest 이름.

## 완료 기준과 직접 검수

1. 재현 테스트: 전날 최초 공시 + 다음날 정정 → update, 첫 정보 불변, 두 snapshot.
2. 같은 날 최초+정정 여러 개 → 공시번호별 snapshot 전부 보존, 최신 요약 정상.
3. 동일 접수번호 재실행 → 이력·revision_count·first_collected_at 불변, LLM 재호출 없음.
4. 정정 no-update/60일/기존없음 → 관측 이력 존재, 현 요약 유지.
5. 과거 재실행/동일일 접수번호 역순 → 최신 내용 후퇴 없음.
6. 레거시 날짜별 JSON + 기존 SQLite → 가능한 최초 메타·snapshot 보존, 알 수 없는 수집시각 NULL.
7. 실패→성공 재시도 → 같은 행, 최초 관측시각 보존, 성공 snapshot.
8. DB sync 재실행·legacy 최신 레코드 메타 누락 → DB 기존 최초정보와 snapshot 유지.
9. 기존 etl unittest 회귀 통과, 변경 모듈 import 정상, DB_SCHEMA는 검수자가 생성기로 갱신.

## 진행

- [x] 문서·현행 코드 읽기, 사용자 DB 방식 승인.
- [x] Muse 상세 조사·구현.
- [x] Codex diff 검수·회귀 테스트.
- [x] 기존 DB 안전 적용·기존 자료 이력 적재·스키마 문서 갱신.

## 호출 에이전트 확인 결과

- 변경 전 `tests.test_build_db`, `tests.test_pipeline_modules`: 68 tests, OK.
- 실 DB: etf_records 50행, revision_count>0 0행. 기존 JSON 53개/26폴더.
- JSON 중 같은 ETF 중복 3쌍은 같은 접수번호 재분석이며 과거 정정 snapshot은 아님.
- 기존 JSON에 first_collected_at/collected_at는 모두 없음. 기존 실제 수집시각은 NULL로 유지해야 함.
- 적용 전 SQLite backup: `etl/runs/dart_history_migration_20261004/etf_insight.before.sqlite3`.
