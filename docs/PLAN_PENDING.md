# 미체결 조회/정정/취소 구현 계획

## 한 줄 요약
미체결(ka10075) **조회 UI** + 정정(kt10002) 백엔드/프론트 추가. 조회 백엔드·취소는 이미 있음(재활용).
**뼈대 먼저**(end-to-end 한 줄 흐름 검증) → **살 붙이기**(정정·인라인편집·배지) 순.

## 이미 있는 것 (재활용 — 신규 X)
- **ka10075 조회**: `orders.get_unfilled(side)` + `GET /orders/unfilled?side=` (close-bet 체결확인용으로 구현됨, 연속조회 병합·테스트 완료). 현재 정규화 5필드(`order_no/ticker/oso_qty/ord_stt/raw`)뿐 → UI용 필드(주문가·종목명·주문수량·구분·시간)는 **정규화에 추가**만 하면 됨. `list_pending`/`/orders/pending` **신설 안 함**.
- **취소**: `cancel_order` + `brokerClient.cancelOrder` 동작. 재사용만.

## 작성 원칙
- **재활용 우선**: 새 패턴 만들지 말 것. 아래 기존 코드 그대로 따라감.
  - 백엔드 TR 호출: `orders.place_order`/`cancel_order` 패턴 (`tr` 상수 → `request()` → `OrderResult`)
  - LIST 응답 파싱: `account.py`의 잔고 파싱 패턴 (`res.data.get("oso", [])`)
  - 프론트 데이터 패널: `AccountPanel` 패턴 (`useState`+`load()`+`DataTable`/`StatCard`)
  - 실시간 재조회: 기존 `useBrokerEvents` 훅 (`00` 채널)
  - 테이블/포맷: `DataTable`, `formatKrw`, `parseKrwString`, `Button` 그대로
- **취소는 손대지 않음**: `cancel_order` + `brokerClient.cancelOrder` 이미 동작. 재사용만.

## TR 스펙 (kiwoom_api.xlsx 확인됨 — `.claude/skills/kiwoom-api/parse.py`로 재확인 가능)

### ka10075 미체결요청 — EP_ACNT (`/api/dostk/acnt`)  ✅ 백엔드 구현됨
- req: `all_stk_tp`(0전체/1종목), `trde_tp`(0전체/1매도/2매수), `stk_cd`(opt), `stex_tp`(0통합/1KRX/2NXT)
- resp `oso[]`: `ord_no, stk_cd, stk_nm, ord_qty, ord_pric, oso_qty(미체결수량), ord_stt(접수/확인…), io_tp_nm(+매수/+매도), trde_tp(지정가/시장가), cur_prc, tm, orig_ord_no`
- 현재 라우트 정규화: `order_no, ticker, oso_qty, ord_stt, raw` → **S0-1에서 `ord_pric/stk_nm/ord_qty/io_tp_nm/tm` 추가**

### kt10002 주식 정정주문 — EP_ORDR (`/api/dostk/ordr`)
- req: `dmst_stex_tp("KRX"), orig_ord_no, stk_cd, mdfy_qty`(0=잔량전부), `mdfy_uv`(정정단가)
- resp: 새 `ord_no`

---

## Phase 0 — 뼈대 (전체 흐름 한 줄로 검증)

> 목표: "미체결 1건이 키움 → 백엔드 → 화면 표 1줄"까지 **최소 코드로** 뚫는다.
> 정정/취소/배지/실시간/스타일 전부 제외. 흐름만 확인되면 Phase 1로.

### S0-1. 백엔드 조회 — 정규화 필드 보강만 (신규 X)
- `orders.get_unfilled` / `GET /orders/unfilled` **그대로 씀**. `tr`·`list_pending` 추가 안 함.
- `routers/orders.py` `get_unfilled` 정규화 dict에 UI용 필드 추가: `stk_nm`(종목명), `ord_pric`→`ord_price`(주문가), `ord_qty`(주문수량), `io_tp_nm`(매수/매도구분), `tm`(시간). `_to_int` 패턴 그대로.
- 기본 `side="sell"` → 미체결 패널은 전체 보여야 하므로 프론트에서 `side=all` 호출(라우트는 이미 지원).
- **검증**: `curl "http://localhost:8001/orders/unfilled?side=all"` → 보강 필드 포함 정규화 배열

### S0-2. 프론트 표 한 개
- `broker-client.ts`: `listUnfilled: (side="all") => get<UnfilledOrder[]>("/orders/unfilled?side="+side)` + `UnfilledOrder` 타입(느슨하게, `raw` 포함 — Holding 패턴 복붙)
- `components/trading/pending-orders.tsx` (신규): `account-panel.tsx` 축소판 — `load()` + `DataTable`, 컬럼은 종목명/주문번호/미체결수량/주문가/상태 5개만
- trading page 우측에 임시로 `<PendingOrders/>` 직접 끼워 표시 (탭 아직 X)
- **검증**: 미체결 주문 1건 넣고(또는 모의 잔량) 표에 1줄 뜨는지

