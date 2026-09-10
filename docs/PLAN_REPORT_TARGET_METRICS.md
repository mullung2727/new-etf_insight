# PLAN — 증권사 리포트 목표주가/추정치 지표화 (report_metrics)

요약: 이미 받아둔 증권사 리포트 PDF(2112건, 2.19GB)에서 **목표주가·현재주가·투자의견·연도별 추정치**를
뽑아 ① 목표가 대비 상승여력, ② 동일 증권사 직전 리포트 대비 목표가 변화율, ③ RIM 입력값을
계산한다. 뼈대(시그니처+스키마+테스트) 먼저 세우고 단계별로 살을 붙인다. 배치 등록은 이번 범위 밖
(호출 가능한 CLI 진입점까지만).

---

## 0. 현황 실측 (추정 아님, 2026-09-09 측정)

- 저장 위치: `etl/exports/stock_reports/{종목명}_{종목코드}/{YYYY-MM-DD}_{증권사}_{pdf_key}.pdf`
  - 생성자: `etl/scripts/download_naver_research.py:dest_path()`
  - `pdf_key` = pstatic PDF 파일 stem = 안정 식별자 (`download_naver_research.py:pdf_key()`)
- 보유량: 2112건 / 2.19GB. 발간월 분포 = 2026-05:1, 2026-06:1, 2026-07:991, 2026-08:1012, 2026-09:107
- 랜덤 200건 표본 pypdf 1페이지 텍스트 추출 결과:

| 항목 | 적중 | 비고 |
|---|---|---|
| `목표주가` 또는 `목표가` | 159/200 (79.5%) | 미적중 상당수는 NOT RATED(한국IR협의회·NICE평가정보) |
| `현재주가` 또는 `현재가` | 161/200 | |
| `직전 목표` | 18/200 | 유안타 등 일부만 인쇄 → 교차검증용, 주 소스 아님 |
| `상승여력`/`Upside` | 71/200 | 인쇄값 존재 시 자체 계산과 대조 |
| `20\d\dF` (추정표) | 86/200 | 1페이지 한정. 추정표가 뒷장인 리포트 있음 |
| 1페이지 텍스트 200자 미만 | 16/200 (8%) | **전부 미래에셋증권 + iM증권 1건** |

- **미래에셋증권 리포트는 전체 페이지 합쳐도 텍스트 약 140자 = 이미지 PDF.** pypdf로 파싱 불가.
  → 이번 범위에서 `unsupported_image_pdf` 로 분류하고 **조용히 실패시키지 않는다**. OCR은 범위 밖.
- 증권사 17곳: 대신/유안타/미래에셋/신한투자/키움/교보/SK/유진투자/하나/한국IR협의회/iM/IBK/DS/한화/NICE 등

### 실제 1페이지 텍스트 형태 (3종)

```
신한투자증권:  ✓ 투자판단 매수 (유지)  ✓ 목표주가 6,500 원 (유지)
              ✓ 상승여력 43.8%  ✓ 현재주가 (8 월 14 일) 4,520 원
유진투자증권:  투자의견 BUY(유지) / 목표주가 7,000원(유지) / 현재주가 4,520 원(8/14)
유안타증권:    NOT RATED (I) / 목표주가 -원 (I) / 직전 목표주가 -원 / 현재주가 (8/7) 19,660원 / 상승여력 -
```

추정표(신한 예):

```
12월 결산 매출액 영업이익 지배순이익 PER ROE PBR EV/EBITDA DY
       (십억원) (십억원)  (십억원)   (배) (%) (배)  (배)     (%)
2024    964.6    76.3      21.8      9.0  5.0 0.4   4.9      6.3
2026F  1,172.0   86.7      40.4      5.1  8.6 0.4   4.5      8.2
```

---

## 1. 요구사항 (사용자 발화 기준)

| # | 요구사항 | 구현 지점 | 검증 테스트 |
|---|---|---|---|
| R1 | 리포트에서 현재주가 대비 목표가 계산 함수 | `report_metrics/metrics.py:upside()` | `test_report_metrics_stage2.py` |
| R2 | 이전 리포트(**동일 증권사**) 대비 목표가 변화율 함수 | `report_metrics/metrics.py:target_revision()` | `test_report_metrics_stage3.py` |
| R3 | R1·R2를 배치에서 같이 돌릴 수 있게 (등록은 아직 안 함) | `scripts/run_report_metrics.py:main()` | `test_report_metrics_stage6.py` |
| R4 | 미래 매출 추정치 등 파싱 | `report_metrics/parse.py:parse_estimates()` | `test_report_metrics_stage4.py` |
| R5 | RIM 모형 돌릴 수 있도록 준비 | `report_metrics/rim.py:build_rim_inputs()`, `rim_value()` | `test_report_metrics_stage5.py` |
| R6 | 뼈대 먼저 → 살 붙이기, TDD | Stage 0에서 시그니처·스키마·테스트 계약 전부 확정 | `test_report_metrics_stage0.py` |

