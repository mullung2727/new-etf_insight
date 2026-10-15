# PLAN — 공용 분할 체결 (VWAP 근사: 하루 N회 수동 지정가 → 잔량 이월 → 마지막 날 시장가)

한 줄: 여러 날에 걸쳐 나눠 사고팔 주문을 하루 N회(기본 09:30·11:00·13:00·14:30) 같은 쪽 최우선호가 지정가로 내고,
회차 끝에 안 채워진 잔량은 취소해 다음 회차·다음 날로 넘기며, 마지막 날 마지막 회차 잔량만 시장가로 정리하는
공용 함수 `run_sliced` 를 `etl/scripts/sliced_exec.py` 에 만든다. 주문·정정·취소·조회는 `chase_engine` 을 그대로 쓴다.

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음 (전략이 호출, 전략 PLAN 에서 등록)
- [ ] 첫 실가동 확인 (날짜) — 해당 없음 (첫 사용 전략에서 확인)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 왜
- 거래대금이 하루 수천만원인 종목은 한 번에 시장가로 사면 호가 여러 칸을 먹는다(실측 스프레드 중앙값 0.5%, 상위 10% 1%+).
- 기존 `run_chase` 는 "지정가 → 정정 추격 → 같은 날 시장가" 한 창이라, 여러 날·여러 회차 분할과 "시장가 없이 잔량 이월"이 없다.
- 전략마다 따로 짜지 않도록 공용으로 둔다 (PLAN_SHARED_CHASE_ENGINE 방향).

## 2. 확정 결정
| # | 결정 | 누가 정했나 | 구현 위치 |
|---|---|---|---|
| S1 | 회차 시각 기본 09:30·11:00·13:00·14:30, 호출자가 바꿀 수 있음 | 내 판단 (사용자 "장중 조금씩" 승인) | `run_sliced(slices=)` |
| S2 | 회차 수량 = 그날 남은 수량 ÷ 남은 회차 수 (내림, 마지막 회차는 나머지 전부) | 내 판단 | `sliced_exec.slice_qty` |
| S3 | 지정가 = 매수는 매수1호가, 매도는 매도1호가 (같은 쪽 최우선, 스프레드 안 넘음). 회차 동안 step 마다 최우선호가가 바뀌면 그 가격으로 정정 | 사용자 (VWAP·유리하게) | `price_fn` 으로 `chase_engine` 에 전달 |
| S4 | 회차 끝(다음 회차 1분 전, 마지막 회차는 15:10) 잔량은 **취소만** 하고 시장가 안 냄 → 다음 회차 수량에 합산 | 내 판단 | `chase_engine` 에 state 옵션 `cancel_only_at` 추가 (아래 §3) |
| S5 | 그날 끝 잔량은 반환값으로 돌려줌 → 호출 전략이 원장에 기록하고 다음 날 다시 넘김 | 내 판단 | `run_sliced` 반환 `{key: 남은수량}` |
| S6 | `final=True` 인 주문(마지막 날)만 15:15 시장가로 잔량 정리, 15:20 이후 시장가 금지 | 사용자 (마지막 날 맞춤) | `chase_engine` 기존 `market_at`/`end_at` |
| S7 | 회차 수량 상한 = 호출자가 준 `cap_qty` (전략이 20일 평균 거래대금 × 참여율로 계산). 넘는 몫은 다음 회차로 | 내 판단 | `slice_qty(cap=)` |
| S7a | 같은 회차에 매도·매수가 섞이면 매도 주문을 먼저 내고, 매수 회차 수량은 호출자가 준 `cash_fn()`(그 시점 가용현금) ÷ 최우선호가 이내로 줄임. 줄인 몫은 다음 회차 이월. `cash_fn` 조회 실패면 그 회차 매수 안 함 | 사용자 (10-15, 방식 C) | `run_sliced(cash_fn=)` |
| S8 | fail closed 는 `chase_engine` 불변식 그대로 (조회 실패·결과 불명이면 주문 안 함, 남의 미체결 안 건드림) | 기존 | `chase_engine` |

