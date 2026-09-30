# backtest_daily — 일봉 전용 공용 백테스트 모듈

`etl/db/krx_ohlcv.duckdb` 일봉만 다룬다. 분봉은 추후 별도 작업.

## 모듈별 함수

| 모듈 | 함수·상수 |
|---|---|
| `data.py` | `load_px`, `market_dates`, `stock_names`, `DB` |
| `adjust.py` | `adj_returns`, `adj_price`, `corp_action_events`, `ma_n`, `SH_TH`, `PX_TH`, `MATCH` |
| `universe.py` | `listing_flags`, `SPAC_RE` |
| `guards.py` | `limit_up_close`, `limit_up_open`, `liquidity`, `halt_ok`, `jump_ok`, `fin_flags`, `AVAIL`, `FIN_DB` |
| `bench.py` | `size_index`, `cap_bucket`, `entry_cap`, `bench_return`, `CAP_EDGES`, `CAP_LABELS` |
| `exits.py` | `hold_exit`, `trailing_exit` |
| `paths.py` | `event_paths` |
| `stats.py` | `weighted`, `day_key`, `month_key`, `cost_table`, `COSTS`, `day_daily_means`, `day_weighted_mean`, `day_median`, `day_win_rate`, `day_tstat` |
| `validate.py` | `wf_train`, `select_best`, `walk_forward`, `placebo_percentile` |

## 결정

- D2: 상폐·장기정지는 마지막 가격 고정 (`volume_shock_anchor` 의 −100% 방식과 다름)
- D3: 기업행위 보정은 `adj_returns` 방식 하나로 통일 (list_shrs 밴드 방식 폐기)
- D4: 정지 후 재개일 수익 0 처리 + 재개 점프 ±31% 초과면 `jump_ok` 로 표본 제외
- D7: walk-forward 학습은 진입일·청산일 모두 Y0101 미만 강제, 우회 인자 없음

분봉은 추후 별도.
