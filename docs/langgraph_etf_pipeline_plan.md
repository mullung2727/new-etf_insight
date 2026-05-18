# LangGraph ETF Extraction Rough Plan

## 전제

- 목표는 신규 ETF 투자설명서 PDF에서 쓸 만한 정보를 구조화하고, PDF에 없는 정보는 추종지수/운용사/PCF 같은 외부 출처로 보강하는 파이프라인을 만드는 것이다.
- 초기에는 PDF 전체 텍스트를 LLM에 그대로 넣어 목표 스키마를 채운다.
- 페이지 분리, 섹션 라우팅, RAG는 처음부터 넣지 않는다. 전체 PDF 방식에서 토큰, 속도, 비용, 누락 문제가 확인되면 후속 최적화 옵션으로 검토한다.
- 최종적으로는 LangGraph로 상태, 분기, 반복, 재시도 흐름을 관리한다.
- 초기에는 Codex CLI의 `codex exec`를 LangGraph 노드에서 자동 호출하는 흐름을 전제로 한다.
- `codex exec` 호출부는 단일 어댑터로 분리해서, 나중에 OpenAI API나 다른 LLM 호출 방식으로 바꿀 수 있게 한다.
- 나중에 API 키를 쓰게 되면 같은 노드 인터페이스에 OpenAI API 호출을 붙일 수 있게 LLM 호출부를 분리한다.

## 전체 방향

```text
Phase -2. Codex exec Hello World 파이프라인
Phase -1. PDF 통요약 LangGraph 파이프라인
Phase 0. 목표 스키마 초안
Phase 1. 전체 PDF 기반 목표 스키마 추출
Phase 2. 구성종목 비중 존재 여부 분기
Phase 3. 외부 리서치 후보 URL 추출
Phase 4. 외부 리서치 결과 병합
Phase 5. 배치 실행/결과 저장
Phase 6. 추출 품질 평가
Phase 7. 페이지 분리/RAG 최적화 여부 결정
```

## Phase -2. Codex exec Hello World 파이프라인

### 목적

- LangGraph 자체가 프로젝트에서 정상 실행되는지 아주 작게 확인한다.
- LangGraph 노드에서 `codex exec`를 자동 호출하고 JSON 결과를 파싱할 수 있는지 확인한다.

### 러프 흐름

```text
start
-> build_prompt
-> call_codex_exec
-> parse_json_result
```

### 입력

```json
{
  "topic": "hello world"
}
```

### 출력

```json
{
  "prompt": "...",
  "codex_output": "...",
  "result": {
    "message": "..."
  }
}
```

### 완료 기준

- LangGraph 그래프를 실행하면 `codex exec`가 자동 호출된다.
- Codex 출력이 JSON으로 파싱되어 최종 결과가 된다.
- 이 단계에서는 PDF, DART, 외부 리서치를 다루지 않는다.

## Phase -1. PDF 통요약 LangGraph 파이프라인

### 목적

- 복잡한 섹션 라우팅 전에, PDF 하나를 읽고 전체 텍스트를 요약 프롬프트로 만드는 최소 흐름을 확인한다.
- 이 단계는 일부러 단순하게 만든다. 토큰 효율이 나쁘더라도 LangGraph와 `codex exec` 자동 호출이 실제로 굴러가는지 보는 용도다.

### 러프 흐름

```text
start
-> load_pdf_text
-> build_summary_prompt
-> call_codex_exec
-> parse_summary_json
```

### 입력

```json
{
  "pdf_path": "downloads/pdfs/20260429000012_11352014.pdf"
}
```

### 출력

```json
{
  "pdf_path": "downloads/pdfs/20260429000012_11352014.pdf",
  "page_count": 56,
  "prompt": "...",
  "codex_output": "...",
  "summary": {
    "summary": "..."
  }
}
```

### 완료 기준

- PDF 텍스트를 읽는다.
- PDF 전체 텍스트를 포함한 요약 프롬프트를 만든다.
- `codex exec`를 자동 호출한다.
- Codex 출력 JSON을 파싱해 요약 결과로 만든다.
- 이 단계에서는 정확한 구조화 추출보다 "`codex exec` 자동 호출로 PDF 요약 루프가 가능한가"만 본다.