## 3. `chase_engine` 변경 (최소)
- state 키 `cancel_only_at: "HH:MM:SS"` 추가: 그 시각에 이 state 의 살아 있는 주문을 취소하고 `status="carried"`, 남은 수량을 `carry_qty` 에 기록. 시장가 안 냄.
  - 취소 실패·미확인이면 `unknown` (재주문 금지, 기존 규칙과 동일).
- 나머지 동작 변경 없음. 기존 전략(무상·되돌림·Jev)은 이 키를 안 쓰므로 영향 없음 → 기존 테스트 전부 통과가 조건.

## 4. 인터페이스
```python
run_sliced(today, orders, api, clock, *, slices=("09:30:00","11:00:00","13:00:00","14:30:00"),
           last_cut="15:10:00", final_market_at="15:15:00", end_at="15:20:00",
           step_seconds=30, on_event=None) -> dict[str, int]
# orders: [{"key", "ticker", "side", "qty"(오늘 목표), "cap_qty"(회차 상한, None=무제한), "final"(bool)}]
# 반환: {key: 오늘 못 채운 수량}
```

## 5. 요구사항 → 테스트 (`etl/tests/test_sliced_exec.py`, 가짜 api·시계)
| 요구 | 테스트 |
|---|---|
| 회차마다 남은 수량 균등 분할, 마지막 회차 나머지 | `test_slice_qty_split` |
| 회차 상한 넘는 몫은 다음 회차로 | `test_slice_cap_carry` |
| 매수 지정가 = 매수1호가, 매도 = 매도1호가 | `test_passive_price_side` |
| 회차 끝 잔량 취소만, 시장가 없음 (final=False) | `test_cancel_only_no_market` |
| 취소 미확인이면 unknown, 다음 회차 주문 안 함 | `test_cancel_unconfirmed_stops` |
| final=True 만 15:15 시장가 | `test_final_market_only_last` |
| 15:20 이후 시장가 금지 | 기존 `chase_engine` 테스트 재사용 + `test_no_market_after_end` |
| 반환 잔량 = 목표 − 체결 | `test_returns_remaining` |
| 남의 미체결 안 건드림 | `test_foreign_orders_untouched` |
| 같은 회차 매도 먼저, 매수는 가용현금 이내·나머지 이월 | `test_sell_first_buy_cash_capped` |
| 가용현금 조회 실패면 그 회차 매수 안 함 | `test_cash_lookup_fail_no_buy` |
| 기존 run_chase 동작 불변 | 기존 `test_chase_engine.py` 전부 통과 |

## 6. 운영 체크 (§7)
- 휴장일·시각 게이팅은 호출 전략 책임. 이 함수는 시각 이후 시작이면 남은 회차만 처리, 전부 지났으면 아무것도 안 하고 전량 반환.
- 알림·원장 기록은 `on_event` 콜백(전략 쪽). 이 모듈은 print 하지 않음.

## 구현 차이
- 원래 `price_fn` 이 api 의 아무 가격이나 사용 → 실제: `best_quote` 가 없는 api 면 None(주문 안 함). `api.price` 는 추격가(1호가±1틱)라 스프레드 1틱 종목에서 즉시 체결돼 수동 대기가 깨짐 (검수 지적). `ClientEngineApi.best_quote` 추가.
- 원래 회차 끝 잔량 계산 시점 미지정 → 실제: 취소가 재조회로 확인된 **뒤** 체결을 다시 조회해 `carry_qty` 계산 (조회~취소 사이 체결 누락 → 과매수 방지).
- 원래 `cancel_only_at` 은 시장가 시각과 같을 때만 분리 → 실제: 키가 있으면 `_bounds` 가 `market_dt` 를 취소 시각으로 맞추고 시장가 경로에 절대 안 넣음.
- 작성: GPT(gpt-6-luna, Codex CLI) 작성, Claude 검수 (사용자 지정 2026-10-15, muse 할당량 소진). Codex Windows 샌드박스 고장으로 사용자가 `!` 로 샌드박스 없이 실행.
