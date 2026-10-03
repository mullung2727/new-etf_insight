# PLAN — 미국 거시 데이터 축적 + TQQQ·OAS 리서치

한 줄: FRED 신용 스프레드·금융여건 지표는 **받은 시각과 함께** `etl/db/us_macro.duckdb` 에, 미국 ETF 가격·배당은 기존 미국 일봉 DB `us_ohlcv.duckdb` 의 별도 테이블에 매일 쌓고,
그 위에서 "신호 → 목표 비중 → 일별 수익" 리서치 틀을 만든 뒤 첫 규칙으로 TQQQ·OAS 전략을 최근 3년치로 돌려 결과만 본다.
주문은 하지 않는다.

작성 2026-10-03. 구현 전 설계. 원 전략 명세는 사용자 제공 "TQQQ 중심 OAS 전략 검증 및 자동매매 명세"(이하 명세).

## 구현 전 결정할 것

| # | 결정 | 추천 | 이유 |
|---|---|---|---|
| D1 | FRED 수집 방식 | **확정: FRED API (키 root `.env` `FRED_API_KEY`)** | 2026-10-03 키 등록·동작 확인. 단 공개일(`realtime_start`)은 과거 3년치가 전부 관측일과 같은 날로 덮여 있어 못 쓴다(§0). 공개 시각은 우리가 받은 시각(`fetched_at`)으로만 측정한다 |
| D2 | ETF 가격 저장 위치 | **확정: `us_ohlcv.duckdb` 안 별도 테이블 `etf_ohlcv`·`etf_dividends`** | 미국 가격을 한 DB로 모은다. 주식 `ohlcv` 에 섞으면 레버리지 ETF가 거래량·급등 스크린을 오염시키므로 테이블은 나눈다(한국 DB도 ETF 없음, KODEX·TIGER 0건 확인). 미국 일봉 DB의 멈춘 작업(P2 검증·매일 갱신)도 이번 범위에 넣는다 |
| D3 | 매일 배치 시각 | **P0 측정 후 확정. 임시안 KST 07:30** | 미국 장 마감(KST 05:00/06:00) 뒤라 당일 ETF 종가는 확정된다. OAS가 몇 시에 올라오는지는 아직 모른다 → P0에서 몇 시간 간격으로 받아 처음 나타난 시각을 기록한다 |
| D4 | 세금 | **확정: 계산 안 함** (사용자 결정 2026-10-03). §7 주의사항에만 기재 | — |
| D5 | 리서치 코드 공개 위치 | **확정: `research/us_oas/` (공개)** | 검증된 유효 전략 아님. 유효 판정 시 `research/private/` 로 이동 |

---

## 0. 확인된 사실 (2026-10-03 실측)

