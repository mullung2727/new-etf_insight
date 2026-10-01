# PLAN — 리서치 공용 백테스트 모듈 (분봉 전용) `research/backtest_minute/` (2026-10-01)

**요약** — 분봉 리서치(minute_top30 31~59차 등)가 매번 새로 짜거나 private 하네스(`mharness`·`mclean`)에 묶어 쓰던
유니버스·분봉 로드·룩어헤드 차단·지정가 체결·TP/SL 청산·가격제한 판정·장중 벤치·월 단위 검증을 한 패키지로 모은다.
**일봉에서 되는 건 `research/backtest_daily` 를 import 해서 쓰고 다시 만들지 않는다.** 기존 리서치 코드는 고치지 않는다.
완료 기준 = 과거 분봉 리서치 무작위 샘플을 이 모듈로 재구현했을 때 원래 숫자가 나오는 것.

상태: 구현·재현 완료 (2026-10-01). 사용자 요청 "분봉도 작업해야해" · "진행", 수정 지시 "유니버스 자체를 거래량 상위 30종목으로 할 이유가 없음" · "여러날 보유하는건 일봉이랑 동일하게" · "나머지는 추천대로".

> **분봉 적재 실태 (2026-10-01 측정)** — 전 종목 분봉은 **2025-12-01~** (하루 ~2,770종목, ETF 포함). 2025-08~11 은 하루 ~130종목(당시 거래량 상위 위주 수집)뿐 → 전 종목 리서치 기간은 약 10개월.

> **분봉 전용.** 가격 경로 입력은 `etl/db/minute_bars.duckdb` 정규장(09:00~15:30) 1분봉. 전일까지 정보(유니버스·가드·피처)는 일봉 DB 를 쓴다.
> 시간외(16~20시, 2026-09-14~) 봉은 이번 범위 아님.

---

## 1. 왜

- 분봉 공통 코드가 **private 폴더 안 하네스**(`research/private/minute_top30/mharness.py`, `mclean.py`)에 있다 → 공개 리서치가 못 쓰고, 다른 폴더(envelope exp20·21, jev_quant_live, high52 m1520 …)는 또 따로 짰다
- 분봉 함정은 일봉보다 많고, 한 번 고친 게 다음 실험에 안 따라온다
  - 룩어헤드: exp47~49 일봉 조건을 `date=D` 에 붙여 +0.4% → +6% 로 부풀었다 (mclean 이 생긴 이유)
  - 유니버스 오염: D 당일 정보(커버리지·봉수·주식수)로 유니버스를 걸렀다 → exp59 에서 외부 리뷰로 수정
  - 빈자리 채우기, 분봉 결측 종목 제외, 장중 거래정지, 호가 단위, 하한가 매도 불가 — exp58·59 에서 하나씩 발견
- 일봉 공용 모듈로 통계·가드·placebo 는 이미 있다 → 분봉에선 **분봉에만 있는 부분만** 만들면 된다

## 2. 현재 흩어진 구현 (2026-10-01 grep)

| 기능 | 현재 위치 | 비고 |
|---|---|---|
| 유니버스 (전일 거래량 상위 30) | `minute_top30/mharness.py` `_UNIV_SQL`·`build` | D 당일 행 존재·D 주식수 밴드로 거름 → exp59 가 F 정보만 쓰게 다시 짬 |
| 스냅샷 가격 (09:00 시가, 시각별 종가) + pkl 캐시 | `mharness.build`, `SNAPS` | 캐시 경로 `temp/minute_top30.pkl` 고정 |
| 분봉 배열 로드 | `mclean.load` 끝부분 (`(h, lo, c, vol, time)` 튜플, 60봉 미만 버림) | `watchlist_expected_return/minute_bar_store.load_bars` 는 **없는 날짜를 키움에서 받아 적재** (부작용) |
| 전일 피처 붙이기 + 룩어헤드 assert | `mclean._FEAT_SQL`, `load` 규칙 2·3, `leak_report` | 피처 목록은 전략마다 다름 → 붙이는 규칙·검사만 공용 대상 |
| 지정가 체결 + TP/SL/시간 청산 (당일) | `mclean.simulate` | 체결 다음 봉부터 판정, SL 먼저 |
| TP/SL 청산 (다일) | `watchlist_expected_return/phase8_minute_pullback_strategy.simulate_minute_exit` | 봉 시가 갭 먼저, 같은 봉 둘 다 → SL, 날짜 하나라도 없으면 None |
| 매수 불가 (진입가 ≥ 전일종가 +29.5%) | `mharness.buyable`, `mclean.load` (시가 상한가) | |
| 하한가 매도 불가·목표가 상한가 위·매수가 하한가 밑 | `minute_top30/exp58_audit_sizing.py` 감사용 인라인 | 시뮬레이션에 반영돼 있지 않음 |
| 분봉 결측 → 일봉 보수 처리, 장중 정지 → 다음날 시가 청산, 호가 단위 | `minute_top30/exp59_clean_universe.py` 인라인 | |
| 일 가중 통계·월별 양수 | `mharness.day_stats`, `mclean.stats` | backtest_daily `stats` 와 같은 일 |

