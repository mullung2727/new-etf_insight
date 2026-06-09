# watchlist 배치 전환 계획

## 한 줄 요약
pykrx KRX 주가 캐시(`krx_ohlcv.duckdb`) + 급등 필터 + LLM 스코어(`watchlist.duckdb`) → `api(:8000)` 서빙 → `broker-web` fetch 교체. 빈 거래일은 배치가 KRX api로 자동 채움(자기치유).

---

## 배경

| 현재 | 목표 |
|------|------|
| `stock_data.json` 수동 관리, broker-web 정적 import | etl 배치 → DuckDB → api 서빙 |
| LLM 스코어 JSON 파일 날짜별 산재 | DuckDB `llm_scores` 테이블 통합 관리 |
| 차트: 키움 ka10081 매번 호출 | krx_ohlcv DB 조회로 대체 가능 |

참고 원본 로직: `C:\local_document\stock_scout\python_scripts\load_krx_data\krx_hight_volumns.py`

---

## 결정사항 (검토 확정)

| # | 항목 | 결정 |
|---|------|------|
| 1 | 날짜 포맷 | 전 테이블 `'YYYYMMDD'` 통일 (llm_scores도 변환 저장) |
| 2 | 거래량 상위 30 | KOSPI+KOSDAQ **통합** 30, **volume(주식수)** 기준 |
| 3 | 신규진입 기준 | 60 **거래일** lookback (`find_new_top` lookback_dt=60) |
| 4 | watchlist 컬럼 | `(date, stock_code)`만 유지 (지표는 llm_scores 조인) |
| 5 | DuckDB 락충돌 | api는 요청마다 read_only connect 후 close (싱글톤 X) |
| 6 | 구 notion.json | ratio/volume 등 누락 필드 NULL 허용 적재 |
| 7 | sources 컬럼 | 파일 최상위 sources를 해당 날짜 전 row에 복사 |
| 8 | 초기 백필 | 고정 범위 없음 — 자기치유 갭필(빈 거래일 자동 api 채움) |

---

## DB 구조 (3개 파일 완전 분리)

```
etl/db/
├── etf_insight.duckdb   ← 기존. DART ETF 분석 전용
├── krx_ohlcv.duckdb     ← 신규. KRX 주가 범용 캐시 (pykrx 원본)
└── watchlist.duckdb     ← 신규. 급등 필터 결과 + LLM 스코어
```

| DB | 용도 | 참조처 |
|----|------|--------|
| `etf_insight.duckdb` | DART 공시 ETF 분석 | api/routers/etfs.py |
| `krx_ohlcv.duckdb` | KOSPI+KOSDAQ 전종목 OHLCV 캐시 | watchlist 배치, 차트 조회, 향후 분석 |
| `watchlist.duckdb` | 급등 필터 종목 목록 + LLM 스코어 | api/routers/watchlist.py |

---

## 데이터 흐름

```
[매일 장 마감 후 — build_watchlist.py 단독 실행]

build_watchlist.py
  ├─ 거래일 달력 확보 (기준종목 005930 ohlcv index)
  │   → 휴장일/주말 vs 미적재 거래일 구분
  ├─ 필요 거래일 ohlcv 누락 감지 (자기치유 갭필)
  │   └─ build_krx_ohlcv (내부 호출) → pykrx → krx_ohlcv.duckdb upsert
  │      ※ 서버 며칠 꺼져 있었어도 빠진 거래일 전부 채움
  ├─ krx_ohlcv에서 급등 필터 계산
  │   (통합 거래량 상위 30 → 과거 60거래일 신규진입 → 종가 ≤500 제외)
  │   → watchlist.duckdb / watchlist 테이블 upsert
  └─ REPORTS_DIR/volume_spike_*.notion.json 스캔
      → watchlist.duckdb / llm_scores 테이블 upsert

[수동/초기 적재 시]
build_krx_ohlcv.py (단독 실행 가능) → krx_ohlcv.duckdb

watchlist.duckdb → api(:8000) → broker-web
```

### 자기치유 갭필 규칙
- watchlist를 거래일 D에 대해 계산하려면 `D-60거래일 ~ D` 구간 ohlcv 필요.
- 매 실행 시 그 구간 거래일 목록을 기준종목 달력으로 산출 → krx_ohlcv에 없는 거래일만 pykrx 호출 → upsert.
- 첫 실행 = 전 구간 비어있음 → lookback 창 자동 적재.
- 휴장일은 달력에 없으므로 재호출 안 함(무한루프 방지).