## Phase 0. 목표 스키마 초안

### 목적

- 최종적으로 어떤 정보를 채울지 JSON 형태를 대충 정한다.
- 너무 자세히 확정하지 않고, 테스트 PDF 한 개를 저장할 수 있는 최소 구조부터 시작한다.

### 후보 필드

- `fund_name`
- `asset_manager`
- `fund_code`
- `document_type`
- `written_as_of_date`
- `effective_date`
- `risk_grade`
- `asset_class`
- `fund_type`
- `investment_objective`
- `primary_assets`
- `stock_min_ratio`
- `index.name`
- `index.provider`
- `index.isin`
- `index.ticker`
- `index.url`
- `index.description`
- `strategy.replication_method`
- `strategy.uses_full_replication`
- `fees.total_fee`
- `holdings.available_in_pdf`
- `holdings.needs_external_research`
- `holdings.items`
- `risks`
- `missing_fields`
- `source_pages`

### 완료 기준

- "PDF 한 개 결과를 저장할 수 있겠다" 싶은 최소 JSON 구조를 정한다.
- `null` 처리 규칙과 출처 페이지 기록 규칙을 정한다.

## Phase 1. 전체 PDF 기반 목표 스키마 추출

### 목적

- PDF 전체 텍스트를 `codex exec`에 전달해 목표 JSON 스키마를 채운다.
- 페이지 분리나 섹션 라우팅 없이, 먼저 통째 추출 품질을 확인한다.

### 산출물 후보

- `etl/scripts/pdf_langgraph/pdf_analysis_langgraph.py`
- `etl/scripts/pdf_langgraph/summary_schema.json`

### 입력

```json
{
  "pdf_path": "../downloads/pdfs/20260429000012_11352014.pdf"
}
```

### 출력

```json
{
  "is_pre_listing_etf": true,
  "fund_name": "...",
  "asset_manager": "...",
  "index": {
    "name": "...",
    "provider": "...",
    "description": "..."
  },
  "holdings": {
    "available_in_pdf": false,
    "summary": null,
    "items": [],
    "where_to_find_more": []
  },
  "keywords": [],
  "trend_summary": "...",
  "missing_info": []
}
```

### 완료 기준

- PDF 경로를 입력으로 받아 실행할 수 있다.
- PDF 전체 텍스트를 기반으로 `summary_schema.json` 형식의 JSON을 출력한다.
- PDF에 없는 내용은 추측하지 않고 `null`, 빈 배열, `missing_info`로 남긴다.

## Phase 2. 구성종목 비중 존재 여부 분기

### 목적

- PDF 추출 결과의 `holdings.available_in_pdf`를 기준으로 LangGraph 분기를 만든다.
- PDF에 구성종목과 비중이 있으면 외부 검색 없이 저장 가능한 결과로 보낸다.
- PDF에 구성종목과 비중이 없으면 `holdings.where_to_find_more`를 근거로 외부 리서치 단계로 보낸다.

### 산출물 후보

- `etl/scripts/pdf_langgraph/pdf_analysis_langgraph.py`

### 분기 흐름

```text
extract_from_pdf
-> route_holdings

route_holdings:
  holdings.available_in_pdf == true
    -> finalize_pdf_result

  holdings.available_in_pdf == false
    -> build_external_research_targets
```

### 완료 기준

- `holdings.available_in_pdf=true`면 외부 검색 없이 다음 저장 단계로 보낼 수 있다.
- `holdings.available_in_pdf=false`면 외부 리서치 후보 추출 단계로 보낸다.
- `where_to_find_more`가 비어 있으면 `missing_info`에 외부 검색 단서 부족을 남긴다.

## Phase 3. 외부 리서치 후보 URL 추출

### 목적

- PDF 안에 구성종목과 비중이 없을 때만 실행한다.
- `holdings.where_to_find_more`, 지수명, 운용사명, ETF명, PCF 단서를 바탕으로 외부 검색 후보를 만든다.

### 산출물 후보

- `etl/src/new_etf_insight/research_hints.py`

### 출력 예시

