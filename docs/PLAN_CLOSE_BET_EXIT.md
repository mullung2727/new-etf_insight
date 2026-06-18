# PLAN — 종가베팅 청산(익절/손절) 가격 감시

## 요약

T일 15:19 매수한 종목을 T+1일 **3초 폴링(REST ka10095 관심종목정보)** 으로 감시해
**체결가 대비 +5% 익절 / -3% 손절** 시장가 매도, 미발동분은 **T+1 15:19 일괄 강제청산**한다.
broker엔 "복수종목 시세 일괄조회(ka10095)" 범용 엔드포인트를 신규 추가(보유/관심 실시간 표시에도 재사용),
청산 전략·판정은 etl 장중 워커(`run_close_bet_exit.py`)에 둔다.

확정 결정(사용자):
- 트리거 = **3초 폴링** (WS 아님). 근거: 50종목·3초면 실시간급이고 stateless·자기복구로 단순/견고, 표시+청산 한 메커니즘 통합. WS의 latency 우위는 비-HFT엔 무의미. ([[project_kiwoom_realtime_0B]])
- TP/SL = **+5% / -3%** — 기준가 = **매수호가(buy_bid, ka10095)** / 체결가 `cntr_price`. (시장가 매도는 매수호가에 체결되므로 실현가 기준.)
- **발동 시간대 = 연속매매(09:00~15:20)만**. 장전 시가단일가(08:50~09:00)·종가단일가(15:20~) **TP/SL 미발동**(예상체결가 noise 회피). 강제청산 15:19도 연속매매 내.
- 강제청산 = **T+1 15:19** (오버나이트 1박만 의도). 주체 = 워커(주) + 별도 백스톱 배치(종). **기존 15:19 매수배치엔 미포함**(리스크등급 분리).
- **lock/미체결 정책**: 강제청산은 시장가 **1회 전송**(하한가·VI lock이어도 큐잉). 미체결이면 **캐리**(다음날 overnight로 자동 재감시), 무한재시도 안 함. 15:20 잔존 미체결 → Discord 알람.
- **갭 슬리피지 허용**: 오버나이트 갭으로 SL 임계 초과 체결(예 -3% 의도, -8% 실현) 단타로 수용.
- **WS는 가격감시·체결확인 모두 미사용**. 워커는 100% REST 폴링(가격=ka10095, 체결확인=ka10075/kt00007). broker WS는 기존 체결통보용 유지하나 **워커는 구독 안 함**. 조건검색 실시간·호가창은 추후 별건.
- **상태 비영속·매 기동 재부팅**: WS의 서버측 REG/`00` push 폐기 대가로, 워커 watch set은 인메모리 영속 0. 08:50뿐 아니라 **크래시·broker재시작·머신리부트 등 모든 기동마다** DB+잔고에서 재부팅(아래 워커 1번).

---

## 포지션 생애주기

```
T일  15:19   매수            → close_bet_orders (status=filled, cntr_price 체결가)
T일  체결대조 배치           → cntr_price/cntr_qty 확정 (기존)
T+1  08:50   exit 워커 기동   → 미청산 보유 로드 + 잔고 대조
T+1  장중    3초마다 폴링     → buy_bid/cntr_price 가 +5%/-3% 도달 → 시장가 매도 → 청산기록
T+1  15:19   강제청산         → 워커가 잔여 전량 시장가 (벽시계, 폴링 무관)
T+1  15:19:30 백스톱 배치     → 잔고에 아직 남은 종목만 매도(워커 정상이면 무동작·멱등)
T+1  15:25   워커 종료
```

---

## 범위 / 비범위

- **범위**: broker 복수시세 일괄조회 API(ka10095), etl exit 워커(3초 폴링), close_bet_orders 청산 스키마, Task Scheduler XML, 테스트.
- **비범위**: 며칠 홀딩/트레일링스탑, 분할매도, 실시간 조건검색(ka10173), 호가창 WS, broker-web 표시 UI(같은 ka10095 엔드포인트 소비, 별건). (필요 시 후속)

---

## 진실 소스 / 스키마

- 청산 대상 = `close_bet_orders` 중 `status=filled` AND `sell_status IS NULL` AND `date < 오늘`(오버나이트분).
- **실보유는 키움 잔고(kt00018)가 진실** → 매도 전 대조해 `min(보유수량, 기록수량)`만 매도(부분체결·괴리 방지).
- `close_bet_orders` 컬럼 추가(ALTER, 기존행 NULL):
  - `sell_order_no TEXT, sell_status TEXT, sell_price INTEGER, sell_qty INTEGER, sold_at TEXT, exit_reason TEXT, pnl_pct REAL`
  - `sell_status`: `ordered`(주문전송) → `filled`(체결확인). `exit_reason`: `tp`/`sl`/`forced`.

