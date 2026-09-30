# PLAN — 리서치 공용 백테스트 모듈 (일봉 전용) `research/backtest_daily/` (2026-09-30)

**요약** — 리서치마다 muse 가 500줄씩 새로 짜던 데이터 로드·기업행위 보정·가드·사이즈중립 벤치·일/월 가중 통계·walk-forward·placebo 를
한 패키지로 모은다. **기존 리서치 코드는 고치지 않는다.** 완료 기준 = `ipo_drift` 1·2단계 결과를 backtest_daily 로 재구현했을 때 숫자가 똑같이 나오는 것.

상태: 계획 확정, 개발 시작. 사용자 승인(2026-09-30 "공용 모듈 뽑아" · "일봉먼저 하고 나중에 해야한다는 것 명시 · 일봉전용 명확히 · 개발시작").

> **일봉 전용.** 입력은 `etl/db/krx_ohlcv.duckdb` 일봉뿐이다. 분봉(`etl/db/minute_bars.duckdb`)은 이 패키지에서 다루지 않는다.
> **분봉 공용 모듈은 이 작업이 끝난 뒤 별도로 한다** (그때까지 분봉은 기존 `minute_bar_store` / `simulate_minute_exit` 사용, BACKTEST_DATA §3).
> 가드·통계·검증 중 분봉에서도 쓸 부분(상한가·점프 판정, 일 가중, walk-forward)은 분봉 작업 때 공용으로 끌어올릴지 정한다 — 지금은 옮기지 않는다.

---

## 1. 왜

- 같은 기능이 최소 5곳에 따로 있다 (아래 §2). 매번 새로 짜서 **같은 함정을 매번 다시 검수**해야 한다
- 이미 **처리 방식이 갈렸다**: 상장폐지를 `volume_shock_anchor.compute_returns` 는 −100%, `ipo_drift.event_paths` 는 마지막 가격 고정. 같은 질문에 리서치마다 답이 다르다
- 외부 라이브러리(vectorbt·backtrader·alphalens)는 상한가·권리락·정리매매·스팩합병사를 모른다 → 도입 보류 (2026-09-30 검토)

## 2. 현재 흩어진 구현 (2026-09-30 grep)

| 기능 | 현재 위치 | 비고 |
|---|---|---|
| 일봉 로드 + 시장 거래일 순번 `ms` | `research/private/ipo_drift/common.py` `load_px` / `research/private/volume_shock_anchor/common.py` `load_px`,`load_market_dates` | 거의 같음 |
| 기업행위 보정 일수익 | `ipo_drift/common.py` `adj_returns` (주식수 이벤트 ±30행 매칭, 테스트 있음) | list_shrs 밴드 방식보다 정확 (권리락 잡음) |
| 기업행위 분류(제외용) | `research/private/envelope/exp44_corp_actions.py` `detect` | volume_shock_anchor 가 import |
| 사이즈중립 지수 | `ipo_drift/common.py` `size_index` / `research/high52_strategy/backtest.py` `size_neutral_index` | 앞쪽이 배열·일반형 |
| 정지 가드(직전 20일) | `volume_shock_anchor/common.py` `halt_pass` | |
| ±31% 점프 가드 | `volume_shock_anchor/common.py` `jump_pass` | BACKTEST_DATA §2c |
| 상한가 진입 가드 | `ipo_drift/step2_strategy.py` `guard_limit`, `step4_rebound_trend.py` `guard_limit_open` | 종가 진입용/시가 진입용 두 벌 |
| 유동성 가드 | `ipo_drift/step2_strategy.py` `guard_liquidity` | |
| 상폐 재무요건 가드 | `research/private/envelope/exp29_fin_guard.py` `fin_flags` | private 에 있음 — §4 D5 |
| 스팩·스팩합병사·우선주 | `ipo_drift/common.py` `ipo_universe` 안에 섞여 있음 | |
| 청산(고정보유·트레일링) | `ipo_drift/step2_strategy.py` `exit_row`, `step4_rebound_trend.py` `exit_ts` | |
| 일 가중 통계 | `volume_shock_anchor/common.py` `day_weighted_mean` 등 | |
| 월 가중 통계 | `ipo_drift/common.py` `month_weighted` | |
| walk-forward | `ipo_drift/step2_strategy.py` `wf_train`,`select_best` / `close_bet_research/harness.py` `walk_forward` | |
| placebo | `close_bet_research/harness.py` `placebo`, ipo_drift step2·4 안 인라인 | |

