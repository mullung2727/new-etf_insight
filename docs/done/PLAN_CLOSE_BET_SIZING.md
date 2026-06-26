# PLAN — 종가배팅 예산 분할 매수 (1주 고정 → 금액 배분)

## 요약

종가배팅을 **1주 고정 매수**에서 **총 500만원 예산 분할**로 바꾼다. score >= 70 종목을
**score 1순위·시총(market_cap) 2순위**로 정렬해 **상위 3개**만 산다. 매수 종목 수에 따라
종목당 예산이 정해지고(1개=300만, 2개=200만, 3개=167만), 매수 직전 broker 현재가로
`qty = floor(예산 ÷ 현재가)` 환산해 시장가 매수한다.

확정 결정(사용자):
- **시총 출처 = krx_ohlcv DuckDB**(`ohlcv.market_cap`, 대상일 이하 최신 거래일). llm_scores엔 없음.
- **수량 = 매수 직전 broker 현재가 floor 환산**. 잔여현금 버림.
- **예산은 선정 시점 N으로 고정.** 현재가 조회 실패·qty=0으로 일부 빠져도 남은 종목 예산 재배분 안 함.

---

## 예산 테이블

| 선정 종목 수 N | 종목당 예산 | 총액 |
| --- | --- | --- |
| 1 | 3,000,000 | 3,000,000 |
| 2 | 2,000,000 | 4,000,000 |
| 3 | 1,666,666 (500만÷3, floor) | 4,999,998 |

N은 `rank_and_cut` 후 실제 선정 수(최대 3). 0이면 주문 없음.

---

## 현재 구조 (`etl/scripts/run_close_bet.py`)

- `load_order_candidates`: `llm_scores` score>=threshold → `ORDER BY score DESC LIMIT max_order_count`,
  이미 `close_bet_orders`에 있는 종목 제외.
- `main` 루프: 후보마다 현재가 조회 → `place_order_via_broker(ticker, qty=args.qty_per_symbol=1)`.
- 시총 정렬·예산 개념 없음.

## 변경 핵심

1. **SQL은 cut 안 함** — score>=threshold 전체를 score DESC로 반환(시총 동점깸을 SQL에서 못 함:
   krx_ohlcv는 별 DuckDB 파일이라 SQLite와 직접 JOIN 불가). LIMIT 제거.
2. **Python에서 시총 붙여 최종 정렬·cut** — `(score DESC, market_cap DESC)` → 상위 3.
3. **예산→수량** — 선정 N으로 예산 결정, 종목별 현재가로 floor 환산.

---

## 신규/변경 함수

| 함수 | 종류 | 역할 |
| --- | --- | --- |
| `fetch_market_caps(krx_db, tickers, date) -> dict[str,int]` | 신규(DuckDB RO) | 각 ticker의 date 이하 최신 거래일 `market_cap`. 없으면 키 부재. |
| `rank_and_cut(candidates, caps, n=3) -> list[dict]` | 신규(순수) | `(score DESC, caps.get(ticker,0) DESC)` 정렬 후 상위 n. |
| `budget_for(n) -> int` | 신규(순수) | {1:3_000_000, 2:2_000_000, 3:1_666_666}. |
| `qty_from_budget(budget, price) -> int` | 신규(순수) | `budget // price`, price<=0이면 0. |
| `load_order_candidates` | 변경 | `LIMIT` 제거 → score>=threshold 전체 score DESC 반환(제외 로직 유지). |
| `main` | 변경 | 후보 로드→시총 조회→rank_and_cut→budget_for→루프서 현재가로 qty 환산. `--qty-per-symbol` 제거. |

> `qty=0`(예산<현재가) → 매수 안 함, `status='skipped'` message `"예산<현재가"`로 기록.

---

## TDD 단계 (각 단계: 테스트 작성→구현→`uv run python -m pytest` 통과 보고→사용자 확인→다음)

### G1. 순수 함수 3종 (DB 무관, 제일 쉬움)
- `test_close_bet.py`에 추가:
  - `budget_for`: 1→3M, 2→2M, 3→1,666,666. (0이나 4 입력은 호출 안 되지만 정의역 밖 처리 명시)
  - `qty_from_budget`: 200만/12,340 → 162; price>budget → 0; price=0 → 0.
  - `rank_and_cut`: score 동점이면 시총 큰 것 우선; cut to 3; 시총 없는 종목은 0 취급(맨뒤).
- 구현 후 통과 보고.

### G2. `fetch_market_caps` (DuckDB)
- 신규 `test_market_caps` (DuckDB 임시파일 seed):
  - ticker별 date 이하 최신 거래일 market_cap 반환.
  - 대상일 이후 행은 무시.
  - DB에 없는 ticker는 결과 dict에 키 없음.
- 구현(`duckdb.connect(read_only=True)`, `DEFAULT_KRX_DB` 경로, run_watchlist_research 패턴 재사용).

### G3. `load_order_candidates` LIMIT 제거
- 기존 `test_max_order_count_cap`(5개 cap) → cut이 Python으로 이동했으니 의미 변경:
  load는 전체 반환, cap 책임은 rank_and_cut로 이전. 테스트 갱신(전체 반환 + score DESC 유지,
  이미주문 제외 유지). `max_order_count` 인자 제거 또는 무력화.

### G4. `main` 통합 + 인자 정리
- `--qty-per-symbol` 제거, 시총 조회·rank_and_cut·budget_for·qty 환산 배선.
- 통합 테스트(`test_close_bet_integration.py` 확장): broker 현재가 mock + place_order 캡처로
  - 1종목 선정 → qty=floor(3M/price)
  - 3종목 선정 → 각 floor(1,666,666/price)
  - 예산<현재가 종목 → skipped, 주문 호출 0회
  - 시총 동점 4종목 → 시총 큰 3개만 주문.
- dry_run 경로 회귀 확인.

### G5. 실측 (dry-run)
- `uv run python scripts/run_close_bet.py --date <영업일> --dry-run true --allow-order-outside-close-window`
  로 선정 3종목·예산·환산수량 로그 확인. 실주문(`--dry-run false`)은 사용자 판단.

---

## 안 건드리는 것

- `confirm_fills` / kt00007 체결확정 — qty만 바뀌고 체결 대조 로직 동일.
- `close_bet_orders` 스키마 — `qty` 컬럼 이미 존재, 값만 1→환산수량.
- 청산 워커(run_close_bet_exit) — 매수 수량 무관, 보유분 전량 청산이라 영향 없음(G4 때 확인).
- broker `/orders`, `/close-bet/positions` — 변경 없음.

## 미확인 / 리스크

1. **시총 데이터 신선도**: krx_ohlcv가 대상일 D-1까지 갭필돼 있어야 동점깸 정확. 누락 시 0 취급 →
   동점 종목이 임의 순서. G2에서 "없으면 0" 폴백 명시(주문 자체는 됨, 정렬만 흔들림).
2. **잔여현금/증거금**: floor 환산이라 예산 미달 매수. 총 사용액 < 500만 보장. 증거금 부족 에러는
   broker `accepted=False`로 기존 failed 처리에 흡수(추가 처리 없음).
3. **고가주 qty=0**: 167만 예산에 200만짜리 종목이면 매수 0. skipped 기록만, 재배분 없음(확정 결정).
</content>
</invoke>
