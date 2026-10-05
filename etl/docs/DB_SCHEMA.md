# DB 스키마 카탈로그

> 자동 생성: `uv run python scripts/dump_db_schema.py`. **직접 수정 금지.**
> 스키마 변경(테이블/컬럼 추가·변경) 후 재실행해 갱신할 것.
> 실제 DDL 소스는 각 스크립트의 `CREATE TABLE` 문.

## `early_signals.sqlite3`

테이블 7개: `assessments`, `event_evidence`, `events`, `manifest_entries`, `processing_results`, `runs`, `source_versions`

```sql
CREATE TABLE assessments (
  assessment_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  episode_id TEXT,
  evidence_json TEXT,
  price_json TEXT,
  grades_json TEXT NOT NULL,
  action TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL,
  valid_until TEXT,
  previous_assessment_id TEXT,
  created_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (run_id, subject_type, subject_id),
  FOREIGN KEY (run_id) REFERENCES runs(run_id)
)
```

```sql
CREATE TABLE event_evidence (
  event_id TEXT NOT NULL,
  source_version_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  locator_hash TEXT NOT NULL,
  locator_json TEXT NOT NULL,
  origin_group_id TEXT NOT NULL,
  independence TEXT NOT NULL,
  PRIMARY KEY (event_id, source_version_id, relation, locator_hash),
  FOREIGN KEY (event_id) REFERENCES events(event_id),
  FOREIGN KEY (source_version_id) REFERENCES source_versions(source_version_id)
)
```

```sql
CREATE TABLE events (
  event_id TEXT PRIMARY KEY,
  source_version_id TEXT NOT NULL,
  event_fingerprint TEXT NOT NULL,
  change_key TEXT NOT NULL,
  extract_version TEXT NOT NULL,
  entity_ids_json TEXT NOT NULL,
  anchor_locators_json TEXT NOT NULL,
  change_json TEXT NOT NULL,
  supersedes_event_id TEXT,
  first_detected_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (source_version_id, event_fingerprint, extract_version),
  FOREIGN KEY (source_version_id) REFERENCES source_versions(source_version_id)
)
```

```sql
CREATE TABLE manifest_entries (
  manifest_id TEXT NOT NULL,
  source_version_id TEXT NOT NULL,
  PRIMARY KEY (manifest_id, source_version_id),
  FOREIGN KEY (source_version_id) REFERENCES source_versions(source_version_id)
)
```

```sql
CREATE TABLE processing_results (
  stage TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model_identity TEXT NOT NULL,
  code_version TEXT NOT NULL,
  status TEXT NOT NULL,
  output_json TEXT,
  raw_response TEXT,
  attempts INTEGER NOT NULL DEFAULT 1,
  elapsed_sec REAL,
  char_count INTEGER,
  error TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (stage, input_hash, policy_version, prompt_version, model_identity, code_version)
)
```

```sql
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  cutoff_at TEXT NOT NULL,
  price_as_of TEXT,
  policy_version TEXT NOT NULL,
  prompt_version TEXT,
  model_identity TEXT,
  code_version TEXT,
  identity_version TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  manifest_id TEXT,
  manifest_hash TEXT,
  coverage_json TEXT,
  throughput_json TEXT,
  lease_owner TEXT,
  lease_heartbeat_at TEXT,
  run_status TEXT NOT NULL,
  result_json TEXT,
  error TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  artifact_path TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (mode, cutoff_at, policy_version, revision)
)
```

```sql
CREATE TABLE source_versions (
  source_version_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_key TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  document_key TEXT,
  origin_group_id TEXT NOT NULL,
  independence TEXT NOT NULL,
  published_at TEXT,
  published_precision TEXT,
  first_observed_at TEXT NOT NULL,
  available_at TEXT NOT NULL,
  extracted_text TEXT NOT NULL,
  raw_json TEXT,
  pdf_bytes_hash TEXT,
  pdf_path TEXT,
  entity_ids_json TEXT,
  quality_json TEXT,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (source_type, source_key, content_hash)
)
```

## `etf_insight.sqlite3`

테이블 3개: `etf_filing_history`, `etf_holdings`, `etf_records`

```sql
CREATE TABLE etf_filing_history (
    etf_key             TEXT,
    rcept_no            TEXT,
    rcept_dt            TEXT,
    first_collected_at  TEXT,
    action              TEXT,
    reason              TEXT,
    filing_json         TEXT,
    record_json         TEXT,
    PRIMARY KEY (etf_key, rcept_no)
)
```

```sql
CREATE TABLE etf_holdings (
    etf_key  TEXT,
    seq      INTEGER,
    name     TEXT,
    ticker   TEXT,
    exchange TEXT,
    weight   TEXT,
    PRIMARY KEY (etf_key, seq)
)
```

```sql
CREATE TABLE etf_records (
    etf_key             TEXT PRIMARY KEY,
    route               TEXT,
    is_pre_listing_etf  INTEGER,
    fund_name           TEXT,
    asset_manager       TEXT,
    index_name          TEXT,
    index_provider      TEXT,
    index_description   TEXT,
    primary_country     TEXT,
    theme_status        TEXT,
    theme_bucket        TEXT,
    structure_tags      TEXT,
    classification_confidence REAL,
    classification_evidence TEXT,
    holdings_available_in_pdf INTEGER,
    holdings_summary    TEXT,
    keywords            TEXT,
    trend_summary       TEXT,
    missing_info        TEXT,
    rcept_no            TEXT,
    rcept_dt            TEXT,
    corp_code           TEXT,
    corp_name           TEXT,
    report_nm           TEXT,
    fund_code           TEXT,
    pdf_path            TEXT,
    first_rcept_dt      TEXT,
    revision_count      INTEGER,
    db_updated_at       TEXT
, first_rcept_no TEXT, first_collected_at TEXT)
```