---

## 2. 확정 결정

### 2.1 "현재주가"는 두 개다 — 필드를 분리한다

리포트에 인쇄된 현재주가는 **발간 시점 기준**이고 오늘 주가와 다르다. 하나로 뭉개면 R1의 의미가 흐려진다.

- `price_at_report` = PDF에 인쇄된 "현재주가" (+ `price_at_report_date`)
- `price_now` = `etl/db/krx_ohlcv.duckdb` 의 `ohlcv.close` 에서 `as_of` 날짜로 조회
- 산출: `upside_at_report = target/price_at_report - 1`, `upside_now = target/price_now - 1`
- `price_now` 조회 실패(휴장일·미수집)면 `upside_now = None`. **0으로 채우지 않는다.**

### 2.2 "이전 리포트" 정의

`(stock_code, broker)` 가 같고 `report_date` 가 현재 리포트보다 **엄격히 작은** 것 중 가장 최근 1건.
동일 날짜 같은 증권사 복수 리포트는 이전으로 보지 않는다(같은 날 상·하향 판단 불가).
조회 함수 = `report_metrics/storage.py:find_previous_report()` (DB 질의).

PDF에 인쇄된 "직전 목표주가"(표본의 9%)는 **주 소스로 쓰지 않는다.** 파싱은 하되
`prev_target_printed` 로 따로 저장하고, DB 기반 값과 불일치 시 `prev_target_mismatch` 플래그만 남긴다.

### 2.3 식별자·중복

- PK = `pdf_key` (파일 stem). 파일명에서 `split("_", 2)` 로 `report_date`, `broker`, `pdf_key` 분해.
  - 근거: `dest_path()` 가 `f"{date}_{sanitize(broker)}_{key}.pdf"` 로 만들고, `sanitize()` 는
    `[\/:*?"<>|]` 만 치환하므로 `_` 를 보존한다. 증권사명 17곳에 `_` 없음(실측).
  - **단정 회피**: 증권사명에 `_` 가 들어오면 broker 가 잘린다 → `split("_", 2)` (maxsplit=2) 로
    3번째 조각 이후를 전부 key 로 붙인다. 즉 key 는 항상 원본 stem 의 꼬리와 일치한다.
- 종목코드/종목명 = 부모 디렉터리명 `{종목명}_{종목코드}` 에서 **마지막** `_` 기준 rsplit.
  - 종목명에 `_` 가 들어갈 수 있음(sanitize 가 `/` 를 `_` 로 바꾸므로 실제 발생 가능) → rsplit 필수.

### 2.4 저장소

- 신규 `etl/db/report_metrics.sqlite3` (SQLite). `wl_sqlite.py` 는 watchlist 전용이라 재사용 안 함
  (`wl_sqlite.py` docstring 이 그렇게 명시). 연결 헬퍼는 `report_metrics/storage.py` 안에 자체 정의.
- 테이블 2개:

```sql
CREATE TABLE IF NOT EXISTS report_facts (
  pdf_key TEXT PRIMARY KEY,
  pdf_path TEXT NOT NULL,
  stock_code TEXT NOT NULL,
  stock_name TEXT,
  broker TEXT NOT NULL,
  report_date TEXT NOT NULL,            -- YYYY-MM-DD
  opinion TEXT,                         -- 원문 그대로 (매수/BUY/NOT RATED/...)
  target_price INTEGER,                 -- 원. NOT RATED 는 NULL
  price_at_report INTEGER,
  price_at_report_date TEXT,            -- YYYY-MM-DD (연도는 report_date 에서 보정)
  upside_printed REAL,                  -- 리포트 인쇄 상승여력(소수, 0.438)
  prev_target_printed INTEGER,
  parse_status TEXT NOT NULL,           -- ok | no_target | unsupported_image_pdf | parse_error
  parse_error TEXT,
  parser_version TEXT NOT NULL,
  parsed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_stock_broker_date
  ON report_facts(stock_code, broker, report_date);

CREATE TABLE IF NOT EXISTS report_estimates (
  pdf_key TEXT NOT NULL REFERENCES report_facts(pdf_key) ON DELETE CASCADE,
  fiscal_year INTEGER NOT NULL,
  is_forecast INTEGER NOT NULL,         -- 'F'/'E' 표기면 1
  revenue REAL,                         -- 억원으로 정규화
  operating_profit REAL,                -- 억원
  net_profit REAL,                      -- 억원 (지배주주)
  eps REAL, per REAL, roe REAL, pbr REAL, div_yield REAL,
  PRIMARY KEY (pdf_key, fiscal_year)
);
```

