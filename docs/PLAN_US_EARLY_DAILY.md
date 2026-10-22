# PLAN — 미국 일봉 아침 조기 적재 (키움 + CBOE) · 야후 빈 종가 대비

한 줄: 미국 장 마감 직후(KST 06:30, 서머타임 해제 후 07:30) 신호에 필요한 소수 종목만 **키움 usa06012** 로, VIX·VIX3M 은 **CBOE 공식 CSV** 로 받아 적재하고, 기존 11:00 야후 배치는 전체 종목·배당·보충을 맡되 **야후 종가가 빈 날은 덮어쓰지 않는다**.

## 진행

### P1 broker 미국 일봉 엔드포인트
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음 (broker 재기동만)
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P2 아침 조기 적재 배치 `us-early`
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (레지스트리 → 스케줄 작업 06:30)
- [ ] 첫 실가동 확인 (날짜, 06:30·07:30 값 비교 1주)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P3 11:00 야후 배치 — 빈 종가 날 보호
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 — 해당 없음 (기존 us-daily)
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P4 비공개 신호 쪽 연결 (research/private, 별도 기록)
- [ ] 구현·테스트 반영
- [ ] 운영 등록 — 신호 계산 시각을 us-early 뒤로
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 배경 (2026-10-22 실측)

- 야후가 ETF(QQQ·SPY 등)의 10-21 봉을 **거래량은 있고 시가~종가는 NaN** 으로 반환 → `ensure_etf` 가 NaN 봉을 버려 ETF 최신일 10-20, `audit_gaps` 가 "SPY calendar is not current" 로 11·13·15·17시 4회 실패. 개별 주식 10-21 은 정상(597종목).
- 알려진 야후 문제: yfinance #2925 "Last days data missing"(2026-07-27 등록, 08-24 not planned 로 닫힘). SPY·QQQ·^VIX·^VIX3M 포함, 며칠 지나도 안 채워진 날 있음. `repair=True` 는 우리 환경에서 QQQ 10-21 = 719.42 로 틀린 값(실제 725.20), `prepost=True` 1일 조회는 빈 응답.
- 키움 `usa06012`(미국주식 일 차트, `/api/us/chart`) 는 10-13~10-20 QQQ·SPY·TQQQ·IEF·BIL 종가가 야후와 소수점까지 동일(차이 0.000%), 10-21 도 있음(QQQ 725.20). 진행 중인 당일 봉도 마지막 행으로 준다.
- 키움 지수 목록(`usa10102`)은 나스닥·S&P500·다우만 — VIX 없음. 배당 조회 TR 없음(계좌 수익률 TR 만).

## 2. 규칙

| 항목 | 값 | 누가 |
|---|---|---|
| 아침 적재 출처 | 종목 = 키움 usa06012 (`upd_stkpc_tp=1`, `exrt_appl_tp=0`), VIX·VIX3M = CBOE `VIX_History.csv`·`VIX3M_History.csv` | Claude 제안, 사용자 승인(10-22) |
| 아침 적재 범위 | `build_us_ohlcv.ETF_TICKERS` 9종 + 비공개 신호 쪽이 넘기는 개별 종목 목록(20개 안팎) | 사용자(10-22: ETF만이 아니라 개별 종목도) |
| 아침 적재 시각 | KST 06:30 (서머타임 해제 후 07:30). 첫 1주는 07:30 재조회로 값 확정 시각 실측 | Claude |
| 당일 봉 | 뉴욕 기준 오늘(진행 중) 봉은 버림 — 끝난 날만 | Claude |
| 거래소 구분 | 종목별 `stex_tp` 를 ND→NY→NA 순으로 시도해 응답 있는 것, 성공값을 캐시 | Claude (실측: QQQ·TQQQ·IEF=ND, SPY·BIL=NY) |
| 11:00 야후 | 지금처럼 전 종목·배당·과거 보충. 야후 값이 정상이면 덮어씀(출처 일관), 종가 NaN 날은 기존 값 유지 | 사용자(10-22) |
| 배당 | 야후만 (총수익률 계산은 기존 `load_etf_tr`) | Claude |
| 실패 알림 | 아침 배치가 "직전 미국 거래일" 봉을 못 채운 종목이 있으면 Discord 경고 1회 | Claude |

## 3. 작업