## 3. 범위

**만든다** — `research/backtest_minute/` (공개 저장소 커밋 대상: 전략 조건·파라미터 없음)

| 파일 | 내용 (출처) |
|---|---|
| `data.py` | `universe(krx_db, since="20251201")` — KOSPI·KOSDAQ 전 종목, **전일(F) 정보만**으로 D 의 후보 확정, D 정보는 시가뿐 (exp59 규칙 1). 거래량 상위 n 같은 좁히기는 전략 쪽 몫. `day_bars(mb_db, keys)` — 정규장 봉 배열, DB 읽기 전용(키움 조회 없음). `snapshots(keys, times)` — 시각별 가격. `coverage(keys)` |
| `prevday.py` | `attach_prev(d, feat)` — F 피처를 D=F+1 에만 붙이고 F종가=전일종가 assert (mclean 규칙 2·3). `leak_report(d, cols)` 이식 |
| `ticks.py` | `tick_size(price, market)`, `upper_limit(pc, market)`, `lower_limit(pc, market)` — 2023-01 이후 호가표 (데이터가 2025-08~ 이라 한 벌) |
| `fills.py` | `limit_buy_fill(bars, price, after, before)` → 체결 봉 번호 또는 없음. 체결 규칙은 §4 M4 |
| `exits.py` | `tp_sl_exit(bars_by_day, entry_idx, entry_px, tp_px, sl_px, end)` — 체결 다음 봉부터, 봉 시가 갭 먼저, 같은 봉 둘 다 → SL, 하한가 매도 불가(M5), 장중 정지(M6), 다일 보유 지원. 종가 청산·n봉 시간 청산은 `tp_sl_exit` 인자(tp·sl 없음, `time_bars`)로 처리 |
| `bench.py` | `hold_bench(bench_id, entry_date, entry_time, exit_date, exit_at)` — 여러 날 보유용 얇은 래퍼. 진입 09:00 시가면 `entry_at="open"`, 그 외 장중이면 `"close"` 로 `bench_daily.bench_return` 호출 (M7) |
| `validate.py` | `wf_monthly(trades, test_month)` — 학습 = 진입·청산 모두 그 달 1일 전 (호출자가 못 끔). 선택·placebo 는 `backtest_daily.validate.select_best`·`placebo_percentile` 재사용 |
| `tests/` | §5 표 |

**backtest_daily 에서 import (다시 만들지 않음)**

| 쓰임 | 함수 |
|---|---|
| 일 가중·월 가중·비용별 표 (0.60/0.35/0.23%) | `stats.weighted`, `day_key`, `month_key`, `cost_table`, `COSTS` |
| 스팩·우선주·스팩합병사 | `universe.listing_flags` |
| 상폐 재무요건 | `guards.fin_flags` |
| 직전 20일 정지·±31% 점프·거래대금 10억 (전일 기준 진입 필터) | `guards.halt_ok`, `jump_ok`, `liquidity` |
| 보유 기간 중 기업행위(권리락 등) 제외 | `adjust.corp_action_events` |
| 여러 날 보유 전략의 벤치 (일봉과 동일, §4 M7) | `bench_daily.bench_id`, `bench_return` |
| placebo 백분위·최적 선택 | `validate.placebo_percentile`, `select_best` |