| 항목 | 결과 |
|---|---|
| FRED 하이일드 OAS (`BAMLH0A0HYM2`) | 2023-10-03 ~ 2026-10-01, 795행. 2026년 4월부터 ICE BofA 시리즈는 **3년 롤링**만 제공 |
| ALFRED 과거 시점 기록 | 2026-03-31 시점 기록도 2023-10-03부터 시작(소급 적용됨). 2020·2010 시점은 404 |
| 장기 원본 | ICE·Bloomberg(H0A0)·LSEG 유료뿐 |
| 무료 장기 지표 | `BAA10Y` 1986~, `NFCI` 1971~(주간), `STLFSI4` 1993~(주간). **이번엔 신호로 안 쓴다**(사용자 결정) |
| 3년 OAS 범위 | 최저 2.59%, 최고 4.61%(2025-04-07). **5% 이상 한 번도 없음** → 위기 재진입 단계는 3년치로 검증 불가 |
| ΔOAS10 ≥ +50bp 일수 | 15일 (2024-08 3일, 2025-03 1일, 2025-04 10일, 2026-10 1일) |
| ETF 대체치 | ΔOAS10 을 HYG·IEI(또는 JNK·IEI) 10일 로그수익으로 회귀: R² 0.88~0.89, +50bp 15일 중 14일 포착. HYG 단독 R² 0.53 |
| 기존 미국 일봉 DB | `us_ohlcv.duckdb` 2024-01-02~2026-09-11, 5,774종목, 배당 미반영, ETF는 SPY·QQQ·IWM만(주식 `ohlcv` 에 섞임), 스케줄 없음. `PLAN_US_OHLCV.md` P1 완료·P2 적재됨(검증 기록 없음)·P3 문서 미작성 |
| SPY·QQQ·IWM 사용처 | `build_us_ohlcv.py`·테스트 외 참조 없음(research 0건). 단 `audit_gaps()` 가 **SPY를 거래일 달력**으로 씀 |
| FRED API 공개일 | `realtime_start` 전 구간 조회: 786개 관측일 모두 공개일 = 관측일(지연 0일), 수정 이력 0건. 실제로는 다음 날 공개되므로(토요일 KST 기준 최신이 목요일 10-01) **3년 롤링 전환 때 재작성된 값으로 판단, 사용 불가** |
| 공용 백테스트 모듈 | `research/backtest_daily` 는 KRX 전용(상한가 가드, 비용 0.35%). CAGR·MDD·Sharpe 같은 포트폴리오 지표는 없음 → 이번 틀에서 새로 만든다 |

---

## 1. 범위

**한다**
- `etl/scripts/build_us_macro.py` 신규 — FRED 지표 수집, 받은 시각 기록, 매일 판정 기록
- `etl/tests/test_build_us_macro.py` 신규 — 가짜 fetch 주입, 네트워크 없음
- `etl/scripts/build_us_ohlcv.py` 수정 — ETF 수집 경로 추가(§3.2), SPY·QQQ·IWM을 ETF 테이블로 이동, `audit_gaps` 달력을 `etf_ohlcv` 의 SPY로
- `etl/tests/test_build_us_ohlcv.py` 수정 — ETF 테스트 추가, 기존 T6(SPY·QQQ·IWM 포함)·T12·T15(SPY 달력) 갱신
- 미국 일봉 DB 멈춘 작업 마무리 — `PLAN_US_OHLCV.md` P2 검증 기록, 9/11 이후 이어받기
- `research/us_oas/` 신규 — 비중 백테스트 엔진, 지표, TQQQ·OAS 규칙, ETF 대체치 비교
- 매일 스케줄 등록 — `ops/scheduled-tasks/us-daily.xml` + `run-us-daily.ps1` 하나가 `build_us_ohlcv.py` → `build_us_macro.py` 순서로 실행 (기존 `krx-ohlcv` 와 같은 형태)

**안 한다**
- 주문·실매매 (미국 주문 경로 미정)
- OAS 장기 이력 복원 (미결 할 일 메모리 "중요" 항목으로 보류)
- 장기 대체 지표(BAA10Y 등)를 신호로 사용
- BIL/현금 보조 전략 (명세 8절에 조건 없음. BIL 가격만 받아둠)
- 통과 기준 판정, 무작위 대조, 임계값 민감도 (결과만 보고 전략을 손본 뒤 붙임)
- 텔레그램 알림
- `PLAN_US_OHLCV.md` P3 문서화(`BACKTEST_DATA.md` 미국 절) — 별도로 남김

---

## 2. 데이터

### 2.1 수집 대상

**FRED (9개)** — 목록은 스크립트 상수 `FRED_SERIES` 한 곳. 추가는 한 줄.

