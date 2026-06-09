---
name: etf-watchlist-batch
description: Run the KRX watchlist batch — build the daily 급등(거래량 신규진입) 종목 목록 into watchlist.duckdb, then read those tickers so an LLM can analyze/score them. Use whenever you need today's watchlist stocks, to refresh the KRX OHLCV cache, or to know how to feed watchlist종목 into llm_scores.
---

# ETF watchlist batch — run & consume

배치가 KRX 거래량 급등 종목을 골라 `watchlist.duckdb / watchlist (date, stock_code)`에 적재한다.
**다른 LLM은 이 watchlist 종목을 입력으로 분석하여 `llm_scores` 테이블에 (date, ticker) 키로 써넣는다.**
즉 watchlist = 분석 대상 종목 목록, llm_scores = 그 분석 결과.

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