---

## DB 스키마

### `krx_ohlcv.duckdb` — `ohlcv` 테이블
```sql
CREATE TABLE ohlcv (
    date        VARCHAR,   -- 'YYYYMMDD'
    ticker      VARCHAR,
    market      VARCHAR,   -- 'KOSPI' | 'KOSDAQ'
    open        INTEGER,
    high        INTEGER,
    low         INTEGER,
    close       INTEGER,
    volume      BIGINT,
    trading_value BIGINT,
    PRIMARY KEY (date, ticker)
);
```

### `watchlist.duckdb` — `watchlist` 테이블
```sql
CREATE TABLE watchlist (
    date       VARCHAR,   -- 'YYYYMMDD'
    stock_code VARCHAR,
    PRIMARY KEY (date, stock_code)
);
```

### `watchlist.duckdb` — `llm_scores` 테이블
```sql
CREATE TABLE llm_scores (
    date           VARCHAR,   -- 'YYYYMMDD' (notion.json 'YYYY-MM-DD' → 변환 저장)
    ticker         VARCHAR,
    name           VARCHAR,
    ratio          DOUBLE,    -- 구 json 누락 시 NULL
    today_volume   BIGINT,    -- 구 json 누락 시 NULL
    avg5_volume    BIGINT,    -- 구 json 누락 시 NULL
    trading_value  BIGINT,    -- 구 json 누락 시 NULL
    close          INTEGER,   -- 구 json 누락 시 NULL
    score          INTEGER,
    category       VARCHAR,   -- 구 json 누락 시 NULL
    reason_summary TEXT,
    final_opinion  TEXT,
    evidence_board TEXT,
    evidence_news  TEXT,
    evidence_web   TEXT,
    sources        TEXT,      -- 파일 최상위 sources(JSON 배열 문자열)를 전 row 복사
    PRIMARY KEY (date, ticker)
);
```

> notion.json 인코딩: utf-8 (`open(..., encoding="utf-8")`). item 키 드리프트(구 json은 ratio/today_volume/avg5_volume/trading_value/close/category 없음) → `.get()` 으로 None 허용.

---

## 급등 필터 로직 (krx_hight_volumns.py 기반)

1. per-date 전종목 OHLCV(KOSPI+KOSDAQ concat) → 거래량(volume) 데이터프레임 구성
2. 날짜별 거래량 **통합 상위 30종목** 추출 (`get_top_trading_data`, n_top=30)
3. `find_new_top(lookback_dt=60)`: 날짜 i 기준 `[i-60거래일, i)` top30 집합에 없던 종목이 i일 top30에 신규 등장 → 타깃
4. 동전주(종가 ≤ 500원) 제외
5. → `watchlist` 테이블 upsert (`{date: [stock_code]}`)

---

## 구현 단계

### STEP 1 — KRX OHLCV 캐시 배치 ✅ 완료

> **데이터 출처 정정**: pykrx `get_market_ohlcv_by_ticker`(전종목 스냅샷)는 죽은 스크래핑
> 호스트 `data.krx.go.kr`를 쳐서 현 환경에서 빈 응답/실패(한글 컬럼 리터럴 mojibake로 내부 KeyError).
> → **KRX 공식 OpenAPI**(`data-dbg.krx.co.kr`)로 교체. 거래일 달력만 pykrx(네이버 호스트
> `get_market_ohlcv`, 005930 index) 유지. (※ `openapi.krx.go.kr`은 존재 안 하는 도메인.)

