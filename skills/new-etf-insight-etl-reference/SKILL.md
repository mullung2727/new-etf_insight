---
name: new-etf-insight-etl-reference
description: Code reference for the new_etf_insight ETL pipeline (modules, public functions, data flow, JSON record schema, DuckDB schema). Use this skill instead of reading the etl/ source tree when the agent needs to call, debug, or extend pipeline code.
---

# new_etf_insight ETL — Code Reference

Source root: `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`
For execution commands see `new-etf-insight-batch` skill. For repo conventions see `etl/CLAUDE.md`.

---

## 1. Module Map

| Path | Role |
|------|------|
| `src/new_etf_insight/daily_pipeline.py` | Main orchestrator. Candidates → PDF → analysis → JSON → DB sync. |
| `src/new_etf_insight/batch_collect.py` | DART API candidate collection + filter. |
| `src/new_etf_insight/dart_client.py` | DART `list.json` HTTP wrapper. |
| `src/new_etf_insight/dart_pdf.py` | Prospectus PDF download (DART viewer → file URL → bytes). |
| `src/new_etf_insight/dart_viewer.py` | DART viewer HTML parsing. Extracts fund_code, builds etf_key. |
| `src/new_etf_insight/filing_filter.py` | Include/exclude keyword filter for ETF candidates. |
| `src/new_etf_insight/etf_classifier.py` | Pre-listing equity-ETF detection from text. |
| `src/new_etf_insight/holding_identifier.py` | KRX + NASDAQ master lookup. Enriches holdings with ticker/exchange. |
| `src/new_etf_insight/pdf_text.py` | Generic PDF → text utility. |
| `src/new_etf_insight/storage.py` | JSONL append helper. |
| `src/new_etf_insight/models.py` | `FilingCandidate`, `EtfClassification` dataclasses + DART URL constants. |
| `src/new_etf_insight/llm/__init__.py` | Provider router (`get_provider`, `generate_json`). |
| `src/new_etf_insight/llm/base.py` | `LlmProvider` Protocol. |
| `src/new_etf_insight/llm/codex_provider.py` | `CodexProvider` (default). |
| `src/new_etf_insight/llm/openclaw_provider.py` | `OpenClawProvider`. |
| `scripts/pdf_langgraph/pdf_analysis_langgraph.py` | LangGraph state machine. PDF → LLM → normalized JSON. |
| `scripts/pdf_langgraph/prompts/pdf_summary.md` | Main analysis prompt. |
| `scripts/pdf_langgraph/prompts/external_research.md` | Fallback when holdings missing from PDF. |
| `scripts/pdf_langgraph/prompts/correction_review.md` | Judges if correction filing needs update. |
| `scripts/pdf_langgraph/prompts/correction_update.md` | Merges correction text + existing record. |
| `scripts/pdf_langgraph/summary_schema.json` | Output schema — main summary. |
| `scripts/pdf_langgraph/external_research_schema.json` | Output schema — holdings research. |
| `scripts/pdf_langgraph/correction_review_schema.json` | Output schema — `{needs_update, reason}`. |
| `scripts/pdf_langgraph/correction_update_schema.json` | Output schema — updated summary. |
| `scripts/build_db.py` | runs/ scan → DuckDB upsert. Standalone-runnable. |
| `scripts/collect_etf_candidates.py` | CLI wrapper for `collect_candidates`. |
| `scripts/openclaw_llm_adapter.py` | LLM CLI shim. |

---

## 2. Public Function Reference

### `daily_pipeline.py`

```python
run_daily_pipeline(
    begin: str,            # YYYYMMDD
    end: str,              # YYYYMMDD
    records_dir: Path,     # runs/{date}/records
    pdf_dir: Path,         # runs/{date}/pdfs
    max_pages: int = 50,
    query: str | None = None,
) -> dict[str, Any]
```
Full daily run. Collect candidates → for each: extract fund_code → branch (normal | correction) → save JSON → final DuckDB sync.

Returns: `{begin, end, candidate_count, results, db_synced, db_path}`. `results[i].action ∈ {created, updated, skipped, failed}`.

```python
run_period_as_daily_runs(
    begin: str, end: str,
    runs_dir: Path = Path("runs"),
    max_pages: int = 50,
    query: str | None = None,
) -> dict[str, Any]
```
Loop calling `run_daily_pipeline` per day. Output dirs: `runs/{YYYYMMDD}/records`, `runs/{YYYYMMDD}/pdfs`.

### `batch_collect.py`
```python
collect_candidates(begin, end, page_count=100, max_pages=50, query=None) -> list[dict]
```
DART API → filtered candidate dicts (`asdict(FilingCandidate)` shape).

