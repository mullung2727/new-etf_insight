# PLAN — 미국 재무지표 DB (us_financials.sqlite3)

한 줄: SEC EDGAR CompanyFacts로 미국 상장사 재무제표를 받아
`etl/db/us_financials.sqlite3` 에 한국 지표 DB(`financial_indicators.sqlite3`)와
동형으로 쌓는다. ETL 배치 범위고 broker-web 연동·스케줄 등록은 별도 단계.

작성 2026-10-06. 구현 전 설계.

확정됨 (사용자 선택): 소스 SEC EDGAR / 새 DB 파일 / 연간+분기.

## Goal

- 한국 `build_financial_indicators.py`에 대응하는 미국 배치
  `etl/scripts/build_us_financials.py` 신규 작성
- `corps / accounts / indicators` 3테이블 + `v_key_*` 2뷰 적재
- 연간(10-K) + 분기(10-Q, Q4 파생) 기간 지원

## 구현 전 결정할 것

| # | 결정 | 추천 | 이유 |
|---|---|---|---|
| D1 | 대상 기간 | **연간 최근 5년 + 분기 최근 8분기** | broker-web compare가 5기간 기준. CompanyFacts는 1콜에 전 이력이라 기간을 늘려도 호출 수 동일. 조정은 CLI로 |
| D2 | 유니버스 | **Nasdaq + NYSE + CBOE (OTC·거래소 미상 제외)** | 실측 7,715사. OTC 2,540사는 재무 보고가 부실해 제외 |
| D3 | `SEC_USER_AGENT` 연락처 | **`.env` 신규 변수, 실행 전 실연락처 입력** | SEC 정책상 식별 가능한 UA 필수. 없으면 차단될 수 있음. 형식 `new-etf-insight/1.0 (contact: 실제이메일)` |
| D4 | 일일 갱신 | 이번 범위 밖. 초기 적재 후 결정 | 한국 `run_from_filings` 대응물은 submissions 기반 설계가 필요해 별도 단계 |

---

## 0. 확인된 사실 (2026-10-06 실측, SEC 총 8콜)

| 항목 | 결과 |
|---|---|
| 유니버스 | `sec.gov/files/company_tickers_exchange.json`, fields/data 구조, 10,434건 |
| 거래소 분포 | Nasdaq 4,371 / NYSE 3,300 / OTC 2,540 / 미상 179 / CBOE 44 |
| **SEC `frame`은 쓰지 않는다** | SEC는 같은 기간 fact 중 **1건에만** frame을 붙인다. JPM 순이익 CY2024는 `DEF 14A`(2026-04, 보수-성과 공시)가 frame을 가져가고 10-K 2건은 frame 없음 (2026-10-08 P1 self-check 실패로 확인). DEF 14A 보수-성과 공시(2023~ 의무)는 순이익을 5년치 태깅하므로 대부분 회사에서 재현 |
| 기간 매핑 (자체 계산) | fact의 `start`/`end`로 frame을 직접 만든다. 연간 flow: 기간 365±30일, 중간점 연도 → `CY{Y}`. 분기 flow: 91±30일, 중간점 분기 → `CY{Y}Q{q}`. stock(start 없음): 가장 가까운 분기말 ±35일(1월 말·2월 초 결산 대응) → `CY{Y}Q{q}I`. 그 밖 기간(YTD 6·9개월 등)은 매핑 없음 → 자동 제외 |
| 연간 선택 | `form ∈ {10-K, 10-K/A}` + 계산 frame `CY{Y}`(flow) / `CY{Y}Q[1-4]I`(stock, end 최대) |
| 분기 선택 | `form ∈ {10-Q, 10-Q/A}` + 계산 frame `CY{Y}Q{1..3}`(flow) / `CY{Y}Q{q}I`(stock) |
| form 엄격 필터 필요 | JPM `NetIncomeLoss` CY2024에 `DEF 14A` fact 존재 — 10-K/10-Q 외 form은 반드시 제외 |
| 동일 기간 중복 | 후속 10-K 비교열·정정으로 복수 존재 (AAPL FY2024 매출이 FY2025 10-K 비교열에도 있음) → `filed` 최신 채택 |
| `fy`/`fp` 불사용 | 비교열 fact는 `fy=2025`인데 기간은 2024 (AAPL·JPM 실측). 날짜+form만으로 선택 |
| 매출 폴백체인 | `RevenueFromContractWithCustomerExcludingAssessedTax` (AAPL) → `Revenues` (JPM) → `SalesRevenueNet` |
| 은행 영업이익 | JPM `OperatingIncomeLoss` 개념 자체가 없음 → null 허용 |
| NCI 태그명 | `MinorityInterest` (VZ 실측). `NoncontrollingInterest` 개념은 없음 |
| 자본총계 폴백 | `...IncludingPortionAttributableToNoncontrollingInterest` → `StockholdersEquity` |
| known value | AAPL FY2024 매출 `391035000000` (2023-10-01~2024-09-28, 10-K, filed 최신) |
| SEC rate limit | 공식 문서 확인: 사용자당 초당 최대 10회. 배치는 0.12초 간격(약 8/s)으로 여유 |

