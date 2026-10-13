# PLAN_ETF_MINUTE — 지수 ETF·추적 ETF 1분봉 수집

한 줄: KRX ETF 일별시세를 `krx_ohlcv.duckdb` 새 테이블 `etf_ohlcv` 에 쌓고, 새벽 분봉 배치 대상에 **추적 ETF 4개 + 코스피200·코스닥150 지수 ETF(레버리지·인버스 포함, TR 제외) 약 60개**를 더한다.

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 — 기존 스케줄(KRX 일봉 08:30, 분봉 02:00) 그대로, ETF 일봉 초기 백필 1회 수동 실행(§4)
- [ ] 첫 실가동 확인 (날짜) — 다음 날 02:00 분봉 결과 json 에 ETF 종목 포함, `minute_fetched` 에 069500 신규 날짜
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 0. 배경

- 분봉 배치(`etl/scripts/backfill_minute_bars.py`, 매일 02:00)는 KRX 일봉 DB `ohlcv` 의 (종목, 날짜)만 받는다.
  `ohlcv` 는 `build_krx_ohlcv.py` 가 KRX OpenAPI `stk_bydd_trd`·`ksq_bydd_trd`(주식)로만 채워 ETF 가 없다.
- 069500·122630 분봉(2025-10-01~2026-09-09)은 종가베팅 오버나이트 리서치의 1회성 백필이었다(`research/private/close_bet_overnight/KOSPI200_ETF_COMPARISON.md`).
- KRX OpenAPI `etp/etf_bydd_trd`(ETF 일별매매정보) 이용신청 완료, 2026-10-12 호출 200·1,172행 확인.
- C: 여유 22GB(2026-10-13). ETF 전체(약 7GB/년)는 외장 SSD 구매 후 별도 결정. 이번엔 작은 범위만.

## 1. 결정

| 항목 | 값 | 누가 정했나 |
|---|---|---|
| 대상 ① | 추적 ETF 4개: 426030·385720·495060·456600 | 사용자 |
| 대상 ② | KRX `IDX_IND_NM` 이 정확히 `코스피 200`, `코스피 200 선물지수`, `코스닥 150`, `F-코스닥150 지수` 인 ETF (2026-10-12 기준 25+16+12+7=60) | 사용자(지수·레버리지·인버스), 지수명 매핑은 내 판단 |
| 제외 | TR(`코스피 200 TR`), 섹터·커버드콜·고배당·동일가중·채권혼합·롱숏 | 사용자(TR), 나머지 내 판단(지수명 정확 일치라 자동 제외) |
| 목록 갱신 | 고정 목록 없음. 매일 ETF 일봉의 지수명으로 선정 → 신규 상장 지수 ETF 자동 편입, 상장폐지는 일봉 행이 없어 자연 종료 | 내 판단 |
| 수집 기간 | 기존 분봉 배치와 같음(최근 12개월, 현재 월 우선 + 과거 미완료 월 1개) | 기존 |
| ETF 일봉 저장 | `etl/db/krx_ohlcv.duckdb` 새 테이블 `etf_ohlcv` (기존 `ohlcv` 는 주식 전용 유지 — 유니버스로 쓰는 코드가 많음) | 내 판단 |
| ETF 일봉 범위 | 1,172개 전체 저장(하루 1콜, 약 수십KB). 분봉 대상만 위 선정 | 내 판단 |

## 2. ETF 일봉 — `etl/scripts/build_krx_ohlcv.py`

- 상수 `ETF_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/etp/etf_bydd_trd"`.
- 테이블:

```sql
CREATE TABLE IF NOT EXISTS etf_ohlcv (
    date VARCHAR, ticker VARCHAR, name VARCHAR, idx_name VARCHAR,
    open INTEGER, high INTEGER, low INTEGER, close INTEGER,
    volume BIGINT, trading_value BIGINT, market_cap BIGINT, list_shrs BIGINT,
    nav DOUBLE, net_asset BIGINT,
    PRIMARY KEY (date, ticker)
)
```
  - 매핑: BAS_DD, ISU_CD, ISU_NM, IDX_IND_NM, TDD_OPNPRC/HGPRC/LWPRC/CLSPRC, ACC_TRDVOL, ACC_TRDVAL, MKTCAP, LIST_SHRS, NAV, INVSTASST_NETASST_TOTAMT.
