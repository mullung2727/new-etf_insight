# LangGraph ETF Extraction Rough Plan

## 전제

- 목표는 신규 ETF 투자설명서 PDF에서 쓸 만한 정보를 구조화하고, PDF에 없는 정보는 추종지수/운용사/PCF 같은 외부 출처로 보강하는 파이프라인을 만드는 것이다.
- PDF 전체를 매번 LLM에 그대로 넣는 방식은 토큰 낭비가 크고, 누락/오탐 가능성이 높다.
- 최종적으로는 LangGraph로 상태, 분기, 반복, 재시도 흐름을 관리한다.
- 다만 초기에는 Codex 구독형 UI를 사람이 직접 쓰는 흐름을 전제로 한다.
- Codex 구독형은 일반적인 API provider처럼 LangGraph 런타임에서 안정적으로 직접 호출하기 어렵다고 보고, 처음에는 "프롬프트 파일 생성 -> 사람이 Codex에 입력 -> 결과 JSON 저장" 방식으로 시작한다.
- 나중에 API 키를 쓰게 되면 같은 노드 인터페이스에 OpenAI API 호출을 붙일 수 있게 LLM 호출부를 분리한다.

## 전체 방향

```text
Phase -2. Codex 구독형 Hello World 파이프라인
Phase -1. PDF 통요약 LangGraph 파이프라인
Phase 0. 목표 스키마 초안
Phase 1. PDF 텍스트/페이지 추출
Phase 2. 섹션/페이지 라우팅
Phase 3. PDF 기반 정보 추출 프롬프트 생성
Phase 4. LangGraph 상태 그래프 도입
Phase 5. 외부 리서치 후보 URL 추출
Phase 6. 외부 리서치 결과 병합
Phase 7. 배치 실행/저장
```

## Phase -2. Codex 구독형 Hello World 파이프라인

### 목적

- LangGraph 자체가 프로젝트에서 정상 실행되는지 아주 작게 확인한다.
- Codex 구독형을 직접 API처럼 호출하지 않고도, 사람이 Codex에 넣을 입력과 사람이 붙여 넣을 출력을 파이프라인 단계로 다룰 수 있는지 확인한다.

### 러프 흐름

```text
start
-> build_prompt
-> wait_for_manual_codex_result
-> save_result
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
  "prompt_path": "artifacts/prompts/hello_world.md",
  "manual_result_path": "artifacts/manual_results/hello_world.json",
  "final": {
    "message": "..."
  }
}
```

### 완료 기준

- LangGraph 그래프를 실행하면 Codex에 넣을 프롬프트 파일이 생성된다.
- 사람이 Codex 결과를 JSON 파일로 저장하면 다음 실행에서 그 결과를 읽어 최종 결과로 만든다.
- 이 단계에서는 PDF, DART, 외부 리서치를 다루지 않는다.

## Phase -1. PDF 통요약 LangGraph 파이프라인

### 목적

- 복잡한 섹션 라우팅 전에, PDF 하나를 읽고 전체 텍스트를 요약 프롬프트로 만드는 최소 흐름을 확인한다.
- 이 단계는 일부러 단순하게 만든다. 토큰 효율이 나쁘더라도 LangGraph와 Codex 수동 루프가 실제로 굴러가는지 보는 용도다.

### 러프 흐름

```text
start
-> load_pdf_text
-> build_summary_prompt
-> wait_for_manual_codex_summary
-> save_summary
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
  "prompt_path": "artifacts/prompts/20260429000012_summary.md",
  "summary_path": "artifacts/manual_results/20260429000012_summary.json"
}
```

### 완료 기준

- PDF 텍스트를 읽는다.
- Codex에 붙여넣을 요약 프롬프트 파일을 만든다.
- 사람이 Codex에서 요약 결과를 받아 JSON으로 저장하면 파이프라인이 그 결과를 읽는다.
- 이 단계에서는 정확한 구조화 추출보다 "수동 Codex 루프가 가능한가"만 본다.

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

## Phase 1. PDF 텍스트/페이지 추출

### 목적