---

## 1. 범위

**한다**

- `etl/scripts/build_us_financials.py` 신규 — 유니버스, 수집, 추출, Q4 파생, 비율 계산, 적재
- `etl/tests/test_build_us_financials.py` 신규 — 가짜 facts 주입(네트워크 없음)
- `etl/db/us_financials.sqlite3` 신규 (`.gitignore`의 `etl/db/`에 걸림)
- `etl/docs/DB_SCHEMA.md` 갱신 (`dump_db_schema.py`가 `*.sqlite3` 자동 편입)
- `.env`에 `SEC_USER_AGENT` 추가 (D3)

**안 한다**

- ADR·외국기업 20-F/6-K (TSM·ASML·BABA 등) — 10-K/10-Q filers만
- broker-web 미국 재무 UI 연동
- 일일 갱신 스케줄 등록 (D4)
- 분기 TTM 비율 (v1은 연간 비율 + 분기 영업이익률만)
- 주식수·시총 (재무제표 범위 밖. 시총은 `us_ohlcv` 조인으로 별도)

---

## 2. 스키마

한국 `financial_indicators.sqlite3`와 테이블·컬럼명을 맞춘다.
다른 점: `fs_div` 없음(EDGAR는 연결 단일), `reprt_code`는 `FY/Q1..Q4`,
`currency`는 `USD` 고정, 지표 코드는 자체 정의(아래 §3-6).

```sql
corps (
    cik         TEXT PRIMARY KEY,  -- 10자리 제로패딩 ("0000320193")
    ticker      TEXT NOT NULL,
    corp_name   TEXT NOT NULL,
    exchange    TEXT,              -- Nasdaq / NYSE / CBOE
    updated_at  TEXT NOT NULL
)
accounts (
    cik         TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,     -- "2024" (캘린더 연도)
    reprt_code  TEXT NOT NULL,     -- FY / Q1 / Q2 / Q3 / Q4
    sj_div      TEXT,              -- BS / IS
    account_nm  TEXT NOT NULL,     -- 매출액·영업이익·당기순이익·자산총계·부채총계·자본총계·이익잉여금·비지배지분·보통주자본
    amount      REAL,              -- USD
    ticker      TEXT,
    filed_dt    TEXT,              -- 채택 fact의 filed (비교열·정정 추적용)
    currency    TEXT,              -- 'USD'
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (cik, bsns_year, reprt_code, account_nm)
)
indicators (
    cik         TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    idx_code    TEXT NOT NULL,     -- ROE / DEBT_RATIO / REV_GROWTH / NET_MARGIN / OP_MARGIN
    idx_nm      TEXT,
    idx_val     REAL,
    ticker      TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (cik, bsns_year, reprt_code, idx_code)
)
```

뷰 `v_key_indicators`(5지표 피벗), `v_key_accounts`(매출·영업이익·순이익·자산 + 영업이익률) —
한국 DDL과 동형으로 만든다.

---

## 3. 수집 로직 — `build_us_financials.py`

한국 배치가 **corp 묶음 × 카테고리 다중호출**이라면, 미국은 **회사당 1콜**로
전 기간·전 개념을 한 번에 받는다. 멱등 기준은 "적재된 (cik, 기간)"이다.