---

## 매도 판정

기준가 = ka10095 응답의 **`buy_bid`(매수호가)** — 시장가 매도 실현가. (`cur_prc`(현재가) 아님; `abs(int)` 파싱, 부호는 등락방향.)

- **발동 창**: TP/SL 판정은 wall-clock **09:00:00 ≤ now < 15:20:00**(연속매매)일 때만. 그 밖(장전·종가단일가) 판정 skip. 강제청산(15:19)은 이 창 안.
- 익절: `buy_bid/cntr_price - 1 >= +0.05`
- 손절: `buy_bid/cntr_price - 1 <= -0.03`
- 강제청산: 위 미발동분, T+1 `--force-exit-time`(=15:19:00) 도달 시 잔여 전량 시장가 **1회** 전송.
- **매도 상태머신(orphan 박멸)**: `NULL → ordered → filled`.
  - 전송 흐름: ① 잔고 재조회로 실보유 확인 → ② `POST /orders` 호출 → ③ **broker가 order_no 반환한 뒤에만** `sell_status='ordered', sell_order_no=...` 기록. (전송 전 crash = `NULL` 유지 = 안전 재시도. '전송 즉시 기록' 아님.)
  - 거부/취소 응답 → `sell_status` **NULL 복귀**(재감시) + Discord 경고. 'ordered' 박힌 채 감시이탈 금지.
- **중복매도 가드 2중**: (a) `sell_status='ordered'` 또는 **ka10075 미체결주문 존재**면 재주문 차단. (b) 매도 직전 kt00018 잔고 재조회, 실보유 수량만 매도. → 잔고반영 지연 race에도 (a)의 미체결주문 체크로 안전(멱등).
- 호가 공백(`buy_bid` 빈값/0)인 폴링 응답은 **TP/SL 판정만 skip**. 단 강제청산 시각엔 공백이어도 시장가 전송(lock 큐잉) → 미체결 캐리.
- **lock 감지(ka10095 내장 필드)**: `cur_prc==lst_pric` or `pred_pre_sig=="4"`(하한 lock) → SL 발동해도 시장가 미체결 예상 → 전송은 하되 캐리 전제로 로깅·알람. 별도 TR 불요.

---

## broker 신규 (범용 복수시세 일괄조회 — 전략 아님, 재사용 목표)

> **ka10095 관심종목정보요청 스펙 확정** (kiwoom_api.xlsx sheet88, xlsx-read 스킬로 추출):
> REST `POST /api/dostk/stkinfo`. 요청 `{"stk_cd": "code1|code2|..."}` — **여러 종목 `|` 구분, 1콜에 N종목**.
> 응답 `atn_stk_infr[]` 종목별: `cur_prc`(현재가), `sel_bid`(매도호가), `buy_bid`(매수호가),
> `sel_1~5th_bid`/`buy_1~5th_bid`(5단계 호가), `open/high/low/close_pric`, `flu_rt`(등락율),
> `trde_qty`(거래량) 등 — 앱 화면 데이터 한 방. 값은 부호 포함 → 가격은 `abs(int)`.

1. **tr.py**: `TR_WATCHLIST_QUOTE = "ka10095"` (관심종목정보요청 → `EP_STKINFO`) 추가.
2. **kiwoom/quotes.py**: `get_watchlist_quotes(codes: list[str]) -> list[dict]` — 코드 `|` 조인 후 ka10095 호출, `atn_stk_infr` 정규화 반환(필요 필드: stk_cd, cur_prc, buy_bid, sel_bid, flu_rt …). **종목수 상한 시 분할콜**(상한 미확인 → 아래 미확인).
3. **routers/quotes.py**: `GET /quotes?codes=A,B,C` (또는 POST body) → `get_watchlist_quotes` 결과 반환. **짧은 TTL 캐시(~2초)** 내장 → 다중 소비자(broker-web 표시 + etl 청산워커)가 동시에 3초 폴링해도 상류 ka10095 콜 중복 안 됨.

이 broker 엔드포인트(`GET /quotes`)가 **재사용 단위**다. 종가베팅 청산워커, broker-web 보유/관심
실시간 표시, 향후 알림 등 모든 "스냅샷 시세" 수요가 동일 진입점을 3초 폴링한다.
broker **WS는 손대지 않음** — 체결통보(`00`)용으로 그대로 유지.

---

## etl 신규 — `scripts/run_close_bet_exit.py` (장중 워커)

> **폴링은 시계 드리븐이라 "거래 없으면 미발동" 함정 없음** — 매 3초 무조건 ka10095
> 조회. TP/SL·강제청산 모두 같은 루프 안에서 벽시계로 처리. 시장가 매도는 가격 데이터
> 불필요(REST place_order가 호가에 체결).

