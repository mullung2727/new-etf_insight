# us_oas — TQQQ·OAS 리서치

목적: 명세 전략을 최근 3년치로 돌려 결과만 본다. 통과 판정 없음.

## 실행

`etl/` 에서:

```powershell
cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_tqqq_oas.py
```

## 데이터 출처

- `us_ohlcv.duckdb` `etf_ohlcv`·`etf_dividends` (TQQQ·QQQ 종가·배당)
- `us_macro.duckdb` `fred_obs` (OAS `BAMLH0A0HYM2`)

## 체결 시점

T 이전 OAS 관측값으로 판단 → T 종가에 체결, 수익은 T+1부터 반영.

## 규칙 두 종류

- 명세: 상태 A~E (TQQQ 100/70/40/20/70%), ΔOAS10 기준
- 볼린저: OAS 126일 ±1σ 국면, 위험 회피 시 A안 40%·B안 70%, 그 외 100%

설계: `docs/PLAN_US_MACRO_OAS.md`. 결과: `RESULTS.md` (실행 시 덮어쓰기).