## `financial_indicators.sqlite3`

테이블 3개: `accounts`, `corps`, `indicators`

```sql
CREATE TABLE accounts (
    corp_code   TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    fs_div      TEXT NOT NULL,       -- CFS 연결 / OFS 별도
    sj_div      TEXT,                -- BS / IS
    account_nm  TEXT NOT NULL,       -- 매출액·영업이익·자산총계 등 (주요계정은 account_id 없음)
    amount      REAL,                -- thstrm_amount
    stock_code  TEXT,
    currency    TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div, account_nm)
)
```

```sql
CREATE TABLE corps (
    corp_code  TEXT PRIMARY KEY,
    stock_code TEXT NOT NULL,
    corp_name  TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```

```sql
CREATE TABLE indicators (
    corp_code   TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    idx_cl_code TEXT NOT NULL,
    idx_code    TEXT NOT NULL,
    idx_nm      TEXT,
    idx_val     REAL,
    stock_code  TEXT,
    stlm_dt     TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (corp_code, bsns_year, reprt_code, idx_code)
)
```

## `jev_candidate.sqlite3`

테이블 13개: `answer_cache`, `decisions`, `filter_cache`, `jev_answers`, `live_candidates`, `live_orders`, `live_outcomes`, `live_runs`, `live_snapshots`, `outcomes`, `posts`, `runs`, `states`

```sql
CREATE TABLE answer_cache (
    key TEXT PRIMARY KEY, model TEXT, rows_json TEXT, usage_json TEXT, created_at TEXT
)
```

```sql
CREATE TABLE decisions (
    run_id TEXT, date TEXT, ticker TEXT, arm TEXT,
    score REAL, rank INTEGER, decision TEXT,
    PRIMARY KEY (run_id, ticker, arm)
)
```

```sql
CREATE TABLE filter_cache (
    key TEXT PRIMARY KEY, about_noul REAL, created_at TEXT
)
```

```sql
CREATE TABLE jev_answers (
    run_id TEXT, date TEXT, ticker TEXT, qid TEXT, type TEXT,
    value TEXT, confidence REAL, probs_json TEXT,
    PRIMARY KEY (run_id, ticker, qid)
)
```

```sql
CREATE TABLE live_candidates (
    date TEXT, ticker TEXT, phase TEXT, model_score REAL, top20 INTEGER,
    mentioned INTEGER, has_jev INTEGER, q3 REAL, q8 REAL, q11 REAL,
    jev_score REAL, combined REAL, combined_rank INTEGER,
    snapshot_price INTEGER, selected INTEGER, created_at TEXT,
    PRIMARY KEY (date, ticker, phase)
)
```

```sql
CREATE TABLE live_orders (
    date TEXT, ticker TEXT, side TEXT, qty INTEGER, order_no TEXT,
    status TEXT, message TEXT, fill_price INTEGER, fill_qty INTEGER,
    filled_at TEXT, source TEXT, created_at TEXT,
    PRIMARY KEY (date, ticker, side)
)
```

```sql
CREATE TABLE live_outcomes (
    date TEXT, ticker TEXT, next_open_ret REAL, ret_5 REAL,
    ret_10 REAL, ret_20 REAL, filled_at TEXT,
    PRIMARY KEY (date, ticker)
)
```

```sql
CREATE TABLE live_runs (
    date TEXT, phase TEXT, started_at TEXT, finished_at TEXT,
    status TEXT, detail TEXT,
    PRIMARY KEY (date, phase)
)
```

```sql
CREATE TABLE live_snapshots (
    date TEXT, phase TEXT, ticker TEXT, price INTEGER, open INTEGER,
    high INTEGER, low INTEGER, tv INTEGER, base INTEGER DEFAULT 0,
    created_at TEXT,
    PRIMARY KEY (date, phase, ticker)
)
```

```sql
CREATE TABLE outcomes (
    date TEXT, ticker TEXT, d0_close INTEGER, d1_open INTEGER, d1_0930 INTEGER,
    d1_close INTEGER, d1_high INTEGER, d1_low INTEGER,
    ret_open_0930 REAL, ret_open_close REAL, max_ret_o REAL, min_ret_o REAL,
    excluded_open TEXT,
    PRIMARY KEY (date, ticker)
)
```