- 새 함수 `fetch_etf_day(date, key, session)` → 행 리스트. 200인데 `OutBlock_1` 없으면 `RuntimeError` (`fetch_day` 와 같은 규칙).
- 새 함수 `ensure_etf_ohlcv(con, from_date, to_date, key, force=False)`:
  - 대상 날짜 = `ohlcv` 에 행이 있는 날짜(구간 내) − `etf_ohlcv` 에 이미 있는 날짜 (force 면 빼지 않음). 휴장일 판단은 주식 `ohlcv` 에 맡긴다(별도 휴장 로직 없음).
  - 날짜별 실패는 그 날짜만 건너뛰고 `failed_days` 증가(다음 실행 재시도).
  - 반환: `{"missing_days", "fetched_days", "inserted_rows", "failed_days"}`.
- `main()`: `ensure_ohlcv` 다음에 `ensure_etf_ohlcv` 호출. **ETF 쪽 예외가 주식 결과를 실패로 만들지 않게** try/except 로 감싸고 `etf_error=...` 를 출력. 출력 줄 끝에 `etf=fetched Nd/Mrows failed=K` 추가.
- 다른 `ensure_ohlcv` 호출처(`build_watchlist.py:255`, `build_vfs.py:168` 갭필)는 손대지 않는다 — ETF 일봉은 `build_krx_ohlcv.py` main 경로에서만 채운다.

## 3. 분봉 대상 — `etl/scripts/backfill_minute_bars.py`

- 상수:
  - `TRACKED_ETFS = ("426030", "385720", "495060", "456600")`  # build_etf_holdings.ETFS 와 같은 목록 (import 하면 notify·dotenv 부작용이 있어 복사)
  - `ETF_INDEX_NAMES = ("코스피 200", "코스피 200 선물지수", "코스닥 150", "F-코스닥150 지수")`
- `load_month_plan`: 주식 행에 더해 아래 행을 합친다.

```sql
SELECT ticker, date FROM etf_ohlcv
WHERE substr(date,1,6)=? AND (idx_name IN (...) OR ticker IN (...))
```
  - `etf_ohlcv` 테이블이 없으면 ETF 는 건너뛴다(주식 동작 그대로).
- `run`: `base_tickers`(KRX 일봉 이후 날짜 미리받기용)에 `latest_date` 의 선정 ETF 도 더한다(`etf_ohlcv` 의 그 날짜 행 기준).
- 수집·완료표시·실패관리(`minute_fetched`, `minute_backfill_failures`)는 기존 로직 그대로. 키움 `ka10080` 은 ETF 코드도 받는다(069500 기존 수집으로 확인).

## 4. 운영

- 스케줄 변경 없음. KRX 일봉 러너(`ops/scheduled-tasks/run-krx-ohlcv.ps1`)가 `build_krx_ohlcv.py` 를 부르므로 ETF 일봉이 같이 쌓인다.
- 초기 백필 1회(머지 후, 장외 시간): `build_krx_ohlcv.py --from 20251001` — 주식 날짜는 이미 있어 ETF 만 받음(약 260콜).
  그 뒤 02:00 분봉 배치가 현재 월 ETF 를 받고, 이후 밤마다 과거 미완료 월을 하나씩 채운다.
- 용량 예상: 64종목 × 약 25KB/일 ≈ 1.6MB/일, 약 0.4GB/년 + 12개월 소급 약 0.4GB.

## 5. 테스트 (`etl/tests/`)

| 요구사항 | 테스트 |
|---|---|
| ETF 일봉 파싱·저장 | `fetch_etf_day` 가짜 응답 → 행 필드 매핑(idx_name, nav 포함) |
| 응답 이상 | 200인데 `OutBlock_1` 없음 → RuntimeError |
| 주식 날짜만, 이미 받은 날은 재호출 안 함 | `ensure_etf_ohlcv`: ohlcv 3일·etf_ohlcv 1일 → 2일만 fetch |
| ETF 실패가 주식을 안 깸 | main 경로에서 ETF fetch 예외 → 주식 적재·종료코드 정상 (함수 단위로 검증 가능하게 분리) |
| 지수 ETF·추적 ETF만 대상 | `load_month_plan`: etf_ohlcv 에 코스피 200 / 코스피 200 TR / 코스피 200 선물지수 / 섹터 / 추적 ETF 행 → TR·섹터 제외 확인 |
| etf_ohlcv 없을 때 | 기존 주식 계획과 동일 |
| 미리받기 날짜에 ETF 포함 | extra_dates 있을 때 선정 ETF 도 그 날짜 포함 |

## 6. 손 안 대는 것

- 기존 `ohlcv` 테이블·스키마, `stock_names`, 휴장일 로직 — ETF 는 별도 테이블이라 주식 유니버스를 쓰는 코드(리서치·배치) 영향 없음. 확인: `grep -rn "FROM ohlcv"` 결과는 그대로 주식만 본다.
- 분봉 저장 구조(`minute_bar_store`), 스케줄 xml.

## 구현 차이
없음
