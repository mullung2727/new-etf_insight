# PLAN — etf_insight / watchlist DB: DuckDB → SQLite 전환

**한 줄 요약:** OLAP 집계가 필요 없는 2개 DB(`etf_insight`, `watchlist`)를 stdlib `sqlite3` 기반 SQLite로 교체한다. 데이터는 복사하지 않고 기존 산출물(runs/·reports JSON)에서 **배치 재실행으로 재빌드**한다. `krx_ohlcv.duckdb`(OHLCV)는 DuckDB 그대로 유지한다. **`watchlist.duckdb`는 5개 스크립트·4+테이블이 공유하므로 원자적 동시 전환**이 핵심.

---

## 0. 결정 사항 (확정)

| 항목 | 결정 |
|------|------|
| 이전 방식 | 배치 재실행 재빌드 (LLM 재호출 0, JSON→SQLite 재적재) |
| 적용 범위 | 지금 `main`에 바로 전환 |
| 접근 계층 | stdlib `sqlite3` 유지 (신규 의존성 0, raw SQL 스타일 유지) |
| watchlist 범위 | **전체 SQLite 전환** (5스크립트·4+테이블, close_bet/verify 트레이딩 테이블 포함) |
| 동시성 모드 | WAL 활성화. **단, reader는 `mode=ro` 금지** → `connect()` 후 `PRAGMA query_only=ON` (사유: §5-A) |
| 유지 대상 | `krx_ohlcv.duckdb` 는 DuckDB (OLAP 집계 21~30배 우위) |

## 1. 배경 / 범위

### 전환 대상 ① `etf_insight.duckdb` → SQLite
테이블 `etf_records`, `etf_holdings`
- writer: `etl/scripts/build_db.py` (+ STEP0에서 `daily_pipeline.py`의 duckdb 참조 경로 확인)
- reader: `api/duck.py` → `api/routers/etfs.py`, `api/routers/stats.py`

### 전환 대상 ② `watchlist.duckdb` → SQLite (★공유 DB, 원자적 전환)
| 테이블 | writer 스크립트 | reader |
|--------|----------------|--------|
| `watchlist` | `build_watchlist.py`, `build_intraday_ranking.py` | `api/routers/watchlist.py` |
| `llm_scores` | `build_watchlist.py`, `run_watchlist_research.py` | `api/routers/watchlist.py`, `run_watchlist_research.py` |
| `intraday_ranking` | `build_intraday_ranking.py` | `build_intraday_ranking.py` |
| `close_bet_orders` | `run_close_bet.py` | `run_close_bet.py`, `run_verify.py` |
| (verify 결과/갱신) | `run_verify.py` | `run_verify.py` |

- API reader 진입점: `api/duck_watchlist.py` 의 `watchlist_cursor`
- 영향 테스트: `test_verify.py`, `test_close_bet.py`, `test_close_bet_integration.py`, `test_intraday_*`, `test_watchlist_research_safety.py`
- **SQLite는 DuckDB 파일을 못 읽으므로 한 스크립트만 전환하면 나머지가 깨진다 → 5스크립트+테스트 동시 전환 필수.**

### 유지 대상 `krx_ohlcv.duckdb` (DuckDB)
테이블 `ohlcv`, `holidays`
- `build_watchlist.compute_watchlist` window 집계, `build_intraday_ranking`/`run_watchlist_research`의 krx 읽기, `api`의 `get_ohlcv`(`krx_cursor`)
- **이유:** 유일한 대용량(현 20만행, 10년 ≈700만행) + OLAP 집계. SQLite 대비 21~30배 빠름.

> `build_watchlist.py`/`run_watchlist_research.py`의 관계: **LLM 실행은 `run_watchlist_research.py`** 가 하고 결과를 `reports/volume_spike_*.notion.json` + `llm_scores`에 적재. `build_watchlist.load_llm_scores()`는 그 JSON을 스캔해 upsert할 뿐. 재빌드는 LLM 재호출과 무관.