**안 한다**
- 기존 리서치(`minute_top30`, `watchlist_expected_return`, `envelope`, `jev_quant_live` …) 수정·이관
- 시간외(16~20시) 봉
- 장중 시각 기준 벤치 (분봉 지수). 당일 매매는 비용 후 수익 + placebo 로 판정 (§4 M7)
- 계좌 잔고·종목수 배분 시뮬레이션 (exp59 의 잔고÷3) — 필요해지면 따로
- backtest_daily 수정 (필요해지면 먼저 보고)

## 4. 결정 (구현 전 고정)

| # | 항목 | 결정 | 정한 사람 | 이유 |
|---|---|---|---|---|
| M1 | 위치 | `research/backtest_minute/`, 커밋함. backtest_daily 를 import (역방향 import 없음) | Claude | 전략 정보 없음. 일봉 기능 중복 방지 |
| M2 | 유니버스 | KOSPI·KOSDAQ **전 종목**, 기간 2025-12-01~ (전 종목 분봉 있는 구간). 걸러내기는 전일 정보로만(backtest_daily 의 스팩·우선주·정지·점프·유동성). **D 정보로 거르지 않음**(커버리지·봉수·주식수 X). D 에 쓸 수 있는 건 시가 형성 여부·시가 상한가뿐. 빈자리 안 채움 | 사용자(전 종목) · Claude(전일 정보만, exp59 리뷰 반영안) | 거래량 상위 30 은 분봉이 그것만 있던 시절 제약. 09:00 에 알 수 없는 정보로 유니버스가 바뀌면 생존 편향 |
| M3 | 분봉 로드 | DB 읽기 전용. 없는 날짜를 키움에서 받지 않음 | Claude | 리서치가 몰래 수집·적재하면 재현성 깨짐 |
| M4 | 지정가 매수 체결 | ① **장 시작 전 주문(동시호가 참여)**: 시가 ≤ 지정가면 체결, 체결가 = 시가. ② **장중(그 외)**: 봉 저가 ≤ 지정가 − 1호가면 체결, 체결가 = 지정가 (닿기만 하면 미체결). 함수가 체결 봉 번호와 체결가를 함께 반환. 장 시작 전 주문인지는 호출부가 인자로 명시(기본값 없음) | Claude ②, 사용자 승인 ①(2026-10-01 "추천대로 진행") | ② 닿기만 한 봉은 대기열 뒤라 못 사는 경우가 많다. ① 2026-10-01 재현에서 발견 — 동시호가는 시가 이상 지정가 전부 체결되는데 ②만 쓰면 갭 상승 종목이 통째로 빠져 시가 매수 결과가 크게 왜곡(38차 재현). 기존 `mclean.simulate` 는 `저가 ≤ 매수가` → 재현 시 규약 차이로 표기 |
| M5 | 하한가 청산 | 청산 봉 가격이 하한가면 매도 불가 → 하한가가 풀린 첫 봉 종가, 당일 안 풀리면 다음 거래일 시가 | Claude | BACKTEST_DATA "하한가 매도 불가". 기존 코드엔 없음 |
| M6 | 장중 거래정지 | 매수 후 15:15 전에 봉이 끊기면 다음 거래일 시가 청산 (exp59 규칙 3) | Claude | |
| M7 | 벤치 | 당일 매매: 벤치 없음 — 비용 후 수익 + 같은 시각 무작위 진입 placebo 로 판정. 여러 날 보유: **일봉과 동일하게 `bench_daily`**. 장중 진입이면 `entry_at="close"`(진입일 종가부터), 청산은 시가/종가 중 전략 청산 시점 | 사용자(벤치 불필요·여러 날은 일봉과 동일) · Claude(장중 진입 → 진입일 종가부터) | 몇 시간 사이 시장 움직임은 비용보다 작고 placebo 가 그날 장 분위기를 거름. 진입일 남은 장중 구간은 placebo 몫 |
| M8 | 분봉 결측 종목 | 선택된 종목에 분봉이 없으면 표본 제외하지 않고 일봉으로 보수 처리 (체결 = 일봉 저가 < 매수가, 목표 도달 모름 → 종가 청산. 보유 중 분봉 없는 날: 시가 갭 먼저, 일봉 저가 ≤ 손절가면 손절, TP 는 안 봄) | Claude (exp59 규칙 2, 손절 쪽은 보수적으로 추가) | 결측 제외는 생존 편향 |
| M9 | 검증 분할 | 월 단위 walk-forward (확장 창). 연 단위 불가 — 전 종목 분봉이 2025-12-01~ 약 10개월뿐 | Claude | 연 단위면 검증 구간 1개 |
| M10 | 코드 생성 | muse 가 작성, Claude 검수·테스트 실행. muse 명세에 backtest_daily 재사용 필수 지시 | 기존 규칙 | |