- 단위 정규화: 표 헤더의 `(십억원)`/`(억원)`/`(백만원)` 을 읽어 **억원**으로 환산해 저장.
  헤더 단위를 못 읽으면 해당 행 금액 컬럼은 NULL (추정 금지).

### 2.5 RIM 파라미터 — 미확정, 기본값 명시

RIM: `V0 = B0 + Σ_t (ROE_t - r) * B_{t-1} / (1+r)^t + 잔여가치`

- `B0`(자기자본)는 리포트 표에 직접 안 나온다 → `B_t = net_profit_t / roe_t` 로 역산 (roe 단위 %).
  roe 가 0/NULL 이면 해당 연도 스킵.
- 기본 파라미터(코드 상수, 인자로 override): `r = 0.08`, 지속계수 `omega = 0.8`,
  예측기간 = 표에 있는 F연도 전부.
- **이 세 값은 사용자 확정 필요.** 확정 전까지 기본값으로 계산하고 결과에 `params` 를 같이 반환한다.

### 2.6 손 안 대는 것

- `download_naver_research.py` — 수정 없음. `pdf_key`/`dest_path` 만 import 재사용.
  요구사항 커버 재확인: 다운로드 경로 규칙이 R2의 `(종목, 증권사, 날짜)` 키를 전부 파일명에 담고
  있으므로 파싱 전 단계 정보는 파일 경로만으로 충분하다. 다운로더 변경 불필요.
- `build_krx_ohlcv.py` — 수정 없음. `DEFAULT_DB_PATH` 와 `ohlcv` 테이블만 읽는다(read_only).
- 배치 스케줄러(ops/작업스케줄러) — **등록하지 않는다.** 사용자가 "포함하라는 말은 아니다"라고 명시.

---

## 3. 파일 구성 (신규)

```
etl/scripts/report_metrics/__init__.py       공개 API re-export
etl/scripts/report_metrics/models.py         ReportFacts, YearEstimate, RimInputs dataclass
etl/scripts/report_metrics/parse.py          경로 파싱 + PDF 텍스트 → facts/estimates
etl/scripts/report_metrics/metrics.py        upside(), target_revision()
etl/scripts/report_metrics/storage.py        sqlite 연결/스키마/upsert/조회
etl/scripts/report_metrics/rim.py            build_rim_inputs(), rim_value()
etl/scripts/run_report_metrics.py            CLI 진입점 (배치에서 호출 가능)
etl/tests/test_report_metrics_stage0..6.py   단계별 테스트
etl/tests/fixtures/report_pages/*.txt        증권사별 실측 1페이지 텍스트 고정본
```

---

## 4. 단계 (뼈대 → 살). 각 단계는 테스트 먼저 → 구현 → unittest 통과

### Stage 0 — 뼈대

- 산출: 위 7개 모듈 파일, dataclass 필드 확정, 모든 공개 함수 시그니처 확정.
  본문은 최소 동작(스키마 생성은 진짜, 파싱/계산은 빈 결과 반환).
- verify: `test_report_metrics_stage0.py`
  - 모든 공개 심볼 import 가능
  - `ReportFacts` 필드가 `report_facts` 테이블 컬럼과 1:1 (누락 시 실패)
  - `init_db()` 후 두 테이블 + 인덱스 존재
  - `init_db()` 재실행 멱등

### Stage 1 — 경로/헤더 파싱 (살①)

- `parse_path(pdf_path) -> (stock_code, stock_name, broker, report_date, pdf_key)`
- `parse_header(text, report_date) -> dict` : opinion, target_price, price_at_report,
  price_at_report_date, upside_printed, prev_target_printed
