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

## 지연 대응

당일 OAS 추정(nowcast): T 이전 실제 OAS + HYG·IEI 하루 변화 예측. LAG1·NOWCAST·ORACLE 비교.

`etl/` 에서:

```powershell
cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_nowcast.py
```

결과: `RESULTS_NOWCAST.md` (실행 시 덮어쓰기, ORACLE은 실매매 불가 상한).

## ETF 단독 신호

실 OAS 없이 HYG·IEI만으로 수준·Δ10 추정 → 2010-02~ 장기 백테스트 (D9). 계수는 OAS 전 구간으로 한 번 고정, 2010~2023은 표본외. ETF_LEVEL(수준+Δ)·ETF_DELTA(Δ만).

`etl/` 에서:

```powershell
cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_etf_proxy.py
```

결과: `RESULTS_ETF_PROXY.md` (실행 시 덮어쓰기).

## VIX 보정

당일·전날 VIX 로그변화와 전날 HYG를 더한 ETF 신호 변형 비교 (D10). 계수는 OAS 전 구간 고정, 2010~2023은 표본외. 수준 조건 끔(Δ10만).

`etl/` 에서:

```powershell
cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_vix_proxy.py
```

결과: `RESULTS_VIX_PROXY.md` (실행 시 덮어쓰기).

## VIX 비대칭

VIX 로그변화의 오름·내림을 분리한 신호 변형 비교 (D11). 오름은 믿고 내림은 덜 믿는 가설: ASYM_A(오름·내림 계수 분리)·ASYM_B(오름만 반영). 계수는 OAS 전 구간 고정, 수준 조건은 평가 1(실매매 방식)에서만 사용.

`etl/` 에서:

```powershell
cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_vix_asym.py
```

결과: `RESULTS_VIX_ASYM.md` (실행 시 덮어쓰기).

## 200일선 유지

OAS 감축 + QQQ 200일선 아래에선 비중 상향 금지(HOLD, D12). 감축은 OAS, 되돌림은 200일선 위에서만. MA200(아래 0.4·위 1.0)은 추세만 쓴 참고 기준.

`etl/` 에서:

```powershell
cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_hold.py
```

결과: `RESULTS_HOLD.md` (실행 시 덮어쓰기).

## 추세+OAS 격자

200일선 아래 TQQQ 상한(ma_tqqq 0.7/0.4/0.2/0.0) × 방어자산(QQQ/BIL) × OAS(on/off) 16개 전 조합 비교 (D13). 200일선 길이 고정, 값 하나 선택 아님.

`etl/` 에서:

```powershell
cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_trend_grid.py
```

결과: `RESULTS_TREND_GRID.md` (실행 시 덮어쓰기).

설계: `docs/PLAN_US_MACRO_OAS.md`. 결과: `RESULTS.md` (실행 시 덮어쓰기).
