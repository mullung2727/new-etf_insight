# PLAN: 리서치 리포트 선택 다운로드

## 목표

`broker-web/app/research/page.tsx`의 조회 결과 목록 왼쪽에 체크박스를 추가하고, 전체 선택/해제와 선택된 리포트만 다운로드하는 흐름을 만든다.

현재 `POST /research/stock/{code}/download`는 종목+기간 기준 전체 목록을 다시 조회해서 일괄 다운로드한다. 선택 다운로드를 안정적으로 만들려면 프론트 선택 상태만 추가하면 부족하고, `api/routers/research.py`가 선택된 `researchId` 목록을 받아 해당 건만 다운로드하도록 계약을 확장해야 한다.

## 성공 기준

- 조회 후 각 리포트 행 왼쪽에 체크박스가 보인다.
- 헤더 체크박스로 현재 조회 목록 전체 선택/해제가 가능하다.
- 일부 선택 시 헤더 체크박스가 indeterminate 상태가 된다.
- 다운로드 버튼은 선택된 리포트만 다운로드한다.
- 선택이 없으면 다운로드를 시작하지 않거나 명확한 안내를 보여준다.
- 다운로드 완료 후 목록의 `downloaded` 상태가 갱신된다.
- 기존 “기간 기준 전체 다운로드” 테스트는 선택 목록이 없을 때 기존 동작으로 유지되거나, 의도적으로 선택 필수로 바꾼 경우 테스트가 그 정책을 반영한다.

## 결정

- v1 정책: 조회 목록에서 선택된 항목만 다운로드한다.
- 기본 선택값: 조회 직후 `downloaded=false`인 항목을 자동 선택한다.
  - 이미 받은 항목까지 기본 선택하면 사용자가 다시 눌렀을 때 대부분 skip이라 UX가 둔하다.
  - 사용자는 이미 받은 항목도 수동 선택 가능하게 둔다. 선택 시 서버는 기존처럼 파일 존재 여부로 skip한다.
- 선택 식별자: `researchId`만 프론트에서 보낸다.
  - 서버는 같은 `code/name/since/until`로 목록을 다시 조회한 뒤 `researchId` 교집합만 다운로드한다.
  - 프론트가 `pdf_url`이나 경로를 보내지 않게 해서 신뢰 경계를 단순하게 유지한다.

## 1단계: API 계약 확장

### 변경

- `api/routers/research.py`
  - `DownloadRequest`에 `researchIds: list[str] | None = None` 추가.
  - `_run_job(job, since, until)`에서 `researchIds`가 있으면 `list_stock_reports(...)` 결과를 해당 ID로 필터.
  - `job["total"]`은 필터 후 건수로 설정.
  - 요청한 ID가 목록에 없으면 조용히 제외하고, 결과적으로 total 0이면 done 처리.

### TDD

- `api/test_research.py`에 테스트 추가:
  - `test_download_only_selected_reports`
    - fake reports 2개 구성.
    - `researchIds=["22"]`로 POST.
    - `download_pdf`가 `22`에 대해서만 호출되는지 검증.
    - job 최종 상태 `total=1`, `downloaded=1`.
  - `test_selected_download_skips_existing`
    - 선택한 report 파일이 이미 있으면 `download_pdf` 호출 없이 `skipped=1`.
  - `test_selected_ids_missing_from_current_query_are_ignored`
    - `researchIds=["not-found"]`.
    - `total=0`, `downloaded=0`, `skipped=0`, `status=done`.

### 놓치기 쉬운 포인트

- `researchId`와 저장 파일 키가 다를 수 있다.
  - 현재 `_dest_for()`는 `dnr.pdf_key(report["pdf_url"])`를 사용한다.
  - 필터링은 `researchId`로 하되, 저장/skip 판정은 반드시 기존 `_dest_for()`를 그대로 사용해야 한다.
- 스레드 내부에서 job dict를 직접 갱신한다.
  - 기존 구조 유지하되, 테스트가 빠르게 끝나도록 `REQUEST_SLEEP=0` monkeypatch를 계속 사용한다.
- 선택 ID가 기간 필터 밖이면 서버 재조회 결과에 없을 수 있다.
  - 에러보다 제외가 v1에 적합하다. UI는 현재 조회 결과에서만 선택하므로 정상 경로에서는 발생하지 않는다.

## 2단계: 프론트 선택 상태 추가

### 변경

- **선행: checkbox 컴포넌트 추가.** `broker-web/components/ui/checkbox`가 없다.
  - `npx shadcn@latest add checkbox`로 추가(또는 `@base-ui/react` Checkbox 직접 래핑).
  - shadcn checkbox는 `checked="indeterminate"`를 지원하므로 헤더 indeterminate은 ref 조작 없이 값으로 처리한다.