- verify: `test_report_metrics_stage1.py`
  - 신한/유진/유안타 3종 fixture 텍스트에서 각 필드 정확 추출
  - NOT RATED (`목표주가 -원`) → `target_price is None`, `parse_status='no_target'`
  - 종목명에 `_` 포함 디렉터리 → rsplit 으로 코드 분리 성공
  - 증권사명에 `_` 가 있는 가상 파일명 → key 가 파일 stem 의 꼬리와 일치 (2.3 방어)
  - `현재주가 (8 월 14 일)` / `(8/7)` 두 형식 모두 → `YYYY-MM-DD` (연도는 report_date 기준,
    12월 리포트의 1월 표기 같은 역전 시 연도 -1 보정)
  - 텍스트 200자 미만 → `unsupported_image_pdf`, 예외 안 던짐

### Stage 2 — R1 상승여력 (살②)

- `upside(target, price) -> float | None`
- `resolve_price_now(stock_code, as_of, db_path) -> int | None` (duckdb read_only)
- verify: `test_report_metrics_stage2.py`
  - 6500/4520 → 0.4380 (소수 4자리)
  - price 0 / None / 음수 → None (ZeroDivisionError 안 남)
  - target None → None
  - 인쇄 상승여력과 계산값 차이 0.01 초과 → `upside_printed_mismatch` True
  - `resolve_price_now`: 없는 종목/없는 날짜 → None

### Stage 3 — R2 목표가 변화율 (살③)

- `storage.upsert_facts()`, `storage.find_previous_report(stock_code, broker, report_date)`
- `metrics.target_revision(cur, prev) -> dict{prev_target, change_pct, direction}`
- verify: `test_report_metrics_stage3.py`
  - 동일 종목·동일 증권사 2건 → change_pct 정확, direction `up|down|flat`
  - **다른 증권사 리포트는 이전으로 잡히지 않는다** (부정 요구 → 차단 테스트)
  - 같은 날짜 리포트는 이전으로 잡히지 않는다 (차단 테스트)
  - 이전 없음 → `direction='new'`, `change_pct=None`
  - 이전 target 이 NULL(NOT RATED) → `change_pct=None`, `direction='new_target'`
  - `prev_target_printed` 와 DB 값 불일치 → `prev_target_mismatch=True`
  - upsert 멱등: 같은 pdf_key 2회 → 1행

### Stage 4 — R4 추정표 파싱 (살④)

- `parse_estimates(text) -> list[YearEstimate]`
- verify: `test_report_metrics_stage4.py`
  - 신한 fixture → 2024/2025/2026F/2027F/2028F 5행, `2026F.is_forecast=True`
  - `(십억원)` 헤더 → 억원 환산 (1172.0 십억 → 11720.0 억)
  - 헤더 단위 없음 → 금액 컬럼 NULL, 행은 유지
  - 천단위 콤마, 괄호 음수 `(3.1)` → -3.1
  - 1페이지에 추정표 없음 → 전 페이지 스캔 후 빈 리스트 (예외 없음)

### Stage 5 — R5 RIM 준비

- `build_rim_inputs(facts, estimates, r=0.08, omega=0.8) -> RimInputs | None`
- `rim_value(inputs) -> dict{equity_value, params, warnings}`
- verify: `test_report_metrics_stage5.py`
  - roe/net_profit 로 B 역산 정확
  - roe=0 또는 NULL 연도 스킵 + warning 남김
  - F연도 0개 → None 반환 (예외 없음)
  - r 변경 시 값이 단조 감소
  - 손계산 케이스 1건과 소수 4자리 일치

### Stage 6 — R3 CLI/배치 호출 가능

- `run_report_metrics.py --since/--until/--stock/--limit/--db-path/--reports-dir`
- `run(paths, ...) -> dict{scanned, parsed, no_target, unsupported, errors}`
- verify: `test_report_metrics_stage6.py`
  - 임시 디렉터리 PDF 목록 → 통계 dict 정확
  - 파싱 실패 1건 있어도 전체 중단 안 함 (`parse_status='parse_error'` 저장 후 계속)
  - 재실행 멱등 (행 수 불변)
  - `--stock 095570` 필터 동작
- **스케줄러 등록·기존 배치 파일 수정 없음.**

---

## 5. 실행/검증 명령

```bash
cd etl
PYTHONPATH=. uv run python -m unittest tests.test_report_metrics_stage0 -v
PYTHONPATH=. uv run python -m unittest discover -s tests -p "test_report_metrics_*" -v
uv run python scripts/run_report_metrics.py --limit 20
```

---

## 5.1 구현 중 확정·변경 사항 (2026-09-10)

- **이미지 PDF는 포기** (사용자 결정). `unsupported_image_pdf` 로 상태만 남기고 OCR 안 함.
  커버리지 100% 목표 아님 — 새로 들어오는 리포트가 잘 파싱되는 게 우선.