### `dart_client.py`
```python
get_api_key() -> str                                # reads .env DART_API_KEY
recent_date_range(days: int) -> tuple[str, str]
fetch_filing_page(api_key, begin, end, page_no, page_count) -> dict
fetch_all_filings(api_key, begin, end, page_count, max_pages) -> tuple[list[dict], dict]
```

### `dart_pdf.py`
```python
fetch_dart_main_html(rcept_no: str) -> str
extract_pdf_download_dcm_no(html: str, rcept_no: str) -> str
build_pdf_download_main_url(rcept_no: str, dcm_no: str) -> str
extract_prospectus_file_url(html: str) -> str
download_representative_prospectus_pdf(rcept_no: str, output_dir: Path) -> Path
```
Main entry: `download_representative_prospectus_pdf`. Returns saved path.

### `dart_viewer.py`
```python
@dataclass class ViewerSection
extract_viewer_sections(main_html: str) -> list[ViewerSection]
fetch_viewer_section_text(section: ViewerSection) -> str
html_to_text(raw_html: str) -> str
extract_fund_code(text: str) -> str | None                # regex on `펀드 코드 : XXXX`
fetch_fund_code_from_dart_viewer(rcept_no: str) -> str | None
fetch_dart_viewer_text(rcept_no: str) -> str             # full viewer text (used for correction review)
build_etf_key(corp_code: str, fund_code: str) -> str     # f"{corp_code}_{fund_code.upper()}"
```

### `filing_filter.py`
```python
INCLUDE_KEYWORD = "상장지수투자신탁"
EXCLUDE_REPORT_KEYWORDS = ("파생결합증권","상장지수증권","ETN","부동산투자회사","리츠","REIT","MMF","머니마켓")

is_candidate_filing(filing: dict) -> bool                # must contain INCLUDE + "주식", no EXCLUDE
matches_candidate_query(filing: dict, query: str | None) -> bool   # substring search across corp_name/report_nm/rcept_no
to_candidate(filing: dict) -> FilingCandidate
```

### `etf_classifier.py`
```python
classify_pre_listing_equity_etf(text: str) -> EtfClassification   # is_pre_listing_equity_etf, reasons
```

### `holding_identifier.py`
```python
@dataclass class SecurityMasterItem(name, ticker, exchange)

normalize_holding_name(value: str | None) -> str
    # uppercase → strip parentheses → strip CORP/INC/LTD/PLC/CO/COMMON/STOCK/ADR/THE/보통주 → collapse whitespace

class HoldingIdentifierResolver:
    def __init__(bas_dd: str | None = None) -> None       # base date for KRX master
    def enrich_items(items: list[dict], primary_country: str | None = None) -> list[dict]
        # fills missing ticker/exchange in-place by exact normalized-name match
    def resolve(name: str, primary_country: str | None = None) -> SecurityMasterItem | None
    @property kr_master -> list[SecurityMasterItem]       # lazy fetch_krx_master
    @property us_master -> list[SecurityMasterItem]       # lazy fetch_us_master

fetch_krx_master(bas_dd: str | None = None) -> list[SecurityMasterItem]
    # KRX OpenAPI stk_bydd_trd + ksq_bydd_trd. Walks back up to 10 weekdays for trading day.
fetch_us_master() -> list[SecurityMasterItem]
    # parses NASDAQ-listed + other-listed text files (pipe-delimited)
```

### `pdf_text.py`
```python
download_pdf(url: str) -> bytes
extract_pdf_text(pdf_content: bytes) -> str
```

### `storage.py`
```python
append_jsonl(path: Path, records: list[dict]) -> None
```

### `models.py`
```python
DART_BASE_URL = "https://dart.fss.or.kr"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

@dataclass(frozen=True) FilingCandidate(rcept_no, rcept_dt, corp_code, corp_name, report_nm, dart_url)
@dataclass(frozen=True) EtfClassification(is_pre_listing_equity_etf, reasons)
```

### `llm/__init__.py`
```python
DEFAULT_PROVIDER = "codex"
PROVIDER_ENV = "ETF_LLM_PROVIDER"

get_provider(provider_name: str | None = None) -> LlmProvider
    # "codex"/"codex_cli" → CodexProvider, "openclaw" → OpenClawProvider, else ValueError
generate_json(prompt, *, output_schema_path: Path, search: bool = False, provider_name: str | None = None) -> str
```

