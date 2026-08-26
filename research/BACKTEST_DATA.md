# 백테스트 데이터 지침

주제와 무관하게 이 저장소에서 백테스트를 돌릴 때 쓰는 데이터·도구 정리.
전략별 결과는 각 `research/<주제>/README.md` 참조.

## 1. 일봉 — `etl/db/krx_ohlcv.duckdb`

```
테이블   ohlcv(date, ticker, market, open, high, low, close, volume,
               market_cap, list_shrs, ...)   PK (date, ticker)
         holidays(date)                      자기학습 휴장일
범위     20240801 ~ 최신   (498거래일 / 137만행, 2026-08-26 기준)
출처     KRX OpenAPI (data-dbg.krx.co.kr). pykrx 사용 안 함
```

### 범위 늘리기

```powershell
cd etl
PYTHONPATH=. uv run python -c "
import sys, duckdb; sys.path.insert(0,'scripts')
import _bootstrap
from build_vfs import load_dotenv_paths; load_dotenv_paths()
from build_krx_ohlcv import ensure_ohlcv, load_api_key
con = duckdb.connect('db/krx_ohlcv.duckdb')
print(ensure_ohlcv(con, '20230801', '20240731', load_api_key()))
con.close()"
```

- 거래일당 약 4.5초 (fetch 2.2초 + insert 2.25초). **1년 ≈ 20분, 3년 ≈ 1시간**
- 멱등. 이미 적재된 거래일은 건너뛰고, 확정 휴장일은 재호출하지 않음
- 중간에 끊겨도 다음 실행이 남은 날짜만 채움

### 쿼리 시 반드시 걸어야 하는 것

**(a) 0값 행 제외** — 거래정지 구간이 `open=0 / volume=0` 으로 들어와 있다(전체의 4.1%).

```sql
WHERE volume > 0 AND open > 0 AND close > 0
```

**(b) 거래정지 가드** — (a)로 행을 빼면 종목별 순번이 이어붙어, 이동평균 구간이 달력상
몇 달을 걸치거나 "다음 거래일"이 몇 주 뒤가 된다. 실측 오염률은 이동평균 1.6% / 다음날 0.8%이고,
재개 후 미조정 가격 점프(예: 672 → 3500)가 섞이므로 결과가 손실 쪽으로 치우친다.

시장 전체 거래일에 순번(`ms`)을 붙여 놓고 연속성을 검사한다:

```sql
WITH mkt AS (SELECT DISTINCT date FROM ohlcv),
     m   AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS ms FROM mkt),
     base AS (
       SELECT o.*, m.ms, ROW_NUMBER() OVER (PARTITION BY o.ticker ORDER BY o.date) AS seq
       FROM ohlcv o JOIN m USING (date)
       WHERE o.volume > 0 AND o.open > 0 AND o.close > 0
     ),
     w AS (
       SELECT *, MIN(ms) OVER win AS ma_first_ms, LEAD(ms) OVER p AS ms1
       FROM base
       WINDOW win AS (PARTITION BY ticker ORDER BY seq ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
              p   AS (PARTITION BY ticker ORDER BY seq)
     )
SELECT * FROM w
WHERE ms - ma_first_ms = 19    -- 20일 이동평균 구간이 연속 거래일
  AND ms1 = ms + 1             -- 다음 행이 실제 다음 거래일
```

같은 가드가 `etl/scripts/build_vfs.py` 의 `_VFS_SQL` 에 이미 들어가 있다. 참고 구현으로 쓸 것.

**(c) 가격 미조정** — 액면분할/병합이 반영돼 있지 않다. 위 가드가 정지 구간을 걸러 대부분 잡히지만,
정지 없이 분할된 경우는 남는다. 수익률 이상치(±100% 이상)가 보이면 이 원인을 먼저 의심할 것.

## 2. 분봉 — `etl/db/minute_bars.duckdb`

```
테이블   minute_bars(ticker, scope, timestamp, date, time, OHLCV)  PK (ticker, scope, timestamp)
         minute_fetched(ticker, scope, date)                       PK (ticker, scope, date)
규모     110만 봉 / 496종목 / 3,366 종목·일 (2026-08-26 기준)
출처     키움 ka10080 (주식분봉차트). broker 서버 불필요, REST 직접 호출
```

### 조회 하한이 있다

**ka10080 은 약 13개월치만 준다. 실측 하한 20250801.** 그 이전 날짜는 조회 자체가 불가하므로
과거 구간 백테스트는 일봉으로만 가능하다(§4 참조).

### 쓰는 법