- **파싱 단위 = PDF 1개.** `parse.py:parse_report(pdf_path)` 가 다른 파일을 보지 않는다.
  직전 목표가(R2)만 DB 에 먼저 적재된 결과를 조회한다.
- §2.1 변경: `price_now` 는 as_of **이하 최근 거래일** 종가(최대 7일 전까지,
  `metrics.py:PRICE_LOOKBACK_DAYS`). 휴장일 배치에서 전부 None 이 되는 걸 막는다.
- §2.2 보강: `direction` 값 = `up|down|flat|new(직전 없음)|new_target(직전 NOT RATED)|no_target(이번 NOT RATED)`.
- 헤더 양식 보강(표본 실측 근거): `TP 405,000원`(교보), `목표주 가(12M)`(iM, 12 를 목표가로 오인하던 것),
  `종가(2026.08.14)`(iM), `주가(7/1):`(키움), `(26/08/05)` 형식 날짜, 잘못된 월/일은 None.
- §2.4 추정표: 두 방향 모두 지원 — 지표가 행(연도 헤더: 유진·유안타·키움) / 연도가 행(지표 헤더: 신한,
  열별 단위 줄 지원). 같은 연도가 여러 표에 있으면 먼저 나온 표 우선, 뒤 표는 빈 칸만 채움.
  A/E/F 표기 없는 연도는 발간연도 이상을 추정치로 봄. 지배순이익 > 당기순이익 우선, 비지배 제외.
- §2.5 RIM: 자기자본 기준(억원)으로 확정. `rim.py:rim_value(inputs, shares=None)` — 주식수를 주면
  주당가치(원). 기준연도 자본이 없으면 `첫 추정연도 자본 - 그 해 순이익` 으로 근사하고 warning.
  r=0.08, ω=0.8 은 여전히 **사용자 확정 필요**.
- Stage 6: 발간일 오름차순 처리, 같은 parser_version 이미 적재분 스킵(신규분만 파싱), 건별 커밋.
  상승여력·변화율은 **저장하지 않고 실행 시 계산**(과거 리포트가 나중에 백필돼도 값이 낡지 않게).

## 6. 구현 완료 후 재검토 (plan-writing skill §5)

- 요구사항 R1~R6 ↔ 구현 함수 ↔ 테스트 3자 대조표를 다시 채운다.
- 미래에셋(전체의 약 9%)은 이미지 PDF라 목표가 산출 불가 — "PLAN 기준 완료"지 "요구사항 기준
  완료"가 아니다. 완료 보고 시 이 커버리지 숫자를 반드시 같이 낸다.
- RIM 파라미터 3개(r, omega, 예측기간)는 사용자 확정 전 기본값 → 보고 시 명시.

### 6.1 재검토 결과 (2026-09-10)

| # | 구현 | 테스트 | 상태 |
|---|---|---|---|
| R1 | `metrics.py:upside()`, `report_upside()`, `resolve_price_now()` | stage2 (9) | 완료 |
| R2 | `metrics.py:target_revision()`, `storage.py:find_previous_report()` | stage3 (10) | 완료 |
| R3 | `scripts/run_report_metrics.py:run()/main()` — 스케줄러 미등록 | stage6 (6) | 완료 |
| R4 | `parse.py:parse_estimates()` | stage4 (12) | 완료 |
| R5 | `rim.py:build_rim_inputs()`, `rim_value()` | stage5 (7) | 완료(파라미터 미확정) |
| R6 | Stage 0 계약 | stage0 (5) + stage1 헤더 (18) | 완료 |

실제 PDF 120건 무작위 스모크(scratch DB, as_of=2026-09-09):
- 텍스트 PDF 114 / 이미지 6(포기). 목표가 추출 94, 목표가 없음 20(대부분 실제 없음).
- 목표가 있는 94건 중 발간시점 상승여력 계산 가능 86(91%), 현재 상승여력 94(100%).
- RIM 계산 가능 76/114(67%) — 추정표에 순이익·ROE 가 둘 다 있어야 함.
- 스모크에서 잡은 버그(수정·테스트 추가 완료): KRX `date` 가 `YYYYMMDD` 형식, 대신증권 줄 분리,
  `2026F` 를 주가로 오인, `163만원` 단위.
- 남은 미검출: 발간시점 주가 없는 8건(iM 3, 키움 2, 대신·하나·현대차 각 1) — 커버리지 100% 비목표.