| 단계 | 함수 | 동작 |
|---|---|---|
| 1 목록 | `load_universe()` | tickers_exchange 수신(24시간 캐시 `db/us_tickers.json`) → D2 필터 → `(cik10, ticker, name, exchange)`. `corps` upsert |
| 2 구간 결정 | `plan_periods()` | D1 범위 후보 생성. 회사별 가용은 facts에 달려 있어 후보만 만들고, 없는 기간은 결측 집계 |
| 3 받기 | `fetch_facts()` | `data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`, UA 헤더 필수, 0.12초 간격. 429·403·5xx는 지수 백오프 3회 재시도 후 실패 cik 기록 |
| 4 추출 | `extract_accounts()` | §0 규칙(start/end 계산 frame + form, filed 최신)으로 기간별 금액 추출. 개념 폴백체인은 기간마다 독립 적용 |
| 5 Q4 파생 | `derive_q4()` | flow: `Q4 = FY − (Q1+Q2+Q3)`, 3개 모두 있어야. stock: Q4 = FY값. 한국 `deriveQ4List`와 같은 정의 |
| 6 비율 | `compute_ratios()` | ROE = 순이익/평균자본×100 (연간만), 부채비율·매출증가율(YoY)·순이익률 (연간만), 영업이익률 (연간+분기). 한국 `computeRatio`와 동일 정의 |
| 7 적재 | `run()` | 한국 `_process_source`와 동일: skip → chunk 호출 → upsert → chunk commit. `--force`면 skip 무시 |

개념 폴백체인 (전부 §0 실측 기반, 순서대로 첫 적중):

| 한국 행 | us-gaap 체인 |
|---|---|
| 매출액 | RevenueFromContractWithCustomerExcludingAssessedTax → Revenues → SalesRevenueNet |
| 영업이익 | OperatingIncomeLoss (은행 null) |
| 당기순이익 | NetIncomeLoss |
| 자산총계 / 부채총계 | Assets / Liabilities → 없으면 LiabilitiesAndStockholdersEquity − 자본총계 (AMZN은 Liabilities 미태깅, P1 실측) |
| 자본총계 | StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest → StockholdersEquity |
| 이익잉여금 | RetainedEarningsAccumulatedDeficit |
| 비지배지분 | MinorityInterest |
| 보통주자본 | CommonStockValue (결측 허용) |

CLI (한국 스크립트와 같은 모양):

```powershell
# etl/ 에서
.\.venv\Scripts\python.exe scripts/build_us_financials.py --self-check  # AAPL·JPM·VZ known-value
.\.venv\Scripts\python.exe scripts/build_us_financials.py --limit 5     # 소량 적재
.\.venv\Scripts\python.exe scripts/build_us_financials.py               # 전체
```

공통 헬퍼 재사용: `scripts/_bootstrap.py`(cp949 가드·path),
`scripts/wl_sqlite.py`의 `connect_rw`(WAL + chunk commit).

---

## 4. 요구사항 → 테스트

`etl/tests/test_build_us_financials.py`, unittest, 가짜 facts 주입(네트워크 없음).

| 요구사항 | 테스트 |
|---|---|
| 10-K/10-Q만 채택 | T1 같은 frame에 DEF 14A fact이 있어도 10-K 값만 채택 |
| YTD 혼입 금지 | T2 6개월 YTD fact이 있어도 Q2 단독(3개월) 기간만 채택 |
| 개념 폴백 | T3 1순위 개념이 없으면 2순위 값 채택 (AAPL/JPM 패턴) |
| 비교열·정정 반영 | T4 동일 기간 2건 → filed 최신값 채택 |
| Q4 파생 | T5 FY−(Q1+Q2+Q3), 하나라도 없으면 null. Q4 stock = FY stock |
| 은행 null 허용 | T6 OperatingIncomeLoss 전무 → 해당 행 null, 나머지 정상 적재 |
| 여러 번 돌려도 안전 | T7 같은 facts로 두 번 실행 → 행 수 불변 |
| 이어받기는 남은 것만 | T8 적재된 (cik, 기간)은 fetch 안 함 (fetch 호출 수로 검증) |
| 상장 종목만 | T9 OTC·거래소 미상 제외, Nasdaq/NYSE/CBOE 포함 |
| 비율 산식 | T10 fixture 수치로 ROE(평균자본)·부채비율·매출증가율·마진 일치 |
| 10-K/A 정정 | T11 원본보다 filed 최신이면 정정값 채택 |
| 부채 보완 | T14 Liabilities 없으면 LSE−자본총계, 있으면 원값 |
| UA·간격 | T12 fetch 호출에 UA 헤더 포함, 간격 상수 ≥ 0.1초 (코드 상수 검사) |
| 기간 매핑 | T13 SEC frame이 DEF 14A에만 붙어도 10-K 값 채택(JPM 패턴), 9월·1월 결산 연간·분기·잔액이 올바른 CY로 매핑 |

