# backtest_minute — 분봉 전용 공용 백테스트 모듈

`etl/db/minute_bars.duckdb` 정규장(09:00~15:30) 1분봉만 다룬다.
전일까지 정보·통계·가드·벤치는 `research.backtest_daily` 를 import 해서 쓴다.

## 모듈별 함수

| 모듈 | 함수·상수 |
|---|---|
| `data.py` | `universe`, `day_bars`, `snapshot`, `coverage`, `MB`, `OPEN_T`, `CLOSE_T`, `FULL_SINCE` |
| `prevday.py` | `attach_prev`, `leak_report` |
| `ticks.py` | `tick_size`, `tick_below`, `upper_limit`, `lower_limit`, `buyable` |
| `fills.py` | `limit_buy_fill`, `daily_buy_fill` |
| `exits.py` | `tp_sl_exit` |
| `bench.py` | `hold_bench` |
| `validate.py` | `wf_monthly`, `walk_forward_monthly` |

## 일봉 모듈에서 가져다 쓰는 것

| 쓰임 | 함수 |
|---|---|
| 일·월 가중·비용표 | `stats.weighted`, `day_key`, `month_key`, `cost_table`, `COSTS` |
| 스팩·우선주·합병사 | `universe.listing_flags` |
| 정지·점프·유동성·재무 | `guards.halt_ok`, `jump_ok`, `liquidity`, `fin_flags` |
| 일봉·시장일 | `data.load_px`, `market_dates` |
| 여러 날 보유 벤치 | `bench_daily.bench_return` |
| 후보 선택·placebo | `validate.select_best`, `placebo_percentile` |

## 기존 분봉 코드와 다른 규약

| 규약 | backtest_minute | 기존 |
|---|---|---|
| M4 지정가 체결 | 장 시작 전 주문(`pre_open=True`): 시가 ≤ 지정가면 시가 체결 / 장중: 저가 ≤ 지정가−1호가면 지정가 체결, (봉, 체결가) 반환 (`fills.limit_buy_fill`·`daily_buy_fill`) | `mclean.simulate`·exp38:60·146 은 저가 ≤ 지정가 (동시호가 구분 없음) |
| M5 하한가 매도 불가 | 청산가 ≤ 하한가면 풀린 봉 종가·다음날 시가 (`exits.tp_sl_exit`) | `mclean.simulate`·`simulate_minute_exit` 없음 |
| M8 분봉 결측 | 표본 유지, 일봉 보수 처리 (`fills.daily_buy_fill`·`tp_sl_exit` daily_*) | `simulate_minute_exit` 은 날짜 하나라도 없으면 None |