| 시리즈 | 뜻 | 주기 | 3년 롤링 |
|---|---|---|---|
| `BAMLH0A0HYM2` | 하이일드 전체 OAS (전략 핵심) | 일 | O |
| `BAMLH0A3HYC` | CCC 이하 OAS | 일 | O |
| `BAMLH0A1HYBB` | BB OAS | 일 | O |
| `BAMLC0A0CM` | 투자등급 전체 OAS | 일 | O |
| `BAMLC0A4CBBB` | BBB OAS | 일 | O |
| `BAMLH0A0HYM2EY` | 하이일드 실효수익률 | 일 | O |
| `NFCI` | 시카고 연준 금융여건지수 (매주 과거값 수정) | 주 | X |
| `STLFSI4` | 세인트루이스 연준 금융스트레스지수 | 주 | X |
| `VIXCLS` | CBOE VIX 종가 (1990~, 사용자 추가 2026-10-04) | 일 | X |

**ETF (9개)** — `build_us_ohlcv.py` 상수 `ETF_TICKERS` (기존 `BENCHMARKS` 대체). 시작일 `ETF_FROM_DATE = "19900101"` — 상장일부터 전부 (사용자 결정: 과거치 최대로. 장기 OAS 대체치 복원에도 씀).

| 티커 | 쓰임 |
|---|---|
| SPY, IWM | 기존 벤치마크 (SPY는 거래일 달력) |
| TQQQ, QQQ | 전략 자산·비교 대상 |
| BIL | 데이터만 (보조 전략 보류) |
| HYG, JNK | 대체치 (하이일드) |
| IEI, IEF | 대체치 (국채 3~7년, 7~10년) |

### 2.2 스키마

**`etl/db/us_ohlcv.duckdb` 추가 테이블** (기존 `ohlcv`·`splits`·`stock_names` 는 그대로. ETF는 `splits` 를 안 씀 — 매번 전 구간 재수신이라 분할 반영이 늘 일관)

```sql
etf_ohlcv (
    date, ticker VARCHAR,                -- 주식 ohlcv 와 같은 규약 (YYYYMMDD 뉴욕 거래일, 야후 심볼)
    open, high, low, close DOUBLE,       -- 분할 반영, 배당 미반영
    volume BIGINT,
    PRIMARY KEY (date, ticker)
)
etf_dividends (
    ticker VARCHAR, date VARCHAR,        -- 배당락일
    amount DOUBLE,                       -- 주당 배당 (분할 반영 기준)
    PRIMARY KEY (ticker, date)
)
```
- 총수익(일) = (close + 그날 배당) / 전일 close − 1.
- 기존 `ohlcv` 의 SPY·QQQ·IWM 행은 ETF 테이블 적재·검증 후 삭제.

**`etl/db/us_macro.duckdb`** (신규)

```sql
-- 받은 값의 이력. 같은 (series_id, date)라도 값이 바뀌면 새 행을 쌓는다.
fred_obs (
    series_id     VARCHAR,
    date          VARCHAR,   -- YYYYMMDD 관측일
    value         DOUBLE,    -- 단위 그대로 (OAS는 %)
    fetched_at    TIMESTAMP, -- 우리가 받은 시각 (UTC)
    PRIMARY KEY (series_id, date, fetched_at)
)
-- 같은 날 다시 받아 값이 같으면 행을 추가하지 않는다 (§3 단계 3)

signal_log (
    run_at        TIMESTAMP,  -- 판정 실행 시각 (UTC)
    rule_id       VARCHAR,    -- 예 'tqqq_oas_v1'
    oas_date      VARCHAR,    -- 판정에 쓴 가장 최근 OAS 관측일
    oas           DOUBLE,
    d_oas10_bp    DOUBLE,
    state         VARCHAR,    -- A~E
    weights_json  VARCHAR,    -- {"TQQQ":0.7,"QQQ":0.3}
    PRIMARY KEY (run_at, rule_id)
)
```

- 최신값 조회 = `(series_id, date)` 별 `fetched_at` 최대 행.
- "그 시점에 보였던 값" 조회 = `fetched_at <= T` 중 최대 행. 초기 3년 적재분은 전부 첫 실행 시각으로 들어가므로 과거 시점 재현은 불가 — 백테스트는 §4.1 가정을 쓴다.

