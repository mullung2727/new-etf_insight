# 최종 검수 보완 — 파일 편집만

명령 실행 금지, 파일만 작성. 테스트·실행·검증은 호출 에이전트가 담당한다. 외부 도구 호출 금지.
수정 범위는 기존 명세의 4개 코드·테스트 파일 및 docs/DART_HISTORY_PRESERVATION_PROGRESS.md만. 실 DB, runs, 비밀 파일 변경 금지.

## 1. 기존 테스트 경로 격리 (필수)

Codex가 2차 구현 도중 테스트를 실행하니 94 tests 중 기존 DailyPipelineTest에서 failures 3/errors 1. 원인은 기존 fixture가 base_dir/records라서 pipeline의 records_dir.parent.parent 및 DB 계산이 임시 폴더 밖 공유 DB를 가리키기 때문. 절대 공유 DB 삭제하거나 제품 DB fallback을 제거하지 말 것.

DailyPipelineTest의 실제 run_daily_pipeline 호출 fixture만 base_dir/runs/<날짜>/records와 같은 날짜 pdfs로 바꿔 매번 temp 안 DB가 되도록 격리하라. 기존 assert와 동작 기대는 유지. 새로운 history 테스트는 이미 temp/runs/<날짜> 형태면 그대로.

## 2. 최초 수집시각 추정 금지 및 최초번호 일치

현재 _resolve_first_meta는 earliest_record의 collected_at을 원래 공시의 시간으로 무조건 사용한다. earliest surviving JSON이 정정이고 first_rcept_dt는 더 오래된 원본을 기억하는 상황에서 원본 수집시각을 정정 시각으로 채우는 버그다. 기존 DB first_rcept_dt=20260601, first_rcept_no=NULL, first_collected_at=NULL; 살아있는 correction JSON source=20260610, first_rcept_dt=20260601, collected_at=2026-06-10T00:00:00+00:00, first_collected_at=NULL이면 original first_collected_at와 first_rcept_no는 NULL이어야 한다. DB 없는 같은 상황도 NULL. history의 correction 첫 관측시각만 6/10이어야 한다.

최초 메타 식별을 일관되게 해결하고 복잡한 분기를 최소화하라. 날짜·번호에 일치하는 원본 JSON의 collected_at, 또는 명시적으로 보존한 first_collected_at만 최초 수집시각 근거로 사용할 수 있다. 기존 DB에 알고 있는 최초 시각은 최신 JSON의 다른 시각으로 덮지 않는다. 단순히 earliest surviving source만으로 원본번호를 붙이지 않는다.

daily_pipeline._first_meta도 first_rcept_dt<earliest surviving source 날짜이고 원본번호 없는 경우 정정번호를 first_rcept_no로 붙이지 않아야 한다. 기존 legacy correction-only JSON + 다음 정정 → first_rcept_no/first_collected_at NULL, first_rcept_dt 원본 날짜 유지를 테스트하라. get_db_latest_snapshot의 DB 최초번호 NULL을 snapshot의 잘못된 정정번호로 보완하지 않도록 함께 검수.

## 3. 재시도 공시별 시각 일치

현재 collected_at은 failed history에서만 가져오므로 skipped/correction_without_existing_record -> 원본 등장 -> 성공 시 JSON.collected_at은 재시도 시각, history.first_collected_at은 첫 관측시각이 된다. 기존 history가 있으면 action과 무관하게 history의 최초 관측시각을 사용하라. NULL은 재시도 시각으로 위조하지 말 것. _save_* 내부의 collected_at or now가 NULL을 바꾸는 문제도 호출·헬퍼 경계에서 처리하라 (기존 직접 호출의 기본값 호환 유지).
테스트: skipped history T1 -> 성공 T2에서 history.first_collected_at 및 snapshot.collected_at 모두 T1. failed 최초시각 NULL -> 성공해도 NULL. 최초 신규는 정상 UTC 시각.

## 4. 이력 성공 snapshot reconciliation

현재 _upsert_history_preserve는 skipped 중 correction_without_existing_record만 성공으로 전환한다. 이미 성공 JSON이 실제 존재한다면 다른 skipped 상태도 sync가 snapshot을 채워야 한다. 성공 snapshot 있는 행은 절대 교체하지 말 것. NULL인 행에 신뢰 가능한 성공 JSON이 들어오면 created/updated로 채우되 최초 filing_json과 시각 보존. terminal skipped는 pipeline 재분석은 하지 않는다. 테스트: skipped/no-update + 성공 JSON -> sync로 snapshot 복구.

## 5. 성공 snapshot 복구 저장 실패

pipeline 복구 record_path.write_text를 except Exception: pass로 삼키면 파일이 안 생겼는데 성공 skip을 보고한다. 이 silent failure는 제거해라. 기존 pipeline I/O 예외 처리 원칙을 따라 명확히 실패시키고 성공 이력 snapshot은 건드리지 않는다. 요청하지 않은 복잡한 복구 계층 추가 금지.

완료 후 진행 문서에서 1차의 '미구현/재분석 허용' 설명을 최종 구현에 맞게 정정하고, 테스트 이름을 적어라. 직접 실행 금지.