## 3. 범위

**만든다** — `research/backtest_daily/` (공개 저장소 커밋 대상: 전략 조건·파라미터 없이 데이터 처리·통계만 담는다)

| 파일 | 내용 (출처) |
|---|---|
| `data.py` | `load_px(db, since=None)` (ms 포함), `market_dates`, `names` |
| `adjust.py` | `adj_returns` (ipo_drift 이식), `adj_price`(=종목별 누적곱, step2 `stock_P`), `corp_action_events` (exp44 `detect` 이식) |
| `universe.py` | `spac_flags` (이름 + 합병사 판별 — ipo_drift 규칙), `pref_flag`, `new_listings` |
| `guards.py` | `limit_up(entry, prev_close)`, `jump_ok`, `halt_ok(20일)`, `liquidity_ok(20행 평균 ≥ 10억)`, `fin_ok` (§4 D5) |
| `bench.py` | `CAP_EDGES/LABELS`, `size_index`, `bench_return(bucket, ms_from, ms_to)` |
| `exits.py` | `hold_exit(H, freeze)`, `trailing_exit(stop, max_hold)` |
| `stats.py` | `day_weighted`, `month_weighted` (평균·t·n), `cost_table(exc, keys, costs=(0.006,0.0035,0.0023))` |
| `validate.py` | `walk_forward(trades, years, select_fn)` (학습 = 진입<Y & 청산<Y 강제), `placebo(draw_fn, n=200, seed=0)`, `split_holdout` |
| `tests/` | 이식 원본 테스트 + §5 표 |

**안 한다**
- 기존 리서치(`ipo_drift`, `volume_shock_anchor`, `close_bet_research`, `high52_strategy`, `envelope` …) 수정·이관. 과거 결과 재현성 유지. 새 일봉 리서치부터 backtest_daily 사용
- **분봉 — 이번 범위 아님. 일봉 완료 후 별도 작업** (현재는 `minute_bar_store`, `simulate_minute_exit` 사용, BACKTEST_DATA §3)
- 계좌 잔고·동시 보유 제한 포트폴리오 시뮬레이션 — 필요해지면 따로

## 4. 결정 (구현 전 고정)

| # | 항목 | 결정 | 정한 사람 | 이유 |
|---|---|---|---|---|
| D1 | 위치 | `research/backtest_daily/`, 커밋함 | Claude | 전략 정보 없음. private 에 두면 공개 리서치가 못 씀 |
| D2 | 상장폐지 처리 | **마지막 거래 가격 고정** (정리매매 행 포함). −100% 아님 | Claude | 정리매매는 거래돼 가격이 남는다. −100% 는 과대 손실. ipo_drift D17 과 일치. `volume_shock_anchor` 방식과 다름을 backtest_daily README 에 명시 |
| D3 | 기업행위 보정 | `adj_returns` 방식 하나로 통일 (list_shrs 0.67~1.5 밴드 방식 폐기) | Claude | 밴드는 권리락·20% 무상증자를 못 잡음 (BACKTEST_DATA §2c) |
| D4 | 정지 후 재개일 수익 | 0 처리 (NaN → 누적곱에서 1) + 재개 점프가 ±31% 넘으면 `jump_ok` 로 표본 제외 | Claude | ipo_drift D18 과 일치 |
| D5 | `fin_ok` | `envelope/exp29_fin_guard.fin_flags` 코드를 backtest_daily 로 **복사** 이식 (원본 유지). **확인 완료(2026-09-30)**: `fin_flags` 본문은 KRX 상폐 재무요건만(매출·세전손실·자본잠식·자기자본·시총, `x2` 배수). 파일 상단 import 만 엔벌로프 전략 것 → 함수 본문·`AVAIL` 상수만 복사 | Claude | public 모듈이 private 를 import 하면 공개 저장소에서 깨짐 |
| D6 | 가중 기본값 | 통계 함수는 `weight="day"|"month"` 인자 필수(기본값 없음) | Claude | 어느 가중인지 호출부에서 보이게 |
| D7 | walk-forward 누설 차단 | 함수 안에서 `exit_date < Y0101` 을 강제(호출자가 못 끔) | Claude | 과거 사고 방지용 불변식 — 우회 경로 없게 |
| D8 | 코드 생성 | muse 가 이식·작성, Claude 가 검수·테스트 실행 | 기존 규칙 | |