---

## 3. 수집

### 3.1 `build_us_macro.py` (신규)

| 단계 | 함수 | 동작 |
|---|---|---|
| 1 FRED 받기 | `fetch_fred(series_id)` | API `series/observations`, 전 구간. `value == "."`(결측)는 버림 |
| 2 변경분만 적재 | `upsert_fred()` | DB 최신값과 값이 다르거나 새 관측일인 행만 INSERT. 같으면 건너뜀 |
| 3 판정 기록 | `record_signal()` | `research/us_oas` 규칙을 불러 최신 OAS로 상태·비중 계산 → `signal_log` 1행 |
| 4 결과 출력 | — | 시리즈별 새 행 수, 실패 목록. 하나 실패해도 나머지는 적재 |

```powershell
# etl/ 에서
uv run python scripts/build_us_macro.py
```

### 3.2 `build_us_ohlcv.py` ETF 경로 (수정)

기존 주식 흐름(§3 of `PLAN_US_OHLCV.md`)의 함수를 재사용하고, 대상 테이블만 다르게 한다.

| 단계 | 동작 |
|---|---|
| 1 목록 | `load_universe()` 에서 `BENCHMARKS` 추가 제거. ETF는 `ETF_TICKERS` 상수만 |
| 2 받기 | 같은 `fetch_batch()` 로 **매 실행 전 구간**(`ETF_FROM_DATE`~) 재수신. 9종목×30년이라 한 번 호출. 뉴욕 오늘 행 버림 |
| 3 적재 | 티커별 한 트랜잭션: `etf_ohlcv`·`etf_dividends` 해당 티커 DELETE 후 INSERT. 응답이 비었거나 첫 날짜가 저장된 첫 날짜보다 늦으면(잘린 응답) 그 티커는 실패 처리·기존 행 보존 |
| 4 정리 | ETF 적재 후 주식 `ohlcv`·`splits`·`stock_names` 에서 `ETF_TICKERS` 행 삭제 (멱등) |
| 5 달력 | `audit_gaps()` 는 `etf_ohlcv` 의 SPY 거래일을 쓴다 |

---

## 4. 리서치 틀 — `research/us_oas/`

```text
data.py     load_fred(series, as_of=None), load_tr(tickers)   # 총수익 = (close+dividend)/전일 close − 1
engine.py   run(weights: DataFrame, returns, cost=0.001)      # 비중표 → 일별 포트폴리오 수익
metrics.py  summary(), yearly(), worst_drawdowns(n=5), state_days(), rebalance_count()
rules/tqqq_oas.py  states(oas, params) → state·weights 시계열
rules/tqqq_oas_v1.json  임계값 (명세 값 그대로)
proxy.py    ETF 대체치 회귀·신호 일치율
run_tqqq_oas.py  비교 3종(QQQ·TQQQ·OAS 전략) 표 출력
```

확장 지점은 하나다: **규칙은 "날짜별 목표 비중표"만 내면 된다.** 엔진·지표는 그대로 재사용한다.

### 4.1 체결 시점 (확정)
- 미국 거래일 T의 목표 비중 = **T 이전 관측일 중 가장 최근 OAS**(보통 T−1)로 판단, **T 종가에 체결**.
- 수익 반영: T 종가에 바꾼 비중은 T+1 수익부터 적용.
- 이 가정(T−1 값이 T 미국 장 마감 전 공개)은 P0 공개 시각 측정으로 확인한다. 안 맞으면 하루 더 미룬다(T−2 값).

### 4.2 TQQQ·OAS 규칙 (사용자 결정 반영)

판정 순서 (매일, 전일 상태 `prev` 사용):