```json
{
  "index_name": "Solactive K-Tech Top 10 Index PR",
  "index_isin": "DE000SL0JYE7",
  "index_url": "https://www.solactive.com/indices/?index=DE000SL0JYE7",
  "manager_url": "www.kiwoometf.com"
}
```

### 완료 기준

- `where_to_find_more`에 있는 구체적 단서를 우선 사용한다.
- PDF의 지수명, 지수 산출기관, ETF명, 운용사명을 검색 힌트로 정리한다.
- 단서가 부족하면 외부 검색을 억지로 진행하지 않고 부족 사유를 남긴다.

## Phase 4. 외부 리서치 결과 병합

### 목적

- Solactive factsheet 같은 외부 정보에서 구성종목을 가져와 최종 결과에 합친다.
- 처음에는 자동 브라우징보다 수동 입력도 허용한다.

### 산출물 후보

- `etl/src/new_etf_insight/merge_records.py`

### 입력

- PDF 추출 JSON
- 외부 리서치 JSON

### 출력

- 최종 ETF record

### 완료 기준

- 구성종목이 없으면 `needs_external_research=true`로 표시한다.
- 외부 구성종목 JSON이 있으면 `holdings.items`에 병합한다.

## Phase 5. 배치 실행/결과 저장

### 목적

- `../downloads/pdfs/*.pdf`를 반복 처리한다.
- 개별 PDF 추출 결과와 외부 리서치 필요 여부를 파일로 저장한다.

### 산출물 후보

- `etl/scripts/extract_etf_pdf_info.py`

### 출력

```text
../artifacts/extracted_etfs/{rcept_no}.json
```

### 완료 기준

- 20260429 PDF들에 대해 JSON 결과를 생성한다.
- 실패한 PDF는 실패 이유와 파일 경로를 남긴다.
- 외부 리서치가 필요한 PDF와 필요 없는 PDF가 구분되어 저장된다.

## Phase 6. 추출 품질 평가

### 목적

- 통째 PDF 추출 방식과 구성종목 분기 방식이 실제로 충분한지 확인한다.
- 어떤 필드가 잘 뽑히고, 어떤 필드가 누락/환각/불안정한지 기록한다.

### 산출물 후보

- `../artifacts/extracted_etfs/evaluation.md`

### 입력

- PDF 추출 JSON들
- 원본 PDF

### 출력

- 필드별 품질 메모
- 누락/오탐 사례
- 외부 리서치 분기 정확도
- 다음 단계에서 보강할 항목

### 완료 기준

- 최소 3~5개 PDF 결과를 보고 현재 방식 유지 여부를 판단한다.
- 성능, 비용, 누락 문제가 크지 않으면 페이지 분리/RAG 없이 다음 단계로 간다.

## Phase 7. 페이지 분리/RAG 최적화 여부 결정

### 목적

- 전체 PDF 방식에서 문제가 확인될 때만 페이지 분리, 섹션 라우팅, RAG 중 하나를 검토한다.
- 이 단계는 필수 구현 단계가 아니라 성능/품질 개선 옵션이다.

### 검토 조건

- PDF 길이 때문에 `codex exec`가 실패하거나 너무 느리다.
- 비용 또는 토큰 사용량이 감당하기 어렵다.
- 긴 문서 중간의 핵심 필드가 반복적으로 누락된다.
- 출처 페이지 기록이 제품 요구사항으로 확정된다.

### 옵션

- 페이지별 텍스트 추출
- 키워드 기반 넓은 후보 페이지 선정
- LLM 기반 페이지 선택
- 벡터 검색/RAG

### 완료 기준

- 문제가 명확하지 않으면 구현하지 않는다.
- 필요성이 확인되면 별도 phase로 쪼개서 설계한다.

## 당장 다음 작업

1. Phase 1: PDF 경로를 입력으로 받아 전체 PDF 기반 목표 스키마 JSON을 출력한다.
2. Phase 2: `holdings.available_in_pdf` 기준 LangGraph 분기를 만든다.
3. Phase 3: PDF에 비중이 없을 때 `where_to_find_more` 기반 외부 리서치 후보를 만든다.
