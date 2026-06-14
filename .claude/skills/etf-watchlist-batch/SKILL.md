---
name: etf-watchlist-batch
description: Run the KRX watchlist batches — D-1 확정 배치(build_watchlist.py, KRX OpenAPI)와 당일 15:35 배치(build_intraday_ranking.py, 키움 ka10030)로 거래량 신규진입 종목을 watchlist.duckdb에 적재하고, 그 ticker를 읽어 LLM 분석/스코어 입력으로 쓴다. Use whenever you need today's or yesterday's watchlist stocks, to refresh the KRX OHLCV cache, or to feed watchlist종목 into llm_scores.
---

# ETF watchlist batch — run & consume

배치가 거래량 급등(신규진입) 종목을 골라 `watchlist.duckdb / watchlist (date, stock_code)`에 적재한다.
**다른 LLM은 이 watchlist 종목을 입력으로 분석하여 `llm_scores` 테이블에 (date, ticker) 키로 써넣는다.**
즉 watchlist = 분석 대상 종목 목록, llm_scores = 그 분석 결과.

배치는 2개 (같은 필터: 통합 거래량 top30 → 직전 60거래일 신규진입 → 종가>500):

| 배치 | 스크립트 | 대상일 | 소스 | 실행 시점 |
|------|---------|--------|------|----------|
| D-1 확정 | `build_watchlist.py` | 전일까지 | KRX OpenAPI | 아무때나 (KRX는 전일까지만 제공) |
| 당일 | `build_intraday_ranking.py` | 오늘 | 키움 ka10030 | **거래일 15:35 이후** (openclaw cron) |

모든 명령은 `etl/` 기준. 최초 1회 `uv sync` → 이후 `uv run`.

## 1. 배치 실행 (watchlist 생성)

```bash
cd etl
uv sync                                          # 최초 1회 (deps)
uv run python scripts/build_watchlist.py         # 최신 거래일 대상
uv run python scripts/build_watchlist.py --date 20260608   # 특정 거래일
uv run python scripts/build_watchlist.py --force           # 갭필 구간 강제 재적재 후 재계산
```

기본은 **누락 거래일만** 받는다(멱등·빠름). 이미 적재된 날짜를 KRX에서 **다시 받아 덮어쓰려면**
`--force`. OHLCV 캐시만 강제 재적재하려면:

```bash
uv run python scripts/build_krx_ohlcv.py --date 20260608 --force          # 특정일 강제
uv run python scripts/build_krx_ohlcv.py --from 20260601 --to 20260608 --force   # 구간 강제
```

`ensure_ohlcv(con, from, to, key, force=True)` — 함수 직접 호출 시.

하는 일 (순서):
1. 대상 거래일 D 결정 (기본 = pykrx 달력 최신 거래일).
2. `D-100캘린더일 ~ D`(≈68거래일) OHLCV 누락분을 KRX OpenAPI로 자기치유 갭필 → `krx_ohlcv.duckdb`.
3. 통합 거래량 상위 30 → 과거 60거래일 **신규진입** → 종가>500 → `watchlist.duckdb / watchlist` upsert.
4. (현행) `REPORTS_DIR/volume_spike_*.notion.json` 스캔 → `llm_scores` upsert.
   ※ 이 외부 스캔은 **다른 선정기준**이라 watchlist와 종목이 안 겹친다. watchlist 기준 분석으로 전환 시 무시/대체.

> **첫 실행만 수분** — 빈 캐시면 ~68거래일 순차 다운로드(일당 KOSPI+KOSDAQ 2콜, `sleep 0.1`).
> 한 번 채우면 다음부턴 누락 0건 → 즉시. 멱등(재실행 안전).

## 1-B. 당일 배치 (build_intraday_ranking.py — 키움 ka10030)

KRX OpenAPI는 전일까지만 제공 → 당일 후보는 키움 REST로 뽑는다. **거래일 15:35 이후 실행**
(정규장 마감 15:30 전엔 최종 거래량 아님).

```bash
cd etl
uv run python scripts/build_intraday_ranking.py                  # 오늘 스냅샷 + 후보 산출
uv run python scripts/build_intraday_ranking.py --no-candidates  # 스냅샷만
uv run python scripts/build_intraday_ranking.py --date 20260611  # 저장 날짜 지정 (조회는 항상 당일)
```

