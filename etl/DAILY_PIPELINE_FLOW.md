# 일 단위 ETF 수집/분석 파이프라인 흐름

이 문서는 `etl` 기준 일 단위 ETF 수집, PDF 분석, 기재정정 처리 흐름을 잃지 않기 위한 작업 기준이다.

## 목적

- DART에서 신규 및 상장 예정 ETF 후보를 일 단위로 수집한다.
- 일반 공시는 PDF를 다운로드하고 분석해 ETF JSON을 생성한다.
- 기재정정 공시는 새 ETF로 오인하지 않는다.
- 기재정정 공시는 기존 ETF JSON이 있으면 정정 사유를 먼저 판단하고, 필요한 경우에만 업데이트한다.
- 날짜별 JSON과 DB 성공 snapshot을 함께 조회해 전날 ETF에도 정정을 연결한다.
- 업데이트 시 `first_rcept_dt`, `first_rcept_no`, `first_collected_at`을 보존한다.
- 접수번호별 관측·성공 snapshot은 SQLite `etf_filing_history`에 보존한다.

## 핵심 식별자

ETF 단위 저장 키는 DART viewer 본문에서 추출한 `펀드코드`와 DART `corp_code`를 조합한다.

```text
etf_key = "{corp_code}_{fund_code}"
```

예:

```text
corp_code = 00104500
fund_code = ET942
etf_key = 00104500_ET942
```

`rcept_no`는 공시 이벤트 단위 키라서 ETF 단위 저장 키로 쓰지 않는다.

## 현재 코드 역할

- `etl/scripts/collect_etf_candidates.py`
  - DART 후보 수집
  - 필요 시 PDF 다운로드
- `etl/src/new_etf_insight/dart_client.py`
  - DART `list.json` 조회
- `etl/src/new_etf_insight/dart_pdf.py`
  - DART 공시 HTML 조회
  - 대표 투자설명서 PDF 다운로드
- `etl/src/new_etf_insight/dart_viewer.py`
  - DART viewer 섹션 파라미터 추출
  - DART viewer 본문 텍스트 수집
  - `펀드코드` 추출
  - `etf_key` 생성
- `etl/scripts/pdf_langgraph/pdf_analysis_langgraph.py`
  - 이미 다운로드된 PDF 분석
  - PDF 내부 구성종목 여부에 따른 외부 리서치 분기
  - 기재정정 공시의 업데이트 필요 여부 LLM 판단
  - 기존 ETF JSON을 기재정정 내용 기준으로 업데이트
  - 분석 결과 JSON 저장

## 전체 목표 흐름

```text
일자 입력
-> DART API 후보 수집
-> (rcept_dt, rcept_no) 순으로 정렬
-> 후보별 처리
```

후보별 공통 선행 단계:

```text
DART 후보 메타 확보
-> DART viewer 본문에서 fund_code 추출
-> etf_key = corp_code + "_" + fund_code 생성
-> report_nm으로 기재정정 여부 판단
```

## 일반 공시 흐름

```text
기재정정 아님
-> 기존 ETF가 있으면 분석을 생략하고 관측 이력을 기록
-> PDF 다운로드 경로 파악
-> PDF 다운로드
-> PDF 분석 LangGraph 실행
-> records/{etf_key}.json 저장
```

저장 시:

```json
{
  "source": {
    "rcept_no": "공시번호",
    "rcept_dt": "공시일",
    "corp_code": "운용사 DART 코드",
    "corp_name": "운용사명",
    "report_nm": "공시명",
    "pdf_path": "분석 PDF 경로"
  },
  "first_rcept_dt": "최초 공시일",
  "first_rcept_no": "최초 공시번호",
  "first_collected_at": "최초 수집시각(UTC ISO 8601, 모르면 null)",
  "collected_at": "이번 공시 최초 관측시각(UTC ISO 8601, 모르면 null)",
  "revision_count": 0,
  "route": "PDF 분석 라우트",
  "summary": {},
  "research_prompt": ""
}
```

