# watchlist 당일 후보 배치 (키움 ka10030)

## 한 줄 요약
KRX OpenAPI가 전일까지만 제공 → 당일(D) 거래량 상위는 키움 `ka10030`으로 15:35 이후 수집, 직전 60거래일 top30(krx_ohlcv DB)에 없던 신규진입 종목을 당일 watchlist 후보로 산출. 기존 KRX 배치(D-1)는 그대로 유지.

---

## 배경

- 기존 `build_watchlist.py`는 krx_ohlcv.duckdb만 사용 → KRX OpenAPI는 **전일까지만** 데이터 제공 → 당일 후보를 당일에 못 뽑음.
- 키움 REST `ka10030`(당일거래량상위요청)으로 당일 거래량 상위 100 조회 가능 — **실호출 검증 완료** (2026-06-11).
- 키움 순위 TR은 전부 당일/전일만 지원, 과거 일자 조회 불가 → 당일 랭킹은 매일 스냅샷 저장 필요.

---

## 검증된 사실 (ka10030 실호출, temp/test_ka10030.py)

| 항목 | 결과 |
|------|------|
| 엔드포인트 | `POST /api/dostk/rkinfo`, api-id=`ka10030` |
| 반환 건수 | 1회 100건, `cont-yn=Y` 페이징 (100건이면 충분) |
| ETF/ETN 제외 | `mang_stk_incls: "16"` 정상 동작 — 1위부터 순수 주식만 |
| `stk_cd` 포맷 | `005930_AL` — **`_AL` suffix 부착됨 → strip 필요** (krx_ohlcv ticker와 join 시) |
| 거래량 오버플로우 | `trde_qty`가 UINT32 (`4294967295` = 2^32-1) 캡 — 초고거래량 ETF에서 발생, 주식만 쓰면 실질 무관 |
| 응답 필드 | `stk_cd`, `stk_nm`, `cur_prc`(부호 포함), `trde_qty`, `trde_amt`(백만원), `flu_rt` 등 |

요청 body (확정):
```json
{
  "mrkt_tp": "000",
  "sort_tp": "1",
  "mang_stk_incls": "16",
  "crd_tp": "0",
  "trde_qty_tp": "0",
  "pric_tp": "0",
  "trde_prica_tp": "0",
  "mrkt_open_tp": "0",
  "stex_tp": "3"
}
```
(전체시장, 거래량 정렬, ETF+ETN 제외, KRX+NXT 통합)

---

## 필터 파라미터 (기존 build_watchlist.py와 동일)

| 파라미터 | 값 | 출처 |
|---------|----|------|
| 신규진입 lookback | **60 거래일** | `LOOKBACK = 60` |
| 일별 거래량 상위 | **top 30** | `N_TOP = 30` |
| 동전주 컷 | **종가 ≤ 500 제외** | `PENNY_MAX = 500` |

신규진입 정의 (`find_new_top` 동일): 당일 top30 집합 − 직전 60거래일 top30 합집합.

---

## 데이터 흐름

```
배치 1 (기존 유지 — 장 마감 후/익일, D-1 기준)
  build_krx_ohlcv.py / build_watchlist.py
    KRX OpenAPI → 전종목 OHLCV + 시총 → krx_ohlcv.duckdb upsert
    (D-1까지의 확정 데이터, 자기치유 갭필)

배치 2 (신규 — 매 거래일 15:35 이후)
  ① 키움 ka10030 호출 (위 body)
       → 당일(D) 거래량 상위 100 수신
       → stk_cd에서 "_AL" strip → 6자리 ticker
       → cur_prc 절대값 ≤ 500 동전주 제외
       → 상위 30 슬라이스
       → 랭킹 스냅샷 테이블에 (date, rank, ticker, trde_qty, cur_prc) upsert
  ② krx_ohlcv.duckdb 쿼리 (API 재호출 없음)
       → 직전 60거래일(D-60 ~ D-1)의 일별 거래량 top30 합집합
  ③ ① top30 − ② 합집합 = 당일 신규진입 후보
       → watchlist.duckdb / watchlist 테이블에 (D, ticker) upsert
```

- 15:35 이유: 코스피/코스닥 정규장 마감 15:30 — 그 전 호출은 최종 거래량 아님.
- ②는 기존 `get_top_trading_data` + `find_new_top` 재사용 가능 (당일 1일치만 키움 데이터로 대체하는 구조).
- D일 데이터는 다음날 KRX 배치(배치 1)가 확정값으로 적재 → 키움 스냅샷과 KRX 확정값 이중화. 후보 산출은 당일 키움 기준.

---

## 미해결 / 결정 필요

- [x] ~~랭킹 스냅샷 저장 위치~~ → watchlist.duckdb 의 `intraday_ranking` 테이블 (STEP 1)
- [ ] 당일 후보의 LLM 스코어 연동 시점 (당일 즉시 vs 익일 확정 후)
- [ ] `_AL` suffix 의미 공식 확인 (NXT 통합 구분자 추정) — 다른 suffix 존재 여부
- [x] ~~키움 토큰 발급 주체~~ → 배치가 직접 발급 (재발급 시 동일 토큰 반환 실측 — broker와 충돌 없음)
- [x] ~~휴장일 처리~~ → 직전 키움 스냅샷과 (ticker, volume) 완전 동일 시 skip (STEP 3)

---

## 구현 단계