- `broker-web/app/research/page.tsx`
  - `selectedReportIds: Set<string>` 상태 추가.
  - `loadReports()` 성공 후 `downloaded=false`인 `researchId`를 기본 선택.
  - 종목/기간/조회 결과가 바뀌면 이전 선택을 새 목록 기준으로 정리.
  - 테이블 첫 열에 체크박스 추가.
  - 헤더 체크박스로 전체 선택/해제.
  - 헤더 체크박스는 `checked="indeterminate"` 값으로 부분선택 표시(ref 조작 불필요).
  - 선택 개수 표시: 예) `3건 선택됨`.
  - 다운로드 버튼은 `selectedReportIds.size === 0 || running`이면 비활성화.

### TDD

- 가능하면 Playwright component/e2e보다 우선 page 수준 테스트가 이미 없으므로, 최소 e2e로 검증한다.
- `broker-web` Playwright 테스트 추가 후보:
  - API 응답 mock 가능하면 `/research/search`, `/research/stock/.../reports`, `/research/stock/.../download`, `/research/jobs/...`를 route mock.
  - `__tests__/research-selective-download.spec.ts`
    - 종목 선택 후 조회.
    - 미다운로드 2건, 다운로드됨 1건 응답.
    - 기본 선택이 미다운로드 2건인지 확인.
    - 하나 체크 해제.
    - 다운로드 클릭.
    - POST body의 `researchIds`가 선택된 1건만 포함하는지 확인.

### 놓치기 쉬운 포인트

- `Set` 상태는 직접 mutate하면 React가 렌더링을 놓친다.
  - 항상 `new Set(prev)`로 새 객체를 반환한다.
- `TableRow`에는 현재 클릭 이동 동작이 없지만, 체크박스 셀은 향후 행 클릭 추가에도 안전하게 `onClick(e.stopPropagation())`를 둘 수 있다.
- 전체 선택 대상은 “현재 조회된 모든 행”으로 할지 “미다운로드 행만”으로 할지 헷갈릴 수 있다.
  - v1은 모든 행을 전체 선택 대상으로 둔다.
  - 단, 조회 직후 기본 선택만 미다운로드 행으로 한다.
- 조회 결과가 0건이면 전체 선택 체크박스는 disabled 상태가 자연스럽다.

## 3단계: 프론트 다운로드 요청 변경

### 변경

- `startDownload()` body에 `researchIds: Array.from(selectedReportIds)` 추가.
- 선택이 없으면 POST하지 않고 `setError("다운로드할 리포트를 선택해줘")`.
- 다운로드 완료 후 `loadReports()`를 호출하면 선택 상태는 새 downloaded 상태 기준으로 다시 계산된다.

### TDD

- e2e/mock 테스트에서 POST body 확인:
  - `name`, `since`, `until`, `researchIds`가 같이 전달되는지.
  - 선택 해제한 ID가 body에 없는지.
- 선택 0건:
  - 다운로드 버튼 disabled 또는 클릭 시 에러 문구 확인.
  - 네트워크 POST가 발생하지 않는지 확인.

### 놓치기 쉬운 포인트

- `loadReports()`는 `useCallback`이고 `poll()`에서 참조한다.
  - `selectedReportIds`를 `loadReports` dependency에 넣으면 불필요한 재생성이 생길 수 있다.
  - 선택 초기화는 `setData(await r.json())` 직후 로컬 변수로 처리하는 게 단순하다.
- job 진행 중 선택 상태를 바꾸는 것을 허용할지 막을지 정해야 한다.
  - v1은 진행 중에도 체크박스 변경은 가능하지만, 실행 중 job에는 영향 없다고 본다.
  - 혼란을 줄이려면 running 중 체크박스와 조회/다운로드 버튼을 disabled 하는 방안도 가능하다.

## 4단계: 통합 검증

### 검증 명령

API:

```powershell
cd api
.\.venv\Scripts\python.exe -m pytest test_research.py
```

broker-web:

```powershell
cd broker-web
npm run lint
npm run test -- __tests__/research-selective-download.spec.ts
```

수동 검증:

```powershell
.\scripts\restart_all_servers.ps1
```

브라우저에서:

1. `/research` 진입.
2. 종목 검색 후 선택.
3. 기간 설정 후 조회.
4. 일부 항목만 체크.
5. 다운로드 클릭.
6. 진행률 완료 후 선택한 항목만 `받음`으로 바뀌는지 확인.

### 놓치기 쉬운 포인트

- README 기준 최종 검증은 `broker-web`을 `next build && next start`로 하는 게 더 안전하다.
- API는 현재 `POST /research/...`를 제공하므로 `api`의 public-read 경계와 충돌한다.
  - 이번 작업은 기존 research POST를 확장하는 범위로 한정한다.
  - 새 write endpoint를 추가하지 않는다.
- 실제 네이버 다운로드는 외부 네트워크와 디스크 쓰기가 걸린다.
  - 자동 테스트는 반드시 mock으로 하고, 수동 검증만 실제 다운로드를 사용한다.

## 작업 순서

1. API 테스트 추가 -> 실패 확인.
2. API `researchIds` 필터 구현 -> API 테스트 통과.
3. broker-web e2e/mock 테스트 추가 -> 실패 확인.
4. `page.tsx` 체크박스/선택 상태 구현 -> 프론트 테스트 통과.
5. 수동으로 `restart_all_servers.ps1` 실행 후 `/research` 흐름 확인.
