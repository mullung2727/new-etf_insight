# PLAN — 미국 일봉 2010년부터 확장 (us_ohlcv 백필)

한 줄: 운영 `etl/db/us_ohlcv.duckdb` 주식 일봉(`ohlcv`)을 2024-01 시작에서 **2010-01 시작**으로 늘린다.
1회성 `--backfill` 로 전 종목을 2010부터 다시 받아 종목별로 통째 교체하고, 기본 시작일을 2010으로 바꿔 이후 분할 재조회도 2010부터 맞춘다.
목적: CCC(현금전환주기) 논문 재현 리서치의 수익률 기간 확보.

작성 2026-10-11.

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [x] 운영 등록 — 해당 없음(기존 `daily-us-ohlcv-macro` 그대로). 백필 1회 실행 (2026-10-11)
- [ ] 첫 실가동 확인 — 백필 다음 날 11:00 일일 배치 정상 (날짜)
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

## 확정 결정

| # | 항목 | 결정 | 누가 |
|---|---|---|---|
| D1 | 위치 | 운영 DB 확장(별도 DB 아님) | 사용자 |
| D2 | 시작일 | 2010-01-01 (us_facts 분기 재무가 2009~ 충분) | 사용자(추천 수락) |
| D3 | 생존편향 | 그대로 둠. yfinance 는 상장폐지 종목을 안 줌 → 결과는 상한으로 읽음 | 사용자 |
| D4 | 백필 방식 | 종목별 [2010, 오늘) 전체 재수신 후 `_replace_split_history` 로 통째 교체(앞만 붙이면 그 사이 분할 조정이 어긋날 수 있음) | 내 판단 |
| D5 | 기본 시작일 | `DEFAULT_FROM_DATE` 20240101 → 20100101. 안 바꾸면 이후 분할 재조회가 2024부터만 교체해 2010~2023 행이 옛 조정가로 남음 | 내 판단 |
| D6 | 주식수·시가총액 | 교체된 종목은 list_shrs 가 비므로 같은 실행의 `ensure_shares` 가 2010부터 다시 받음(기존 로직). 야후 주식수 이력이 짧으면 앞 구간 market_cap 은 NULL — 리서치는 us_facts dei 주식수로 계산 | 내 판단 |
| D7 | 배당 | 반영 안 함(기존과 동일, `auto_adjust=False` Close) | 기존 |
| D8 | 실행 시각 | 일일 배치(11:00 KST, DuckDB 쓰기 잠금) 와 겹치지 않게 저녁~새벽 1회 수동 | 내 판단 |

## 처리 규칙 (`etl/scripts/build_us_ohlcv.py`)

| 단계 | 함수 | 내용 |
|---|---|---|
| 백필 대상 | `backfill_ohlcv(con, universe, from_date, end_date, download, today_ny)` | universe 전 종목(이미 덮인 종목도 건너뛰지 않음 — 첫 거래일이 from_date 와 달라 판정이 애매하고 1회성이라 단순하게). BATCH_SIZE 단위로 [from_date, end_date) 수신 |
| 교체 | 같은 함수 | 종목별 `to_rows` 결과를 `_replace_split_history` 로 원자 교체(응답이 기존 시작일을 못 덮으면 그 종목 실패 처리, 기존 행 보존). 응답 없으면 실패 목록 |
| CLI | `main` | `--backfill` 이면 ETF·일일 수집 대신 `backfill_ohlcv` → `ensure_shares(from_date)` 만. 출력 `backfill tickers=.. replaced=.. failed=.. rows=..` |
| 재실행 | — | 중단 시 다시 실행하면 됨(교체는 멱등, 전 종목 다시 받음) |

## 사용처 영향 (grep `us_ohlcv`)

- `research/backtest_daily/data_us.py`: `etf_ohlcv`·`index_ohlcv` 만 읽음 → 영향 없음.
- `research/private/high52_quiet_breakout/us_quiet_breakout.py`: `ohlcv` 전체로 지표 계산 후 `--from` 으로 신호 필터 → 결과 동일, 실행 느려짐.
- `build_us_macro.py`: `etf_ohlcv` 만.
- 용량 약 6배(약 1GB), 월간 백업 증가.

## 테스트 (`etl/tests/test_build_us_ohlcv.py`, 네트워크 없음)

| # | 요구 | 케이스 |
|---|---|---|
| B1 | 앞 구간 채움 | 2024~ 행만 있는 종목 → 백필 후 2010~ 행, 2024~ 값은 새 응답 값 |
| B3 | 잘린 응답 보존 | 응답 시작일 > 기존 시작일 → 그 종목 기존 행 그대로, failed 에 포함 |
| B4 | 분할 기록 | 백필 응답에 과거 분할 → splits 에 기록 |
| B5 | 기본 시작일 | `DEFAULT_FROM_DATE == "20100101"` |

## 한계

- 생존편향(D3). 티커 변경·재사용 종목은 야후가 현재 티커로 준 구간만.
- 배당 미반영 → 고배당주 수익률 과소.

## 구현 차이

- **삽입 속도**: `_insert_rows` 의 `executemany` 가 4천 행 3.06초(행당 ~1ms)라 첫 백필이 배치 1개에서 8분+ 정체 → 중단 후 DataFrame 등록 + `INSERT ... SELECT`(0.014초)로 교체, 재실행.
- **주식수 UPDATE 속도**: `apply_share_history` 의 `executemany UPDATE` 도 1,500만 행 테이블에서 초당 ~200행(종목당 ~8초, 전체 ~12시간 예상) → 425종목에서 중단, DataFrame + `UPDATE ... FROM` 일괄로 교체. 주식수만 재실행(이미 받은 종목 건너뜀), 5,347종목 13분.
- **백필 실측(2026-10-11)**: 일봉 29배치 × ~70초 ≈ 35분, 누락·실패 0. 결과 15,356,361행 · 5,838종목 · 20100104~20261009, DB 585MB → 1.86GB. 3,443종목은 2010-02 이후 상장(현재 상장 종목만이라 생존편향, D3).
- **주식수 커버리지(D6 확인)**: yfinance 주식수 이력이 2015년 이후라 `list_shrs` 비중 2010~14 0~4%, 2015 19%, 2016~ 76~96%. 빈 응답 172종목. 2015 이전 시가총액은 us_facts dei 주식수로 계산.