### `scripts/pdf_langgraph/pdf_analysis_langgraph.py`
```python
analyze_pdf(
    pdf_path: str,
    output_path: Path | None = None,
    source: dict | None = None,
    holding_identifier_resolver: HoldingIdentifierResolver | None = None,
) -> dict
    # Builds & invokes LangGraph. Returns {route, summary, validation_warnings, research_prompt, source?}.

is_correction_source(source: dict) -> bool                  # "[기재정정]" in source.report_nm
review_correction_filing(filing: dict) -> dict              # LLM → {needs_update: bool, reason: str}
update_record_from_correction(existing_record, filing, review) -> dict   # LLM → merged summary

# Internal helpers (still callable):
load_pdf_text(state), preprocess_classification_hints(state), make_summary_prompt(state),
call_llm_node(state), parse_summary_json(state), route_holdings(state),
finalize_pdf_result(state), prepare_external_research(state),
research_external_holdings(state), enrich_missing_holding_identifiers(state)

normalize_summary(summary: dict) -> tuple[dict, list[str]]
    # null → [] for keywords/missing_info/structure_tags/holdings.items/where_to_find_more
    # clamps theme_classification.confidence to [0,1]
    # non_theme → bucket="none"; theme/mixed → bucket="unknown" if was "none"
    # blank ticker/exchange → null

call_llm(prompt, *, search=False, output_schema_path=SUMMARY_SCHEMA_PATH) -> str
build_graph()  # returns compiled LangGraph
save_graph_png(path)
```

### `scripts/build_db.py`
```python
DEFAULT_RUNS_DIR = etl/runs
DEFAULT_DB_PATH  = etl/db/etf_insight.sqlite3

sync_to_db(runs_dir: Path, db_path: Path = DEFAULT_DB_PATH) -> int
    # Scans runs/*/records/*.json, dedups by etf_key keeping max(rcept_dt), upserts both tables. Idempotent.

# Internal:
_load_records(runs_dir) -> dict[etf_key, record]
_ensure_schema(con)   # creates etf_records, etf_holdings; runs ALTER migrations for theme_* columns
_upsert_record(con, etf_key, record)
```

---

## 3. LangGraph State Machine

State (TypedDict):
```
pdf_path, pdf_text, classification_hints, prompt, codex_output,
summary, validation_warnings, route, research_prompt, source,
holding_identifier_resolver
```

Graph:
```
START
 → load_pdf_text
 → preprocess_classification_hints
 → make_summary_prompt
 → call_llm_node                   [pdf_summary.md → JSON]
 → parse_summary_json              [normalize_summary]
 → [route_holdings]
      ├─ available_in_pdf=true  → finalize_pdf_result          → END
      └─ false                  → prepare_external_research
                                → research_external_holdings   [external_research.md → JSON]
                                → enrich_missing_holding_identifiers
                                → END
```

Routes set on output: `pdf_holdings_available` or `external_research_completed`.

---

## 4. End-to-End Data Flow

### Path A — normal filing
```
DART list.json
 → collect_candidates(begin, end, query)            [filter: INCLUDE+주식, exclude ETN/REIT/…]
 → fetch_fund_code_from_dart_viewer(rcept_no)       [None → action=failed/fund_code_not_found]
 → etf_key = build_etf_key(corp_code, fund_code)
 → (record exists?) → skipped/existing_record
 → download_representative_prospectus_pdf(rcept_no, pdf_dir)
 → analyze_pdf(pdf_path, source=filing, resolver)
 → write runs/{date}/records/{etf_key}.json
       first_rcept_dt = rcept_dt, revision_count = 0
 → sync_to_db(runs_dir, db_path)
```

### Path B — correction filing (`[기재정정]` in report_nm)
```
is_correction_source(filing) == true
 → record exists? no  → skipped/correction_without_existing_record
                  yes →
   days_between(previous.first_rcept_dt, filing.rcept_dt) ≥ 60
       → skipped/correction_after_60_days
 → review = review_correction_filing(filing)        [LLM judges via correction_review.md]
     review.needs_update == false → skipped/<reason>
     true →
 → updated = update_record_from_correction(existing, filing, review)
     normalize_summary, preserve first_rcept_dt, revision_count += 1, source ← latest
 → overwrite record JSON → sync_to_db
```

### Skip / fail reasons (`results[i].reason`)
`fund_code_not_found`, `existing_record`, `correction_without_existing_record`, `correction_after_60_days`, `<LLM-supplied review reason>`, `new_record`.

---

## 5. Identifiers & Key Constants

```
etf_key   = "{corp_code}_{fund_code.upper()}"        # ETF-level PK across DART events
rcept_no                                              # per-filing event id (NOT an ETF key)
INCLUDE_KEYWORD       = "상장지수투자신탁"
EXCLUDE_REPORT_KEYWORDS = (파생결합증권, 상장지수증권, ETN, 부동산투자회사, 리츠, REIT, MMF, 머니마켓)
Correction marker     = "[기재정정]"  in report_nm
Correction age cutoff = 60 days vs first_rcept_dt
.env path             = etl/.. /.env  (one level above etl/)
LLM env var           = ETF_LLM_PROVIDER  (default "codex")
```

---

## 6. ETF JSON Record Schema

