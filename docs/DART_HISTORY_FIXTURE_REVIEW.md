# 테스트 경로 최종 수정 (범위 좁힘)

명령 실행 금지, 파일만 작성. 테스트·실행·검증은 호출 에이전트가 담당한다. 외부 도구 호출 금지.

허용 편집은 etl/tests/test_pipeline_modules.py 및 docs/DART_HISTORY_PRESERVATION_PROGRESS.md만. 다른 코드와 실제 DB 수정 금지.

DailyPipelineTest에 방금 추가한 setUp/tearDown의 tempfile.TemporaryDirectory 재정의는 잘못된 우회다. `with`는 객체 인스턴스에 넣은 __enter__가 아니라 타입의 special method를 조회하므로 ctx.__enter__=_enter는 효과가 없다. 이 setUp/tearDown 전체를 제거하라. CRLF 때문에 실패하면 find 문자열에 실제 CRLF를 정확히 사용하라. 쉘 실행으로 해결 금지.

8개 fixture의 한 줄씩 직접 수정만 하라. 멀티라인 find 금지: 한 줄 텍스트 자체에는 CRLF가 들어가지 않으므로 단일줄 find는 문제 없다.

- `records_dir = base_dir / "records"` → `records_dir = base_dir / "runs" / "20260429" / "records"`
- `pdf_dir = base_dir / "pdfs"` → `pdf_dir = base_dir / "runs" / "20260429" / "pdfs"`

DailyPipelineTest 안의 8회 반복에만 적용. 입력 날짜가 다른 60일 테스트도 폴더 날짜는 현재 테스트 assert에 영향을 주지 않지만 원하면 해당 fixture만 20260531로 맞춰도 된다. 기존 asserts·mocks 및 새 history tests는 건드리지 말 것. tempfile 라이브러리를 재정의하거나 제품코드를 우회하지 말 것.

완료 문서에 이 fixture 실패와 직접 경로 변경으로 해결한 점만 짧게 추가. 실행은 Codex가 맡는다.