- PDF 전체를 LLM에 바로 던지지 않고 페이지별 텍스트로 분리한다.

### 산출물 후보

- `etl/src/new_etf_insight/pdf_pages.py`

### 입력

```text
../downloads/pdfs/20260429000012_11352014.pdf
```

### 출력

```json
[
  {"page": 1, "text": "..."},
  {"page": 2, "text": "..."}
]
```

### 완료 기준

- 페이지 수와 페이지별 텍스트 길이를 확인할 수 있다.
- 페이지별 텍스트를 파일로 캐시할 수 있다.

## Phase 2. 섹션/페이지 라우팅

### 목적

- 필요한 페이지 후보만 찾는다.
- 처음에는 LLM 없이 키워드 매칭으로 시작한다.

### 산출물 후보

- `etl/src/new_etf_insight/section_locator.py`

### 출력 예시

```json
{
  "basic_info_pages": [1, 4, 9],
  "strategy_pages": [14, 19, 20],
  "fee_pages": [4, 33],
  "index_pages": [4, 14, 19],
  "risk_pages": [4, 20, 21]
}
```

### 완료 기준

- 테스트 PDF에서 기초지수/투자전략 페이지가 잘 잡힌다.

## Phase 3. PDF 기반 정보 추출 프롬프트 생성

### 목적

- Codex에 붙여넣을 수 있는 짧고 정확한 프롬프트 패키지를 만든다.

### 산출물 후보

- `etl/src/new_etf_insight/prompt_builder.py`

### 입력

- 페이지별 텍스트
- 섹션 라우팅 결과
- 목표 JSON 스키마

### 출력

```text
../artifacts/prompts/20260429000012_pdf_extract.md
```

### 프롬프트 규칙

- PDF 전체가 아니라 관련 페이지만 포함한다.
- 원하는 JSON 스키마를 포함한다.
- 모르면 `null`로 둔다.
- 각 값에 `source_pages`를 붙인다.
- PDF 안에 없는 구성종목은 추측하지 않는다.

### 완료 기준

- Codex에 프롬프트 하나를 넣으면 구조화 결과 초안을 받을 수 있다.

## Phase 4. LangGraph 상태 그래프 도입

### 목적

- 지금까지 만든 함수들을 노드로 연결한다.

### 산출물 후보

- `etl/src/new_etf_insight/etf_graph.py`

### 상태 예시

```python
class EtfState(TypedDict):
    pdf_path: str
    pages: list[dict]
    sections: dict
    prompt_path: str
    extracted: dict
    needs_external_research: bool
    external_sources: list[dict]
    final_record: dict
```

### 초기 그래프

```text
load_pdf
-> locate_sections
-> build_pdf_extract_prompt
-> stop_for_manual_llm
```

### 완료 기준

- LangGraph 실행으로 PDF에서 프롬프트 파일까지 생성된다.
- 이 단계에서도 자동 LLM 호출은 하지 않는다.

## Phase 5. 외부 리서치 후보 URL 추출

### 목적

- PDF에서 기초지수명, URL, ISIN 같은 리서치 힌트를 뽑는다.

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

- 테스트 PDF에서 `DE000SL0JYE7`와 Solactive URL을 추출한다.

## Phase 6. 외부 리서치 결과 병합

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

## Phase 7. 배치 실행/저장

### 목적

- `../downloads/pdfs/*.pdf`를 반복 처리한다.

### 산출물 후보

- `etl/scripts/extract_etf_pdf_info.py`

### 출력

```text
../artifacts/extracted_etfs/{rcept_no}.json
../artifacts/prompts/{rcept_no}_pdf_extract.md
```

### 완료 기준

- 20260429 PDF들에 대해 프롬프트와 초기 JSON을 생성한다.

## 당장 다음 작업

1. Phase -2: Codex 구독형 Hello World 파이프라인을 만든다.
2. Phase -1: PDF 통요약 LangGraph 파이프라인을 만든다.
3. 두 단계가 실제로 돌아가는 걸 확인한 뒤 Phase 0 스키마를 조정한다.