> Phase 0 끝나면 사용자 확인 받고 Phase 1.

---

## Phase 1 — 살 붙이기 (정정·취소·실시간·탭·배지)

### S1-1. 정정 백엔드
- `tr.py`: `TR_ORDER_MODIFY = "kt10002"` 추가
- `orders.py`: `modify_order(order_no, symbol, qty, price) -> OrderResult` — `cancel_order` 복붙 후 body를 `{dmst_stex_tp, orig_ord_no, stk_cd, mdfy_qty, mdfy_uv}`로 교체 (qty=0→잔량전부)
- `routers/orders.py`: `PATCH /orders/{order_no}` body `{symbol, qty, price}` → `modify_order(...)`. 에러는 기존 `_friendly_order_error` 재사용
- `broker-client.ts`: `modifyOrder(orderNo, symbol, qty, price) => patch<OrderResult>(...)`
- **검증**: 미체결 주문 가격 정정 → 새 ord_no 반환 확인 (장중)
- 정정 후 목록은 `/orders/unfilled` 재조회로 갱신

### S1-2. 인라인 정정/취소 UI
- `pending-orders.tsx`에 행별 액션 추가: 가격/수량 input(기본값 = 현재 주문값) + [정정][취소] 버튼
- 정정/취소 후 `load()` 재호출
- 취소는 **기존** `brokerClient.cancelOrder` 그대로 사용

### S1-3. 실시간 재조회
- `pending-orders.tsx`에 `useBrokerEvents` 추가: `channel === "00"` 이벤트 오면 `load()` 재호출 (접수/체결/취소로 목록 바뀜)
- 마운트 1회 로드 + 수동 새로고침 버튼(`AccountPanel` 버튼 복붙)

### S1-4. 탭 컨테이너 + 배지
- 우측 패널을 탭으로: `[잔고][미체결(N)]` — `account/page` 우측을 `AccountTabs` 래퍼로 감쌈
  - 거래내역(kt00007/kt00009) 자리만 주석으로 비워둠 (메모리 TODO, 이번 범위 밖)
- **배지 카운트는 상시 마운트**: 탭 닫혀도 미체결 건수 보이게 — `FillToast`처럼 탭 헤더에서 `00` 구독해 카운트 갱신, 목록 본체는 탭 열 때만 로드
  - 구조: 탭 헤더(상시) = 배지 / 탭 패널(조건부) = 목록. 카운트 출처는 가벼운 `listUnfilled().length` 또는 `00` 이벤트 누적 — S1-4에서 방식 확정

> 투자노트는 nav 그대로 둔다 (탭으로 안 내림 — 별도 작업모드, 폭 필요).

---

## 파일 변경 요약

| 파일 | Phase | 변경 |
|------|-------|------|
| `broker/kiwoom/tr.py` | 1 | `TR_ORDER_MODIFY` 상수 (조회 TR은 이미 `TR_UNFILLED` 있음) |
| `broker/kiwoom/orders.py` | 1 | `modify_order()` (조회 `get_unfilled` 재사용) |
| `broker/routers/orders.py` | 0,1 | `get_unfilled` 정규화 필드 보강(0), `PATCH /orders/{order_no}`(1) |
| `broker-web/lib/broker-client.ts` | 0,1 | `UnfilledOrder` 타입, `listUnfilled`, `modifyOrder` |
| `broker-web/components/trading/pending-orders.tsx` | 0,1 | 신규 — 목록+인라인 정정/취소+실시간 |
| `broker-web/components/trading/account-tabs.tsx` | 1 | 신규 — 잔고/미체결 탭 + 배지 |
| `broker-web/app/trading/page.tsx` | 0,1 | 우측에 끼움(0) → 탭 래퍼(1) |

## 재활용 안 하는(=신규) 것
- `pending-orders.tsx`, `account-tabs.tsx` 컴포넌트 본체 (탭/인라인편집은 기존에 없음)
- 나머지는 전부 기존 패턴 복붙·재사용

## 범위 밖 (나중에)
- 거래내역 탭 (kt00007/kt00009) — 자리만 비워둠. 메모리 TODO와 연결.
- 정정 시 조건단가(`mdfy_cond_uv`), NXT/SOR 거래소 — KRX 고정.
- 부분정정/연속조회(cont-yn) — 미체결이 페이지 넘칠 일 드묾, 1페이지만.