`runs/{YYYYMMDD}/records/{etf_key}.json`

```json
{
  "route": "pdf_holdings_available | external_research_completed",
  "summary": {
    "is_pre_listing_etf": true,
    "fund_name": "...",
    "asset_manager": "...",
    "index": {"name":"", "provider":"", "description":""},
    "market_exposure": {"primary_country":"KR", "evidence":""},
    "holdings": {
      "available_in_pdf": false,
      "items": [{"name":"", "ticker":"", "exchange":"", "weight":""}],
      "where_to_find_more": []
    },
    "theme_classification": {
      "theme_status":"theme|mixed|non_theme",
      "theme_bucket":"...|none|unknown",
      "confidence":0.0,
      "structure_tags":[]
    },
    "keywords":[], "trend_summary":"", "missing_info":[]
  },
  "research_prompt": "...",
  "validation_warnings": [],
  "source": {
    "rcept_no":"", "rcept_dt":"", "corp_code":"", "corp_name":"",
    "report_nm":"", "fund_code":"", "etf_key":"", "pdf_path":""
  },
  "first_rcept_dt": "YYYYMMDD",
  "revision_count": 0
}
```

### Preservation invariants (DO NOT VIOLATE)
- `first_rcept_dt`: set once on creation. Corrections must never overwrite.
- `revision_count`: increment only on new `rcept_no`. Same rcept_no reprocess → no increment.
- `source`: always overwritten to latest filing.

---

## 7. DB Schema

**전체 DB 스키마 카탈로그: `etl/docs/DB_SCHEMA.md`** — `etl/db/`의 모든 sqlite3/duckdb
테이블을 한눈에 본다. 자동 생성이므로 **직접 수정 금지**. 스키마(테이블/컬럼) 변경 후:
```
uv run python scripts/dump_db_schema.py     # docs/DB_SCHEMA.md 갱신
```
DB 파일은 git 제외지만 카탈로그 문서는 커밋된다. 아래는 핵심 예시만.

### `db/etf_insight.sqlite3`

`etf_records` (PK `etf_key`, 1 row per ETF):
```
etf_key, route, is_pre_listing_etf, fund_name, asset_manager,
index_name, index_provider, index_description,
primary_country,
theme_status, theme_bucket, structure_tags, classification_confidence, classification_evidence,
holdings_available_in_pdf, holdings_summary,
keywords, trend_summary, missing_info,
rcept_no, rcept_dt, corp_code, corp_name, report_nm, fund_code, pdf_path,
first_rcept_dt, revision_count, db_updated_at
```

`etf_holdings` (PK `(etf_key, seq)`):
```
etf_key, seq, name, ticker, exchange, weight
```

`sync_to_db` is idempotent — safe to run repeatedly.

---

## 8. Common Tasks → Entry Points

| Task | Call |
|------|------|
| One-day full run | `run_daily_pipeline(begin, end, records_dir, pdf_dir)` |
| Date range as per-day runs | `run_period_as_daily_runs(begin, end, runs_dir)` |
| Re-sync existing JSON → DuckDB only | `sync_to_db(runs_dir, db_path)` |
| Reprocess single PDF | `analyze_pdf(pdf_path, output_path, source, resolver)` |
| Test correction logic | `review_correction_filing(filing)` then `update_record_from_correction(...)` |
| Resolve ticker for an unknown holding name | `HoldingIdentifierResolver(bas_dd=...).resolve(name, primary_country)` |
| Filter raw DART filings | `is_candidate_filing(filing)` + `matches_candidate_query(filing, query)` |
| Get fund_code from a rcept_no | `fetch_fund_code_from_dart_viewer(rcept_no)` |
| Download a prospectus PDF only | `download_representative_prospectus_pdf(rcept_no, output_dir)` |
| Build a graph PNG | `save_graph_png(path)` |

---

## 9. Edit-Time Cautions

- 새 테이블/컬럼 추가·변경 시 → `scripts/dump_db_schema.py` 재실행해 `docs/DB_SCHEMA.md` 갱신.
- `filing_filter.py` keyword change → existing `runs/` may need reprocessing.
- LLM schema `*.json` change → update matching prompt `*.md` together.
- `daily_pipeline.py` change → run `tests/test_pipeline_modules.py`.
- `normalize_summary` is the single normalization gate — additions should also be applied via correction path (`update_record_from_correction` already routes through it).
- Holdings enrichment runs only when items are missing `ticker` or `exchange`; pre-filled items pass through.

---

## 10. Related Skills
- `new-etf-insight-batch` — PowerShell commands to actually launch the pipeline + tests + DuckDB verification.
- Repo doc: `etl/CLAUDE.md`, `etl/DAILY_PIPELINE_FLOW.md`, `etl/OPENCLAW_PIPELINE_GUIDE.md`.
