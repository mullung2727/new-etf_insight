# backtest_daily — 일봉 전용 공용 백테스트 모듈

`etl/db/krx_ohlcv.duckdb` 일봉만 다룬다. 분봉은 추후 별도 작업.

## 모듈별 함수

| 모듈 | 함수·상수 |
|---|---|
| `data.py` | `load_px`, `market_dates`, `stock_names`, `DB` |
| `data_us.py` (미국) | `load_etf_tr`, `load_fred`, `load_index`, `US_OHLCV_DB`, `US_MACRO_DB` |
| `adjust.py` | `adj_returns`, `adj_price`, `corp_action_events`, `ma_n`, `SH_TH`, `PX_TH`, `MATCH` |
| `universe.py` | `listing_flags`, `SPAC_RE` |
| `guards.py` | `limit_up_close`, `limit_up_open`, `liquidity`, `halt_ok`, `jump_ok`, `fin_flags`, `AVAIL`, `FIN_DB` |
| `bench.py` (구방식 — 신규 리서치는 bench_daily) | `size_index`, `cap_bucket`, `entry_cap`, `bench_return`, `CAP_EDGES`, `CAP_LABELS` |
| `bench_daily.py` | `build`, `ensure`, `load`, `bench_id`, `bench_return`, `bench_returns`, `BenchCache`, `BENCH_DB` |
| `exits.py` | `hold_exit`, `trailing_exit` |
| `paths.py` | `event_paths` |
| `stats.py` | `weighted`, `day_key`, `month_key`, `cost_table`, `COSTS`, `day_daily_means`, `day_weighted_mean`, `day_median`, `day_win_rate`, `day_tstat` |
| `validate.py` | `wf_train`, `select_best`, `walk_forward`, `placebo_percentile` |
| `portfolio.py` (시장 무관) | `run` |
| `perf.py` (시장 무관) | `equity`, `cagr`, `vol`, `sharpe`, `max_drawdown`, `calmar`, `yearly`, `drawdowns`, `trades_per_year`, `summary`, `PERIODS` |

## portfolio · perf (비중 전략)

- DB를 읽지 않는다. 수익률표·목표비중표(pandas)만 받는다 → 한국·미국 어느 데이터에도 쓴다
- `portfolio.run`: T일 행 = T 종가에 맞출 목표 비중, 수익은 T+1부터 반영. 목표가 바뀐 날만 매매하고 그 외엔 드리프트 유지
- `portfolio.run(..., rebalance_every=N)`: 목표가 그대로라도 마지막 매매일로부터 N 거래일마다 목표로 재조정, 기본 None=목표 변경일에만 매매
- 비용은 인자(`cost`, 한 방향 체결 금액당)로 넘긴다. `turnover = Σ|목표 − 드리프트 비중|` 라 매도·매수 양쪽이 다 잡힌다 (100/0→70/30 이면 turnover 0.6). 한국 이벤트용 `stats.COSTS` 와 별개
- `perf.max_drawdown` 은 시작 자산 1.0을 고점에 포함한다

## bench_daily

- 12종 `{CAP1..CAP5,MKT}_{ALL,LIQ10}` 일별 동일가중, 전일 시총·전일 거래대금 기준
- 하루 세 조각 `r_cc`·`r_on`·`r_in` 저장, `bench_return` 으로 진입·청산 시점 조합
- `ensure()` 로 `etl/db/bench_daily.duckdb` 갱신 후 `load()` 로 읽기만

## 결정

- D2: 상폐·장기정지는 마지막 가격 고정 (`volume_shock_anchor` 의 −100% 방식과 다름)
- D3: 기업행위 보정은 `adj_returns` 방식 하나로 통일 (list_shrs 밴드 방식 폐기)
- D4: 정지 후 재개일 수익 0 처리 + 재개 점프 ±31% 초과면 `jump_ok` 로 표본 제외
- D7: walk-forward 학습은 진입일·청산일 모두 Y0101 미만 강제, 우회 인자 없음

분봉은 추후 별도.

## 벤치마크 vs 대조군 (D13)
- `bench_daily` 는 **지수**다: 매일 전일 조건으로 종목을 다시 짜 동일가중 수익을 이어붙인다. 전략과 비교할 시장 잣대
- "진입일에 같은 조건 종목을 아무거나 샀다면" 은 벤치가 아니라 **대조 전략** → placebo 로 잰다
- 둘은 여러 날 보유에서 다르다 (CAP2·LIQ10 20일 약 0.24%p). 1일 보유는 거의 같다
- 전략 모양별 호출은 `docs/done/PLAN_BACKTEST_DAILY.md` 2차 API 표 참조
