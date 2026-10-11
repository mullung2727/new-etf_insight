# PLAN — 미국 재무제표 전체 보관 (us_facts)

한 줄: SEC XBRL 재무 원본을 D:에 회사별로 보관하고, 정기보고서(10-K·10-Q 등)의 **모든 계정 값**을
"값이 바뀐 공시만" 버전으로 `etl/db/us_facts.sqlite3` 에 쌓는다. 상장폐지 회사 포함, 매일 04:00 새 공시분만 갱신.
기존 9계정 요약 DB(`us_financials.sqlite3`)와 `build_us_financials.py` 는 폐기한다.

작성 2026-10-11.

## 진행
- [x] 구현·테스트 머지 (PR #70)
- [x] 운영 등록 (스케줄 작업 `daily-us-facts` 04:00, 2026-10-11 등록)
- [ ] 첫 실가동 확인 (날짜)
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

## 배경

- 이전 PLAN(`docs/done/PLAN_US_FINANCIALS.md`)은 한국 `financial_indicators.sqlite3` "동형"을 기준으로 계정 9개만 받았다.
  사용자 목적은 **재무제표 전체 보관**(CCC 등 이후 리서치·백테스트용)이었고, 한국 DB도 재무제표 전체를 저장하지 않는다
  (DART 주요계정 32·지표 66만. 전체 재무제표 `fnlttSinglAcntAll` 은 broker-web 이 실시간 조회만).
- 원본은 SEC가 회사당 전 이력을 이미 준다 — 계정을 고를 이유가 없다.

## 확정 결정

| # | 항목 | 결정 | 누가 |
|---|---|---|---|
| D1 | 보관 대상 | SEC `companyfacts.zip` 전체 — 상장폐지·비상장 포함(생존편향 제거) | 사용자 |
| D2 | 원본 보관 | D:에 회사별 gzip, 갱신 시 덮어씀(파일 안에 전 이력·정정 이력 포함) | 사용자 |
| D3 | D: 구조 | `D:/sec_edgar/companyfacts/CIK##########.json.gz`, `D:/sec_edgar/daily_index/YYYY/QTRn/form.YYYYMMDD.idx.gz` | 사용자(추천 수락) |
| D4 | DB 저장 방식 | 같은 회사·계정·단위·기간에서 공시일 순으로 **값이 바뀔 때만** 행 추가(공시일·공시번호 포함). 백테스트 시점 조회용 | 사용자 |
| D5 | DB 위치 | C: `etl/db/us_facts.sqlite3` (추정 9~11GB, SSD 조회 속도) | 사용자 |
| D6 | 기존 DB | `us_financials.sqlite3`·`build_us_financials.py`·테스트 폐기. 요약표는 필요 시 새 DB 위 뷰로 | 사용자 |
| D7 | 갱신 감지 | SEC 일일 공시 목록(`daily-index/form.YYYYMMDD.idx`, 하루 1콜)에서 정기보고서 낸 CIK만 재수집 | 사용자(추천 수락) |
| D8 | 스케줄 | 매일 04:00 KST. 기존 새벽 작업과 DB·API 공유 없음(검토 완료) | 사용자 |
| D9 | 첫 적재 | `D:/sec_edgar/bulk/companyfacts.zip` 1회 처리(수동). 성공 후 zip 삭제 | 내 판단 |
| D10 | 정기보고서 form | `10-K 10-K/A 10-Q 10-Q/A 10-KT 10-KT/A 10-QT 10-QT/A 20-F 20-F/A 40-F 40-F/A` | 내 판단 |
| D11 | taxonomy | 전부(us-gaap·ifrs-full·dei·srt 등). dei 에 발행주식수·유동시가총액 있음 | 내 판단 |
| D12 | 갱신 겹침 | 매 실행마다 직전 처리 3일치 목록의 CIK도 다시 받음(공시 직후 companyfacts 반영 지연 대비, 재처리는 멱등) | 내 판단 |
| D13 | 요약 뷰 | 이번 범위 밖. 옛 계정 폴백·기간 계산 로직은 `docs/done/PLAN_US_FINANCIALS.md` §0·git 이력에 남음 | 내 판단 |
| D14 | 업종 분류 | 파마프렌치 분류 8종 전부(5·10·12·17·30·38·48·49). 원본 Ken French `Siccodes{n}.zip` | 사용자 |
| D15 | 회사 SIC | SEC `submissions/CIK##########.json` 의 **현재** `sic`·`sicDescription` 1개. 과거 이력 없음(업종 바뀐 회사는 과거 시점 업종이 틀림) | 사용자 |
| D16 | 과거 SIC 이력 | 보류. 사용자가 요청하면 회사·연도별 10-K 헤더(이미 DB 에 있는 accn 으로 주소 생성, 약 11만 콜·최대 10시간)로 연 단위 이력 | 사용자 |
| D17 | 업종 저장 | us_facts DB: `companies.sic`·`sic_desc` 칸 + `ff_industries`·`ff_sic_ranges` 표. 원본 `D:/sec_edgar/submissions/CIK##########.json.gz`, `D:/sec_edgar/ff_industry/Siccodes{n}.zip` | 사용자(추천 수락) |
| D18 | SIC 갱신 | 일일 갱신에서 companyfacts 를 받은 CIK 는 submissions 도 받아 SIC 덮어씀(회사당 +1콜) | 내 판단 |
| D19 | 미매칭 SIC | 범위 없는 업종(5·10·12·38 의 Other)이 있는 분류는 그 업종, 없는 분류(17·30·48·49)는 미분류(NULL). Ken French 파일 그대로 | 내 판단 |

## 실측 (2026-10-10~11)

- `companyfacts.zip` 1.41GB(매일 04:26 GMT 갱신), 회사 파일 20,454개, 압축 해제 19.3GB.
- 정기보고서 값이 있는 회사 17,039개, D4 방식 행 수 **7,150만**(현재 상장사만이면 4,100만).
- 10-K 총자산 제출 회사: 2009 1,361 / 2010 6,175 / 2012 7,956 / 2018 6,455 / 2025 6,072. 2009 이전 거의 없음(소형사 XBRL 의무 2011).
- 일일 공시 목록 게시: 다음날 02:01~02:02 GMT(= KST 11:02). 10-09 하루 10-K/10-Q 15건.
  → 04:00 실행은 미국 D일 공시를 KST D+2 04:00에 반영.
- 값의 공시일 `filed` 는 날짜만(시각 없음). 백테스트는 공시 다음 거래일부터 쓰는 게 안전.

## DB 스키마 (`etl/db/us_facts.sqlite3`)

```sql
companies (
    cik         TEXT PRIMARY KEY,   -- 10자리 0패딩
    entity_name TEXT,
    ticker      TEXT,               -- company_tickers_exchange.json 현재 매핑, 없으면 NULL
    exchange    TEXT,
    is_listed   INTEGER NOT NULL,   -- 현재 매핑에 있으면 1 (상장폐지·비상장은 0)
    raw_updated TEXT,               -- 원본 마지막 수신 시각
    updated_at  TEXT,
    sic         INTEGER,            -- SEC 현재 SIC (D15), 없으면 NULL
    sic_desc    TEXT
)
ff_industries (
    scheme      INTEGER NOT NULL,   -- 5,10,12,17,30,38,48,49
    ind_no      INTEGER NOT NULL,
    ind_short   TEXT NOT NULL,      -- Agric, Food ...
    ind_name    TEXT,
    PRIMARY KEY (scheme, ind_no)
)
ff_sic_ranges (
    scheme      INTEGER NOT NULL,
    ind_no      INTEGER NOT NULL,
    sic_lo      INTEGER NOT NULL,
    sic_hi      INTEGER NOT NULL,
    sic_desc    TEXT,
    PRIMARY KEY (scheme, sic_lo, sic_hi)
)
concepts (
    concept_id  INTEGER PRIMARY KEY,
    taxonomy    TEXT NOT NULL,      -- us-gaap / ifrs-full / dei / ...
    tag         TEXT NOT NULL,
    label       TEXT,
    UNIQUE (taxonomy, tag)
)
facts (
    cik         TEXT NOT NULL,
    concept_id  INTEGER NOT NULL,
    unit        TEXT NOT NULL,      -- USD / shares / USD/shares ...
    start       TEXT NOT NULL,      -- 시점값(재무상태표)은 ''
    end         TEXT NOT NULL,
    filed       TEXT NOT NULL,      -- 이 값이 처음 공시된 날
    val         REAL NOT NULL,
    accn        TEXT NOT NULL,
    form        TEXT NOT NULL,
    fy          INTEGER,
    fp          TEXT,
    PRIMARY KEY (cik, concept_id, unit, start, end, filed)
) WITHOUT ROWID
state (key TEXT PRIMARY KEY, value TEXT)   -- last_index_date
```

- 시점 조회: `filed <= 기준일` 중 `filed` 최대 행 = 그날 알려져 있던 값.
- 기간 라벨(CY2024Q1 등)은 저장 안 함 — start/end 로 계산(옛 PLAN §0 규칙 재사용 가능).

## 처리 규칙

| 단계 | 함수 (`etl/scripts/build_us_facts.py`) | 내용 |
|---|---|---|
| 버전 추출 | `fact_versions(facts_json)` | 정기보고서 form 만, (taxonomy, tag, unit, start, end) 별로 (filed, accn) 오름차순 정렬, 직전 val 과 다를 때만 행. 같은 filed 에 val 이 여럿이면 accn 이 큰 것 1개 |
| 회사 반영 | `replace_company(con, cik, facts_json)` | 그 회사 facts 행 삭제 후 재삽입(한 트랜잭션). 원본이 전 이력이라 멱등 |
| 원본 저장 | `save_raw(cik, bytes)` | `.tmp` 에 gzip 쓰고 `os.replace` (쓰다 죽어도 이전 파일 유지) |
| 첫 적재 | `init_from_zip(zip_path)` | zip 각 파일 → `save_raw` + `replace_company`. 끝나면 `last_index_date` = zip Last-Modified 날짜 − 3일 |
| 목록 파싱 | `parse_daily_index(text)` | form 이 D10 목록인 행의 CIK 집합 |
| 일일 갱신 | `update()` | `last_index_date`+1 ~ 어제까지 날짜별로 목록 수신·D: 저장. 404(또는 S3 AccessDenied 403) 이면: 주말 또는 오늘−4일 이전이면 휴일로 보고 넘어감, 아니면 아직 미게시로 보고 중단(미국 평일 휴일은 며칠 늦게 넘어감). 받은 날짜들 + 그 직전 3일(D12) 저장 목록의 CIK 를 companyfacts 로 재수집 → `save_raw` + `replace_company`. 전부 성공해야 `last_index_date` 전진 |
| 상장 매핑 | `refresh_companies()` | 매 실행 `company_tickers_exchange.json` 으로 ticker·exchange·is_listed 갱신 |
| 스키마 이전 | `ensure_schema()` | 기존 DB 에 `companies.sic`·`sic_desc` 없으면 `ALTER TABLE ADD COLUMN` |
| FF 분류 파싱 | `parse_ff_siccodes(scheme, text)` | `^ *N Short  Name` 줄 = 업종, `NNNN-NNNN 설명` 줄 = 범위 |
| FF 적재 | `load_ff(con, raw_root, get_bytes)` | 8개 zip 수신 → D: 저장 → 그 분류 행 삭제 후 재삽입(멱등). `--load-ff` |
| SIC 적재 | `load_sic(con, raw_root, get_bytes, ciks)` | 각 CIK submissions 수신 → D: 저장 → `companies.sic`·`sic_desc` 갱신, 200개마다 commit. `--load-sic`(companies 전체, 1회 약 1시간). 404 는 건너뜀 |
| SIC 갱신 | `update()` | companyfacts 성공한 CIK 마다 `load_sic` (D18) |
| 업종 조회 | `ff_industry(con, scheme, sic)` | 범위 매칭 → (ind_no, ind_short), 없으면 D19 규칙 |

- SEC 호출: User-Agent `.env SEC_USER_AGENT`, 0.12초 간격(약 8/s), 429·5xx·네트워크 오류 지수 백오프 3회.
- companyfacts 404(XBRL 없는 회사): 건너뜀, 기존 행 유지.
- 실패: 네트워크 3회 실패 등 → 그 실행의 `last_index_date` 전진 안 함(다음 실행이 같은 날짜부터 멱등 재처리), exit 1.
  알림은 러너(`run-us-facts.ps1` catch → `send_report_messages.py --channel batch`)만 보낸다 — 파이썬은 notify 안 함(중복 방지).
- D: 미연결(`RAW_ROOT` 없음) → exit 1.
- cp949 가드: `_bootstrap` import.

## 운영

- `ops/scheduled-tasks/us-facts.xml` + `run-us-facts.ps1`(UTF-8 BOM), 매일 04:00, 한도 1시간. 로그 `etl/logs/us-facts-YYYYMMDD.log`.
- 등록: `Register-ScheduledTask -Xml (Get-Content ops\scheduled-tasks\us-facts.xml -Raw -Encoding UTF8) -TaskName daily-us-facts -TaskPath \new-etf_insight\ -Force`
- `ops/batches/daily-us-facts.md` 배치 문서.
- 월간 백업(`backup_db.py`)에 10GB 추가 → 11-02 백업 로그로 소요 시간 확인(한도 2시간).

## 테스트 (`etl/tests/test_build_us_facts.py`, 네트워크 없음)

| # | 요구 | 케이스 |
|---|---|---|
| T1 | 변한 값만 | 같은 값이 10-K 3번 → 1행(최초 filed). 값 바뀐 10-K/A → 2행 |
| T2 | 같은 날 중복 | 같은 filed, 다른 accn·val → accn 큰 것 1행 |
| T3 | 정기보고서만 | 8-K·S-1·DEF 14A fact 제외 |
| T4 | 시점값 | start 없는 fact → start '' |
| T5 | 멱등 | `replace_company` 2번 → 행 동일 |
| T6 | 원본 원자 저장 | `save_raw` 후 gzip 왕복 일치, `.tmp` 안 남음 |
| T7 | 목록 파싱 | 10-K·10-Q/A·20-F CIK 포함, 8-K·4 제외 |
| T8 | 휴일·미게시 | 지난 날짜 404 → 넘어가고 전진, 어제 404 → 중단·미전진 |
| T9 | 겹침 재수집 | 직전 3일 목록 CIK 도 재수집 대상 |
| T10 | 404 회사 | companyfacts 404 → 기존 행 유지 |
| T11 | 실패 시 미전진 | 수신 실패 → `last_index_date` 그대로, exit 1 |
| T12 | 시점 조회 | 정정 전후 두 행에서 기준일별로 다른 값 |
| T13 | 상장 매핑 | 매핑 있는 CIK is_listed=1, 없는 CIK 0, 중복 CIK 첫 행 |
| T14 | 큰 정수 | SQLite 정수 범위 밖 val → REAL 저장 |
| T15 | FF 파싱 | 업종 줄·범위 줄 분리, 범위 없는 업종(Other)도 업종 행 생성 |
| T16 | 업종 조회 | 범위 안 → 그 업종, 범위 밖 → 12분류는 Other·48분류는 None |
| T17 | SIC 적재 | submissions 응답 → companies.sic 갱신, 404 → 기존값 유지, 원본 D: 저장 |
| T18 | 스키마 이전 | sic 칸 없는 옛 companies 표 → ensure_schema 후 칸 생김, 기존 행 보존 |
| T19 | 갱신 시 SIC | update 에서 companyfacts 받은 CIK 의 submissions 도 호출 |

## 폐기 (같은 PR)

- `etl/scripts/build_us_financials.py`, `etl/tests/test_build_us_financials.py` 삭제.
- `etl/db/us_financials.sqlite3`, `etl/db/us_tickers.json` 삭제(gitignore 영역, 수동).
- `etl/docs/DB_SCHEMA.md` us_financials 절 → us_facts 절로 교체.
- `docs/done/PLAN_US_FINANCIALS.md` 맨 위에 "이 PLAN 의 DB 는 PLAN_US_FACTS 로 대체" 한 줄.

## 한계

- 공시일은 날짜만(접수 시각 없음).
- 20-F·40-F(외국 기업)는 ifrs-full 태그라 us-gaap 과 계정명이 다름.
- 회사마다 같은 개념을 다른 태그로 쓸 수 있음(매출 4종 등) — 조회 쪽 폴백 필요.
- companyfacts 는 SEC가 XBRL 로 받은 값만. 2009 이전·XBRL 미제출 공시는 없음.

## 구현 차이

- 원래 val 정수 그대로 → 실제 SQLite 정수 범위(2^63) 밖은 REAL 저장 (SEC 전체 3건, 회사 단위 오기. 첫 적재가 이 값에서 OverflowError 로 중단됨, T14)
- 원래 업종 없음 → 실제 SIC·파마프렌치 8종 추가(D14~D19) (CCC 업종 비교용, 사용자 요청)
- 원래 첫 적재 수동 실행 → 실제 1회용 스케줄 작업으로 실행 (세션 종료와 무관하게 돌리려고)
- 실측: 첫 적재 회사 20,454·행 7,203만·10.7GB, 약 2시간 40분. SIC 회사 21,455 중 17,708 보유(상장 8,028 중 6,937, 없는 건 대부분 펀드·ETF)
- 원래 목록 없는 날 404 → 실제 SEC Archives 는 403 + S3 `AccessDenied` XML (2026-10-11 수동 실가동에서 주말 날짜로 실패, `http_get` 이 이 403 만 "없음" 처리, 요청과다 403 HTML 은 재시도 유지, T20)