### P1 broker `GET /us/daily?code=&from=` (`broker/routers/quotes.py` 또는 새 라우터 `us.py`)
- `kiwoom.client.request("usa06012", "/api/us/chart", {...})` 로 조회, `stex_tp` ND→NY→NA 시도.
- 반환: `[{date, open, high, low, close, volume}]` (끝난 날만 — 뉴욕 현재 날짜 미만). `cont-yn` 이어받기는 `from` 이 100거래일 넘을 때만.
- 테스트: 거래소 순차 시도·당일 봉 제외·숫자 문자열 → float 변환 (`client.request` mock).

### P2 `etl/scripts/build_us_early.py` + `ops/scheduled-tasks/run-us-early.ps1`
- 종목: `ETF_TICKERS` + 비공개 목록 파일(경로는 env 또는 인자, 없으면 ETF 만).
- broker(:8001) `/us/daily` 로 최근 10거래일 받아 `us_ohlcv.duckdb` `etf_ohlcv`(ETF)·`ohlcv`(개별 종목)에 **upsert** (기존 날짜는 값이 다를 때만 갱신, 로그).
- VIX·VIX3M: CBOE CSV 최근 10행 → `us_macro.duckdb` `index_ohlcv` upsert (`^VIX`, `^VIX3M` 같은 키).
- 완료 판정: 모든 대상이 직전 미국 거래일(SPY 기준 아님 — 뉴욕 달력: 평일 & 키움 QQQ 응답 최신일) 봉을 가졌는지. 빠지면 경고.
- 휴장일(뉴욕) 조용히 종료. `.ps1` 은 ASCII 또는 UTF-8 BOM.
- 테스트: upsert 멱등, 당일 봉 제외, CBOE CSV 파싱, 빠진 종목 경고.

### P3 `etl/scripts/build_us_ohlcv.py` `ensure_etf`
- 지금 가드: 응답 마지막일 < 저장 마지막일이면 실패(잘린 응답 보호). 야후가 마지막 날 종가를 비우면 키움이 넣은 날 때문에 매일 실패.
- 변경: NaN 봉은 응답에서 빠지므로, **저장 쪽에서 응답 마지막일 이후 날짜는 보존**(DELETE 범위를 응답 구간으로 한정) 하고 가드는 "응답 첫날 > 저장 첫날"(앞 구간 잘림)만 유지. 마지막 날 비교는 경고 로그로.
- `audit_gaps` 의 SPY 최신일 비교는 그대로 (키움이 채웠으면 통과).
- 테스트: 저장 10-21(키움) + 야후 응답 10-20까지 → 10-21 보존, 실패 아님 / 야후 응답에 10-21 정상 → 덮어씀.

### P4 비공개 (research/private, 이 문서엔 위치만)
- 확신주 가격 갱신 스크립트가 야후 대신(또는 실패 시) broker `/us/daily` 사용.
- 신호 계산을 us-early 뒤로, 신호 기준일 ≠ 직전 미국 거래일이면 신호 안 냄.

## 4. 단정 금지 확인
- "키움 일봉 = 공식 종가": 6거래일×5종목 일치만 확인. 확정 시각(장 마감 후 몇 분)은 P2 첫 주 06:30 vs 07:30 비교로 실측 전까지 미확정.
- "CBOE CSV 가 아침에 갱신": 미확인. P2 구현 전 06:30 에 받아 최신일 확인.
- broker :8001 을 06:30 에 쓰는 다른 배치 없음 확인 필요(레지스트리 grep).

## 구현 차이
- P1: 원래 "뉴욕 현재 날짜 미만만" → 실제 기본 컷 = 뉴욕 16:30 이후면 내일(`routers/us.py:_cutoff`). KST 06:30 은 뉴욕 전날 17:30 이라 방금 끝난 장을 포함해야 함.
- P2: 확신주 목록은 runner 가 env `US_EARLY_EXTRA_TICKERS` 없으면 `research/private/us_oas/early_tickers.txt`(있을 때) 사용 — 스케줄 작업은 .env 를 안 읽음.
- P3: `build_us_macro.replace_index` 도 같은 문제(전 구간 DELETE)라 같이 고침.
- P1: broker 에 `tzdata` 의존성 추가 — Windows 는 IANA 시간대 DB 가 없어 `ZoneInfo("America/New_York")` 가 실패.