```python
from research.watchlist_expected_return.minute_bar_store import connect, load_bars, missing_dates

with connect() as con:                       # etl/db/minute_bars.duckdb
    bars = load_bars(con, "005930", ["20260601", "20260602"])   # 없는 날짜만 키움에서 채워 적재
```

- **날짜 단위**로 저장하므로 보유일수(horizon)를 바꿔도 이미 받은 날은 재조회하지 않는다
- `minute_fetched` 가 따로 있는 이유: 거래정지로 봉이 0개인 날과 아직 안 받은 날을 구분하기 위함
- 조회 비용을 미리 알고 싶으면 `missing_dates(con, ticker, dates)` 로 확인 (건당 2~5초)
- 반환은 정규장(09:00~15:30)만

구버전 JSON 파일 캐시(`research/watchlist_pullback_strategy/minute_cache/`, 952파일 290MB)는
`minute_bar_store --migrate` 로 이미 DB에 흡수됐다. 신규 코드는 DB만 쓸 것.

## 3. 공용 시뮬레이터

```python
from research.watchlist_expected_return.phase8_minute_pullback_strategy import simulate_minute_exit
outcome = simulate_minute_exit(bars, entry, trading_dates, strategy)
```

- `entry` = `{"entry_price": ..., "entry_timestamp": ...}`. 진입봉 자신은 청산 경로에서 제외된다
- `trading_dates` = `[진입일, 보유1일차, ...]`. `trading_dates[1:days+1]` 봉이 전부 있어야 표본 인정
- `strategy` = `{"kind": "tp_sl", "tp": .05, "sl": .03, "days": 3}` 또는 `{"kind": "fixed_close", "days": 1}`
- 청산 순서: **갭 시가 우선 → 동일 1분봉 TP·SL 동시터치 시 SL → 미달 시 마지막날 종가**

요약은 `phase4_holding_strategy.summarize_outcomes(outcomes, cost_rate)`.

## 4. 일봉 근사 (분봉 없는 과거 구간용)

분봉 하한(20250801) 이전을 재려면 장중 규칙을 일봉으로 근사해야 한다.
`research/vfs_strategy/two_day_pullback.py` 의 `load_daily_proxy` / `run_daily_sweep` 가 참고 구현.

```
진입가 = max(기준가, 진입일 시가 x (1 + 문턱))
         그날 고가가 진입가에 닿으면 체결, 못 닿으면 매수 없음
청산   = 보유 마지막날 종가
TP/SL  = 보유일마다 저가 -> 고가 순으로 검사, 같은 날 양쪽 터치는 SL (보수적)
```

**근사 검증 결과** — 분봉이 있는 구간(20250801~)에서 동일 전략을 두 경로로 재면:

| 문턱 | 분봉 n/평균 | 일봉근사 n/평균 |
|---|---|---|
| 0.5% | 80 / +0.90% | 85 / +1.33% |
| 1.0% | 75 / +0.96% | 81 / +1.16% |
| 2.0% | 57 / +1.68% | 62 / +1.67% |
| 3.0% | 44 / +1.85% | 47 / +1.70% |

평균은 잘 맞고 표본 수는 근사가 조금 많다(분봉의 "문턱 넘는 첫 봉 종가"보다
"문턱 가격 지정가 체결"이 더 자주 성립). 새 전략에서도 겹치는 구간에서 먼저 대조하고 쓸 것.

## 5. 실행 규약

```powershell
# 저장소 루트에서
etl\.venv\Scripts\python.exe -m research.<패키지>.<모듈>

# etl 테스트
cd etl; PYTHONPATH=. uv run python -m unittest tests.test_build_vfs

# research 테스트
etl\.venv\Scripts\python.exe -m unittest research.vfs_strategy.tests.test_two_day_pullback
```

- 맨몸 `python` 금지. `etl/` 안에서는 `uv run python`, 루트에서는 `etl\.venv\Scripts\python.exe`
- 테스트는 unittest (pytest 아님)
- `etl/scripts/` 는 패키지가 아니라 `_bootstrap` import 규약을 쓴다
- 콘솔 한글 깨짐 방지: 스크립트 앞에 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`

## 6. 비용·통계 관례

```
왕복 거래비용   0.6%   실계좌 수수료 + 거래세 + 슬리피지 여유
                       (모의계좌 수수료 0.7% 기준의 1.0% 는 과대)
시간 분할       신호일 중앙값으로 전·후반을 갈라 둘 다 양수인지 확인
t 값            mean / (pstdev / sqrt(n))
대칭 TP/SL 손익분기 승률   w = (비용/폭 + 1) / 2
                ±1% -> 80% | ±2% -> 65% | ±3% -> 60% | ±5% -> 56% | ±7% -> 54% | ±10% -> 53%
```
