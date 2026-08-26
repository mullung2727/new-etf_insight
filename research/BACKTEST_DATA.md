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

위 가드는 **20일 같은 짧은 윈도우 전용**이다. 52주(250거래일)처럼 긴 룩백에 그대로 쓰면
정지 하루만 있어도 종목이 통째로 빠진다(실측: 하루 57건 → 2~4건). 긴 룩백은 `ms` RANGE
윈도우 + 커버리지 하한으로 간다:

```sql
MAX(high) OVER (PARTITION BY ticker ORDER BY ms
                RANGE BETWEEN 250 PRECEDING AND 1 PRECEDING) AS prev_hi,
MIN(ms)   OVER (...) AS old_ms, COUNT(*) OVER (...) AS n52
...
WHERE n52 >= 200 AND ms - old_ms >= 240      -- 실거래일 하한 + 달력 길이 하한
```

**(c) 가격 미조정 — 반드시 보정할 것** (구 "의심할 것"에서 격상). 액면분할/병합/무상증자가
반영돼 있지 않다. 전체의 3.1%가 오염돼 있고, **급등한 종목이 무상증자·분할을 자주 해
신고가·모멘텀 모집단에 집중된다.**

```
실측 (전종목 120일 수익률)      보정전      보정후
평균                          +14.16%  →  +10.30%   (중앙값 -0.59%)
  └ 보정대상 3.14% 의 평균     +145.1%  →   +22.2%
52주 신고가 표본의 승률           33.6%  →    45.0%    ← 결론이 뒤집힘
```

`list_shrs` 비율로 보정한다. 밴드 밖일 때만 — 작은 변동(CB 전환·소액 증자)은 실제 희석이라
건드리면 안 된다:

```sql
-- e = 진입행, f = 이후행
f.close * CASE WHEN e.list_shrs>0 AND f.list_shrs>0
                AND (f.list_shrs*1.0/e.list_shrs NOT BETWEEN 0.67 AND 1.5)
               THEN f.list_shrs*1.0/e.list_shrs ELSE 1 END
```

일별 수익률을 쓸 때는 추가로 **가격제한폭 ±30% 로 클립**한다. 그 밖으로 튀는 값은 남아 있는
기업행위 잔재다.

참고 구현: `research/high52_strategy/backtest.py` 의 `_ADJ`.

**(d) 스팩 제외** — `스팩` / `기업인수목적` 이 이름에 들어간 종목. 2,000원 근처에 묶여 이자만큼
우상향하므로 **52주 변동폭이 10% 미만이면서 신고가를 계속 갱신**한다. 거래대금이 0.1~0.9억이라
매매도 불가능하다. 전체 모집단에선 1.7%뿐이지만 저변동·저유동 필터를 쓰는 순간 몰려 들어온다.

```
실측: "저변동 베이스" 밴드의 38%가 스팩. 그것만 보면 승률 74.5% / 초과중앙 +3.81%
```

**(e) 유동성 하한** — 거래대금 10억 미만은 15:19에 거래가 없는 종목이 섞여 일봉 통계를
위로 왜곡한다(VFS에서 탈락 14건의 평균이 +3.58%였다).

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

## 4b. 벤치마크는 사이즈 중립으로

절대수익이나 "전종목 동일가중 평균 대비 초과"로 재면 **대형주를 고르는 전략이 전부 이긴다.**
2025~2026 한국장이 대형주 장세라 시총 구간별 지수 자체가 크게 갈린다:

```
시총구간 지수 누적 (20240801~20260825, 일별 동일가중 연쇄)
  <1천억 +2.3%  |  1~3천억 -3.1%  |  3천~1조 +10.5%  |  1~5조 +43.9%  |  5조+ +48.5%
  전체   +3.6%
```

실측 사례 — 52주 신고가에서 시총이 완벽한 단조로 보였으나 전부 사이즈 베타였다:

```
5조+ 신고가   초과(동일가중 전체) +13.37%  →  초과(동일시총) +3.06%
승률                        57.3%  →     43.0%
동일시총 기준으로는 전 구간 +0.73~+4.65%, 초과중앙 전부 -3.0~-3.7% (차이 없음)
```

**전날 시총**으로 버킷을 배정(룩어헤드 차단)하고 버킷별 일별 동일가중 지수를 만들어 쓴다.
참고 구현: `research/high52_strategy/backtest.py` 의 `size_neutral_index()`.