**워커 = 단일 3초 폴링 루프** (WS/SSE 없음 → stateless·단순. 워커는 broker WS 미구독, 100% REST):

1. **기동(매 프로세스 시작마다 = 재부팅)**: 인메모리 영속 상태 0. watch set = `close_bet_orders`(filled, `sell_status IS NULL`, overnight) ∩ kt00018 잔고에서 **매번 재계산**. 08:50 첫 기동·크래시 재기동·broker재시작·리부트 모두 동일 수렴. **`sell_status='ordered'` 미체결 행도 회수**해 체결확인 대상에 포함(crash 직후 미체결 누락 방지).
2. 루프(3초): `GET {broker}/quotes?codes=보유종목` → 종목별 **buy_bid** 읽어 TP/SL 판정(09:00~15:20 창에서만).
3. 발동: 잔고 재조회 + ka10075 미체결 확인 → `POST {broker}/orders {side:sell, market, qty}` → **order_no 반환 후** `sell_status='ordered'` 기록 → 감시목록서 제거(단 'ordered' 행은 4의 체결확인 대상으로 유지).
4. 체결확인(**폴링, `00` 미사용**): 매 루프 'ordered' 행에 대해 ka10075 미체결 조회 → 사라지면 체결로 간주, kt00007/체결가로 `sell_status='filled'`, `sell_price`, `pnl_pct` 확정. 거부/취소 감지 시 `NULL` 복귀 + 경고.
5. **강제청산**: 매 루프 wall-clock 확인, `--force-exit-time`(=15:19:00) 도달 → 잔여 전량 시장가 1회 전송(`exit_reason='forced'`). 연속매매(~15:20) 중 즉시 체결. lock 미체결분은 캐리.
6. **broker 다운 내성**: `/quotes`·`/orders` conn-refused/5xx = transient → 다음 폴링 재시도, 워커 크래시 금지(루프 try/except). 다운 동안 TP/SL 공백은 백스톱이 최종 방어.
7. `--stop-time`(예 15:25) 후 종료. **15:20 시점 잔존 미체결 있으면 Discord 알람**. Discord 청산 요약 발송(기존 notify 재사용).

인자: `--broker-url --poll-sec 3 --tp 0.05 --sl 0.03 --force-exit-time 15:19:00 --stop-time 15:25:00 --dry-run`.

기동: Task Scheduler `close-bet-exit.xml`, 평일 08:50, ExecutionTimeLimit ~PT7H(또는 워커 자체 stop-time 종료).

### 백스톱 배치 — `scripts/run_close_bet_force_exit.py` (워커 크래시 대비)

- Task Scheduler `close-bet-force-exit.xml`, 평일 **15:19:30**(워커 강제청산 직후).
- 로직: 미청산 보유 로드 → **kt00018 잔고 AND ka10075 미체결주문 AND DB `sell_status`** 3중 대조 → in-flight 매도(잔고 미반영 or 미체결 큐) 있으면 skip, **순수 잔존분만** 시장가 매도(`exit_reason='forced'`). 워커 정상이면 무동작. (잔고 단독 판단 폐기 — 워커 15:19:00 전송 ↔ 백스톱 15:19:30 사이 체결반영 지연 이중매도 차단.)
- 짧게 1회 실행 후 종료. 기존 매수배치(`close-bet-order`)와 **별도 항목**, 상호 불간섭.

---

## 재사용 자산

`get_balance`(kt00018), `place_order(sell,market)`, `get_order_history`(kt00007, 체결대조), **미체결조회(ka10075, 폴링 체결확인·중복가드·백스톱 — 미확인7)**, `notify.send_discord`, wl_sqlite 커넥션, close_bet_orders 테이블, broker `request`(REST 래퍼).
신규 `GET /quotes`(ka10095)는 청산워커뿐 아니라 broker-web 표시·향후 알림이 공유.

---

## 실행 게이트 (TDD, 각 게이트 후 보고→확인→다음)

- **G1. 스키마 + broker `/quotes`(ka10095)**: ALTER 마이그레이션 + tr/quotes.py/라우터 + TTL 캐시. 단위테스트: 코드 `|` 조인, 응답 정규화, 분할콜, 캐시 히트. broker `up` 후 `GET /quotes?codes=005930,000660` → 실데이터(현재가·buy_bid) 실측. **+ 50코드 1콜 상한 실측**(미확인7: 잘리면 분할콜 동작 확인).
- **G2. exit 워커 판정 로직(드라이런)**: 포지션 로드·잔고대조·TP/SL 판정·강제청산 트리거 단위테스트(가짜 quote 주입). `--dry-run`으로 실 `/quotes` 3초 폴링 받아 판정만, 주문 미발동 실측.
- **G3. 통합 실매도(paper)**: 1종목 보유 상태에서 SL/강제청산 실제 시장가 매도 1주 → 체결확인·pnl·Discord 요약 실측. order_no 포맷·sell 체결대조 검증. 백스톱 배치 멱등(잔고0→무동작) 확인.

