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
| D6 | 가중 기본값 | 통계 함수 `stats.weighted(values, keys)` 는 가중 키를 **기본값 없이** 받는다 — 호출부가 `day_key(dates)` 또는 `month_key(dates)` 를 넘겨 일·월 가중을 명시. (2026-10-01 리뷰 #7: 처음 문서의 `weight=` 인자 대신 키 인자로 구현됨, 의도 동일 — 문서를 구현에 맞춤) | Claude | 어느 가중인지 호출부에서 보이게 |
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

---

# 2차 — 벤치마크 일별 수익률 사전 계산 `bench_daily` (2026-09-30)

**요약** — 벤치마크 일별 수익률을 한 번 계산해 `etl/db/bench_daily.duckdb` 에 저장하고, 리서치는 읽기만 한다.
하루를 **종가→종가 / 밤사이 / 장중** 세 조각으로 저장해 진입·청산 시점이 달라도 정확히 조합한다.

상태: 사용자 승인 ("일별 수익률 다 구해놓고 쓰는게 빠를거 같아" · "추천대로. 다만 전략별로 헷갈리지 않게 잘만들어라").

## 왜 (재현 검증에서 발견, `research/private/backtest_daily_repro/README.md`)
- `size_index` 는 종가→종가 누적. **시가 진입** 전략은 벤치만 밤사이 수익(소형 유동성 +0.2~0.3%/일)을 먹어 초과수익이 낮게 나온다
- `size_index` 는 저유동 종목 포함. 소형 구간에서 유동성 종목보다 하루 +0.02~0.11%p 높아 보유가 길수록 초과수익을 깎는다
- h26 재현: 벤치만 바꿔도 gold h20 초과 +0.37% → −0.76%
- 매 실행 400만 행으로 지수를 새로 만드는 비용도 없앤다

## 저장 — `etl/db/bench_daily.duckdb` (gitignore 대상, 파생 데이터)

```
bench_daily(date VARCHAR, ms INT, bench_id VARCHAR, n INT, r_cc DOUBLE, r_on DOUBLE, r_in DOUBLE)  PK(date, bench_id)
bench_meta(bench_id, universe, cap_bucket, label_ko, rule_ko)                 -- 사람이 읽는 설명
bench_build(source_max_date, built_at, row_count, code_version)               -- 1행
```

| 필드 | 뜻 |
|---|---|
| `r_cc` | 전일 종가 → 당일 종가 (기업행위 보정 `adj_returns`, ±30% 클립) |
| `r_on` | 전일 종가 → 당일 시가 (같은 보정 계수, ±30% 클립) |
| `r_in` | 당일 시가 → 당일 종가 |
| `n` | 그날 그 벤치에 들어간 종목 수 |

**bench_id 이름 규칙** — `{구간}_{모집단}`. 이름만 보고 뜻이 보이게 한다.

| 구간 | 뜻 (전일 시총 기준) | 모집단 | 뜻 |
|---|---|---|---|
| `CAP1` | 1천억 미만 | `ALL` | 0값 아닌 전 종목 (스팩 제외) |
| `CAP2` | 1천억~3천억 | `LIQ10` | 전일 거래대금 10억 이상 (스팩 제외) |
| `CAP3` | 3천억~1조 | | |
| `CAP4` | 1조~5조 | | |
| `CAP5` | 5조 이상 | | |
| `MKT` | 시장 전체 | | |

→ 12개 (`CAP1_ALL` … `MKT_LIQ10`). 구간·모집단 판정은 **전일 값**만 쓴다 (룩어헤드 차단). 일별 동일가중.

## API — 전략이 헷갈리지 않게 (전부 인자 필수, 기본값 없음)

```python
bench_id(cap_eok_prev: float, universe: "ALL"|"LIQ10") -> str      # 진입 전일 시총으로 구간 선택
bench_return(bench_id, entry_date, entry_at: "open"|"close",
             exit_date, exit_at: "open"|"close") -> float           # 보유 구간 벤치 수익
```

| 전략 모양 | 호출 | 조합 |
|---|---|---|
| 종가 매수 → 다음날 시가 매도 (종가베팅·Jev) | `entry_at="close", exit_at="open"` | 다음날 `r_on` |
| 다음날 시가 매수 → h일 뒤 종가 (h26, ipo_drift 4단계) | `entry_at="open", exit_at="close"` | 진입일 `r_in` × 이후 `r_cc` |
| 종가 매수 → h일 뒤 종가 (high52, ipo_drift 2단계) | `entry_at="close", exit_at="close"` | 이후 `r_cc` |
| 시가 매수 → 같은 날 종가 (당일매매) | 같은 날짜, `open`→`close` | 당일 `r_in` |

- 진입이 청산보다 늦거나(같은 날 close→open 등) 날짜가 벤치 범위 밖이면 **예외**. 조용히 NaN 주지 않는다
- 사이 날에 벤치 행이 없으면(휴장) 건너뛴다 — 시장 거래일 기준

## 갱신
- `ensure_bench(force=False)`: 파일 없음 / `bench_build.source_max_date` < 일봉 DB 최신일 / `code_version` 다름 → 전체 재계산
- 임시 파일에 쓰고 교체 (읽는 쪽이 반쯤 쓴 파일을 보지 않게)
- 기존 `size_index` / `bench_return(idx_arr, …)` 는 **그대로 둔다** (ipo_drift 골든 재현용). README 에 "구방식 — 신규 리서치는 bench_daily" 표기

## 결정
| # | 항목 | 결정 | 정한 사람 |
|---|---|---|---|
| D9 | 저장 위치 | 별도 파일 `etl/db/bench_daily.duckdb` (일봉 DB 스키마 무변경) | 사용자(추천 수용) |
| D10 | 하루 세 조각 | `r_cc`·`r_on`·`r_in` | Claude |
| D11 | 벤치 12종 | 시총 5구간+시장 × {ALL, LIQ10} | Claude |
| D12 | 인자 기본값 없음 | `universe`·`entry_at`·`exit_at` 매번 명시 — 전략마다 다른 시점을 실수로 섞지 않게 | Claude (사용자 "헷갈리지 않게") |

## 검증
| 요구 | 검증 |
|---|---|
| 조각 정합 | 단위: 보정 없는 날 `(1+r_on)(1+r_in) = 1+r_cc` (클립 안 걸린 행) |
| 조합 규칙 4종 | 단위: 위 표 4행 각각 합성 벤치로 기대값 |
| 전일 기준 배정 | 단위: 당일 시총이 구간 경계를 넘어도 전일 구간에 들어감 |
| 잘못된 구간 예외 | 단위: close→open 같은 날, 범위 밖 날짜 → 예외 |
| 기본값 없음 | 단위: `inspect.signature` 로 세 인자 기본값 없음 확인 |
| 스테일 재계산 | 단위: 임시 DB 로 source_max_date 가 작으면 재계산, 같으면 안 함 |
| 기존 골든 유지 | ipo_drift 골든 GOLDEN PASS 그대로 |
| **h26 재측정 (핵심)** | 벤치를 `CAP*_LIQ10` · `open→close` 로 바꿔 h26 12셀 재계산 → 원본("같은 날 적격 평균") 대비 Δ 가 구방식(−0.30~−1.14%p)보다 크게 줄어드는지 보고 |

## 2차 결과 (2026-09-30)
- 테스트 39개 통과, `bench_daily.duckdb` 첫 생성 76초 (20,844행, 20180103~20260929)
- h26 재측정 (원본 "같은 날 적격 평균" 대비 Δ, 원본 exc 기준):

| 보유 | 구방식 size_index | bench_daily LIQ10 | bench_daily ALL |
|---|---|---|---|
| 1일 | −0.24~−0.30%p | **+0.03~+0.04%p** | −0.07~−0.13%p |
| 5일 | −0.35~−0.49%p | +0.09~+0.18%p | −0.15~−0.27%p |
| 20일 | −0.77~−1.14%p | +0.46~+0.76%p | −0.41~−0.74%p |

- 시점 문제(밤사이)는 해결 — 1일 보유에서 차이 사라짐
- 여러 날 보유 차이는 **정의 차이**다: bench_daily 는 매일 재구성한 지수(연쇄), h26 원본 벤치는 진입일 적격 종목 묶음을 그대로 보유한 수익. CAP2·LIQ10 20일에서 두 정의 차이 실측 0.24%p (`research/private/backtest_daily_repro/orig/cohort_vs_chain.py`)
- **D13 (사용자 결정)**: 벤치마크 = 매일 재구성 지수(bench_daily) 로 확정. "같은 날 같은 조건 종목을 아무거나 샀다면" 비교는 벤치가 아니라 **대조 전략**이므로 `validate.placebo_percentile` 쪽에서 한다

## 2차 보충 — 리뷰 반영 (2026-10-01, `research/private/backtest_daily_repro/prompts/03_review_result.md`)
- **세 조각은 각각 독립된 동일가중 포트폴리오 수익이다** (리뷰 #2). `mean(r_on)`·`mean(r_in)` 을 곱해도 `mean(r_cc)` 와 같지 않다 — 종목 간 밤사이·장중 공분산(갭 뒤 되돌림) 때문. `bench_return` 은 종가→종가 구간을 두 조각으로 쪼개 계산하지 않으므로 결과는 일관된다: 시가 진입 = "그날 시가에 동일가중으로 산 포트폴리오", 시가 청산 = "전일 종가 포트폴리오를 시가에 판 것". 조각을 직접 곱해 종가→종가를 만들지 말 것
- 수정 (F1~F6): event_paths 기준일 수익 제외 / 정확히 ±30% 정상 가격제한 행 유지 · 장중 수익은 가격제한 조합 범위(−46.2%~+85.8%)만 제외 / 벡터 조회 앞 NaN 오염 / 동시 빌드 임시파일 / 스팩 가격행동 판정 추가 / CODE_VERSION 2