```text
1. prev == E 이고 (OAS < 4.5% 또는 ΔOAS10 <= −50bp)  → A  (정상 복귀는 E를 거친 뒤에만)
2. OAS >= 8% 또는 (OAS >= 6% 이고 ΔOAS10 <= 0)        → E
3. prev == E                                           → E 유지
4. OAS < 5%:  ΔOAS10 >= 150 → D, >= 100 → C, >= 50 → B, 그 외 → A
5. 그 외 (5% <= OAS, E 조건 아님)                      → prev 유지
```

| 상태 | TQQQ | QQQ |
|---|---|---|
| A 정상 | 100% | 0% |
| B 초기 위험 | 70% | 30% |
| C 강한 위험 | 40% | 60% |
| D 매우 강한 위험 | 20% | 80% |
| E 위기 재진입 | 70% | 30% |

- ΔOAS10 = 최근 OAS − 10 **OAS 관측일** 전 OAS (bp).
- 3년치에선 OAS가 5%를 넘지 않아 2·3·5번 분기는 안 탄다. 단위 테스트로만 확인한다.

### 4.3 비용
- 기본: 매매한 비중 합(한 방향) × 0.1%.

### 4.4 결과 출력 (명세 9.2)
CAGR, MDD, Sharpe(무위험 0 — BIL 차감은 참고 줄), Calmar, 연환산 변동성, 최종 자산, 연도별 수익, 최악 낙폭 5개, 상태별 일수, 평균 TQQQ 비중, 연간 리밸런싱 횟수.
스트레스 구간은 3년 안에 있는 것만: **2024-08, 2025-03~04, 2026-10(진행 중)**. 각 구간에서 신호일·최초 감축일·최대 감축·저점 전후·최대 손실·복귀일.

### 4.5 ETF 대체치 비교
- ΔOAS10 대 HYG·IEI, JNK·IEI 회귀(R², +50bp 신호 일치율) — §0 실측 재현.
- 대체치로 같은 규칙을 돌려 **하루 빠른 판단**(T일 종가 기준 대체치로 T 종가 체결)의 성과 차이를 OAS 기준과 나란히 낸다.
- 회귀 계수를 같은 3년에서 맞추므로 결과는 상한으로 읽는다.

---

## 5. 요구사항 → 테스트

`etl/tests/test_build_us_ohlcv.py`(E*), `etl/tests/test_build_us_macro.py`(T1~T6), `research/us_oas/tests/`(T7~) — unittest, 네트워크 없음.

| 요구사항 | 테스트 |
|---|---|
| ETF는 주식 테이블에 안 섞임 | E1 ETF 적재 → `etf_ohlcv` 에만 행, 주식 `ohlcv` 에 ETF 티커 0행. 종목 목록에 SPY·QQQ·IWM 안 붙음 (기존 T6 갱신) |
| 배당 저장 | E2 `Dividends` 0.5인 날 → `etf_dividends` 1행, 0인 날은 행 없음 |
| ETF 전 구간 교체·잘린 응답 보호 | E3 두 번째 실행에서 가격이 바뀐 응답(분할 가정) → 행이 새 값으로 교체. 첫 날짜가 늦은 잘린 응답 → 그 티커 실패, 기존 행 유지 |
| 달력은 ETF SPY | E4 `audit_gaps` 가 `etf_ohlcv` SPY로 누락 탐지. SPY 비었으면 점검 실패 (기존 T12·T15 갱신) |
| 장중 봉 금지 | E5 뉴욕 오늘 날짜 ETF 행 적재 안 됨 |
| 받은 값 이력 보존 | T1 같은 관측일 값이 바뀌어 다시 받음 → 행 2개, 최신값 조회는 새 값 |
| 변경 없으면 안 쌓음 | T2 같은 값으로 두 번 실행 → 행 수 불변 |
| 그 시점 값 재현 | T3 `as_of` 를 수정 전 시각으로 주면 옛 값 |
| 결측 무시 | T4 FRED `"."` 행 적재 안 됨 |
| 키 없음 | T5 `FRED_API_KEY` 비었으면 조용히 건너뛰지 않고 오류 종료 |
| 부분 실패 | T6 시리즈 하나 예외 → 나머지 적재, 실패 목록 출력 |
| 총수익 | T7 close 100→99, 그날 배당 2 → 수익 +1% |
| 미래값 금지 | T8 T일 비중은 T일 OAS를 안 씀 (T일 OAS를 극단값으로 바꿔도 T일 비중 불변) |
| 비중 적용 시점 | T9 T 종가에 바꾼 비중은 T일 수익에 안 섞임 |
| 비용 | T10 100/0 → 70/30 전환일 비용 = 0.6(매도 0.3 + 매수 0.3) × 0.1% |
| 복귀는 E 뒤에만 | T11 OAS 3.0→3.8%(+80bp) → B (A로 복귀하지 않음) |
| E 진입·유지 | T12 OAS 8.5%·ΔOAS10 +200 → E (D 아님). 다음날 OAS 7% → E 유지 |
| E 뒤 복귀 | T13 E 상태에서 OAS 4.4% → A. ΔOAS10 −60bp → A |
| 5~8% 공백 | T14 prev=C, OAS 5.5%·ΔOAS10 +80 → C 유지 |
| 판정 기록 | T15 `record_signal` → `signal_log` 1행, `oas_date` < 실행일 |
| 지표 | T16 알려진 수익열의 CAGR·MDD·Sharpe 손계산 값과 일치 |