### STEP 1 — 키움 랭킹 수집 모듈 ✅ 완료
- `etl/scripts/build_intraday_ranking.py`
  - ka10030 호출 → `_AL` strip → `cur_prc` 절대값 종가 → 동전주 컷 → top30
  - `watchlist.duckdb / intraday_ranking` 테이블 (date, rank, ticker, name, volume, close), PK(date, ticker)
  - 멱등: 같은 날짜 DELETE 후 재삽입 (재실행 시 구성 변동 잔재 방지)
  - 토큰: 루트 .env 앱키로 발급, broker/.token_cache.json 공유 (HTTP 1콜 절약용)
    - **실측 확인(2026-06-11, mockapi)**: 유효 토큰 존재 시 재발급 요청에 **동일 토큰 반환** —
      신규 발급이 구 토큰을 무효화하지 않음. 토큰 충돌 이슈 없음. (real host는 미실측, 동일 추정)
  - 429 rate-limit 백오프 재시도 (mockapi 연속 호출 시 발생 확인)
  - 단독 실행(`--db-path`/`--date`) + 재사용 함수 `run()` (STEP 2가 import)
- 테스트(e2e): `etl/tests/test_intraday_ranking_e2e.py` — 실호출 → 임시 DB 적재 → 30행/6자리 ticker/동전주 컷/rank 연속/volume 내림차순/재실행 멱등·중복 0 검증. **통과**
- 검증: 실 DB 단독 실행 → 30행(rank 1~30), 1위 005930 삼성전자, 한글 종목명 저장 정상(utf-8)

### STEP 2 — 신규진입 후보 산출 ✅ 완료
- `build_intraday_ranking.py`에 통합 (단일 배치 진입점)
  - `fetch_past_top_union`: 직전 60거래일 일별 top30 합집합 (krx_ohlcv, SQL ROW_NUMBER —
    pandas `nlargest`와 동치 보장 위해 `volume DESC, ticker ASC` 동점 처리)
  - `run_candidates`: 당일 스냅샷 top30 − 합집합 → 동전주 컷 → `watchlist` 테이블 upsert
    (날짜별 DELETE 후 재삽입 멱등, 스키마 build_watchlist 와 동일)
  - lookback 60일 미달 시 명시적 에러 (갭필 먼저 실행 안내)
  - CLI: 기본 스냅샷+후보 일괄, `--no-candidates` 로 스냅샷만
- **정합성 수정**: STEP 1의 동전주 컷 제거 — 원본은 `top30 → 신규진입 → 동전주 컷` 순서.
  스냅샷은 순수 거래량 top30 저장, 컷은 후보 단계 적용 (원본과 top30 집합 동치)
- 테스트(e2e): `etl/tests/test_intraday_candidates_e2e.py` **통과**
  - 동치성: 실 krx DB 최신일(20260610) 기준 원본 `compute_watchlist` 결과와 신규 경로 결과
    완전 일치 (3종목, 키움 미사용 시딩으로 격리)
  - 실호출: 키움 스냅샷 → 후보 산출 → top30 포함·합집합 미교차·동전주 컷·멱등 검증
- 검증: 실 DB 전체 배치 실행 — 20260611 후보 5종목 산출·적재

### STEP 3 — 운영 가드 ✅ 완료 (스케줄링은 외부 담당)
- 스케줄링: openclaw cron job(외부 LLM)이 매 거래일 15:35 실행 담당 — 이 레포 범위 아님.
  실행 명령: `cd etl && uv run python scripts/build_intraday_ranking.py`
- 휴장일 가드 (`_is_holiday_stale`):
  - 당일 응답의 (ticker, volume)이 **직전 키움 스냅샷**과 완전 동일 → 휴장일 판정, 저장 skip + 후보 산출 생략
  - 같은 날 재실행은 가드 대상 아님 (직전 '다른' 날짜와만 비교) — 멱등 유지
  - 콜드스타트(직전 스냅샷 없음)는 가드 통과
  - **비교 대상을 krx DB → 직전 키움 스냅샷으로 변경한 이유 (실측 2026-06-11)**:
    키움 trde_qty(stex_tp=3, KRX+NXT 통합)와 krx_ohlcv volume(KRX 단독)은 동일 종목·동일 날에도
    불일치(093370: 키움 30,143,874 vs KRX 32,761,114) → krx 비교는 휴장일에도 안 걸림.
    같은 소스(키움 스냅샷)끼리 비교해야 휴장일에 정확히 일치.
  - 빈 응답(장전 등)은 fetch 단계 에러로 이미 차단
- 테스트(e2e): `etl/tests/test_holiday_guard_e2e.py` — 실 API 데이터 1회 수집 후 rows 주입으로
  결정적 재현 (장중엔 호출 간 거래량 변동으로 2회 실호출 방식 비결정적).
  휴장 skip / 거래량 1건 차이 시 저장 / 콜드스타트 / 같은 날 재실행 비차단 4케이스. **통과**

---

## 진행 상태

- [x] ka10030 스펙 확인 + 실호출 검증 (temp/test_ka10030.py)
- [x] 필터 파라미터 기존 코드와 일치 확인 (60일 / top30 / 500원)
- [x] STEP 1: 키움 랭킹 수집 모듈 (e2e 통과, 실 DB 검증 완료)
- [x] STEP 2: 신규진입 후보 산출 (e2e 동치성+실호출 통과, 실 DB 검증 완료)
- [x] STEP 3: 휴장일 가드 (e2e 4케이스 통과) — 스케줄링은 openclaw cron 외부 담당