---

## 미확인

1. ~~시세조회 방법~~ ✅ **확정**: REST ka10095(관심종목정보), 판정 기준 `buy_bid`(매수호가). kiwoom_api.xlsx sheet88.
2. ~~강제청산 시각~~ ✅ **확정**: T+1 15:19:00(워커) + 15:19:30(백스톱).
3. **Kiwoom 서버측 조건부/stop 주문**: 자체구현 확정(결정 유지). ⚠️ 단 ka10075 응답에 `stop_pric`(스톱지정가주문 스톱가)·`sor_yn` 필드 존재 → 키움 **스톱지정가 주문 타입이 실재할 가능성**. 자체폴링이 단순·통합이라 그대로 가나, 향후 서버측 스톱 검토 시 주문 TR(kt100xx)에서 stop 주문구분 재확인 여지.
4. ~~단일가/lock/갭/체결확인/재부팅~~ ✅ **확정**(사용자): 연속매매만 발동 / lock 1회전송+캐리 / 갭 슬리피지 허용 / 체결확인 폴링(`00` 미사용) / 매 기동 재부팅. 상단 확정결정 참조.
5. ~~ka10075 미체결조회 TR~~ ✅ **확정**: `ka10075`(미체결요청, `POST /api/dostk/acnt`). 요청 `{all_stk_tp, trde_tp:"1"(매도), stk_cd?, stex_tp:"0"}`. 응답 `oso[]`: `ord_no`(주문번호), `stk_cd`, `ord_stt`(주문상태), `ord_qty`/`oso_qty`(미체결수량)/`cntr_qty`(체결량), `cntr_pric`(체결가). → 중복가드(oso에 종목 있으면 in-flight)·체결확인(oso에서 사라지면 체결)·sell_price(cntr_pric). kiwoom_api.xlsx 미체결요청(ka10075).
6. **lock/단일가 감지** ✅ **ka10095로 해결**(별도 TR 불요): `upl_pric`(상한가)·`lst_pric`(하한가)·`pred_pre_sig`(1:상한 4:하한)로 lock 판별, `exp_cntr_pric`(예상체결가)·`bid_tm`로 단일가 구간 식별. → 락 종목 정확 로깅·캐리 판단.
7. **ka10095 1콜당 종목수 상한**: ⚠️ **문서 미명시**(stk_cd Length=20은 단일코드 설명, 파이프 다중입력 캡 명기 없음). 50종목 1콜 가정하되 `get_watchlist_quotes`에 분할콜 안전판 유지 → **G1에서 실측**(50코드 1콜 정상응답 확인, 잘리면 분할).
8. **REST 초당/일일 콜 제한**: ⚠️ **문서 미명시·확인 불가**(사용자 확인). 3초 폴링(1콜/3초)+broker 2s TTL 캐시로 상류콜 최소화. 15:19 주문콜 경합은 paper 실측(G3)에서 관찰, 빠듯하면 폴링주기 상향.

---

## 리스크

- **워커 크래시 시 감시 공백**: 장중 폴링 워커가 죽으면 그 사이 TP/SL 미발동 + 강제청산 누락 → 워커 내 예외복구(루프 try/except) + **매 기동 DB+잔고 재부팅 멱등**(08:50 한정 아님) + **백스톱 배치**(15:19:30, 독립 시계)가 최종 방어. Task Scheduler restart-on-fail 권장.
- **lock/캐리 한계**: 하한가·VI lock 종목은 시장가도 미체결 → 강제청산 못 함, 다음날로 캐리(overnight 재감시). 락 풀릴 때까지 손실 노출은 물리적 한계로 수용. 15:20 알람으로 인지.
- **폴링 지연(최대 3초)**: 급락 시 SL 발동이 최대 3초 늦음. 종가베팅 단타엔 허용. 더 빠른 필요 시 `--poll-sec` 하향(rate limit 여유 내).
- **REST 콜 예산 경합**: 폴링이 15:19 주문콜과 같은 예산 사용 → 제한 빠듯하면 주문 굶길 위험. 콜제한 문서 미명시(미확인8) → G3 paper 실측으로 관찰 후 주기 조정.
- **체결가 기준 정확성**: `cntr_price`가 체결대조로 확정돼야 pnl 정확 → exit 워커 기동 전 T일 체결대조 완료 전제.
- **paper/실거래**: 현재 paper 환경. 실거래 전환 시 강제청산·중복가드 재검증.