---

## 6. 진행 단계

```text
P1 FRED 수집 + 테스트 T1~T6  ← 3년 롤링이라 가장 먼저
   → verify: 테스트 통과, 초기 적재 행 수(OAS 약 795행), 8개 시리즈 기간
P0 공개 시각 측정 (P1 직후 시작, 1주, 다른 단계와 병행)
   - 수동/임시로 몇 시간 간격 실행 → OAS 관측일별 처음 나타난 fetched_at
   → verify: 공개 시각(KST) 표, D3 확정
P1b 미국 일봉 DB 마무리 + ETF 경로 + 테스트 E1~E5
   - ETF 9개 상장일부터 적재, 주식 9/11 이후 이어받기, 주식 ohlcv 의 SPY·QQQ·IWM 삭제
   → verify: 테스트 통과, `PLAN_US_OHLCV.md` P2 검증 항목 기록, TQQQ·QQQ 종가·배당 샘플 대조
P2 리서치 틀 + 규칙 + 테스트 T7~T14, T16
   → verify: 테스트 통과
P3 결과 실행 (비교 3종 + 스트레스 구간 + 대체치 비교)
   → verify: 결과 표 보고 (통과 판정 없음)
P4 판정 기록 + 매일 스케줄 `us-daily` (T15)
   → verify: 수동 1회 실행 → 주식·ETF·FRED 새 행 + signal_log 1행, 다음날 자동 실행 로그
```

## 7. 알려진 한계
- **3년치에 OAS 5% 이상이 없음** → 위기 재진입·복귀 규칙은 실데이터로 검증 안 됨.
- **표본 2~3건** (2024-08, 2025-04, 2026-10 진행 중) → 결과는 우연과 구분 불가. 참고용.
- **FRED 3년 롤링** → 오늘부터 쌓지 않으면 2023-10 이전처럼 매일 하루씩 사라진다. 초기 적재를 서두를 이유.
- **yfinance 비공식 API** → 가끔 튀는 값. P1 샘플 대조.
- **과거 공개 시점 기록 없음** → 3년 백테스트는 "T−1 값이 T 장 마감 전 공개"를 가정만 한다. 앞으로 쌓이는 fetched_at 으로만 확인 가능.
- **세금 미반영 (주의).** 한국 거주자 해외주식 양도세 22%(연 250만원 공제)가 리밸런싱마다 실현 차익에 붙는다. 상태 전환이 잦을수록 TQQQ·QQQ 단순 보유 대비 세후 성과가 더 깎인다. 결과 표의 수익률은 전부 세전이다.