## 기재정정 공시 흐름

```text
기재정정임
-> runs/*/records/{etf_key}.json 및 DB 성공 snapshot 조회
-> 최신 (rcept_dt, rcept_no) record 선택
```

기존 JSON과 DB 성공 snapshot이 모두 없는 경우:

```text
기존 ETF JSON 없음
-> correction_without_existing_record로 skip
-> 관측 이력 저장 (성공 snapshot은 null)
-> 원본 record가 나중에 도착하면 재처리 가능
```

기존 JSON이 있는 경우:

```text
기존 ETF JSON 있음
-> DART viewer 전체 텍스트 수집
-> correction_review LLM 실행
-> 업데이트 필요 여부 판단
```

업데이트 불필요:

```text
needs_update = false
-> 기존 JSON 수정하지 않음
-> skipped 관측 이력 저장 및 결과 반환
```

업데이트 필요:

```text
needs_update = true
-> 기존 JSON 읽기
-> 기존 JSON + 정정 사유/정정 전후 내용을 LLM에 전달
-> 업데이트된 JSON 생성
-> 코드가 보호 필드 보정
-> 이전 성공 snapshot을 이력에 보존
-> records/{etf_key}.json 갱신 및 이번 성공 snapshot 보존
```

코드가 반드시 보존/관리할 값:

```text
first_rcept_dt, first_rcept_no, first_collected_at 유지
source는 최신 정정공시 기준으로 갱신
revision_count는 새 rcept_no일 때만 증가
```

동일 접수번호의 성공 snapshot은 재실행해도 바꾸지 않는다. JSON이 없어도 DB snapshot을 재사용하며 LLM을 다시 호출하지 않는다. 과거 공시 재실행은 최신 요약을 후퇴시키지 않는다. 60일 이상 정정·업데이트 불필요 정정·분석 실패도 관측 이력을 남긴다. 실패 재시도는 같은 이력 행과 최초 관측시각을 유지한다.

기존 날짜별 JSON은 중복 제거 전에 이력으로 적재한다. 원본번호·수집시각을 알 수 없는 경우 `null`로 남기며 공시일·파일 mtime·정정 수집시각으로 추정하지 않는다.

## 기재정정 판단 원칙

- 기재정정이라는 이유만으로 PDF를 다시 다운로드하거나 분석하지 않는다.
- 먼저 DART 정정 사유와 정정 전/후 내용을 본다.
- 업데이트 필요 여부는 하드코딩된 화이트리스트/블랙리스트가 아니라 LLM이 판단한다.
- 단, LLM 판단 후 실제 메타 필드 보존과 revision_count 계산은 코드가 담당한다.

## 아직 하지 않을 것

- MCP 생성
- alias 파일 생성
- DART 원격 전체 과거 공시 이력 소급 수집 (현재 보유 JSON과 앞으로 관측하는 공시는 보존)
- 종목코드 기반 키 보강
- 기재정정 PDF를 무조건 재분석
- DART 투자설명서 기반 상장예정일 추출

## 다음 구현 우선순위

완료:

1. DART viewer 본문에서 `fund_code`를 알고리즘으로 추출하는 유틸 추가
2. `etf_key = corp_code + "_" + fund_code` 생성 유틸 추가
3. DART viewer 전체 텍스트 수집 유틸 추가
4. `correction_review` 프롬프트와 스키마 추가
5. `review_correction_filing(filing)` 함수 추가
6. 일 단위 처리 흐름에서 `fund_code`/`etf_key` 생성 연결
7. 일반 공시/기재정정 공시 분기 연결
8. 기재정정 기존 record 존재 여부에 따른 skip/update/create 흐름 연결
9. `needs_update=true`일 때 기존 ETF JSON을 정정 내용 기준으로 업데이트
