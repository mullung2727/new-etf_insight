# ETL — Claude Code Context

코드 수정 전 루트 `README.md`(구성·포트·데이터 흐름) 확인. 실행 방법은 `skills/new-etf-insight-batch/SKILL.md` 참조.

## 작업 디렉토리

모든 명령은 `etl/` 기준. `uv sync` → `uv run` 순서 필수.

```
etl/
├── src/new_etf_insight/   # 파이프라인 핵심 모듈
├── scripts/               # 실행 스크립트 및 LangGraph
│   └── pdf_langgraph/     # PDF 분석 LangGraph + 프롬프트 + 스키마
├── runs/                  # 파이프라인 출력 (git 제외)
│   └── {날짜범위}/
│       ├── records/       # ETF JSON (etf_key.json)
│       └── pdfs/          # 다운로드 PDF
├── db/                    # git 제외. etf_insight/watchlist=SQLite, krx_ohlcv=DuckDB
│   └── etf_insight.sqlite3
├── tests/
└── pyproject.toml
```

## 데이터 흐름

```
DART API
  → batch_collect.py       후보 수집 (상장지수투자신탁+주식 필터)
  → dart_viewer.py         fund_code 추출, etf_key 생성
  → dart_pdf.py            투자설명서 PDF 다운로드
  → pdf_analysis_langgraph.py  LLM 분석 (LangGraph)
  → runs/{날짜}/records/{etf_key}.json  저장
  → build_db.py            runs/ 전체 스캔 → SQLite upsert
```

## 핵심 식별자

```
etf_key = "{corp_code}_{fund_code}"   # 예: 00104500_AL415
```

- `corp_code`: DART 운용사 코드
- `fund_code`: DART viewer 본문에서 정규식으로 추출
- `rcept_no`는 공시 이벤트 키 — ETF 단위 키로 쓰지 않음

## 주요 파일

| 파일 | 역할 |
|------|------|
| `src/new_etf_insight/daily_pipeline.py` | 메인 진입점. 후보 수집~저장~DB 싱크 전체 오케스트레이션 |
| `src/new_etf_insight/dart_client.py` | DART `list.json` API 호출 |
| `src/new_etf_insight/dart_viewer.py` | DART viewer HTML 파싱, fund_code/etf_key 추출 |
| `src/new_etf_insight/dart_pdf.py` | 투자설명서 PDF 다운로드 |
| `src/new_etf_insight/filing_filter.py` | ETF 후보 필터 (포함/제외 키워드) |
| `src/new_etf_insight/llm/` | LLM 프로바이더 추상화 (Codex / OpenClaw) |
| `scripts/pdf_langgraph/pdf_analysis_langgraph.py` | LangGraph 상태머신. PDF → LLM → JSON |
| `scripts/pdf_langgraph/prompts/` | LLM 프롬프트 (pdf_summary, correction_review 등) |
| `scripts/pdf_langgraph/*.json` | LLM 출력 JSON 스키마 |
| `scripts/build_db.py` | runs/ 전체 스캔 → SQLite upsert (standalone 실행 가능) |

## ETF JSON 레코드 구조

`runs/{날짜}/records/{etf_key}.json`

```json
{
  "route": "external_research_completed | pdf_holdings_available | ...",
  "summary": {
    "is_pre_listing_etf": true,
    "fund_name": "...",
    "asset_manager": "...",
    "index": { "name": "", "provider": "", "description": "" },
    "market_exposure": { "primary_country": "KR", "evidence": "" },
    "holdings": { "available_in_pdf": false, "items": [], "where_to_find_more": [] },
    "keywords": [],
    "trend_summary": "",
    "missing_info": []
  },
  "research_prompt": "...",
  "source": {
    "rcept_no": "", "rcept_dt": "", "corp_code": "", "corp_name": "",
    "report_nm": "", "fund_code": "", "etf_key": "", "pdf_path": ""
  },
  "first_rcept_dt": "20260430",
  "revision_count": 0
}
```

## 보존 규칙 (수정 시 필수)

- `first_rcept_dt`: 최초 공시일. 정정공시로 덮어써도 **절대 변경하지 않음**
- `revision_count`: 새 `rcept_no`일 때만 증가. 동일 rcept_no 재처리 시 증가 안 함
- `source`: 항상 최신 공시 기준으로 갱신

## etf_insight 테이블 (SQLite)

`db/etf_insight.sqlite3`

- `etf_records`: etf_key PK, ETF 메타 + 요약 (1 row per ETF)
- `etf_holdings`: (etf_key, seq) PK, 구성종목 목록

runs/ 전체에서 etf_key 기준 최신 rcept_dt 레코드만 upsert.

## LLM 프로바이더

`.env`에서 설정 (`etl/` 상위 디렉토리):

```
ETF_LLM_PROVIDER=codex          # 또는 openclaw
DART_API_KEY=...
```

## 수정 시 주의

- filing_filter.py 키워드 변경 시 기존 runs/ 재처리 필요 여부 확인
- LLM 스키마(`*.json`) 변경 시 대응 프롬프트(`prompts/*.md`)도 함께 수정
- `daily_pipeline.py` 수정 시 `tests/test_pipeline_modules.py` 실행 확인
- `build_db.py`는 멱등 — 여러 번 실행해도 안전