```sql
CREATE TABLE posts (
    run_id TEXT, date TEXT, ticker TEXT, channel TEXT, post_id TEXT,
    posted_at_kst TEXT, section TEXT, text_hash TEXT, about_noul REAL, passed INTEGER,
    PRIMARY KEY (run_id, ticker, channel, post_id)
)
```

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, mode TEXT, track TEXT, date TEXT, as_of TEXT,
    jev_model TEXT, gpt_model TEXT, question_set_ver TEXT, prompt_ver TEXT, created_at TEXT
)
```

```sql
CREATE TABLE states (
    run_id TEXT, date TEXT, ticker TEXT, name TEXT, price_pct REAL,
    state_text TEXT, state_hash TEXT, anon_map_json TEXT,
    n_candidates INTEGER, n_passed INTEGER, n_ambiguous INTEGER, n_included INTEGER,
    truncated INTEGER,
    PRIMARY KEY (run_id, ticker)
)
```

## `orderbook.sqlite3`

테이블 2개: `orderbook_run`, `orderbook_snapshot`

```sql
CREATE TABLE orderbook_run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  mode TEXT NOT NULL,
  source_date TEXT,
  venue TEXT,
  symbols TEXT NOT NULL,
  rows_written INTEGER,
  note TEXT
)
```

```sql
CREATE TABLE orderbook_snapshot (
  date TEXT NOT NULL,
  ticker TEXT NOT NULL,
  venue TEXT NOT NULL,
  ts TEXT NOT NULL,
  recv_ts TEXT NOT NULL,
  quote_tm TEXT,
  ask1_px INTEGER,
  ask2_px INTEGER,
  ask3_px INTEGER,
  ask4_px INTEGER,
  ask5_px INTEGER,
  ask6_px INTEGER,
  ask7_px INTEGER,
  ask8_px INTEGER,
  ask9_px INTEGER,
  ask10_px INTEGER,
  ask1_qty INTEGER,
  ask2_qty INTEGER,
  ask3_qty INTEGER,
  ask4_qty INTEGER,
  ask5_qty INTEGER,
  ask6_qty INTEGER,
  ask7_qty INTEGER,
  ask8_qty INTEGER,
  ask9_qty INTEGER,
  ask10_qty INTEGER,
  bid1_px INTEGER,
  bid2_px INTEGER,
  bid3_px INTEGER,
  bid4_px INTEGER,
  bid5_px INTEGER,
  bid6_px INTEGER,
  bid7_px INTEGER,
  bid8_px INTEGER,
  bid9_px INTEGER,
  bid10_px INTEGER,
  bid1_qty INTEGER,
  bid2_qty INTEGER,
  bid3_qty INTEGER,
  bid4_qty INTEGER,
  bid5_qty INTEGER,
  bid6_qty INTEGER,
  bid7_qty INTEGER,
  bid8_qty INTEGER,
  bid9_qty INTEGER,
  bid10_qty INTEGER,
  ask_total_qty INTEGER,
  bid_total_qty INTEGER,
  exp_px INTEGER,
  exp_qty INTEGER,
  exp_px_ca INTEGER,
  exp_qty_ca INTEGER,
  PRIMARY KEY (date, ticker, venue, ts)
)
```

## `report_metrics.sqlite3`

테이블 7개: `report_api_facts`, `report_collection_runs`, `report_document_stocks`, `report_documents`, `report_estimates`, `report_facts`, `report_sources`

```sql
CREATE TABLE report_api_facts (
  pdf_key TEXT PRIMARY KEY,
  research_id TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  broker TEXT NOT NULL,
  report_date TEXT NOT NULL,
  title TEXT,
  opinion TEXT,
  goal_price INTEGER,
  price_at_write INTEGER,
  content_html TEXT,
  fetched_at TEXT NOT NULL
)
```

```sql
CREATE TABLE report_collection_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  category TEXT,
  date_from TEXT, date_to TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','completed','partial','error')),
  pages_fetched INTEGER NOT NULL DEFAULT 0,
  rows_listed INTEGER NOT NULL DEFAULT 0,
  new_docs INTEGER NOT NULL DEFAULT 0,
  dup_docs INTEGER NOT NULL DEFAULT 0,
  downloaded INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  no_pdf INTEGER NOT NULL DEFAULT 0,
  stop_reason TEXT,
  last_page INTEGER
)
```

```sql
CREATE TABLE report_document_stocks (
  document_id INTEGER NOT NULL REFERENCES report_documents(document_id),
  stock_code TEXT NOT NULL CHECK (length(stock_code) = 6),
  relation_type TEXT NOT NULL CHECK (relation_type IN ('primary','mention','unknown')),
  page_from INTEGER, page_to INTEGER,
  method TEXT NOT NULL,
  review_status TEXT NOT NULL DEFAULT 'auto' CHECK (review_status IN ('auto','confirmed','rejected')),
  PRIMARY KEY (document_id, stock_code)
)
```

```sql
CREATE TABLE report_documents (
  document_id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_type TEXT NOT NULL CHECK (document_type IN ('company','industry','market','invest','economy','unknown')),
  title TEXT,
  broker TEXT NOT NULL,
  published_date TEXT NOT NULL,
  pdf_path TEXT,
  sha256 TEXT UNIQUE,
  pdf_key TEXT UNIQUE,
  file_status TEXT NOT NULL CHECK (file_status IN ('saved','no_pdf','failed','not_pdf')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
, subcategory TEXT)
```

```sql
CREATE TABLE report_estimates (
  pdf_key TEXT NOT NULL REFERENCES report_facts(pdf_key) ON DELETE CASCADE,
  fiscal_year INTEGER NOT NULL,
  is_forecast INTEGER NOT NULL,
  revenue REAL,
  operating_profit REAL,
  net_profit REAL,
  eps REAL,
  per REAL,
  roe REAL,
  pbr REAL,
  div_yield REAL,
  PRIMARY KEY (pdf_key, fiscal_year)
)
```

```sql
CREATE TABLE report_facts (
  pdf_key TEXT PRIMARY KEY,
  pdf_path TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  broker TEXT NOT NULL,
  report_date TEXT NOT NULL,
  opinion TEXT,
  target_price INTEGER,
  price_at_report INTEGER,
  price_at_report_date TEXT,
  upside_printed REAL,
  prev_target_printed INTEGER,
  parse_status TEXT NOT NULL,
  parse_error TEXT,
  parser_version TEXT NOT NULL,
  parsed_at TEXT
)
```

```sql
CREATE TABLE report_sources (
  source TEXT NOT NULL,
  source_report_id TEXT NOT NULL,
  document_id INTEGER NOT NULL REFERENCES report_documents(document_id),
  list_url TEXT, detail_url TEXT, pdf_url TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  run_id INTEGER REFERENCES report_collection_runs(run_id),
  PRIMARY KEY (source, source_report_id)
)
```

## `swing_pick.sqlite3`

테이블 2개: `swing_candidates`, `swing_runs`

```sql
CREATE TABLE swing_candidates (
    date_kst      TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    name          TEXT NOT NULL,
    sources       TEXT NOT NULL,     -- JSON 배열: telegram / youtube / report / high52
    trading_value INTEGER,           -- 1차 컷 근거
    jev_sustain   REAL,              -- 1번 Jev score
    jev_risk      REAL,              -- 2번 Jev noul('예' 확률)
    op_profit     REAL,              -- 3번 최근 분기 영업이익
    op_profit_yoy REAL,              -- 3번 전년 동기 영업이익
    frgn_net_5d   INTEGER,           -- 4번 외인 5일 순매수(금액)
    orgn_net_5d   INTEGER,           -- 4번 기관 5일 순매수(금액)
    ma20_gap      REAL,              -- 5번 종가/20일선 - 1
    ret_5d        REAL,              -- 5번 5일 상승률
    per           REAL,              -- 6번 참고 (적자면 NULL + per_note)
    per_note      TEXT,
    themes        TEXT,              -- 7번 참고: JSON [{name, ret, up, down}]
    s1 INTEGER, s3 INTEGER, s4 INTEGER, s5 INTEGER,   -- 0/1/2
    risk_out      INTEGER NOT NULL,  -- 2번 탈락 1/0 (Jev 실패도 0, jev_error로 구분)
    total         INTEGER,           -- s1+s3+s4+s5 (탈락도 계산해 둠)
    rank          INTEGER,           -- 최종 순위 (상위 3만 1~3, 나머지 NULL)
    errors        TEXT,              -- JSON {항목: 오류명}
    jev_input_hash TEXT,             -- Jev 입력 묶음 sha1 (재현·중복 확인)
    excluded_reason TEXT,            -- 순위 제외 사유 (cut:<사유>/risk/jev_error/status:<사유>), 순위 대상이면 NULL
    created_at    TEXT NOT NULL,
    PRIMARY KEY (date_kst, ticker)
)
```

```sql
CREATE TABLE swing_runs (
    date_kst      TEXT PRIMARY KEY,
    n_candidates  INTEGER NOT NULL,
    n_risk_out    INTEGER NOT NULL,
    n_errors      INTEGER NOT NULL,
    jev_input_tokens INTEGER,
    summary       TEXT,              -- GPT 요약문 JSON (실패 시 NULL)
    summary_model TEXT,
    notified      INTEGER NOT NULL,  -- 전송 성공 1/0
    prev_date     TEXT,              -- 시세 기준일 YYYYMMDD (KRX DB max date)
    warnings      TEXT,              -- JSON 배열
    created_at    TEXT NOT NULL
)
```

## `telegram_public.sqlite3`

테이블 6개: `telegram_analysis_watermark`, `telegram_channels`, `telegram_daily_rollup`, `telegram_posts`, `telegram_session_highlights`, `telegram_stock_insights`

```sql
CREATE TABLE telegram_analysis_watermark (
    channel TEXT PRIMARY KEY,
    last_post_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL
)
```

```sql
CREATE TABLE telegram_channels (
    channel TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```

```sql
CREATE TABLE telegram_daily_rollup (
    date_kst TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    rank INTEGER NOT NULL,
    score INTEGER NOT NULL,
    session_count INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    is_new INTEGER NOT NULL,
    has_flow INTEGER NOT NULL,
    themes TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(date_kst, ticker)
)
```

```sql
CREATE TABLE telegram_posts (
    channel TEXT NOT NULL,
    post_id INTEGER NOT NULL,
    post_ref TEXT NOT NULL,
    posted_at_utc TEXT NOT NULL,
    date_kst TEXT NOT NULL,
    text TEXT NOT NULL,
    links_json TEXT NOT NULL,
    raw_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, post_id)
)
```

```sql
CREATE TABLE telegram_session_highlights (
    date_kst TEXT NOT NULL,
    session TEXT NOT NULL,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    category TEXT NOT NULL,
    importance_reason TEXT NOT NULL,
    score_total INTEGER NOT NULL CHECK(score_total BETWEEN 0 AND 100),
    score_breakdown_json TEXT NOT NULL,
    source_channels_json TEXT NOT NULL,
    source_post_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (date_kst, session, rank)
)
```

```sql
CREATE TABLE telegram_stock_insights (
    date_kst TEXT NOT NULL,
    session TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    mention_channels TEXT NOT NULL,
    source_post_refs TEXT NOT NULL,
    discovery_reason TEXT NOT NULL,
    analysis TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(date_kst, session, ticker)
)
```

## `theme_alert.sqlite3`

테이블 5개: `theme_alerts`, `theme_aliases`, `theme_mentions`, `theme_processed`, `themes`

```sql
CREATE TABLE theme_alerts (
  alert_id       INTEGER PRIMARY KEY,
  theme_id       INTEGER NOT NULL REFERENCES themes(theme_id),
  kind           TEXT NOT NULL,         -- 'new' | 'progress' | 'baseline'(첫 가동, 미전송)
  stage          TEXT NOT NULL,         -- 결정 15 단계
  evidence_level TEXT NOT NULL,         -- 결정 17
  refs_json      TEXT NOT NULL,         -- [{source, ref}]
  message        TEXT NOT NULL,
  sent_at        TEXT                   -- --no-send면 NULL
)
```

```sql
CREATE TABLE theme_aliases (
  alias_norm TEXT PRIMARY KEY,          -- 정규화 표기 (공백 제거·소문자)
  theme_id   INTEGER NOT NULL REFERENCES themes(theme_id),
  source     TEXT NOT NULL,             -- 'manual' | 'llm'
  created_at TEXT NOT NULL
)
```

```sql
CREATE TABLE theme_mentions (
  theme_id     INTEGER NOT NULL REFERENCES themes(theme_id),
  source       TEXT NOT NULL,           -- 'telegram' | 'youtube'
  ref          TEXT NOT NULL,           -- post_ref | video_id
  posted_at    TEXT NOT NULL,           -- UTC ISO
  collected_at TEXT NOT NULL,           -- 원본 DB created_at
  PRIMARY KEY (theme_id, source, ref)
)
```

```sql
CREATE TABLE theme_processed (
  source       TEXT NOT NULL,
  ref          TEXT NOT NULL,
  processed_at TEXT NOT NULL,
  PRIMARY KEY (source, ref)
)
```

```sql
CREATE TABLE themes (
  theme_id   INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,      -- 대표 이름
  registered INTEGER NOT NULL DEFAULT 0, -- 1 = 사용자 등록 테마
  created_at TEXT NOT NULL
)
```

## `watchlist.sqlite3`

테이블 24개: `close_bet_orders`, `close_bet_sell_fills`, `envelope_positions`, `envelope_runs`, `envelope_screen`, `high52_positions`, `high52_runs`, `high52_screen`, `intraday_ranking`, `llm_catalyst_assessments`, `llm_scores`, `pullback_orders`, `rebound_positions`, `rebound_runs`, `rights_dip_candidates`, `rights_dip_capital`, `rights_dip_monitor`, `rights_dip_orders`, `rights_dip_positions`, `rights_dip_runs`, `theme_dict_migrations`, `trading_result_causes`, `watchlist`, `watchlist_market_snapshots`

```sql
CREATE TABLE close_bet_orders (
            date        TEXT,
            ticker      TEXT,
            score       INTEGER,
            qty         INTEGER,
            order_type  TEXT,
            status      TEXT,
            order_no    TEXT,
            message     TEXT,
            raw         TEXT,
            created_at  TEXT,
            cntr_price  INTEGER,
            cntr_qty    INTEGER,
            verified_at TEXT,
            leg         TEXT NOT NULL DEFAULT 'single', sell_order_no TEXT, sell_status TEXT, sell_price INTEGER, sell_qty INTEGER, sold_at TEXT, exit_reason TEXT, pnl_pct REAL, sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,  -- single / auction / chase (청산 반반 분할)
            PRIMARY KEY (date, ticker, leg)
        )
```

```sql
CREATE TABLE close_bet_sell_fills (
            date        TEXT NOT NULL,     -- close_bet_orders.date (매수일)
            ticker      TEXT NOT NULL,
            leg         TEXT NOT NULL,
            order_no    TEXT NOT NULL,
            kind        TEXT NOT NULL,     -- auction / chase / market_0901 / backstop
            round       INTEGER,           -- chase 회차 1~3
            price       INTEGER NOT NULL,
            qty         INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (date, ticker, leg, order_no)
        )
```

```sql
CREATE TABLE envelope_positions (
    signal_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    ent_date TEXT NOT NULL,
    status TEXT NOT NULL,
    depth REAL,
    breadth_pct REAL,
    corp_flag INTEGER NOT NULL DEFAULT 0,
    corp_date TEXT,
    corp_ratio REAL,
    budget INTEGER,
    cap_applied INTEGER NOT NULL DEFAULT 0,  -- MAX_ORDER_AMOUNT 로 자리가 잘렸음
    limit_price INTEGER,
    order_qty INTEGER,
    buy_order_no TEXT NOT NULL DEFAULT '',
    fill_price REAL,
    qty INTEGER,                             -- 매수 체결 수량
    sold_qty INTEGER NOT NULL DEFAULT 0,     -- 누적 매도 체결 수량
    hold_days INTEGER NOT NULL DEFAULT 0,
    last_update_date TEXT,
    tp_status TEXT NOT NULL DEFAULT '',
    tp_order_no TEXT NOT NULL DEFAULT '',
    tp_price INTEGER,
    exp_status TEXT NOT NULL DEFAULT '',
    exp_order_no TEXT NOT NULL DEFAULT '',
    close_reason TEXT,
    close_date TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (signal_date, ticker)
)
```

```sql
CREATE TABLE envelope_runs (
    date TEXT PRIMARY KEY,
    signal_date TEXT,
    gate REAL,
    breadth REAL,
    n_sig INTEGER,
    created_at TEXT NOT NULL
)
```

```sql
CREATE TABLE envelope_screen (
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    close REAL,
    depth REAL,
    z REAL,
    market TEXT,
    market_cap REAL,
    corp_flag INTEGER NOT NULL,
    corp_date TEXT,
    corp_ratio REAL,
    reason TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
)
```

```sql
CREATE TABLE high52_positions (
    signal_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL,             -- planned / submitted / unconfirmed / confirmed / expired / dropped / skipped / rejected / failed
    rs REAL,
    pair_ticker TEXT,                 -- 교체 매수면 같이 판 종목
    budget INTEGER,
    limit_price INTEGER,
    qty INTEGER,
    buy_order_no TEXT,
    buy_price INTEGER,
    buy_qty INTEGER,
    peak REAL,
    stop_price REAL,
    hold_days INTEGER,
    last_update_date TEXT,
    sell_status TEXT NOT NULL DEFAULT '',   -- '' / planned / ordered / partial / filled / missing
    sell_reason TEXT,                        -- stop / tp / max / swap / missing
    sell_date TEXT,
    sell_order_no TEXT,
    sell_price INTEGER,
    sell_qty INTEGER,
    message TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (signal_date, ticker)
)
```

```sql
CREATE TABLE high52_runs (
    date TEXT PRIMARY KEY,
    dry_run INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
```

```sql
CREATE TABLE high52_screen (
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    price INTEGER,
    rs REAL,
    rpct REAL,
    cand INTEGER NOT NULL,
    PRIMARY KEY (date, ticker)
)
```

```sql
CREATE TABLE intraday_ranking (
    date   TEXT,
    rank   INTEGER,
    ticker TEXT,
    name   TEXT,
    volume INTEGER,
    close  INTEGER,
    PRIMARY KEY (date, ticker)
)
```

```sql
CREATE TABLE llm_catalyst_assessments (
            date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            as_of TEXT NOT NULL,
            primary_category_raw TEXT NOT NULL,
            primary_status TEXT NOT NULL,
            primary_duration TEXT NOT NULL,
            primary_alive_score INTEGER NOT NULL,
            max_alive_score INTEGER NOT NULL,
            assessment_json TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model TEXT,
            generated_at TEXT NOT NULL, theme_scores_json TEXT, theme_event_direction TEXT, new_theme_candidate TEXT, theme_dict_version TEXT, theme_escalated INTEGER, theme_escalation_reason TEXT, theme_escalation_model TEXT,
            PRIMARY KEY (date, ticker)
        )
```

```sql
CREATE TABLE llm_scores (
    date           TEXT,
    ticker         TEXT,
    name           TEXT,
    ratio          REAL,
    today_volume   INTEGER,
    avg5_volume    INTEGER,
    trading_value  INTEGER,
    close          INTEGER,
    score          INTEGER,
    category       TEXT,
    reason_summary TEXT,
    final_opinion  TEXT,
    evidence_board TEXT,
    evidence_news  TEXT,
    evidence_web   TEXT,
    sources        TEXT,
    PRIMARY KEY (date, ticker)
)
```

```sql
CREATE TABLE pullback_orders (
            watchlist_date TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            prior_low INTEGER NOT NULL,
            day_open INTEGER NOT NULL,
            signal_price INTEGER NOT NULL,
            qty INTEGER NOT NULL,
            status TEXT NOT NULL,
            buy_order_no TEXT,
            buy_price INTEGER,
            buy_qty INTEGER,
            bought_at TEXT,
            remaining_hold_days INTEGER,
            last_hold_count_date TEXT,
            expiry_date TEXT,
            sell_order_no TEXT,
            sell_status TEXT,
            sell_price INTEGER,
            sell_qty INTEGER,
            sold_at TEXT,
            exit_reason TEXT,
            pnl_pct REAL,
            note_uid TEXT,
            message TEXT,
            raw TEXT,
            created_at TEXT NOT NULL,
            verified_at TEXT, sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,
            PRIMARY KEY (watchlist_date, ticker)
        )
```

```sql
CREATE TABLE rebound_positions (
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    ncand INTEGER,
    split INTEGER,
    w1 REAL,
    rng20 REAL,
    prev_close REAL,
    open_price REAL,
    slot INTEGER,
    e1 INTEGER, q1 INTEGER, ord1 TEXT, st1 TEXT NOT NULL,   -- planned/submitted/dry_run/rejected/failed/skipped → filled/partial/expired/cancelled
    fq1 INTEGER DEFAULT 0, fp1 REAL,
    e2 INTEGER, q2 INTEGER, ord2 TEXT, st2 TEXT,
    fq2 INTEGER DEFAULT 0, fp2 REAL,
    tp_price INTEGER,
    sell_ord TEXT,
    sell_status TEXT NOT NULL DEFAULT '',   -- '' / tp_open / close_ordered / filled / carried / missing
    sell_qty INTEGER,
    sell_px REAL,
    reason TEXT,                             -- tp1 / tp_avg / close / carried
    realized REAL,                           -- 원 (키움 실현손익, 수수료·세금 차감)
    message TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
)
```

```sql
CREATE TABLE rebound_runs (
    date TEXT PRIMARY KEY,
    dry_run INTEGER NOT NULL,
    created_at TEXT NOT NULL
, cash INTEGER)
```

```sql
CREATE TABLE rights_dip_candidates (
    acptno TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    ann_date TEXT NOT NULL,
    ann_time TEXT NOT NULL DEFAULT '',
    time_cls TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    pre20 REAL,
    P TEXT,
    base_price REAL,
    l1 INTEGER,
    record_date TEXT,
    X TEXT,
    record_history TEXT NOT NULL DEFAULT '',
    extra_amount INTEGER,
    status TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    updated TEXT NOT NULL
, alerted TEXT NOT NULL DEFAULT '', near_alerted TEXT NOT NULL DEFAULT '')
```

```sql
CREATE TABLE rights_dip_capital (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    capital INTEGER NOT NULL,
    pnl REAL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
```

```sql
CREATE TABLE rights_dip_monitor (
    date TEXT NOT NULL,                -- 점검일 YYYYMMDD
    item TEXT NOT NULL,                -- m1·m2·…·m9
    key TEXT NOT NULL,                 -- acptno·(acptno,extra,leg)·지표명
    value TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'ok',  -- ok·info·warning·critical·unknown
    detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (date, item, key)
)
```

```sql
CREATE TABLE rights_dip_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                -- 주문일 YYYYMMDD (지정가는 당일 유효)
    acptno TEXT NOT NULL,
    extra INTEGER NOT NULL DEFAULT 0,
    leg TEXT NOT NULL,                 -- b1_1·b1_2·b1_3·b2_1·b2_2·tp·exit·withdraw
    order_no TEXT NOT NULL DEFAULT '',
    price INTEGER,                     -- 지정가 (시장가는 NULL)
    qty INTEGER,                       -- 주문 수량 (skipped 면 0)
    status TEXT NOT NULL DEFAULT '',   -- submitted·unconfirmed·rejected·dry_run·filled·partial·skipped
    filled_qty INTEGER NOT NULL DEFAULT 0,
    filled_price REAL,                 -- 체결 평균가
    note TEXT NOT NULL DEFAULT '',     -- skipped 사유·R22 분할·loose 대조 등
    created_at TEXT NOT NULL
)
```

```sql
CREATE TABLE rights_dip_positions (
    acptno TEXT NOT NULL,
    ticker TEXT NOT NULL,
    extra INTEGER NOT NULL DEFAULT 0,   -- R18 추가 자금 포지션 (본 슬롯과 별도)
    b1_date TEXT,
    b1_price REAL,
    b1_qty INTEGER,
    buy1_order_no TEXT NOT NULL DEFAULT '',
    b2_date TEXT,
    b2_price REAL,
    b2_qty INTEGER,
    buy2_order_no TEXT NOT NULL DEFAULT '',
    avg_price REAL,
    can_water INTEGER NOT NULL DEFAULT 1,
    sold_qty INTEGER NOT NULL DEFAULT 0,   -- 누적 매도 체결 수량 (부분체결·정리 재시도용)
    sell_value INTEGER NOT NULL DEFAULT 0, -- 누적 매도 체결 금액 (청산 손익용)
    tp_status TEXT NOT NULL DEFAULT '',
    tp_order_no TEXT NOT NULL DEFAULT '',
    exit_status TEXT NOT NULL DEFAULT '',
    exit_order_no TEXT NOT NULL DEFAULT '',
    liq_capped INTEGER NOT NULL DEFAULT 0,  -- R19 유동성 상한에 걸려 주문이 잘림
    close_date TEXT,
    close_price REAL,
    close_reason TEXT,                   -- tp / exit / withdrawn
    pnl REAL,
    capital_applied INTEGER NOT NULL DEFAULT 0,  -- R2a 자본 반영됨 (두 번 반영 금지)
    created_at TEXT NOT NULL,
    updated TEXT NOT NULL, pnl_source TEXT NOT NULL DEFAULT '', pnl_applied REAL NOT NULL DEFAULT 0, minor INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (acptno, extra)
)
```

```sql
CREATE TABLE rights_dip_runs (
    date TEXT NOT NULL,
    batch TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (date, batch)
)
```

```sql
CREATE TABLE theme_dict_migrations (
            from_version TEXT NOT NULL,
            to_version TEXT NOT NULL,
            axis TEXT NOT NULL,
            old_value TEXT NOT NULL,
            new_value TEXT,
            action TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (from_version, to_version, axis, old_value)
        )
```

```sql
CREATE TABLE trading_result_causes (
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    strategy TEXT NOT NULL,
    name TEXT,
    bought_date TEXT,
    held_days INTEGER,
    pnl_pct REAL,
    exit_reason TEXT,
    grade TEXT NOT NULL,
    cause TEXT NOT NULL,
    buy_rationale_match TEXT NOT NULL,
    reasoning TEXT,
    evidence_refs_json TEXT NOT NULL,
    catalyst_date TEXT,
    catalyst_category_raw TEXT,
    catalyst_status TEXT,
    catalyst_expected_duration TEXT,
    theme_scores_json TEXT,
    theme_event_direction TEXT,
    grade_runs_json TEXT NOT NULL,
    grade_spread INTEGER,
    filing_count INTEGER NOT NULL,
    news_count INTEGER NOT NULL,
    telegram_count INTEGER NOT NULL,
    as_of TEXT NOT NULL,
    model TEXT,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (date, ticker, strategy)
)
```

```sql
CREATE TABLE watchlist (
    date       TEXT,
    stock_code TEXT,
    PRIMARY KEY (date, stock_code)
)
```

```sql
CREATE TABLE watchlist_market_snapshots (
    date          TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    snapshot_at   TEXT NOT NULL,
    current_price INTEGER,
    open_price    INTEGER,
    high_price    INTEGER,
    volume        INTEGER,
    change_rate   REAL,
    source        TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
)
```

## `youtube_public.sqlite3`

테이블 5개: `youtube_digest_deliveries`, `youtube_digest_runs`, `youtube_stock_insights`, `youtube_video_summaries`, `youtube_videos`

```sql
CREATE TABLE youtube_digest_deliveries (
    channel_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    digest_date_kst TEXT NOT NULL,
    digest_session TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, video_id)
)
```

```sql
CREATE TABLE youtube_digest_runs (
    date_kst TEXT NOT NULL,
    session TEXT NOT NULL CHECK(session IN ('morning', 'evening')),
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('generated', 'sent')),
    source_video_ids_json TEXT NOT NULL,
    digest_json TEXT,
    message_text TEXT,
    warning_json TEXT NOT NULL,
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (date_kst, session)
)
```

```sql
CREATE TABLE youtube_stock_insights (
    date_kst TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    mention_channels TEXT NOT NULL,
    source_video_ids TEXT NOT NULL,
    discovery_reason TEXT NOT NULL,
    analysis TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(date_kst, ticker)
)
```

```sql
CREATE TABLE youtube_video_summaries (
  channel_id   TEXT NOT NULL,
  video_id     TEXT NOT NULL,
  date_kst     TEXT NOT NULL,
  model        TEXT,
  summary_json TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE(channel_id, video_id)
)
```

```sql
CREATE TABLE youtube_videos (
  channel_id       TEXT NOT NULL,
  video_id         TEXT NOT NULL,
  title            TEXT NOT NULL,
  published_at_utc TEXT NOT NULL,
  date_kst         TEXT NOT NULL,
  url              TEXT NOT NULL,
  transcript       TEXT,
  transcript_lang  TEXT,
  transcript_source TEXT,
  raw_json         TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  UNIQUE(channel_id, video_id)
)
```

## `bench_daily.duckdb`

테이블 3개: `bench_build`, `bench_daily`, `bench_meta`

```sql
bench_build (
    source_max_date VARCHAR
    built_at VARCHAR
    row_count INTEGER
    code_version VARCHAR
)
```

```sql
bench_daily (
    date VARCHAR
    ms INTEGER
    bench_id VARCHAR
    n INTEGER
    r_cc DOUBLE
    r_on DOUBLE
    r_in DOUBLE
)
```

```sql
bench_meta (
    bench_id VARCHAR
    universe VARCHAR
    cap_bucket VARCHAR
    label_ko VARCHAR
    rule_ko VARCHAR
)
```

## `krx_ohlcv.duckdb`

테이블 3개: `holidays`, `ohlcv`, `stock_names`

```sql
holidays (
    date VARCHAR
)
```

```sql
ohlcv (
    date VARCHAR
    ticker VARCHAR
    market VARCHAR
    open INTEGER
    high INTEGER
    low INTEGER
    close INTEGER
    volume BIGINT
    trading_value BIGINT
    market_cap BIGINT
    list_shrs BIGINT
    cmp_prev INTEGER
)
```

```sql
stock_names (
    code VARCHAR
    name VARCHAR
    updated_at VARCHAR
)
```

## `minute_bars.duckdb`

테이블 3개: `minute_backfill_failures`, `minute_bars`, `minute_fetched`

```sql
minute_backfill_failures (
    month VARCHAR
    ticker VARCHAR
    scope VARCHAR
    attempts INTEGER
    status VARCHAR
    last_error VARCHAR
    last_attempt_at TIMESTAMP
)
```

```sql
minute_bars (
    ticker VARCHAR
    scope VARCHAR
    timestamp VARCHAR
    date VARCHAR
    time VARCHAR
    open BIGINT
    high BIGINT
    low BIGINT
    close BIGINT
    volume BIGINT
)
```

```sql
minute_fetched (
    ticker VARCHAR
    scope VARCHAR
    date VARCHAR
    status VARCHAR
)
```

## `us_macro.duckdb`

테이블 2개: `fred_obs`, `index_ohlcv`

```sql
fred_obs (
    series_id VARCHAR
    date VARCHAR
    value DOUBLE
    fetched_at TIMESTAMP
)
```

```sql
index_ohlcv (
    date VARCHAR
    ticker VARCHAR
    open DOUBLE
    high DOUBLE
    low DOUBLE
    close DOUBLE
    fetched_at TIMESTAMP
)
```

## `us_ohlcv.duckdb`

테이블 5개: `etf_dividends`, `etf_ohlcv`, `ohlcv`, `splits`, `stock_names`

```sql
etf_dividends (
    ticker VARCHAR
    date VARCHAR
    amount DOUBLE
)
```

```sql
etf_ohlcv (
    date VARCHAR
    ticker VARCHAR
    open DOUBLE
    high DOUBLE
    low DOUBLE
    close DOUBLE
    volume BIGINT
)
```

```sql
ohlcv (
    date VARCHAR
    ticker VARCHAR
    market VARCHAR
    open DOUBLE
    high DOUBLE
    low DOUBLE
    close DOUBLE
    volume BIGINT
    trading_value DOUBLE
    market_cap DOUBLE
    list_shrs BIGINT
)
```

```sql
splits (
    ticker VARCHAR
    date VARCHAR
    ratio DOUBLE
)
```

```sql
stock_names (
    code VARCHAR
    name VARCHAR
    updated_at VARCHAR
)
```

## `broker/notes.db`

테이블 3개: `kiwoom_trade_history`, `note_events`, `notes`

```sql
CREATE TABLE kiwoom_trade_history (
    order_no    TEXT PRIMARY KEY,
    date        TEXT,
    ticker      TEXT,
    side        TEXT,
    order_type  TEXT,
    qty         INTEGER,
    price       INTEGER,
    status      TEXT,
    source      TEXT,
    raw         TEXT,
    created_at  TEXT
)
```

```sql
CREATE TABLE note_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_uid    TEXT NOT NULL REFERENCES notes(uid) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    price       INTEGER NOT NULL,
    qty         INTEGER NOT NULL,
    executed_at TEXT NOT NULL,
    memo        TEXT,
    created_at  TEXT NOT NULL
, order_no TEXT)
```

```sql
CREATE TABLE notes (
    uid            TEXT PRIMARY KEY,
    symbol         TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open',
    target_price   INTEGER,
    holding_period TEXT,
    buy_reason     TEXT,
    memo           TEXT,
    user_id        TEXT NOT NULL DEFAULT 'local',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
, name TEXT, entry_price INTEGER, alert_off INTEGER NOT NULL DEFAULT 0, alerted_on TEXT)
```
