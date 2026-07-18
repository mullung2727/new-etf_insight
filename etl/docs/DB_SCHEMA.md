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

## `etf_insight.duckdb`

테이블 2개: `etf_holdings`, `etf_records`

```sql
etf_holdings (
    etf_key VARCHAR
    seq INTEGER
    name VARCHAR
    ticker VARCHAR
    exchange VARCHAR
    weight VARCHAR
)
```

```sql
etf_records (
    etf_key VARCHAR
    route VARCHAR
    is_pre_listing_etf BOOLEAN
    fund_name VARCHAR
    asset_manager VARCHAR
    index_name VARCHAR
    index_provider VARCHAR
    index_description VARCHAR
    primary_country VARCHAR
    holdings_available_in_pdf BOOLEAN
    holdings_summary VARCHAR
    keywords JSON
    trend_summary VARCHAR
    missing_info JSON
    rcept_no VARCHAR
    rcept_dt VARCHAR
    corp_code VARCHAR
    corp_name VARCHAR
    report_nm VARCHAR
    fund_code VARCHAR
    pdf_path VARCHAR
    first_rcept_dt VARCHAR
    revision_count INTEGER
    db_updated_at TIMESTAMP
    theme_status VARCHAR
    theme_bucket VARCHAR
    structure_tags JSON
    classification_confidence DOUBLE
    classification_evidence VARCHAR
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

## `watchlist.duckdb`

테이블 4개: `close_bet_orders`, `intraday_ranking`, `llm_scores`, `watchlist`

```sql
close_bet_orders (
    date VARCHAR
    ticker VARCHAR
    score INTEGER
    qty INTEGER
    order_type VARCHAR
    status VARCHAR
    order_no VARCHAR
    message VARCHAR
    raw VARCHAR
    created_at TIMESTAMP
    cntr_price INTEGER
    cntr_qty INTEGER
    verified_at TIMESTAMP
)
```

```sql
intraday_ranking (
    date VARCHAR
    rank INTEGER
    ticker VARCHAR
    name VARCHAR
    volume BIGINT
    close INTEGER
)
```

```sql
llm_scores (
    date VARCHAR
    ticker VARCHAR
    name VARCHAR
    ratio DOUBLE
    today_volume BIGINT
    avg5_volume BIGINT
    trading_value BIGINT
    close INTEGER
    score INTEGER
    category VARCHAR
    reason_summary VARCHAR
    final_opinion VARCHAR
    evidence_board VARCHAR
    evidence_news VARCHAR
    evidence_web VARCHAR
    sources VARCHAR
)
```

```sql
watchlist (
    date VARCHAR
    stock_code VARCHAR
)
```