## 2. DuckDB → SQLite 마이그레이션 개요

### (a) 데이터 이동 — "복사" 대신 "재빌드"
두 DB 모두 디스크 원본에서 재현 가능. `etf_insight`←`runs/*/records/*.json`, `watchlist`←`krx_ohlcv.duckdb`+`reports/*.notion.json`(+ intraday/close_bet은 키움 재수집 또는 기존 .duckdb에서 1회 ATTACH 복사). SQLite 스키마로 고친 배치를 한 번 돌리면 새 `.sqlite3` 채워짐.

> close_bet/verify/intraday 같은 **외부 재현 불가/재수집 비용 큰** 테이블은 재빌드 대신 DuckDB sqlite_scanner로 1회 복사 권장:
> `INSTALL sqlite; LOAD sqlite; ATTACH 'watchlist.sqlite3' AS s (TYPE SQLITE); CREATE TABLE s.close_bet_orders AS SELECT * FROM close_bet_orders;` (intraday_ranking 등 동일)

### (b) 코드 포팅 — DuckDB 특화 → SQLite 표준
| 구분 | DuckDB (현재) | SQLite | 영향 |
|------|---------------|--------|------|
| 명명 파라미터 | `$name` | `:name` | `routers/etfs.py`, `stats.py` (+스크립트 점검) |
| 안전 캐스팅 | `TRY_CAST(x AS DOUBLE)` 실패→NULL | `CAST`는 junk→`0.0`(NULL 아님) → 의미 변질 | `routers/stats.py` weight 집계 |
| 현재일자 | `STRFTIME('%Y%m%d', CURRENT_DATE)` | `strftime('%Y%m%d','now','localtime')` | `etfs.py`, `stats.py` |
| 커넥션(읽기) | `duckdb.connect(path, read_only=True)` | `sqlite3.connect(path)` + `PRAGMA query_only=ON` (§5-A) | `duck.py`, `duck_watchlist.py`, 각 스크립트 `read_only=True` 5+곳 |
| execute 반환 | `con.execute()` 가 결과 보유 | `con.execute()` 는 **Cursor** 반환 | `sql_utils.rows_to_dicts` 는 connection이 아닌 cursor를 받아야 함 |
| 컬럼 타입 | `JSON/DOUBLE/BIGINT/BOOLEAN/TIMESTAMP` | `TEXT/REAL/INTEGER` (동적타입) | 각 writer CREATE 문 |

**무변경(양쪽 호환):** `PRAGMA table_info`, `INSERT OR REPLACE`, `executemany`, `ALTER TABLE ADD COLUMN`, `ROUND`, `EXISTS(...)`.

**버전 의존(STEP0서 확인):** `true/false` 리터럴(SQLite ≥3.23, stats.py `is_pre_listing_etf = true`), `NULLS LAST`(≥3.30, watchlist 라우터). 번들 python `sqlite3.sqlite_version` 확인.

**`TRY_CAST` 주의:** stats.py는 `TRY_CAST(REPLACE(weight,'%','') AS DOUBLE)`+`BETWEEN 0 AND 100`으로 비숫자 weight를 NULL→제외. SQLite `CAST`는 `'-'` 같은 junk를 `0.0`으로 바꿔 필터 통과시켜 평균 왜곡 → `CASE WHEN <숫자판정> ...` 또는 `... GLOB` 가드로 NULL 처리.

## 3. 단계별 작업 (TDD + 단계별, 각 단계 후 테스트 결과 보고 → 확인 후 다음)

