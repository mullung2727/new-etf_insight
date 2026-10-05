# Codex 직접 검수 1차 보완 요청

원 명세 `docs/PLAN_DART_HISTORY_PRESERVATION.md`의 구현 초안을 직접 읽었고, 현행 82 tests를 실제 실행해서 OK를 확인했다. 아래는 통과 테스트가 놓친 사용자 요구 위반이다.

명령 실행 금지, 파일만 작성. 테스트·실행·검증은 호출 에이전트가 담당한다. 셸/외부 호출 금지.

수정 허용 파일은 원 명세와 같은 네 코드·테스트 파일뿐. 조사 문서는 `docs/DART_HISTORY_PRESERVATION_PROGRESS.md`만 갱신. 직접 코드 수정은 Codex가 하지 않는다.

## 1. history 상태 전이와 snapshot 누락

`_upsert_history_preserve`는 failed -> created/updated만 허용한다.

- pipeline은 skipped/correction_without_existing_record를 기존 JSON이 나중에 도착하면 재처리한다. 그런데 이력이 skipped라 성공 snapshot은 영구히 저장되지 않는다.
- failed 재시도 후 review가 needs_update=false 또는 오래된 공시로 skipped면 이력이 계속 failed로 남는다. 이후 매번 LLM을 다시 호출하는 원인이 된다.
- history created/updated인데 JSON이 없으면 현재 pipeline은 LLM 재호출/rebuild를 허용한다. 성공 snapshot이 DB에 있으므로 재분석하지 않고 복구하거나 기존정보로 건너뛰어야 한다.
- 같은 번호가 JSON에 이미 있으면 history 확인·저장을 생략하는데, 기존 history에 failed/skipped가 있고 JSON은 성공된 경우 최종 sync가 올바른 성공 snapshot을 채워야 한다.

수정: 성공 snapshot이 이미 있는 row는 절대 덮지 않되, snapshot 없는 failed나 사정성 skipped가 이번 실제 성공을 얻으면 같은 row에 snapshot·최종 action/reason만 채운다. 실패 재시도 -> skipped도 terminal observation으로 전이시킨다. 첫 수집시각과 최초 filing_json은 유지한다(같은 공시 메타 변경 이유 없음). NULL인 legacy 시각을 재시도 시각으로 날조하지 않는다. 최신 결과가 없어도 성공 snapshot은 DB에서 재사용한다.

테스트: absent-parent correction skipped -> original arrives -> correction succeeds; failed -> no-update -> rerun avoids LLM; 성공 JSON 유실 후 재실행 LLM 미호출·snapshot 동일.

## 2. DB sync 최신성·최초 정보 보존

`_load_records`는 rcept_dt만 비교하고 같은 날 접수번호를 비교하지 않는다. `_upsert_record`는 DB에 더 최신 공시가 있어도 오래된 JSON으로 INSERT OR REPLACE한다.

`_resolve_first_meta`는 최신 JSON > 기존 DB > earliest 순서다. 그래서 최신 JSON에 first_rcept_dt/first_rcept_no/first_collected_at가 다른 값으로 설정돼 있으면 이미 보존한 최초정보를 다시 덮는다. DB가 비어있는 레거시 여러 날짜 JSON도 최신 JSON에 first_rcept_dt가 최신 날짜로 초기화돼 있으면 최초 날짜를 놓친다.

수정: record 선택은 `(rcept_dt,rcept_no)` 튜플로 결정. DB에 더 최신 source가 있으면 current row와 holdings는 되돌리지 않고 오래된 JSON의 history만 보존. 기존의 신뢰 가능한 최초정보를 우선 보존하고, 레거시 여러 파일에서 최초 날짜와 일치하는 공시번호를 결정할 때 공시일/번호를 쌍으로 맞춘다. 알려진 최초 공시일이 현재 확보한 source보다 앞서 있는데 그 공시번호는 없으면 최신 번호를 최초 번호로 위조하지 않고 NULL 유지한다. 알 수 없는 원본 수집시각도 계속 NULL.

테스트: new DB old+new 레거시 JSON 각각 first_rcept_dt가 자기 날짜인 경우 earliest 보존; existing DB 메타와 최신 JSON 메타 충돌시 DB 최초값 유지; newer DB sync older-only JSON에서 source/holdings/first 메타 불변; 같은 일자 여러 접수번호 입력 순서와 무관하게 큰 번호 유지; 원본번호 유실한 레거시 first_rcept_dt<source.rcept_dt이면 first_rcept_no NULL.

## 3. 기존 기록 조회의 DB fallback

현재 pipeline은 json_records만 current로 삼는다. DB에 최신 snapshot이 있는데 JSON은 없거나 더 오래된 파일만 있으면 correction_without_existing_record 또는 오래된 요약으로 정정을 수행한다.

수정: DB history snapshot을 current 후보로 함께 비교하고, chosen previous_record의 첫 메타도 기존 DB 값을 반영한다. 신뢰 가능한 JSON 최신 요약과 DB snapshot을 접수일/번호 기준으로 비교한다. 동일 번호 JSON은 수동 재분석한 최신 요약일 수 있으므로 무조건 DB snapshot으로 바꾸진 않는다. 성공했던 동일 번호 재실행은 LLM/PDF 호출없이 기존 snapshot으로 복구 가능해야 한다.

테스트: DB-only previous + new correction; old JSON + newer DB then correction; DB-only same-rcept rerun.

## 4. 새 일반 공시도 관측 이력 남기기

일반 공시이면서 같은 ETF의 기존 기록이 있을 때 branch는 existing_record를 반환하고 history를 쓰지 않는다. 최초/정정 외 공시도 접수번호별 관측 원장에 남겨야 한다. 새 분석/정책 확장은 하지 말고 기존 skip 동작·사유를 유지하면서 메타·수집시각 관측만 저장한다.

테스트: 같은 ETF의 다른 비정정 공시 → 요약 유지·관측 history 추가·rerun 중복0.

## 변경 원칙

- 새 CLI/API/파일/추상화 없음. existing helper 안에서 가장 작은 변경.
- 최초정보·snapshot immutable 규칙과 terminal 상태 전이를 구분할 것.
- 기존 82 tests는 유지하고 위 사례를 같은 기존 test 클래스에 추가.
- 조사 문서에 Codex 발견 사항과 처리 근거, 새 tests 이름을 기록.
- DB/JSON 실제 자료는 수정 금지. Codex가 검증 후 적용한다.
