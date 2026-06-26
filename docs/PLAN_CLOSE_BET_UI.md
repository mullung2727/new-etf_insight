# PLAN — 종가배팅 매매·청산 현황 UI (/close-bet)

## 요약

종가배팅의 **매수→오버나이트 감시→청산(익절/손절/강제)→손익**을 broker-web 신규
`/close-bet` 페이지에서 본다. 상태는 broker 신규 `GET /close-bet/positions`(close_bet_orders
조회)로, 실시간 손익은 기존 broker `GET /quotes`(ka10095) 3초 폴링으로 채운다.
후보·점수·사유는 기존 `/watchlist`가 담당 → close-bet은 **매매 결과**에만 집중(중복 회피).

확정 결정(사용자):
- **API 위치 = broker(:8001) 로컬전용.** 내 주문/손익은 매매 데이터라 공개표면(api:8000) 대신
  로컬전용 broker가 보안경계상 안전. broker가 watchlist.sqlite3를 RO로 읽는 연결을 신규 추가.
- **페이지 = 신규 `/close-bet` 전용.** 매수→감시→청산 생애주기를 한 화면에. 기존 페이지 불간섭.
- **watchlist와 역할 분담(중복 제거):** watchlist=후보 분석(점수·사유·일봉, "왜 주목"),
  close-bet=매매 결과("샀나·얼마 벌었나"). close-bet은 후보 풀 전체를 나열하지 않고
  **매수 확정분만** 표시. 점수·사유는 들고 오지 않고 `/watchlist/{code}`로 **링크**.

---

## 중복 분석 (왜 별 페이지가 정당한가)

| 항목 | watchlist | close-bet |
| --- | --- | --- |
| 후보 종목·LLM 점수·사유·근거 | ✅ 풍부 | ✗ (링크로 위임) |
| 일봉 차트 | ✅ | ✗ |
| 매수 체결가(cntr_price) | ✗ | ✅ |
| 청산 생애주기(감시→tp/sl/forced) | ✗ | ✅ |
| 실시간 손익률 / 확정 pnl | ✗ | ✅ |

겹치는 건 "후보+점수"뿐 → watchlist 영역. close-bet 고유가치(매수가·청산·손익)는
watchlist에 전무. 따라서 분업이지 중복 아님.

---

## 데이터 소스 (현황)

- `close_bet_orders` @ `etl/db/watchlist.sqlite3` — 매수(date·ticker·score·cntr_price·status·order_no)
  + 청산(sell_status·sell_price·sell_qty·exit_reason·pnl_pct·sold_at). **노출 API 0.**
- 종목명(name)은 close_bet_orders에 없음 → 필요 시 `llm_scores`(같은 DB) 또는 ticker만 표기.
- 실시간 시세 = broker `GET /quotes?codes=`(ka10095, routers/quotes.py) **이미 존재**.
  PLAN_CLOSE_BET_EXIT가 *"broker-web 표시도 /quotes 공유"*로 이미 설계 명시.

---

## 백엔드 — broker 신규

1. **watchlist DB RO 연결**: broker는 현재 watchlist.sqlite3를 읽지 않음. RO sqlite 커넥션 헬퍼
   추가(경로 `etl/db/watchlist.sqlite3`, 환경변수로 override 가능하게). 동시읽기라 RO·짧은 커넥션.
2. **`routers/close_bet.py` — `GET /close-bet/positions`** (3분류 반환):
   - `today_buys`: `date = 오늘` — ticker·score·cntr_price·status·order_no (오늘 매수분)
   - `watching`: `date < 오늘 AND status='confirmed' AND sell_status IS NULL` — date·ticker·score·cntr_price·qty
     (오버나이트 감시대상 = 청산 워커 watch set과 동일 기준)
   - `history`: `sell_status='filled'` — date·ticker·cntr_price·sell_price·exit_reason·pnl_pct·sold_at
3. `main.py` 라우터 등록 + 단위테스트(`broker/test_close_bet_router.py`): 3분류 SQL·빈 DB·정렬.

> 청산 워커(run_close_bet_exit)의 `load_unsold_positions` 기준과 `watching` 분류를 **동일**하게
> 맞춰, UI가 "지금 워커가 감시 중인 것"과 일치하게 한다.

---

## 프론트 — broker-web 신규

4. **`app/close-bet/page.tsx`** — 3섹션:
   - **오늘 매수(T일)**: ticker·score·매수가·status. 후보 나열 아님(매수 확정분만). 종목 → `/watchlist/{code}` 링크.
   - **감시중(T+1 오버나이트)**: cntr_price + **실시간 buy_bid(/quotes 3초 폴링)** → 손익률
     `buy_bid/cntr_price − 1` 계산, +5%(tp)·−3%(sl) 근접도 표시. 워커 안 떠 있어도 현재 손익 보임.
   - **청산 이력**: sold_at·exit_reason(tp/sl/forced)·pnl_pct.
5. 네비게이션 링크 추가. broker 호출은 기존 trading 페이지의 broker fetch 패턴 재사용(미확인2).

---

## 재사용 자산

broker `GET /quotes`(ka10095, routers/quotes.py + 2s TTL 캐시), close_bet_orders 스키마,
`run_close_bet_exit.load_unsold_positions` 기준(watching 분류 정합), broker-web 기존 fetch 헬퍼·
레이아웃·`/watchlist/{code}` 라우트(링크 대상).

---

## 실행 게이트 (각 게이트 후 보고→확인→다음)

- **G1. broker `/close-bet/positions`**: watchlist DB RO 연결 + 라우터 + 단위테스트.
  broker `up` 후 `curl /close-bet/positions` → today_buys/watching/history 실데이터 실측
  (현 DB: 6/22 매수 3건, 6/19 다스코, 6/18 청산완료 2건 등으로 3분류 확인).
- **G2. `/close-bet` 페이지(상태 표시)**: positions 소비해 3섹션 렌더. `next build && next start`로 검증
  (next dev 금지). 점수·사유 watchlist 링크 동작.
- **G3. 실시간 손익(/quotes 폴링)**: 감시중 섹션 3초 폴링 결합, 손익률·tp/sl 근접 표시 실측.

---

## 미확인

1. **broker→watchlist.sqlite3 경로/동시성**: etl이 쓰는 동안 broker RO 읽기. SQLite 동시 RO 안전하나
   docker 환경선 두 컨테이너 간 DB 볼륨 공유 필요(리스크 참조). 경로는 env override.
2. **broker-web의 broker 호출 패턴**: trading/page.tsx가 broker(:8001)를 부르는 헬퍼(brokerGet 등)
   존재 여부 G2 착수 시 확인 후 재사용.
3. **종목명 표시**: ticker만 vs llm_scores join. 1차는 ticker, 필요 시 join(같은 DB라 쉬움).

---

## 리스크

- **DB 경로 결합**: broker가 etl 디렉토리의 watchlist.sqlite3를 참조 → 로컬은 무방하나 docker화 시
  볼륨 공유/경로 매핑 필요(PLAN_DOCKERIZE_TENANT와 정합 확인). env로 경로 분리.
- **표시 손익 ≠ 워커 판정**: UI 손익은 /quotes buy_bid로 표시용 재계산. 워커 실제 발동과 폴링 시점차로
  미세 괴리 가능 → "표시는 참고치, 실청산은 워커 기준" 면책. 같은 ka10095라 괴리는 폴링 간격 내.
- **공개 노출 금지**: broker는 로컬전용 경계 유지. /close-bet API도 broker에 두므로 외부 노출 금지 원칙 그대로.