### STEP 0 — 인벤토리 & 환경 확인 (코드 변경 없음) ✅ 완료
결과:
- **환경**: `sqlite3.sqlite_version = 3.50.4`, python 3.14.3 (`uv run`). `true/false`·`NULLS LAST` 전부 가용 → 버전 리스크 없음. 단 py3.14라 sqlite3 datetime 기본 어댑터 deprecated → TIMESTAMP는 ISO 문자열로 명시.
- **daily_pipeline.py**: 직접 duckdb 안 씀. `sync_to_db(runs_dir, db_path)` 위임(L114). 단 L113 하드코딩 `db/etf_insight.duckdb` → `.sqlite3` 수정 필요(STEP1).
- **run_verify.py**: `close_bet_orders` UPDATE만(mark_confirmed/mark_unconfirmed). `verified_at` datetime 파라미터 → ISO 문자열화.
- **행수 스냅샷 (전환 후 일치 검증 기준)**:
  - `etf_insight`: etf_records=30, etf_holdings=798
  - `watchlist`: watchlist=58, llm_scores=48, intraday_ranking=120, close_bet_orders=3
  - `krx_ohlcv`(유지): ohlcv=202528, holidays=4
- **watchlist.duckdb 전체 스키마 확정**:
  - `watchlist`(date, stock_code) PK
  - `llm_scores` 16컬럼 (date,ticker,name,ratio DOUBLE,today_volume/avg5_volume/trading_value BIGINT,close/score INTEGER,category,reason_summary/final_opinion/evidence_* TEXT,sources TEXT) PK(date,ticker)
  - `intraday_ranking`(date,rank INTEGER,ticker,name,volume BIGINT,close INTEGER) PK(date,ticker)
  - `close_bet_orders`(date,ticker,score INT,qty INT,order_type,status,order_no,message TEXT,raw TEXT,**created_at TIMESTAMP**,cntr_price INT,cntr_qty INT,**verified_at TIMESTAMP**) PK(date,ticker)
  - → SQLite 매핑: BIGINT/INTEGER→`INTEGER`, DOUBLE→`REAL`, VARCHAR/TEXT→`TEXT`, **TIMESTAMP→`TEXT`(ISO)**

### STEP 1 — `etf_insight` writer 전환 (`build_db.py`)
- `duckdb`→`sqlite3`, CREATE 타입 SQLite화, 경로 `.duckdb`→`.sqlite3`, `PRAGMA journal_mode=WAL`
- 검증: 재실행 → 행수/대표 레코드가 STEP0 스냅샷과 일치(unittest)

### STEP 2 — `etf_insight` reader 전환 (`api/duck.py` + 라우터)
- `duck.py`: `sqlite3.connect` + `PRAGMA query_only=ON` (mode=ro 아님)
- `sql_utils.rows_to_dicts`: `execute()`가 반환한 cursor를 받도록 호출부 수정
- `etfs.py`/`stats.py`: `$name`→`:name`, `strftime` localtime, `TRY_CAST` 가드, `true` 리터럴 점검
- 검증: API 응답 스냅샷 전후 일치

### STEP 3 — `watchlist` writer 전환 (★5스크립트 원자적)
순서대로, 각 파일 전환 후 해당 테스트 통과 확인:
1. `build_watchlist.py` — `watchlist`/`llm_scores` write를 sqlite3로. **`compute_watchlist`의 krx 읽기(`krx_con`)는 DuckDB 유지** → 한 파일에 DuckDB(읽기)+SQLite(쓰기) 공존
2. `build_intraday_ranking.py` — `intraday_ranking`/`watchlist` write sqlite3화, krx 읽기 DuckDB 유지
3. `run_watchlist_research.py` — `llm_scores` write + `read_only=True` 읽기 sqlite3화, krx 읽기 DuckDB 유지
4. `run_close_bet.py` — `close_bet_orders` write/read sqlite3화
5. `run_verify.py` — watchlist_db read/write 2곳 sqlite3화
- 공통: CREATE 타입 SQLite화, 경로 `.sqlite3`, 첫 write 연결서 WAL, read 연결서 `query_only`
- 데이터: §2-a대로 watchlist/llm_scores는 재빌드, close_bet/intraday는 ATTACH 1회 복사
- 검증: 각 테이블 행수 STEP0 스냅샷 일치 + `test_verify/test_close_bet*/test_intraday_*/test_watchlist_research_safety` 통과