- `etl/scripts/build_krx_ohlcv.py`
  - **거래일 달력**: `stock.get_market_ohlcv(from, to, "005930").index` (네이버). KRX는 거래일에만
    행을 주므로 휴장일/주말은 달력에 애초에 없음 → 누락집합에 안 들어옴 → 재호출 안 함(무한루프 방지).
  - **누락 거래일 식별**: `달력 − 보유일(SELECT DISTINCT date FROM ohlcv)` = 누락
  - 누락 거래일마다 전종목 일괄 수집 (**거래일당 2콜**, KRX OpenAPI):
    - `GET https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd` → `market='KOSPI'` (961행)
    - `GET https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd` → `market='KOSDAQ'` (1784행)
    - 헤더 `AUTH_KEY: <KRX_API_KEY>`, 파라미터 `basDd=YYYYMMDD`, 응답 `{"OutBlock_1":[...]}`
    - 필드: `ISU_CD`(6자리 단축코드=키움 동일)→ticker, `TDD_OPNPRC/HGPRC/LWPRC/CLSPRC`→OHLC,
      `ACC_TRDVOL`→volume, `ACC_TRDVAL`→trading_value. `MKT_NM`='KOSPI'/'KOSDAQ' 그대로.
    - 값은 콤마 없는 정수 문자열, 거래정지 등은 `'-'`/`''` → NULL(`_parse_int`)
  - `time.sleep(0.4)` rate-limit (콜 사이)
  - → `ohlcv` 테이블 INSERT OR REPLACE upsert
  - 단독 실행 가능 (초기 적재·재수집용). `--from/--to/--date` 인자.
  - **재사용 함수** `ensure_ohlcv(con, from, to, key, force=False)` → STEP2가 자기치유 갭필로 import.
  - `--force`: 이미 적재된 거래일도 KRX에서 다시 받아 덮어쓰기(`INSERT OR REPLACE`). 기본은 누락분만(멱등).
  - 검증: 20250102~03 2거래일 → 5490행(2745/일), 005930 정상, 멱등(재실행 missing=0).

### STEP 2 — watchlist 배치 (메인, 매일 실행) ✅ 완료
- `etl/scripts/build_watchlist.py`
  - 대상 거래일 D 결정 (기본: 달력 최신 거래일). `--date` 인자 지원.
  - `D-100캘린더일 ~ D`(≈68거래일, lookback 60 확보) ohlcv 누락 시 `ensure_ohlcv` import 호출 (자기치유 갭필)
  - `krx_ohlcv.duckdb`에서 close/volume **피벗**(index=date, cols=ticker) 로드 → 급등 필터
    - 통합 거래량 상위 30 (`get_top_trading_data`, nlargest)
    - 과거 60거래일 신규 진입 (`find_new_top`)
    - 종가 ≤ 500 제외 (`pd.notna & close>500`)
  - → `watchlist.duckdb / watchlist` upsert (INSERT OR REPLACE)
  - `REPORTS_DIR/volume_spike_*.notion.json` 전체 스캔
    - 날짜 `YYYY-MM-DD` → `YYYYMMDD` 변환
    - item `.get()` 으로 누락 필드 None (구포맷=ratio/volume/close/category 없음)
    - **`sources` 키 없음** → `d.get("sources") or d.get("source_data")` JSON 직렬화하여 각 row 복사
  - → `watchlist.duckdb / llm_scores` upsert (16컬럼)
  - 검증: ohlcv 68거래일 적재, watchlist 6일/34종목(20260529~0608), llm_scores 20행/9일.
    구·중·신 3포맷 필드드리프트 정상 처리.

> **주의(Windows)**: 빈응답 print의 em-dash(`—`)가 cp949 콘솔서 `UnicodeEncodeError` → 배치 중단.
> 두 스크립트 상단 `sys.stdout.reconfigure(encoding="utf-8")` + 해당 print ascii화로 수정.
> `REQUEST_SLEEP` 0.4→0.1 (초기 백필 가속). 첫 실행만 ~68거래일 다운로드(수분), 이후 일 1건.
> **알려진 한계**: KRX 전종목 TR은 우선주/신주인수권/ETN(예 `0117P0`) 포함. 보통주만 원하면
> `SECT_TP_NM`/코드패턴 필터 추가 필요(현재 미적용 — 종가>500 컷만 있음).

### STEP 3 — api 라우터 추가 ✅ 완료
- `api/duck_watchlist.py` — watchlist.duckdb + krx_ohlcv.duckdb 연결
  - **요청마다 `duckdb.connect(read_only=True)` 후 close** (싱글톤 X, contextmanager) — 배치 write 락과 충돌 방지
  - 경로: env `WATCHLIST_DB_PATH`/`KRX_DB_PATH`, 기본 `../etl/db/*.duckdb`(api 기준 상대). `.env.example` 추가됨.