## 5. 요구사항 → 검증

| 요구사항 | 검증 |
|---|---|
| **재현 (완료 기준)** | 분봉 리서치 무작위 샘플(시드 기록)을 backtest_minute 로 재구현 → 원본 baseline 일치(표시 자릿수). 규약 다른 부품(M4·M5·M8)은 교체 전·후 headline 변화를 따로 보고. 과거 리서치는 거래량 상위 30 유니버스라 **유니버스는 원본 것을 그대로 넣고** 나머지 부품(체결·청산·통계·검증)을 대조. 스크립트·결과는 `research/private/backtest_minute_repro/` |
| 룩어헤드 차단 (부정 요구) | 단위: 피처를 같은 날(D)에 붙이려 하면 AssertionError. `leak_report` 가 당일 종가 섞은 반례를 잡음 |
| 유니버스에 D 정보 없음 (부정 요구) | 단위: D 분봉이 없는 종목도 유니버스에 남음, 빈자리 안 채움 |
| 유니버스 = 전 종목 | 단위: 거래량 순위와 무관하게 전일 필터 통과 종목 전부 포함. 기본 시작일 20251201 |
| 여러 날 보유 벤치 | 단위: 장중 진입 → `bench_return(entry_at="close")` 와 같은 값 |
| 로드 부작용 없음 (부정 요구) | 단위: `day_bars` 가 키움 클라이언트를 import·호출하지 않음 (소스 검사) |
| 체결 규칙 | 단위: (장중) 저가 = 매수가 → 미체결 / 저가 = 매수가 − 1호가 → 체결가 = 매수가 / `after`·`before` 시각 밖 → 미체결. (장 시작 전 주문) 시가 = 지정가 → 체결가 = 시가 / 시가 < 지정가 → 체결가 = 시가 / 시가 > 지정가 → 장중 규칙으로 계속. 장 시작 전 여부 인자 누락 → TypeError |
| 청산 순서 | 단위: 봉 시가 갭 TP / 갭 SL / 같은 봉 둘 다 → SL / 체결 봉 자체는 판정 안 함 / 다일 보유 마지막날 종가 |
| 하한가 매도 불가 | 단위: SL 봉이 하한가 고정 → 풀린 봉 종가 / 종일 하한가 → 다음날 시가 |
| 장중 정지 | 단위: 14:00 에 봉 끊김 → 다음날 시가 |
| 호가 단위 | 단위: 2,000원·5,000원·20,000원 등 경계 가격 상·하한가 |
| 월 walk-forward 누설 차단 (부정 요구) | 단위: 청산일이 검증 달에 걸친 트레이드는 학습 제외. 우회 인자 없음 |
| public → private import 없음 (부정 요구) | 단위: 모든 모듈 소스에 `research.private` 없음 |
| backtest_daily 재구현 없음 | 검수: 일 가중·가드·placebo 를 새로 정의하지 않음 (grep) |

## 6. 순서

1. muse 명세 → backtest_minute 코드 + 단위테스트 → Claude 검수·실행
2. 재현 대상 무작위 선정 (모집단·시드 기록) → 원본을 현재 DB 로 재실행(출력 경로 우회) → backtest_minute 재구현 대조
3. `research/BACKTEST_DATA.md` §7 에 분봉 대응표 추가, §2·§3 의 `minute_bar_store`·`simulate_minute_exit` 안내를 backtest_minute 로 교체
4. 커밋. push 는 사용자

## 7. 완료 후 재검토 (체크)

- [ ] §3 표의 파일·함수 전부 존재
- [ ] §5 표 각 행 테스트 통과 (출력 첨부)
- [ ] 재현 샘플 baseline 일치
- [ ] §4 결정이 코드에 반영된 위치(파일:줄) 표로 보고