### STEP 4 — `watchlist` reader 전환 (`api/duck_watchlist.py` + 라우터)
- `duck_watchlist.py` 분리: `watchlist_cursor`→sqlite3(+query_only), `krx_cursor`→DuckDB 유지
- `routers/watchlist.py`: `list_watchlist`/`get_watchlist_scores`(watchlist DB)→sqlite3 커서(+ rows_to_dicts cursor 처리, `NULLS LAST` 동작 확인), `get_ohlcv`(krx)→무변경
- 검증: 세 엔드포인트 응답 전후 일치

### STEP 5 — 설정·문서·정리
- `api/.env.example`: `DUCKDB_PATH`/`WATCHLIST_DB_PATH` 값·주석 갱신(`KRX_DB_PATH` 유지). 변수명 rename 여부 결정
- `api/main.py` health, `duck.py` 등 "DuckDB" 문구 정정
- `etl/CLAUDE.md`, SKILL.md, `ops/batches/*.md`의 `.duckdb` 경로 갱신
- `duckdb` 의존성은 **유지**(krx 읽기). 기존 `.duckdb` 2개 파일은 신규 `.sqlite3` 검증 후 폐기

## 4. 환경변수 / 경로 영향

| 변수 | 현재 | 변경 후 |
|------|------|---------|
| `DUCKDB_PATH` | `…/etf_insight.duckdb` | `…/etf_insight.sqlite3` (변수명 유지/rename은 STEP5 결정) |
| `WATCHLIST_DB_PATH` | `…/watchlist.duckdb` | `…/watchlist.sqlite3` |
| `KRX_DB_PATH` | `…/krx_ohlcv.duckdb` | **무변경** |

## 5. 리스크 / 검증 포인트

- **A. WAL + read-only 충돌 (중요).** SQLite는 read-only 프로세스(`mode=ro`)가 WAL DB를 못 연다 — `-shm` wal-index에 쓰기 권한 필요("unable to open database file"). → 모든 reader는 `mode=ro` URI 대신 **일반 `connect()` + `PRAGMA query_only=ON`** 사용(WAL 유지하며 쓰기 차단). 대안: WAL 미사용(저접속이라 rollback journal로도 충분).
- **B. TRY_CAST 의미 차이** (§2-b) — weight 집계 결과 전후 비교 필수.
- **C. execute 반환 객체 차이** — `rows_to_dicts`는 connection이 아닌 cursor를 받아야. 호출부 전수 점검(etfs/stats/watchlist 라우터).
- **D. 다중 백엔드 공존** — `build_watchlist`·`build_intraday_ranking`·`run_watchlist_research`(DuckDB 읽기 + SQLite 쓰기), `duck_watchlist`(krx=DuckDB, watchlist=SQLite). 커넥션 혼동 주의.
- **E. strftime UTC** — `'now'`는 UTC라 한국 자정 경계서 -1일. `'localtime'` 명시.
- **F. 원자성** — watchlist.duckdb는 5스크립트 공유 → 5개 전부 전환 전엔 어느 하나도 운영 불가. STEP3는 분할 커밋하되 **배포는 5개 묶어** 진행.
- **G. 롤백** — etf/watchlist(재빌드 가능분)는 코드 되돌리고 재실행하면 복원. close_bet/verify(트레이딩 상태)는 ATTACH 복사 원본(.duckdb) 보존 → 데이터 손실 위험 차단.

## 6. 비전환 근거 (krx_ohlcv DuckDB 유지)

read-heavy 소량 테이블(etf/watchlist: 수백~수천 행)은 SQLite WAL의 reader-writer 동시성이 적합. OHLCV는 유일한 대용량 + 날짜/티커 window 집계 → DuckDB 컬럼나 OLAP가 21~30배 빠름. 혼합 유지가 최적.