## 5. 요구사항 → 검증

| 요구사항 | 검증 |
|---|---|
| **이식 정확성 (완료 기준)** | `ipo_drift` step1 표1(k별 초과 평균·t·n)과 step2 표2(39셀 net 0.35% 평균·n)를 backtest_daily 함수로 다시 계산 → 소수 4자리까지 일치. 스크립트 `research/private/ipo_drift/golden_backtest_daily.py` (DB 필요, unittest 와 분리. ipo_drift 가 private 라 backtest_daily 쪽에 두면 public→private 참조가 됨) |
| adj_returns 동작 | ipo_drift 테스트 4건 이식 (2:1 분할, 20% 무상증자 권리락, 종목 경계, ms 끊김) |
| 상폐 = 마지막 가격 고정 | 단위: 보유 중 행이 끊긴 종목 → 청산가 = 마지막 행, `frozen=True` |
| 상한가 가드 | 단위: +29.6% 제외 / +29.4% 통과 / 전일 비연속이면 판정불가 제외 |
| ±31% 점프 가드 | 단위: 종가→다음 시가 −32% → 제외 |
| 정지 가드 | 단위: 직전 20일 중 1일 결측 → False |
| 스팩합병사 판별 | 단위: 시가 2,000원·60일 표준편차 0.01 → spac_origin / 표준편차 0.05 → 아님 |
| 일·월 가중 | 단위: 같은 날 [1,1,1] + 다른 날 [3] → 2 |
| walk-forward 누설 차단 (부정 요구) | 단위: 청산일 ≥ Y0101 트레이드를 학습에 넣으려 해도 제외됨. 우회 인자 없음 확인 |
| public → private import 없음 (부정 요구) | 단위: backtest_daily 모든 모듈 소스에 `research.private` 문자열 없음 |
| 모든 모듈 import 됨 | 단위: 패키지 각 모듈 import 테스트 (2026-09-24 import 오류 사고 대응) |
| 기존 리서치 무변경 | 완료 시 기존 파일 수정 없음 (`research/high52_strategy/*` 는 git diff, private 는 ignore 라 mtime 로 확인). 새로 생기는 건 `research/private/ipo_drift/golden_backtest_daily.py` 하나 |

## 6. 순서

1. muse 명세 작성 → backtest_daily 코드 + 단위테스트 생성 → Claude 검수·테스트 실행
2. golden 스크립트로 ipo_drift 수치 일치 확인. 다르면 원인 기록 후 사용자 보고(어느 쪽이 맞는지 판단 포함)
3. `research/BACKTEST_DATA.md` 에 §7 "공용 모듈" 추가 — 가드별 함수 이름 대응표. 기존 §의 SQL 예시는 유지
4. 커밋 (브랜치는 git-workflow 스킬 따름). push 는 사용자

## 7. 완료 후 재검토 (체크)

- [ ] §3 표의 파일·함수가 전부 존재
- [ ] §5 표 각 행 테스트 통과 (출력 첨부)
- [ ] golden 일치
- [ ] §4 결정이 코드에 반영된 위치(파일:줄) 표로 보고
