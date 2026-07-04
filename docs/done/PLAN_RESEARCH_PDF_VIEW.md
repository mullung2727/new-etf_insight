# PLAN: 리서치 PDF 보기 버튼

## 목표

`/research` 조회 목록에서 이미 받은(`downloaded=true`) 리포트를 "보기" 버튼으로 새 탭에 바로 열어본다.
재다운로드·네이버 재조회 없이, 서버가 이미 계산해둔 파일 경로를 그대로 재사용한다.

## 배경

`api/routers/research.py:stock_reports`가 각 리포트의 `downloaded` 여부를 판단할 때
이미 `_dest_for(report)` → `dnr.dest_path(...)`로 로컬 파일 경로를 계산한다(`research.py:74-87, 106-121`).
이 계산에 필요한 입력(`itemName`, `itemCode`, `writeDate`, `brokerName`, `pdf_key(pdf_url)`)은
`list_stock_reports`가 이미 다 갖고 있다 — **재조회 없이 그대로 클라이언트로 흘려보내면** "보기" 시점엔
네이버 API를 다시 안 건드리고 파일 시스템만 봐도 된다.

## 결정

- **pdfKey 응답 필드 추가**: `ReportItem`에 `pdfKey: str`(= `dnr.pdf_key(report["pdf_url"])`) 추가.
  기존 루프에서 이미 계산하던 값이라 추가 비용 없음.
- **보기 엔드포인트는 파일시스템만 본다**: `GET /research/stock/{code}/reports/{research_id}/pdf`
  쿼리로 `name`(또는 서버가 `_resolve_name` 재사용), `writeDate`, `brokerName`, `pdfKey`를 받아
  `dnr.dest_path(...)`로 경로 재조립 → 존재하면 `FileResponse(media_type="application/pdf")`, 없으면 404.
  네이버 재조회(`list_stock_reports`) 안 함. 쿼리파라미터 wire 이름은 응답 바디(`ReportItem`)와 동일하게
  **camelCase**(`writeDate`/`brokerName`/`pdfKey`) — FastAPI 함수 인자는 관례대로 snake_case로 두고
  `Query(alias="writeDate")` 식으로 wire 이름만 맞춘다. `research_id`는 path에 있지만 **라우팅/가독성용일
  뿐** 경로 조립엔 안 쓰임(실제 파일 위치는 writeDate/brokerName/pdfKey로만 결정).
- **경로 검증 필수, 404로 통일**: `writeDate`/`brokerName`/`pdfKey`는 클라이언트가 그대로 돌려주는 값이라
  서버가 그대로 신뢰하면 안 됨. `dest_path` 계산 후 `resolved.is_relative_to(DEFAULT_EXPORT_BASE.resolve())`
  확인 — base 밖이거나 파일이 없거나 **둘 다 404로 동일 응답**(400 등으로 분리하지 않음). 경로탈출 시도와
  단순 미존재를 구분해 응답하면 공격자에게 "탈출 성공 여부"를 알려주는 오라클이 되므로 하나로 묶는다.
  `pdfKey`엔 별도 문자 필터링(`basename` 등) 안 함 — `is_relative_to` 체크 하나로 충분하고,
  basename은 악성 입력을 조용히 다른 키로 바꿔치기할 수 있어 오히려 애매하다.
- **프론트**: 상태 컬럼(`page.tsx` 표 "상태" 셀, `downloaded` badge 옆)에 `downloaded===true`일 때만
  `<a href={...} target="_blank" rel="noopener">보기</a>` 노출. 새 컴포넌트/의존성 없음(브라우저 내장 PDF 뷰어).
  `Report` 타입(`page.tsx`)에 `pdfKey: string` 필드 추가 필요(API는 이미 내려주는데 타입엔 없음).
- **서버(api) 변경만**: broker-web은 링크 하나 추가, 새 상태/훅 없음.
- **공개 경계**: 서빙 대상은 증권사가 이미 공개 배포한 종목분석 리포트 PDF(네이버 증권에서 누구나 조회
  가능한 자료) — 개인정보·계정정보 아님. api(:8000)의 "public-read 지향" 원칙과 충돌 없음.

## 단계 (TDD)

1. **api**: `ReportItem.pdfKey` 필드 + `stock_reports`에서 채우기. 기존 `test_research.py` 갱신(필드 존재 확인).
2. **api**: `GET /research/stock/{code}/reports/{research_id}/pdf` 라우트 — path 재조립 + 존재확인 + 경로검증 +
   `FileResponse`. 유닛테스트: 정상 케이스(파일 있음 → 200 + content-type), 없는 파일(404),
   경로 조작 시도(base 밖으로 실제 탈출하는 `pdfKey` → 404 동일 응답).
3. **broker-web**: `Report` 타입에 `pdfKey` 추가 + "보기" 링크 추가. Playwright e2e: `downloaded=true`
   행에 "보기" 링크 보이고 `href`가 올바른 쿼리 포함하는지만 확인(실제 새 탭 오픈은 검증 범위 밖).
4. 통합 검증: 실 종목으로 다운로드 → 보기 클릭 → PDF 새 탭 로드 확인(수동, 사용자 화면 확인 필요).

## 놓치기 쉬운 포인트

- `pdfKey`를 URL 쿼리에 그대로 실으면 인코딩(`%2F` 등) 필요 — `encodeURIComponent`.
- 경로 검증 빠뜨리면 `pdfKey=../../../../etc/passwd` 류로 임의 파일 read 가능 — 반드시 base 밖 차단.
- `downloaded=false` 행엔 "보기" 자체를 안 보여줘서 없는 파일 요청 자체를 프론트에서 막음(백엔드 404는 방어선일 뿐).