하는 일 (순서):
1. 키움 ka10030(당일거래량상위, ETF+ETN 제외, KRX+NXT 통합) → 순수 거래량 top30
   → `watchlist.duckdb / intraday_ranking (date, rank, ticker, name, volume, close)` 스냅샷.
2. **휴장일 가드**: 응답 (ticker, volume)이 직전 스냅샷과 완전 동일 → 휴장 판정, 저장·후보 skip.
3. 직전 60거래일 일별 top30 합집합(krx_ohlcv.duckdb) 대비 신규진입 → 종가>500
   → `watchlist` 테이블에 당일 (date, stock_code) upsert. 후보 0건이면 빈 날짜(정상).

전제:
- `krx_ohlcv.duckdb`에 **직전 60거래일** 적재 필수 — 부족하면 명시적 에러. 먼저 `build_watchlist.py`
  (또는 `build_krx_ohlcv.py`) 실행으로 갭필.
- 키움 앱키: 루트 `.env`의 `KIWOOM_APPKEY`/`KIWOON_MOCK_TR_APP_KEY` (+SECRET). `KIWOOM_ENV`
  기본 paper(mockapi — 실데이터 반환). 토큰은 `broker/.token_cache.json` 공유(재발급해도 동일 토큰이라 broker와 충돌 없음).
- 429 rate-limit은 1/2/3초 백오프 재시도 내장.
- 멱등: 같은 날 재실행 시 해당 날짜 DELETE 후 재삽입 (휴장일 가드는 같은 날짜 재실행엔 안 걸림).
- 키움 stk_cd suffix(`005930_AL`)는 strip되어 6자리로 저장. 키움 거래량(KRX+NXT 통합)은
  krx_ohlcv(KRX 단독)와 숫자가 다름 — 직접 비교 금지.
- D일 당일 후보는 다음날 KRX 확정 데이터로 `build_watchlist.py`가 다시 계산 가능(이중화).

e2e 테스트: `uv run python -m unittest tests.test_intraday_ranking_e2e tests.test_intraday_candidates_e2e tests.test_holiday_guard_e2e` (실 API 호출, 네트워크 필요).

상세 설계/실측 기록: `docs/PLAN_WATCHLIST_INTRADAY.md`.

## 2. watchlist 종목 읽기 (분석 입력)

특정 날짜의 분석 대상 종목코드 목록:

```bash
cd etl
uv run python -c "import duckdb; c=duckdb.connect('db/watchlist.duckdb',read_only=True); print([r[0] for r in c.execute(\"SELECT stock_code FROM watchlist WHERE date='20260608' ORDER BY stock_code\").fetchall()])"
```

날짜 목록 확인: `SELECT date, COUNT(*) FROM watchlist GROUP BY date ORDER BY date`.

## 3. 분석 결과 적재 (llm_scores)

`llm_scores` 스키마(16컬럼, PK `(date, ticker)`) — 다른 LLM은 watchlist의 `(date, stock_code)`와
**동일한 date='YYYYMMDD' + ticker(6자리)** 로 써야 watchlist와 조인된다:

```
date, ticker, name, ratio, today_volume, avg5_volume, trading_value,
close, score, category, reason_summary, final_opinion,
evidence_board, evidence_news, evidence_web, sources
```

누락 필드는 NULL 허용. upsert는 `INSERT OR REPLACE`.

## 전제 / 함정

- `.env`(repo 루트): `KRX_API_KEY`(필수), `REPORTS_DIR`, `KRX_DB_PATH`, `WATCHLIST_DB_PATH`.
- **Windows**: 스크립트가 `sys.stdout.reconfigure(utf-8)`로 cp949 print 크래시 방지. 새 print는 ascii 권장.
  grep으로 한글 stdout 보면 "Binary file matches"로 삼켜짐 — 검증은 ascii 컬럼명 사용.
- DuckDB 단일 writer: 배치 실행 중 다른 프로세스가 같은 .duckdb write-connect 불가. 읽기는 `read_only=True`.
- watchlist에 우선주/신주인수권/ETN(예 `0117P0`) 포함될 수 있음(보통주 필터 미적용, 종가>500 컷만).
- 데이터 소스 상세: pykrx=거래일 달력만, OHLCV 본체=KRX OpenAPI(`data-dbg.krx.co.kr`). `build_krx_ohlcv.py` 참조.
