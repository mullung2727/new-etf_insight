# PLAN — broker 미국 주식 주문·잔고 엔드포인트

한 줄: broker 에 키움 미국 주문(`ust20000`/`ust20001`)·잔고(`ust21070`)·달러 예수금(`ust21110`)·당일 체결(`ust21510`) 엔드포인트를 추가한다. 주문은 전략 주문 가드(허용 source·**달러 1건 상한**)를 지나야 키움으로 간다. 전략 로직은 비공개 쪽에 있다.
계좌: 미국 전용 broker :8003 (`KIWOOM_PROFILE=US`).

## 진행
- [x] 구현·테스트 머지 (PR #124, 2026-10-24)
- [x] 운영 등록 — broker 재기동 2026-10-24(토) 12:40, :8003 broker-us 기동. `.env` `MAX_US_ORDER_USD` 미설정 → 기본 5000 적용 중
- [ ] 첫 실가동 확인 (날짜) — 첫 실주문 `us-v38-order` 2026-10-27 04:30 예정
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 엔드포인트 (`broker/routers/us.py` — 기존 `/us/daily` 와 같은 라우터, `broker/kiwoom/us.py` 에 키움 호출)

| 경로 | 키움 TR | 반환 |
|---|---|---|
| `GET /us/balance` | `ust21070` (`stex_tp`·`stk_cd` 빈 값 1회 — 실측 2026-10-23) | `[{code, exchange, qty, sellable_qty, avg_price, last_price, eval_amt}]` USD |
| `GET /us/deposit` | `ust21110` | `{usd_cash, usd_orderable, usd_withdrawable}` (`crnc_code=='USD'` 행의 `fc_entra`, `fc_ord_alowa`, `fc_pymn_alowa`) |
| `GET /us/fills?date=YYYYMMDD` | `ust21510` 당일 주문체결 확인 (또는 `ust21150` 일별) | `[{order_no, code, side, qty, filled_qty, avg_fill_price, status}]` |
| `GET /us/quote?code=` | `usa20101` 현재가 10호가 | `{code, price, bid1, ask1, volume}` USD (수량 계산·매수 상한 확인용) |
| `POST /us/orders/strategy` | 매수 `ust20000` / 매도 `ust20001` | `{status, order_no, message}` (`submitted/rejected/failed/dry_run`) |

`POST /us/orders/strategy` 요청: `{code, side: "buy"|"sell", qty, trde_tp: "03"(시장가)|"00"(지정가)|"30"(LOC), price?, source, dry_run}`.
- 거래소: `/us/daily` 의 종목별 거래소 캐시·ND→NY→NA 폴백(1903) 재사용.
- 가드 (`broker/kiwoom/guards.py` 확장, 키움 호출 전):
  - `ALLOWED_ORDER_SOURCES` 가 있으면 그 밖 source 422 (국내 전략 주문과 같은 규칙).
  - 매수 1건 금액 = qty × (price 또는 직전 종가 `/us/daily` 마지막 close) 가 `MAX_US_ORDER_USD`(기본 5000) 넘으면 422. 매도는 금액 상한 없음.
  - qty ≤ 0, side·trde_tp 이상값 422. 지정가·LOC 는 price 필수.
- 기록: 성공 주문은 기존 `kiwoom_trade_history` 와 같은 방식으로 source 와 함께 남긴다(국내 주문 기록 함수 재사용 가능하면 재사용, 시장 구분 `US`).
- MCP: `operation_id` 로 자동 노출되지만 주문 경로(`/us/orders/strategy`)는 MCP 노출 제외(국내 전략 주문 경로와 같은 처리 — `main.py` 의 exclude 목록 확인).

## 2. 테스트 (`broker/test_us_orders.py`, `kiwoom.us.request` mock)
- 매수 상한 초과 422 / 허용 안 된 source 422 / 매도는 상한 없음 / 시장가 price 빈값 전달 / 지정가 price 없으면 422
- 잔고·예수금 응답 정규화(부호 문자열·콤마)
- 1903 거래소 폴백이 주문에도 적용

## 3. 미확정 (구현 전 실측)
- `ust21070` `stex_tp` 미입력이 전 거래소인지 (스펙 "미입력시 전체" 는 stk_cd 설명) — 장 외 시간 조회 1회로 확인.
- 체결 확인 TR: `ust21510`(당일 주문체결 확인)과 `ust21150`(일별 주문체결내역) 응답 필드 — 첫 주문 뒤 확인.

## 구현 차이
없음