같은 함정이 변동성·업종에도 있다. 팩터가 나오면 **그 팩터와 상관된 다른 축을 통제하고
다시 재라.** 실측에서 ROE는 통제 후 소멸(저변동 안에서 Q5-Q1 = -0.05%)했고, 부채비율은
살아남았다(저변동 +7.24%).

## 4c. 재무지표 — `etl/db/financial_indicators.sqlite3`

```
테이블   indicators(corp_code, bsns_year, reprt_code, idx_cl_code, idx_code,
                    idx_nm, idx_val, stock_code, stlm_dt)
         corps(corp_code, stock_code, corp_name)     이름 ↔ 티커 매핑에도 쓸 것
출처     DART fnlttCmpnyIndx. 종목당 66지표, 2023 사업연도부터
적재     etl/scripts/build_financial_indicators.py --year YYYY --reprt CODE --source indicators
         11011 연간 / 11012 반기 / 11013 1Q / 11014 3Q
         period당 265 chunk x 4 카테고리 = 1,060 호출, 약 9분. 멱등
한도     DART 일 10,000 호출
```

### point-in-time 매칭 (필수)

API가 **공시일(접수일자)을 주지 않는다.** `stlm_dt`(결산일)만 있으므로 법정 제출기한으로
근사한다. 이걸 안 하면 신호 시점에 존재하지 않던 실적으로 필터하는 룩어헤드가 된다.

```python
lag = np.where(f.reprt_code == "11011", 90, 45)   # 사업보고서 90일, 분기·반기 45일
f["avail"] = (pd.to_datetime(f.stlm_dt) + pd.to_timedelta(lag, "D")).dt.strftime("%Y%m%d").astype(int)
df = pd.merge_asof(df.sort_values("dt"), f.sort_values("avail"),
                   left_on="dt", right_on="avail", by="ticker", direction="backward")
```

`stlm_dt`가 종목별 실제 결산일이라 12월 결산이 아닌 법인(2·6·9월 결산 등 실재)도 자동 처리된다.
`merge_asof` 키는 정수여야 한다(문자열이면 `MergeError`).

### 결측률 (실측, 2025 연간 기준)

```
부채비율 0.0% | ROE 1.0% | 총자산영업이익률 1.2% | 영업이익증가율 1.3%
순이익률 3.6% | 매출액증가율 4.0%
이자보상배율 94.7%   ← 사용 불가
```

## 4d. 텔레그램 원문 — `etl/db/telegram_public.sqlite3`

```
테이블   telegram_posts(channel, post_id, date_kst, text, raw_json)
수집     etl/scripts/collect_telegram_public.py
         --date 로 하루, crawl_range(channel, start, end, max_pages) 로 구간
         t.me/s 미리보기 역방향 페이징. ?before=<min post_id>, 20건/page
         2023-04 까지 소급 가능. 실측 2.03s/page (fetch 지연이 지배, CPU 0.1%)
규모     108,999건 / 279MB (2026-08 기준)
```

채널 성격이 갈린다. **정성 정보가 있는 건 일부뿐이다.**

```
awake_realtimeCheck  23,552  자동 지표덤프 + 신고가/거래량 알림   → 반드시 제외 (아래)
butler_works         17,315  증권사 리포트 요약 본문
getfeed              30,803  뉴스 요약
companyreport        20,215  리포트 제목 + URL (중앙 87자)
infomarketopen       12,983  일반 뉴스
corevalue             3,284  애널리스트 코멘트
HS_academy / kimcharger  847  잡담
```

### 함정 1 — `awake_realtimeCheck` 는 채널 통째로 빼라

봇이 만든 종목 카드다. 두 유형 모두 쓸 수 없다.

```
(1) 트리거 알림 13,715건
    ✅ 테스(+14.03%) / ❗️ 52주 신고가 돌파 / 거래대금 222억 ...
    = 신고가·거래량 신호 그 자체. 이걸 "언급"으로 세면 신호가 신호를 예측하는 순환
    트리거 내역: 52주 신고가 돌파 9,397 / 52주 최대 거래량 돌파 1,459

(2) 정보 카드 8,321건
    ✅ 셀트리온(+9.94%) / 📂 키워드 / * 증권사 보고서(최근 1개월) - 19건 ...
    = 자기주식·무상증자·리포트목록·재무지표 덤프. 사람 코멘트가 없어 정성정보 0
```