---

## 5. 알려진 한계

- **ADR 제외.** 20-F/6-K filers는 없음. 대상은 10-K/10-Q 제출 미국 본토 기업.
- **비12월 결산사 Q4 라벨.** 캘린더 frame 기준이라 AAPL 같은 9월 결산사는
  "Q4" 자리에 실제 첫 회계분기가 들어감. 연간·Q1~Q3은 캘린더 정합이라
  종목 간 비교에는 문제없음.
- **생존편향.** tickers_exchange는 현재 상장만. 상폐사는 없음 (한국과 동일 조건).
- **정정은 덮어씀.** 최신 filed 채택이라 이력 보존 안 함. `filed_dt` 컬럼으로
  어느 제출본인지 추적만 가능.
- **은행 영업이익 없음.** XBRL 태그 자체가 없어 null.
- **분기 비율은 영업이익률만.** 분기 ROE·성장률은 TTM 정의가 필요해 v1 제외.

---

## 6. 진행 단계

```text
P0 실측 완료 (2026-10-06, SEC 8콜, 저장소·DB 변경 없음)
   - 유니버스 10,434건·거래소 분포, frame+form 선택규칙(→ P1에서 계산 frame으로 교체), 폴백체인,
     NCI 태그명, DEF 14A·비교열·YTD 함정, SEC 10/s 문서 확인
   → verify: 완료. §0·§3 반영
P1 스크립트 + 테스트 — 완료 (2026-10-08)
   - 결과: unittest 15개 통과, self-check PASS, --limit 5 적재 PK 중복 0·뷰 정상
   - 계획과 차이: SEC frame → start/end 계산 frame (§0), 부채총계 LSE 보완 추가 (T14),
     유니버스는 ticker 7,715 → cik 중복 제거 6,068사
   - `etl/scripts/build_us_financials.py`, `etl/tests/test_build_us_financials.py`
   - `.env` SEC_USER_AGENT (D3, 사용자 입력)
   → verify: T1~T14 통과, --self-check (AAPL FY2024 매출 391,035M·JPM 영업이익 null·VZ NCI),
             --limit 5 적재 후 sqlite 행·PK·뷰 검증
P2 전체 초기 적재 (D1 기간, 약 7,700사 × 1콜 ≈ 20분)
   → verify: 종목 수·행 수·기간 커버리지, known-value 대조,
             개념별 결측률 리포트(은행 null은 정상), 무응답 cik 0건 목표
P3 `etl/docs/DB_SCHEMA.md` 스키마 카탈로그 갱신
   → verify: 카탈로그가 실제 DB와 일치
```

단계마다 결과 보고 후 다음 단계로 넘어간다.

## 성공 기준

- `us_financials.sqlite3`에 3테이블 + 2뷰가 §2 스키마대로 적재됨
- D1 기간·D2 유니버스 커버리지 충족, 무응답 cik 리포트됨
- AAPL known-value 일치, T1~T14 전부 통과
- 스키마 카탈로그가 실제 DB와 일치

## Sources

- <https://www.sec.gov/developer> — Fair Access, 초당 최대 10회 (본문 확인)
- <https://www.sec.gov/files/company_tickers_exchange.json> — 유니버스 실측
- <https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json> — AAPL 실측
- <https://data.sec.gov/api/xbrl/companyfacts/CIK0000019617.json> — JPM 실측
- <https://data.sec.gov/api/xbrl/companyfacts/CIK0000732712.json> — VZ 실측
