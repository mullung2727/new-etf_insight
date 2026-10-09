# DB 스키마 카탈로그

> 자동 생성: `uv run python scripts/dump_db_schema.py`. **직접 수정 금지.**
> 스키마 변경(테이블/컬럼 추가·변경) 후 재실행해 갱신할 것.
> 실제 DDL 소스는 각 스크립트의 `CREATE TABLE` 문.

## `etf_insight.sqlite3`

테이블 2개: `etf_holdings`, `etf_records`

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
)
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

테이블 3개: `report_api_facts`, `report_estimates`, `report_facts`

```sql
CREATE TABLE IF NOT EXISTS report_api_facts (
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
);
```

```sql
CREATE TABLE IF NOT EXISTS report_estimates (
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
);
```

```sql
CREATE TABLE IF NOT EXISTS report_facts (
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
);
```

## `swing_pick.sqlite3`

테이블 2개: `swing_candidates`, `swing_runs`

```sql
CREATE TABLE IF NOT EXISTS swing_candidates (
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
);
```

```sql
CREATE TABLE IF NOT EXISTS swing_runs (
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
);
```

## `telegram_public.sqlite3`

테이블 4개: `telegram_analysis_watermark`, `telegram_channels`, `telegram_posts`, `telegram_stock_insights`

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

## `us_financials.sqlite3`

테이블 3개: `accounts`, `corps`, `indicators`

```sql
CREATE TABLE accounts (
    cik         TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    sj_div      TEXT,
    account_nm  TEXT NOT NULL,
    amount      REAL,
    ticker      TEXT,
    filed_dt    TEXT,
    currency    TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (cik, bsns_year, reprt_code, account_nm)
)
```

```sql
CREATE TABLE corps (
    cik         TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    corp_name   TEXT NOT NULL,
    exchange    TEXT,
    updated_at  TEXT NOT NULL
)
```

```sql
CREATE TABLE indicators (
    cik         TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    idx_code    TEXT NOT NULL,
    idx_nm      TEXT,
    idx_val     REAL,
    ticker      TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (cik, bsns_year, reprt_code, idx_code)
)
```

## `watchlist.sqlite3`

테이블 6개: `close_bet_orders`, `intraday_ranking`, `llm_scores`, `pullback_orders`, `watchlist`, `watchlist_market_snapshots`

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
            verified_at TEXT, sell_order_no TEXT, sell_status TEXT, sell_price INTEGER, sell_qty INTEGER, sold_at TEXT, exit_reason TEXT, pnl_pct REAL, sell_cmsn INTEGER, sell_tax INTEGER, sell_pl_won INTEGER,
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
            verified_at TEXT,
            PRIMARY KEY (watchlist_date, ticker)
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

테이블 3개: `youtube_stock_insights`, `youtube_video_summaries`, `youtube_videos`

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
, name TEXT)
```