- `api/routers/watchlist.py`
  - `GET /watchlist` → date별 종목코드 목록 (`{YYYYMMDD: [code]}`)
  - `GET /watchlist/scores/{date}` → 특정 날짜(`YYYYMMDD`) LLM 스코어 (score DESC, `sources` JSON 디코드)
  - `GET /ohlcv/{ticker}` → krx_ohlcv 일봉(차트용, 오름차순). `begin/end/limit`(기본 120) 쿼리.
    **차트 소스 결정: 옵션 B (krx DB, 키움 ka10081 미사용).** 현재 갭필창 ~68거래일만 보유.
- `api/main.py` — 라우터 등록 + `api/schemas.py` `LlmScore`/`OhlcvCandle` 추가
- 검증: TestClient 3엔드포인트 200. watchlist 6일, scores 정렬/디코드, ohlcv 오름차순 확인.

### STEP 4 — broker-web 교체 ✅ 완료
- `broker-web/app/watchlist/page.tsx`
  - `import stockData from "@/data/stock_data.json"` 제거 → `apiGet<Record<string,string[]>>("/watchlist")`
  - DART corpCodes fetch와 `Promise.all` 병렬. (data/stock_data.json 고아 파일로 잔존, 삭제 안 함)
- `broker-web/app/watchlist/[code]/page.tsx`
  - **차트는 키움 ka10081 유지**(옵션 A 결정). `/ohlcv`는 미사용·보관.
  - `fetchScore`: `/watchlist/scores/{baseDate}`에서 `ticker===code` 필터 → llm_score 패널 추가
    (score/category/reason_summary/final_opinion). 기준일은 기존 헤더에 이미 표시됨.
- 검증: `npx tsc --noEmit` 통과(EXIT=0), stock_data.json 참조 0. (런타임은 next dev 금지 → 미실행)

---

## 환경 변수

| 키 | 위치 | 값 예시 |
|----|------|---------|
| `KRX_API_KEY` | 루트 `.env` | KRX OpenAPI AUTH_KEY (40자) — **이미 존재** |
| `REPORTS_DIR` | 루트 `.env` | `C:\Users\mullu\.openclaw\workspace\reports` |
| `KRX_DB_PATH` | 루트 `.env` | `./etl/db/krx_ohlcv.duckdb` |
| `WATCHLIST_DB_PATH` | 루트 `.env` | `./etl/db/watchlist.duckdb` |

---

## 진행 상태

- [x] STEP 1: `build_krx_ohlcv.py` (거래일 달력 pykrx + KRX OpenAPI 갭필 + 캐시, 단독 실행·재사용 함수)
- [x] STEP 2: `build_watchlist.py` (메인 배치 — 자기치유 갭필 + 급등 필터 + LLM 스코어)
- [x] STEP 3: `api/routers/watchlist.py` + `/ohlcv/{ticker}`(차트=krx DB) (per-request read_only connect)
- [x] STEP 4: `broker-web` fetch 교체 (목록=/watchlist, 차트=키움 유지 + llm_score 패널)

---

## 후속 / 미해결

- **llm_scores ↔ watchlist 종목 발산**: 현재 `llm_scores`는 외부 LLM이 `REPORTS_DIR/volume_spike_*.notion.json`을
  다른 선정기준으로 만든 것이라 watchlist 종목과 안 겹친다. → **다음 작업**: 외부 LLM이 watchlist 종목을
  입력으로 분석하도록 전환(실행법은 skill `etf-watchlist-batch`). 그때 이 파일스캔 적재는 무시/대체.
- **`/ohlcv/{ticker}` 보관**: STEP4 차트는 키움 ka10081로 결정(옵션 A) → `/ohlcv`는 차트 미사용.
  범용 OHLCV 조회·키움 폴백·백테스트용으로 남겨둠(제거 안 함).
- **`broker-web/data/stock_data.json` 고아**: STEP4에서 import 제거됨. 참조 0이나 파일은 잔존(삭제 보류).
- **비보통주 오염**: watchlist에 우선주/신주인수권/ETN(예 `0117P0`) 포함 가능(종가>500 컷만). 보통주 한정 원하면
  `SECT_TP_NM`/코드패턴 필터 추가 필요.