**정규식으로 유형을 가르려 하지 마라.** `^✅.+\(\s*[+-]?[\d.]+%\)\s*\n+❗️` 는
23,552건 중 10,994건만 잡았다 — **53.3% 누수**. ❗️ 대신 📂 로 이어지는 카드가 통과한다.

누수 상태로 낸 결과는 단조 감소처럼 보이는 가짜 패턴을 만든다:

```
                    누수 상태         채널 제외 후
언급 건수별 초과   0/1~2/3~5/6+      0/1~2/3~5/6+
                +2.22/+0.49/+0.36/-3.56   +1.99/-1.38/+1.64/-2.60
                (깔끔한 단조)              (단조 깨짐)
```
크게 오른 종목일수록 awake 가 여러 번 쏘고 그 종목이 되돌린다. 단조성은 알림 개수가 만든 것이었다.

### 함정 2 — 종목명 문자열 매칭 오탐 22.6%

`text LIKE '%종목명%'` 은 셋을 잘못 잡는다.

```
부분문자열   "테스"  ← 스페이스X 스타십 "테스트" 기사
             "컨텍"  ← 엔비디아 "컨텍스트" 기사
발행 증권사   "DB증권" ← 다른 종목 글의 "[DB증권]이명선 - 투자의견 Buy유지"
지수 구성종목 나열
```

이름 길이 필터로는 못 잡는다(2글자 평균 언급 8.67건 vs 3글자 1.47건으로 편중은 보이지만,
`DB증권`·`현대차증권` 등 3글자 이상 오탐이 남는다). **LLM 에 판정 필드를 시켜라.**

```
is_about_target: 원문이 실제로 그 회사를 다루는가.
                 다른 회사 글의 부수정보로만 등장하면 false
→ 실측 186종목 중 42건(22.6%) 이 false
```

### 함정 3 — 신호일 '이전' 과 '당일' 을 분리하라

당일 언급은 신고가가 났기 때문일 수 있다. 예측력으로 치려면 D-6~D-1 만 쓴다.
실측에서 이전 언급 186 / 당일만 51 로, 섞으면 표본이 1.3배 부풀고 전부 역인과다.

### LLM 특징 추출 호출 규약

`codex exec` 는 `--output-schema` 로 JSON 스키마를 강제할 수 있어 파싱 실패가 없다.

```python
cmd = ["codex.cmd", "-a", "never", "exec", "-m", "gpt-5.6-luna",
       "--sandbox", "read-only", "--skip-git-repo-check",
       "--output-schema", str(SCHEMA), "--output-last-message", str(out), "-"]
```
```
모델      gpt-5.6-luna (5.6 계열 최저가). ~/.codex/models_cache.json 에 slug 목록
실측      186종목 / 8병렬 / 228초. 종목당 9.5s. 구독 인증이라 별도 과금 없음
대안      claude -p --model claude-haiku-4-5 --tools "" 도 되지만 종목당 36s 로 3배 느리다
          `--tools ""` 를 빼면 툴 스키마가 실려 호출당 오버헤드 16K 토큰
```

### 실측 결론 — 언급 빈도·정성 모두 예측력 없음 (재시도 금지)

52주 신고가 신호(10일 보유, 사이즈 중립 초과수익)와 결합한 결과다.

```
빈도  진짜 언급 144  초과 -0.15%  t -0.13  전반 +1.67 / 후반 -1.87  실패
      언급 없음  721  초과 +1.65%  t  1.94  전반 +2.85 / 후반 +0.51  통과
      상관 n_pre vs 초과 -0.034, ch_pre vs 초과 -0.062

정성  material_type / tone / specificity / is_forward / has_number / is_recycled
      전 셀 |t| < 1.3. 분할통과 셀 없음
```

원인은 표본이 아니라 **분산 부재**다. 언급의 실체가 대부분 증권사 리포트 요약이라 다 똑같이 생겼다.

```
specificity=2(숫자 특정)  118/144 = 82%
is_forward=True(기대)     128/144 = 89%
has_number=True           136/144 = 94%
material_type=실적         72/144 = 50%
```

표본을 늘려도 이 구조는 안 바뀐다. 텔레그램을 정성 팩터로 쓰려면 채널 구성부터 바꿔야 한다.

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
