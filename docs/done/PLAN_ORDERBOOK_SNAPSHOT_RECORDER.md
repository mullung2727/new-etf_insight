# PLAN — 호가창 1초 스냅샷 수집기

한 줄: 키움 실시간 웹소켓 `0D`(주식호가잔량)를 구독해, 지정한 종목들의 호가창 10단을
1초마다 1행씩 새 SQLite DB에 적재한다. 매매 로직은 건드리지 않는다.

작성 2026-09-07. 개정 2026-09-11. 구현 전 설계.

## 개정 확정 범위와 남은 결정

- **1초 저장**을 채택한다. 소켓은 호가 변동 이벤트를 계속 받고 ETL이 격자로 저장한다.
- **장초 08:45~09:15 + 당일 후보 확정 직후~15:30** 두 구간을 우선한다.
  오전 시작은 원안을 유지한다. 전체 장 08:30~15:30 확대는 실측 후 판단한다.
- 오전 대상은 **미청산 포지션 → 직전 후보 → static**의 순서 보존 합집합이다.
  오후는 산출·저장이 완료된 **당일 후보 + static**이다. 상한 초과 제외 종목을 기록한다.
- 오후는 15:00 시각만으로 시작하지 않는다. 후보 저장 완료를 확인한 뒤 등록하며,
  후보 확정·종목별 등록 완료·첫 수신 시각을 기록한다. 등록 전 호가는 소급 복원하지 않는다.
- broker 사전 솎기(P2)와 실시간 체결(0B)은 기본 범위에서 제외하고 §12.2에 채택 조건을 둔다.
- 아래 단일 구간 config·resolver·실행기록 스키마는 원안 예시다. **두 구간 설정 형태,
  오후 완료 확인 신호, 대상 변경 이력 스키마는 구현 전 확정해야 한다.**
  이 문서 수정은 실행 코드 구현이나 스케줄 등록·실측 완료를 의미하지 않는다.

---

## 0. 왜 만드나 (배경)

### 0.1 풀려는 문제

종가베팅은 T일 15:19 시장가 매수 → T+1일 09:01 시장가 매도로 운용 중이다.
백테스트 결과 이 전략의 수익은 **전부 밤사이 갭에서 나온다** — 다음 날 장중까지 들고
가면 오히려 마이너스다. 그래서 청산을 익일 개장 직후로 옮겼다. 매수 대상은 시가총액
상한과 전일 거래대금 하한으로 걸러 뽑는다.

> 표본 크기·수익률·필터 임계값 등 검증 수치는 `research/private/close_bet_overnight/`
> (gitignore)에 있다. 이 저장소는 공개이므로 여기에 옮겨 적지 않는다.

남은 질문은 **"15:19 매수와 09:01 매도의 실행비용을 어떻게 줄일까"** 다. 단, 이 수집으로 답할 수 있는
것과 없는 것을 처음부터 갈라둔다.

**A군 — 호가 기반 예상 체결비용. 이 수집으로 계산 가능.**

| # | 질문 | 계산 방법 |
|---|---|---|
| A1 | **연속매매 구간**(09:00:01 이후) 중 09:00:30 / 09:01 / 09:03 언제 시장가로 던지는 게 유리한가 | 각 시각 스냅샷의 매수호가 잔량을 위에서부터 소진시켜 가중평균 체결가 산출 |
| A2 | 우리 수량이 호가창을 몇 단계나 훑고 내려가는가 | 같은 방법. 주문 수량 대비 1단 잔량 비율 |
| A3 | 스프레드가 시각·종목별로 얼마인가 | `bid1_px` / `ask1_px` 차이 |
| A4 | 후보 확정 이후 15:19 전후 시장가 매수의 예상 비용은 얼마인가 | 매도호가 잔량 소진으로 수량별 가중평균 가격 계산. 10단 초과 수량은 계산 불가로 표시 |

> **A1에 09:00 정각은 포함되지 않는다.** 09:00 시가는 호가창을 훑어 체결되는 것이 아니라
> **단일가 경매**로 하나의 가격이 결정된다. 호가 소진 계산이 성립하지 않는 다른 메커니즘이라
> 별도 트랙으로 다룬다(§12.1). 08:45~09:00 구간의 매수/매도 호가도 연속매매용이 아니라
> 동시호가 접수 상태이므로 A1의 계산에 쓰지 않는다.

**B군 — 실제 주문의 체결 성과. 이 수집만으로는 답이 안 나온다.**

| # | 질문 | 왜 안 되는가 |
|---|---|---|
| B1 | 지정가로 걸었을 때 실제로 체결됐을까 | 호가창 스냅샷은 **대기 주문의 큐 순서**를 주지 않는다. 같은 가격에 먼저 걸린 물량 뒤에 서게 되는데, 그 큐에서 앞의 주문이 취소됐는지 체결됐는지 스냅샷 사이에서는 알 수 없다 |
| B2 | 지정가 청산이 시장가보다 나았을까 | B1이 안 풀리면 수익 비교가 성립 안 함 |

> B군은 `0B`(체결 실시간)를 추가해도 **완전히는 재현되지 않는다.** 체결 스트림은 "얼마에
> 몇 주 거래됐다"만 주고 "그 체결이 어느 대기 주문의 것이었나"는 주지 않기 때문이다.
> B군의 유일한 정확한 답은 실제로 지정가로 주문을 내보고 그 체결 기록을 남기는 것이다.
>
> **따라서 이 수집기의 목표는 A군으로 한정한다.** A군만으로도 §0.1의 원래 문제("언제
> 시장가로 던질까")는 정량적으로 결정된다. 지정가 방식 채택 여부는 A군 결과를 보고
> 별도로 판단한다.

### 0.2 왜 기존 데이터로는 못 푸나

보유 데이터는 1분봉(`etl/db/minute_bars.duckdb`, 2025-08-05~, 960종목 187만봉)과
일봉(`etl/db/krx_ohlcv.duckdb`)뿐이다.

- **1분봉은 초 단위를 못 준다.** 09:00:30 질문에 답할 수 없다.
- **1분봉은 체결가만 준다.** 호가창(아직 체결 안 된 대기 주문)이 없어서 슬리피지를
  구조적으로 계산할 수 없다.
- **소급 수집이 불가능하다.** 키움에 과거 호가창을 주는 API가 없다(§1.4). 오늘 안 쌓으면
  오늘 호가창은 영원히 없다. 이것이 지금 착수하는 유일하고 결정적인 이유다.

### 0.3 이미 확인된 것 (재측정 불필요)

실체결 60건(`close_bet_orders`의 `sell_price`·`sold_at`)을 같은 시각 1분봉과 대조한 결과:

| 항목 | 값 |
|---|---|
| 1틱 가치 (중앙) | (수치는 research/private) |
| 체결가가 해당 분봉 [저,고] 범위 안 | 58/60 |
| 봉 내 위치 (0=저가, 1=고가) 중앙 / 평균 | 0.50 / 0.44 |
| 체결가 vs 해당 분봉 시가 (중앙 / 평균) | +0.000% / +0.189% |

→ 우리 시장가 매도가 분봉 대비 체계적으로 불리한 자리에 체결되지는 **않는다**.
다만 우리 체결 자체가 그 분봉을 구성하므로 부분적 순환 논증이고, **스프레드 절대값은
이 방법으로 잴 수 없다.** 그걸 재려고 이 수집기를 만든다.

주문 크기에 대해 **지금까지 확인된 것은 여기까지다**: 09:01 1분봉 거래대금 중앙
1억 9,800만원 대비 주문 300만원은 1.52%(최악 18.4%)다.

이것은 **간접 근거일 뿐 결론이 아니다.** 1분 동안의 총 거래대금이 크다고 해서 우리가
주문을 내는 그 순간의 최우선 매수호가에 충분한 잔량이 걸려 있다는 보장이 없다. 1분 안에
거래가 많았어도 그 순간 1단 잔량이 100주뿐이면 우리 500주는 아래 단계까지 훑는다.
**순간 잔량 대비 실제 영향은 이번 수집(A2)으로 검증한다.**

---

## 1. 용어 (이 문서 전용 사전)

### 1.1 호가창 / 잔량

**호가창**은 아직 체결되지 않고 대기 중인 주문들의 목록이다. 매도 쪽은 싼 순서로,
매수 쪽은 비싼 순서로 10단씩 보여준다.

```
        매도 잔량      가격      매수 잔량
          1,200      10,300              ← 매도 2단
            800      10,200              ← 매도 1단 (최우선 매도호가)
                     10,100        500   ← 매수 1단 (최우선 매수호가)
                     10,000      1,700   ← 매수 2단
```

- **최우선 매수호가(`buy_bid`)** = 지금 시장가로 팔면 체결되는 가격. 위 예에서 10,100원.
- **최우선 매도호가(`sel_bid`)** = 지금 시장가로 사면 체결되는 가격. 위 예에서 10,200원.
- **스프레드** = 둘의 차이(100원). 시장가 매도의 비용은 대략 스프레드의 절반이다.
- **잔량** = 그 가격에 걸려 있는 주식 수. 위 예에서 500주를 시장가로 팔면 10,100원에
  전량 체결되지만, 900주를 팔면 500주는 10,100원, 나머지 400주는 10,000원에 체결된다.
  **이 계산을 하려면 잔량이 반드시 필요하다.**

### 1.2 `0B` 주식체결 — 이 수집기는 쓰지 않는다

키움 실시간 항목 코드 `0B`. **거래가 실제로 성사될 때마다** 한 건씩 오는 이벤트다.
"방금 10,100원에 300주가 거래됐다"는 사실을 알려준다. 주요 FID:

| FID | 뜻 |
|---|---|
| 20 | 체결시간 HHmmss |
| 10 | 현재가 (= 그 체결의 가격) |
| 15 | 거래량. **`+`면 매수체결, `-`면 매도체결** (누가 주도했는지) |
| 13 / 14 | 누적거래량 / 누적거래대금(백만원) |
| 27 / 28 | (최우선) 매도호가 / 매수호가 |
| 228 | 체결강도 |
| 290 | 장구분 (1:장전시간외, 2:장중, 3:장후시간외) |

**`0D`와의 차이:** `0B`는 *이미 일어난 거래*, `0D`는 *아직 안 일어난 대기 주문 상태*다.
"500주를 지금 던지면 얼마에 팔리나"는 `0D`로만 계산된다. `0B`는 "실제로 얼마에 팔렸나"의
사후 기록이다.

**이번 범위에서 제외하는 이유:** 이 수집기의 목표는 §0.1 A군(호가 기반 예상 체결비용)이고,
A군은 전부 `0D`만으로 계산된다. `0B`를 넣어도 B군(실제 주문 체결 성과)은 완전히 풀리지
않는다 — 체결 스트림은 "얼마에 몇 주 거래됐다"만 주고 그 체결이 어느 대기 주문의 것이었는지
알려주지 않기 때문이다. `0B` 제외는 A군 우선이라는 범위 결정이며, 강화학습에서 체결 흐름의 가치까지 부정하지 않는다:
이벤트라 마지막 체결 한 건의 스냅샷으로 압축하면 정보가 손실되고(체결 하나하나가 의미), 그대로 저장하면
양이 급증한다.

체결 사실 자체는 이미 1분봉과 `close_bet_orders`(실제 매도 체결가·시각)에 남는다.
나중에 A군 분석에서 "이 시각의 체결 흐름도 같이 봐야겠다"가 되면 같은 구조에 채널만
추가하는 방안을 검토한다. 체결 집계·저장 규약도 별도로 필요하다(§12.2).

### 1.3 `0D` 주식호가잔량 — 이번에 쓰는 것

키움 실시간 항목 코드 `0D`. **호가창이 바뀔 때마다** 10단 전체 상태를 통째로 보내준다.
개장 직후에는 초당 수십 번 올 수 있다.

출처: `kiwoom_api.xlsx` sheet195 (국내주식 > 실시간시세 > 주식호가잔량(0D)).

| FID | 뜻 | 비고 |
|---|---|---|
| 21 | 호가시간 | HHmmss |
| 41 ~ 50 | 매도호가 1~10단 **가격** | 부호 포함 문자열 |
| 61 ~ 70 | 매도호가 1~10단 **잔량** | 단위 1주 |
| 51 ~ 60 | 매수호가 1~10단 **가격** | 부호 포함 문자열 |
| 71 ~ 80 | 매수호가 1~10단 **잔량** | 단위 1주 |
| 121 / 125 | 매도호가총잔량 / 매수호가총잔량 | |
| 23 / 24 | 예상체결가 / 예상체결량 | |
| 291 / 292 | 예상체결가 / 예상체결량 | 동시호가 시간대 전용 |
| 128 / 138 | 순매수잔량 / 순매도잔량 | 부호 포함 |
| 621~630 / 631~640 | LP 매도/매수 잔량 1~10단 | 이번 범위 제외 |

> 주의 1: FID 번호가 단 순서와 어긋나는 구간이 있다. 매도 10단은 가격 50 / 잔량 70,
> 매수 10단은 가격 60 / 잔량 80이다. 구현 시 문서 표를 그대로 옮기고, 실측 1건으로
> 대조할 것(§9 R2).
>
> 주의 2: 예상체결 FID가 두 벌이다. 문서상 **23/24는 설명이 없고, 291/292에만
> "예상체결 시간대에서만 유효한 값"이라는 단서가 붙어 있다.** 어느 쪽이 동시호가
> (08:30~09:00, 15:20~15:30) 구간의 진짜 값인지 문서만으로 확정할 수 없다.
> → **둘 다 저장한다**(§4). 컬럼 4개 늘어날 뿐이고, 첫날 실측으로 어느 쪽이 채워지는지
> 확인한 뒤 분석 시 우선순위를 정한다(§12 U5).

### 1.4 왜 REST가 아니라 웹소켓인가

REST에도 호가창 API가 있다: `ka10004`(주식호가요청). broker에 `get_orderbook`으로 이미
뚫려 있다(`broker/routers/quotes.py:55`). 하지만 **종목당 1콜**이고, broker에는 모든
키움 호출이 통과하는 전역 유량 제한이 있다:

```python
# broker/kiwoom/client.py:31
_MIN_INTERVAL = float(os.getenv("KIWOOM_MIN_INTERVAL", "0.3"))  # 0.3s ≈ 3.3콜/s
```

| 종목 | 주기 | 로깅 부하 | + 청산워커(0.67콜/s) | 한도(3.3) 대비 |
|---|---|---|---|---|
| 3 | 3초 | 1.00 콜/s | 1.67 | 50% |
| 10 | 3초 | 3.33 콜/s | 4.00 | **120% 초과** |
| 10 | 10초 | 1.00 콜/s | 1.67 | 50% |

초과해도 HTTP 429가 아니라 `time.sleep`으로 줄을 세운다(`client.py:37-44`). 즉
**에러 없이 청산 판정이 조용히 밀린다.** 09:01 청산이 09:01:30이 될 수 있다.
이것이 REST 폴링을 폐기하는 이유다.

웹소켓 실시간은 `client.request()`를 경유하지 않으므로 이 유량 제한과 무관하다.
`broker/kiwoom/ws/manager.py`가 이미 상시 연결을 유지하고 있다.

**과거 호가창은 어떤 경로로도 받을 수 없다.** `ka10004`는 현재 스냅샷만 주고, 틱차트
(`ka10079`)는 체결가만 주며 잔량이 없다. 문서 전수 검색(공유 문자열 5,980개)에서
과거 호가 조회 API는 없었다.

### 1.5 웹소켓 등록(REG) 전문

```json
{ "trnm": "REG",
  "grp_no": "2",
  "refresh": "1",
  "data": [{ "item": ["048770", "418620"], "type": ["0D"] }] }
```

- `trnm`: `REG`(등록) / `REMOVE`(해제)
- `grp_no`: 그룹번호. 그룹 단위로 등록·해제가 관리된다.
- `refresh`: **`"1"` = 기존 등록 유지(기본값), `"0"` = 기존 등록 해지**
- [키움 공식 실시간 명세](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom_docs/실시간시세.md) 기준. 초안은 반대로 설명했다. 별도 그룹의 해제 범위와 00 보존은 실측으로 검증한다.
- `item`: 종목코드 배열. 거래소 지정 가능 — `KRX:039490` / `NXT:039490_NX` / `SOR:039490_AL`
- `type`: 실시간 항목 코드 배열

**문서에 없는 것:** 실시간 등록 가능 종목 수 한도, REST 초당 호출 한도. 공유 문자열
전수 검색에서 유량제한·TPS·등록한도 언급이 없었다. `_MIN_INTERVAL=0.3`은 모의투자
서버를 보고 임의로 잡은 보수적 값이다. 등록 한도는 실측으로 확인한다(§12 U1).

---

## 2. 확정 결정

각 항목 = 그걸 구현하는 파일·함수를 지목한다.

| # | 결정 | 구현 위치 |
|---|---|---|
| D1 | 수집원은 웹소켓 `0D`. REST `ka10004` 폴링은 쓰지 않는다 | `broker/kiwoom/ws/manager.py` |
| D2 | 저장은 1초 간격 스냅샷 1행. 원본 이벤트는 저장하지 않는다 | `run_orderbook_recorder.py:_flush_loop` |
| D3 | 저장소는 신규 `etl/db/orderbook.sqlite3` (SQLite, WAL) | `orderbook_store.py:ensure_schema` |
| D4 | 기록 주체는 ETL 워커. broker는 구독·중계만 한다 | `etl/scripts/run_orderbook_recorder.py` |
| D5 | 대상 종목은 `resolve_symbols(cfg, date)` 한 함수가 정한다. mode 값으로 교체 | `orderbook_symbols.py:resolve_symbols` |
| D6 | broker에 구독 제어 REST 2개를 추가한다 | `broker/routers/realtime.py` |
| D7 | SSE에 채널 필터 `?ch=` 를 추가한다 | `broker/routers/events.py` |
| D8 | `parse_message`가 종목코드(`item`)를 payload에 실어 보낸다 | `broker/kiwoom/ws/channels.py` |
| D9 | 재접속 시 `0D` 구독을 자동 복구한다 | `manager.KiwoomWSManager._subscribe` |
| D10 | `0D`는 `grp_no="2"` + `refresh="1"`. 기존 `00` 구독을 깨지 않는다 | `manager.register_orderbook` |
| D11 | 수집창·주기·종목 mode는 전부 config 파일 값 | `etl/scripts/orderbook_recorder.json` |
| D12 | 수집 실패는 매매를 막지 않는다. 로그만 남기고 워커만 죽는다 | `run_orderbook_recorder.main` |
| D13 | 거래소는 **KRX 하나만** 수집한다. `venue`는 **등록 코드(`KRX:종목`) 규약으로 확정**한다. 가격 대조로 판정하지 않는다 | `run_orderbook_recorder._ensure_subscribed` / `orderbook_store` 스키마 |
| D14 | 오전은 직전 후보와 미청산 포지션, 오후는 저장 완료된 당일 후보. 기준일과 수집 시작을 기록한다 | `orderbook_symbols.py` 각 resolver |
| D15 | 신선도 기준 시각은 **broker가 웹소켓에서 받은 시각**이다. ETL의 SSE 수신 시각이 아니다 | `manager._session` → `run_orderbook_recorder._flush_loop` |
| D16 | 스냅샷 시각은 **벽시계 고정 격자**다. 시작 기준 상대시간이 아니다 | `run_orderbook_recorder._flush_loop` |
| D17 | 구독 생명주기(등록 순서·실패·재시작·중복)를 워커가 책임진다 | `run_orderbook_recorder._ensure_subscribed` |
| D18 | `/events`는 `ch` 미지정 시 **`*`가 아니라 기본 채널 집합**을 구독한다. 큐에 상한을 둔다 | `broker/routers/events.py` |
| D19 | 실행 1회마다 `orderbook_run`에 기록을 남긴다 (수집 안 됨 / 수집됐는데 데이터 없음 구분) | `orderbook_store.start_run` / `end_run` |

### D2 보충 — 1초 저장

0D는 변동 이벤트마다 받고, ETL이 **1초마다 1행**을 저장한다.
청산 워커의 3초 폴링은 변경하지 않는다. 1초 데이터를 3초 격자로 줄여 기존 판단 주기와
비교할 수 있다. 실제 주문 시각·전송 지연까지 동일하게 재현되는 것은 아니다.

1초 사이의 잔량 소진·회복은 남지 않는다. 지정가 대기 순서·실제 체결을 완전히
재구성할 수 없으며, 강화학습에 충분한지는 별도 검증 대상이다. 용량은 §3을 따른다.

### D3 보충 — 왜 DuckDB가 아니라 SQLite인가

`minute_bars.duckdb`는 리서치 배치가 단독으로 쓰는 파일이라 DuckDB로 문제없다.
이 DB는 **장중에 워커가 1초마다 쓰는 동시에 리서치가 읽는다.** DuckDB는 단일 writer라
파일 락이 충돌한다. `wl_sqlite`의 `connect_rw` / `connect_ro`(WAL) 패턴을 그대로 쓴다.

### D4 보충 — 왜 broker가 직접 쓰지 않는가

broker에 백그라운드 태스크를 하나 더 붙여 직접 DB에 쓰는 안(`main.py`의
`_fill_sync_loop` 패턴)이 파일 수는 적다. 채택하지 않은 이유:

1. broker는 키움 게이트웨이다. 리서치 데이터 적재를 넣으면 역할이 섞인다.
2. 대상 종목이 `watchlist.sqlite3`의 `llm_scores`에서 나온다. broker가 ETL 소유 DB를
   읽게 되어 경계가 무너진다.
3. `broker/routers/events.py`의 주석이 이미 이 확장(채널 분리 SSE)을 설계 의도로 적어두고
   있다 — 그 확장점을 쓰는 쪽이 구조에 맞다.

대신 SSE 한 홉이 늘어난다. 큐가 가득 차면 이벤트가 버려진다(`event_bus.py:59-61`,
`put_nowait` + 경고 로그). 1초 스냅샷도 유실되면 편향될 수 있으므로,
전체 채널(`*`)이 아니라 `0D,system`을 구독한다(§9 R6). 지속 적체는 큐 확대만으로
해결하지 않고 §12.2의 병목 기준으로 판단한다.

### D12 보충 — 실패해도 매매는 계속된다

이 수집기는 어떤 매매 경로에도 개입하지 않는다. 워커가 죽으면 그날 호가 데이터가
비는 것이 전부다. 매수(15:19)·청산(09:01)은 별도 스케줄 작업이고 REST만 쓴다.

예외는 두 가지이고, 둘 다 §8에서 접점으로 다룬다.

1. **`0D` 등록이 기존 `00`(주문체결) 구독을 깨는 경우** — broker의 체결 자동연결
   (`main.py:_fill_sync_loop`)이 멈춘다. D10(별도 `grp_no`, `refresh="1"`)으로 막고
   R4·R5로 검증한다.
2. **SSE 팬아웃이 브라우저로 새는 경우** — `0D` 직렬화가 주문·체결 통보와 같은
   이벤트 루프에 얹힌다. D18(기본 채널 축소 + 큐 상한)로 막고 R6·R22·R23으로 검증한다.

### D13 보충 — 거래소를 하나로 고정하는 이유와 `venue` 컬럼

키움 실시간 등록은 거래소별 코드를 받는다: `KRX:039490` / `NXT:039490_NX` /
`SOR:039490_AL`(§1.5). 같은 종목이 시장별로 다른 호가창을 갖는다는 뜻이다.

- **수집은 KRX 하나로 고정한다.** 우리 청산은 시장가 1방이고, 슬리피지 분석의 기준은
  주 시장 호가창이다. 두 시장을 섞으면 같은 `(date, ticker, ts)`에 서로 다른 호가창이
  들어와 어느 쪽인지 구분할 수 없다.
- **그럼에도 `venue` 컬럼을 두고 PK에 포함한다.** 지금은 항상 `'KRX'` 한 값이지만,
  나중에 NXT를 추가할 때 PK 변경 = 테이블 재생성이 되기 때문이다. 컬럼 하나 값 하나가
  마이그레이션보다 싸다.

**거래소는 가격 비교로 증명할 수 없다. 등록 규약으로 확정한다.**

가격 대조로 시장을 판정하려던 초안은 폐기한다. 두 방향 모두 틀리기 때문이다:

- 서로 다른 시장이라도 최우선 호가가 같거나 10단 범위가 겹칠 수 있다 → **다른 시장을
  같다고 판정**한다.
- 같은 시장이라도 조회 시차(0D 수신과 `ka10004` 호출 사이)에 호가가 움직이면 어긋난다
  → **맞는데도 수집을 중단**한다.

시장을 정하는 것은 **우리가 무엇으로 등록했는가**다. 키움 REG는 거래소를 코드에
명시할 수 있다(§1.5: `KRX:039490` / `NXT:039490_NX` / `SOR:039490_AL`).

```
규약 (_ensure_subscribed):
  1. 등록은 항상 접두사를 명시한다.  item = ["KRX:048770", "KRX:418620"]
     접두사를 생략하면 어느 시장이 오는지가 규약상 미정의 상태가 된다.
  2. REG 응답 return_code == 0 → 그 코드로 등록이 수락됐다는 뜻.
     거부되면 수집하지 않고 종료한다. 접두사 없이 재시도하지 않는다
     (라벨을 붙일 수 없는 데이터를 쌓지 않기 위해).
  3. REAL 메시지의 item 을 파싱해 venue 를 정한다.
       "KRX:048770" 형태로 되돌아오면 → 그 접두사를 venue 로 기록
       "048770" 로 되돌아오면      → 등록 시 명시한 거래소를 venue 로 기록
     어느 쪽이든 근거는 "우리가 그 거래소로 등록했고 수락됐다"이다.
```

**가격 대조는 보조 점검으로만 쓴다.** 워커 시작 시 대상 1종목을
`GET /quotes/{code}/orderbook`(ka10004, REST 1콜)으로 조회해 첫 0D와 비교하고,
최우선 호가가 상대의 10단 범위를 크게 벗어나면 **경고 로그만** 남긴다.
**수집 중단 조건이 아니다** — 시차로 인한 정상적 불일치와 구분할 수 없기 때문이다.
이 로그가 반복되면 사람이 규약을 다시 확인할 신호로 쓴다.

### D14 보충 — 구간별 기준일

아래 SQL은 오전 집합 구성용이다. 오전은 미청산 → 직전 후보 → static 합집합을 쓴다.
오후는 후보 저장 완료를 확인한 뒤 `date = 수집일` 목록을 읽는다. 부분 저장된 목록을
확정 목록으로 취급하지 않는다. 완료 신호가 없으면 수집 미실행 사유를 남긴다.
후보 확정·등록 완료·첫 수신 시각의 기록 형태는 구현 전 확정한다.

**수집은 아침 08:45~09:15에 돈다. 그 시각에는 "오늘의 종가베팅 후보"가 아직 존재하지
않는다.** `llm_scores`는 장 마감 무렵 배치가 채우고(`build_watchlist.py:204`), 매수는
당일 15:19다. 아침에 감시해야 할 종목은 **어제(정확히는 직전 거래일) 매수해서 지금
들고 있는 종목**이다.

거래일 캘린더에 의존하지 않는다. 휴일·연휴·임시휴장을 따로 다룰 필요가 없도록 **데이터
자체에서 직전 날짜를 뽑는다**:

```python
# close_bet_candidates — 수집일보다 이전 중 가장 최근의 후보일
"SELECT MAX(date) FROM llm_scores WHERE date < ?"   # ? = 수집일 YYYYMMDD

# close_bet_selected — 청산 워커와 동일 규약(run_close_bet_exit.load_unsold_positions)
"SELECT ticker FROM close_bet_orders "
"WHERE status='confirmed' AND sell_status IS NULL AND date < ?"
```

- 월요일이면 `MAX(date)`가 금요일, 연휴 뒤면 연휴 전 마지막 거래일이 자동으로 잡힌다.

**단 `MAX(date)`는 휴일만 건너뛰는 게 아니다.** 후보가 0건이었던 날, `build_watchlist`
배치가 실패한 날도 똑같이 건너뛰고 **며칠 전 후보를 신선한 것처럼 집어온다.** 대응:

```
max_lookback_days  config. 기본 5 (설·추석 연휴 최대 휴장일수를 덮는 값).
  수집일 − 선택된 후보일 > 이 값이면 → 수집하지 않고 종료 + 로그.
  허용 범위 안(예: 연휴 뒤 4일 전 후보)은 정상으로 보고 그대로 쓴다.
  범위를 넘긴 후보를 "직전 거래일 후보"인 척 쓰는 것만 막는다.

source_date        선택된 후보일을 orderbook_run 에 기록한다(D19).
  나중에 "이 데이터는 어느 날 후보였나"를 되짚을 수 있어야 한다.
```

- `close_bet_selected`는 미청산 포지션 기준이라 `max_lookback_days`를 적용하지 않는다.
  며칠 묵은 포지션도 오늘 팔 대상이므로 정상이다. `source_date`에는 `NULL`을 기록한다.
- `close_bet_selected`는 날짜를 아예 안 따지고 **미청산 포지션**을 잡으므로 며칠 묵은
  포지션도 포함된다. 이게 의도다 — 오늘 아침 팔 대상 전부가 감시 대상이다.
- 이 규약은 청산 워커(`load_unsold_positions`)와 **같은 조건식**이다. 두 곳이 갈라지면
  "청산은 하는데 호가 기록은 없는" 종목이 생긴다. 변경 시 양쪽을 함께 본다(§9 R18).

### D15 보충 — 무갱신과 연결 장애를 구분한다

1초마다 "그 순간 메모리에 있는 최신 호가창"을 쓰는 구조라, **웹소켓이 끊겨도 마지막
호가창이 계속 정상값처럼 기록된다.** 09:00에 끊기면 09:15까지 09:00 호가창이 900행
복제되고, 분석에서는 "15분간 호가가 안 움직인 종목"으로 보인다. 치명적이다.

```
recv_ts        그 종목의 마지막 0D 를 broker 가 웹소켓에서 받은 시각. 컬럼으로 저장
staleness      ts - recv_ts. 저장하지 않고 분석 시 계산(파생값)
max_stale_sec  config. 기본 30. 이 값을 넘으면 그 스냅샷은 행을 쓰지 않는다
```

**`recv_ts`는 broker가 찍는다. ETL이 SSE로 받은 시각이 아니다.** SSE 큐에 밀려 있던
오래된 이벤트를 ETL이 뒤늦게 꺼내면서 "지금 받았다"고 도장을 찍으면, 30분 전 호가창도
신선한 값이 되어 D15의 목적이 통째로 무너진다.

```python
# broker/kiwoom/ws/manager.py — REAL 수신 즉시 도장을 찍어 발행한다
if trnm == "REAL":
    recv = datetime.now(KST).isoformat(timespec="milliseconds")
    for channel, values in channels.parse_message(msg):
        bus.publish(channel, {**values, "_recv_ts": recv})
```

FID 21(호가시간)도 저장하지만(`quote_tm`) 신선도 판정에는 쓰지 않는다. 거래소가 찍은
시각이라 전송 지연을 반영하지 못하고, 초 단위라 해상도도 낮다. 두 값의 차이는 분석 시
전송 지연 지표로 쓸 수 있다.

- **행이 있으면 `max_stale_sec` 이내의 신선한 값**임이 보장된다.
- **행이 없으면 그 시각 데이터가 없다**는 뜻이다(끊김·미수신·창 밖 구분 없이 "없음").
  결측이 값으로 위장하지 않는다.
- 정상적인 무갱신(호가가 안 움직임)은 `max_stale_sec` 이내라면 그대로 기록된다.
  거래대금 하한을 통과한 종목이 개장 직후 30초간 호가가 한 번도 안 바뀌는 경우는 사실상
  없으므로, 30초 초과는 곧 이상 상황이다.
- 종목 단위 신선도가 연결 상태보다 정확하다 — 연결은 살아 있는데 특정 종목만 안 오는
  경우(등록 누락)까지 잡아내기 때문이다. 다만 연결 끊김은 **재등록 트리거**라서 따로
  받아야 한다: 워커는 `system` 채널을 **함께 구독한다**(`GET /events?ch=0D,system`).
  `?ch=0D`만 구독하면 연결 상태 이벤트가 오지 않아 재등록 시점을 알 수 없다.
  `disconnected` 수신 시 로그를 남기고, `connected` 수신 시 D17의 재등록을 수행한다.

### D16 보충 — 벽시계 고정 격자

`ts`는 **절대 시각 격자**다. 워커 시작 시점 기준 상대 1초가 아니다.

```
격자 시각 = 벽시계 정수 초.  08:45:00, 08:45:01, ... , 09:14:59
window_end 는 배타적(exclusive) — 09:15:00 은 기록하지 않는다.
```

워커를 재시작해도 격자가 그대로라 종목 간·날짜 간 시각이 정렬된다. 상대 격자면
재시작 때마다 08:45:01.2, 08:45:02.2 식으로 어긋나 비교가 불가능해진다.

**미래 정보 혼입 방지 — 이게 핵심이다.** 격자 시각 `t`에 flush를 도는데, 코드 실행이
`t + 0.8초`에 일어났다면 그 0.8초 사이에 도착한 호가창이 메모리에 들어와 있다.
그것을 `ts = t`로 저장하면 **t 시점에는 알 수 없었던 정보가 t 행에 들어간다.**
분석에서 "t에 팔았으면"을 계산할 때 미래를 훔쳐보게 된다.

**종목당 값을 하나만 들고 있으면 안 된다.** 09:00:59.9에 온 호가창을 09:01:00.1에 온
것이 덮어쓰고, 그 직후 `flush(09:01:00)`이 돌면 "t 이후 도착"이라 제외되어 **그 격자에
행이 사라진다.** 09:00:59.9 값은 멀쩡히 쓸 수 있었는데 버려진 것이다.

더 나쁜 건 이 결측이 무작위가 아니라는 점이다. **호가가 활발할수록 격자 직후에 새 값이
올 확률이 높아지므로, 유동성이 좋은 종목·시각일수록 결측이 늘어난다.** 우리가 재려는
것이 바로 유동성이라 정확히 반대 방향의 편향이다.

**해법은 버퍼를 키우는 게 아니다.** 링버퍼 K개를 두면 격자 직후 이벤트가 K개 들어오는
순간 격자 직전 값이 다시 밀려나 같은 결측이 재발한다. 활발한 종목일수록 K개를 빨리
채우므로 편향의 방향도 그대로다. K를 아무리 키워도 "활발할수록 잘 사라진다"는 성질이
남는다.

규칙은 **크기가 아니라 보존**이어야 한다: *아직 기록하지 않은 격자 직전 값은 덮이지
않는다.* 종목별 슬롯 2개면 충분하고 K 같은 파라미터가 필요 없다.

```
상태: 종목별로 두 칸.  cur  = 다음 격자시각 t 이하인 값 중 최신
                       nxt  = t 를 넘어 도착한 값 중 최신

수신(v, r):
    if r <= t:  cur = v        # t 이하끼리는 최신이 낫다 → 덮어쓰기 OK
    else:       nxt = v        # t 이후 값은 cur 을 건드리지 않는다  ← 핵심

flush(t):
    for 종목 in 대상:
        v = cur[종목]
        if v is not None and t - v.recv_ts <= max_stale_sec:
            write(ts=t, recv_ts=v.recv_ts, ...)
        # 격자 전진
        t2 = t + snapshot_sec
        if nxt[종목] is not None and nxt[종목].recv_ts <= t2:
            cur[종목], nxt[종목] = nxt[종목], None
        # nxt.recv_ts > t2 이면(=flush 가 한 격자 이상 지연) 그대로 두고 다음 회차에 판정
```

`nxt`가 여러 번 덮이는 것은 문제가 없다 — 어차피 다음 격자에서는 그중 최신이 정답이다.
`cur`은 그 격자를 flush할 때까지 **어떤 이벤트로도 덮이지 않는다**. 격자 직후에 이벤트가
8개가 오든 800개가 오든 결과가 같다. R32가 그 조건(격자 이후 20건 주입)을 검증한다.

**지연 회차는 건너뛴다.** flush 실행이 다음 격자를 넘길 만큼 늦어지면, 지나간 격자를
소급 생성하지 않는다. 그 시각은 행이 없는 채로 남고, `orderbook_run`의 기대 행 수와
실제 행 수 차이로 관측된다(D19).

### D17 보충 — 구독 생명주기

**키움 WS 재접속과 broker 프로세스 재시작은 다른 사건이다.**

| 사건 | 무엇이 사라지나 | 누가 복구하나 |
|---|---|---|
| 키움 WS 끊김·재접속 | 키움 쪽 등록만 사라짐. broker 메모리의 종목 목록은 남음 | **broker** — `_subscribe`가 재접속마다 `00`과 `0D`를 함께 재등록 (D9) |
| broker 프로세스 재시작 | 메모리의 `0D` 종목 목록까지 소멸 | **ETL 워커** — 재등록 필요 |
| ETL 워커 재시작 | 워커 상태만 소멸. broker 등록은 남아 있을 수 있음 | 워커가 재등록(멱등) |

워커 규칙:

```
1. 순서    SSE 를 먼저 연결하고, 연결이 확립된 뒤에 REG 를 보낸다.
           반대로 하면 REG ~ SSE 연결 사이에 도착한 이벤트가 통째로 유실된다.

2. 확인    POST /realtime/orderbook 은 키움 REG 응답(trnm="REG", return_code)을
           기다렸다가 결과를 반환한다. 타임아웃 5초.
           실패·타임아웃이면 워커가 2초 간격 3회 재시도, 그래도 실패하면
           로그 남기고 종료한다(부분 수집 상태로 계속 돌지 않는다).

3. 복구    system 채널의 connected 이벤트를 받으면 무조건 재등록한다.
           broker 재시작인지 키움 재접속인지 구분하지 않는다 — REG 는 멱등이라
           이미 등록돼 있어도 해가 없고, 구분하려다 틀리는 쪽이 위험하다.

4. 해제    정상·비정상 종료 모두 finally 에서 DELETE /realtime/orderbook 을 보낸다.

5. 이전    시작 시 REG 앞에 REMOVE 를 먼저 보내 grp_no="2" 를 통째로 비운다.
   목록    refresh="1" 은 "추가 등록"이라 새 목록으로 REG 해도 이전 종목이 남는다.
   제거    (초안의 "새 목록으로 덮어쓴다"는 설명은 틀렸다.) 남은 종목은 계속 0D 를
           보내와 broker 이벤트 루프에 불필요한 부하를 준다.
              REMOVE(grp_no="2")  →  REG(grp_no="2", refresh="1", 새 목록)

   추가    장중 대상 추가에는 REMOVE를 하지 않고 신규 종목만 refresh="1"로 등록한다.
           REG 요청은 직렬 처리하고 ACK 성공 후 관리 목록에 합친다. 첫 수신도 확인한다.
           재접속 시 현재 확정된 전체 목록을 복구한다.

6. 중복    DB PK 만으로는 부족하다. 워커가 2개 뜨면 서로의 REMOVE 가 상대 구독을
   실행    지워 양쪽 다 데이터가 비는 구간이 생긴다(5번 때문에).
   방지    OS 가 프로세스 종료 시 자동 해제하는 락을 쓴다.

              import msvcrt
              fd = os.open(ETL_DB / "orderbook.lock", os.O_CREAT | os.O_RDWR)
              try:
                  msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)   # 비차단
              except OSError:
                  → 이미 실행 중. 즉시 종료(0행, orderbook_run 기록 없음)

           강제 종료(Ctrl+C, kill, 전원)로 finally 가 안 돌아도 **OS 가 핸들을 닫으며
           락이 풀린다.** 그래서 같은 수집창 안의 재시작이 막히지 않고, 고아 락을
           mtime 으로 추측할 필요도 없다.
           (파일을 O_EXCL 로 만들고 finally 에서 지우는 방식은 폐기했다 — 강제 종료
            시 락이 남아 재시작을 막고, 그렇다고 오래된 락을 무시하면 살아 있는
            워커와 중복 실행된다. 어느 쪽으로 정해도 틀린다.)

           덧: 스케줄 작업 XML 에 <MultipleInstancesPolicy>IgnoreNew</...> 를 함께 둔다.
               락은 수동 실행까지 막고, XML 정책은 스케줄러 레벨에서 한 번 더 막는다.
           시작 시 GET /realtime/orderbook 으로 현재 등록 목록을 로그에 남긴다.
```

> `msvcrt`는 Windows 전용이다. 이 워커는 Windows 스케줄 작업으로만 돈다(§7). 다른 OS로
> 옮길 일이 생기면 `fcntl.flock`으로 바꾸면 되고, 두 API 모두 프로세스 종료 시 자동
> 해제라는 성질은 같다.

### D18 보충 — SSE 팬아웃이 프론트로 새는 것을 막는다

**이건 §8에서 "매매와의 유일한 접점은 `00` 구독"이라고 적었던 것의 정정이다.**
현재 `/events`는 `bus.subscribe("*", queue)`로 **모든 채널**을 받고
(`broker/routers/events.py:23`), 브라우저는 파라미터 없이 그 엔드포인트를 연다
(`broker-web/lib/use-broker-events.tsx:27`). 큐도 `asyncio.Queue()` — **maxsize 없음**.

즉 `0D`를 발행하기 시작하면 **아무 조치 없이도 초당 수십 건 × N종목이 열려 있는 모든
브라우저 탭으로 흘러간다.** JSON 직렬화 비용과 큐 메모리가 broker의 이벤트 루프
(주문·체결 통보와 같은 루프)에 얹힌다. 이것이 실제 접점이다.

```python
# routers/events.py
_DEFAULT_CHANNELS = ("system", "00")   # ch 미지정 시. 예전 "*" 대신
_QUEUE_MAX = 2000                      # 소비가 느린 구독자에서 무한 증가 방지

queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
for ch in (parse_ch(request) or _DEFAULT_CHANNELS):
    bus.subscribe(ch, queue)
```

- **프론트 코드는 고치지 않는다.** 현재 실제로 발행되는 채널이 `system`과 `00`뿐이므로,
  기본 집합을 그 둘로 두면 브라우저가 받는 데이터는 지금과 동일하다(§9 R6).
- `0D`는 **명시적으로 `?ch=0D,system`을 요청한 구독자에게만** 간다. 워커만 그렇게 한다.
- 큐 상한 초과분은 `event_bus.py:61`이 경고 로그를 남기고 버린다. 1초 스냅샷 목적상
  몇 건 유실은 허용되지만, 로그가 나면 상한을 올릴 신호다.

### D19 보충 — 실행 기록

아래는 원안 스키마다. 두 구간·대상 변경에 필요한 구간, 후보 확정 시각, 종목별 등록 완료·
첫 수신·제외 사유의 기록 형태는 구현 전 확정한다. 기대 행 수는 종목별 실제 수집 구간으로 계산한다.

D15에 따라 "행이 없음"은 여러 원인을 가진다: 수집기가 아예 안 돌았다 / 돌았지만 그
종목 데이터가 안 왔다 / 오래된 값이라 버렸다. 이걸 구분할 수 있어야 한다.

```sql
CREATE TABLE IF NOT EXISTS orderbook_run (
  run_id      INTEGER PRIMARY KEY AUTOINCREMENT,   -- 실행 1회 = 1행
  date        TEXT NOT NULL,      -- YYYYMMDD 수집일
  started_at  TEXT NOT NULL,      -- ISO. 워커 시작
  ended_at    TEXT,               -- ISO. NULL 이면 비정상 종료(또는 진행 중)
  mode        TEXT NOT NULL,      -- symbols.mode
  source_date TEXT,               -- 후보 원천 날짜 (D14). selected 모드는 NULL
  venue       TEXT,               -- 등록 규약으로 확정된 거래소 (D13). 등록 실패 시 NULL
  symbols     TEXT NOT NULL,      -- 콤마 구분 대상 종목
  rows_written INTEGER,           -- 이 실행이 기록한 행 수
  note        TEXT                -- 종료 사유 / 오류 메시지
);
CREATE INDEX IF NOT EXISTS ix_orderbook_run_date ON orderbook_run(date);
```

**`date`를 PK로 두지 않는다.** 하루 1행으로 덮어쓰면 "08:45 실행이 REG 실패로 죽고
08:50에 재실행해 정상 수집"한 이력이 사라져, 앞 실패를 영원히 못 본다. 같은 날 설정을
바꿔 두 번 돌린 경우도 구분되지 않는다.

- 실행별 1행이라 `rows_written`은 **그 실행이 쓴 행 수**다(누적 아님).
- 하루 요약이 필요하면 `WHERE date=?`로 집계한다. 그 날의 총 행 수는
  `orderbook_snapshot`을 세는 게 정확하다 — 여러 실행이 같은 격자를 `INSERT OR REPLACE`로
  덮었을 수 있어 `SUM(rows_written)`과 다를 수 있다.
- 진행 중인 실행은 `ended_at IS NULL`로 구분된다. D17-6의 파일 락과 함께
  "지금 도는 중인지"를 판단하는 데 쓴다.

---

## 3. 데이터 규모

대상 종목 수는 원래 작다. 최근 71거래일 `llm_scores` 후보 수:

```
중앙 4   평균 4.0   90분위 6   최대 8
```

1초 저장·연 245거래일·행당 220 B라는 기존 가정을 사용한 추정치다.
두 구간은 오후를 15:00부터 확보한 경우이며 후보 확정이 늦으면 줄어든다.

| 구간 | 종목당 행/일 | 8종목 행/일 | 8종목 연간 | 20종목 연간 |
|---|---:|---:|---:|---:|
| 오전 08:45~09:15 | 1,800 | 14,400 | 약 0.78 GB | 약 1.94 GB |
| 오전 + 오후 15:00~15:30 | 3,600 | 28,800 | 약 1.55 GB | 약 3.88 GB |
| 전체 장 08:30~15:30 (미채택 비교안) | 25,200 | 201,600 | 약 10.87 GB | 약 27.17 GB |

GB는 10억 바이트 기준. 오전·오후 종목 수가 다르면 구간별 계산 후 합산한다.
220 B/행은 실측값이 아니므로 SQLite 페이지·인덱스·WAL 크기는 첫 수집 후 측정한다.
신선도 초과·미수신·늦은 등록·지연 격자는 저장하지 않아 실제 행 수가 감소한다.
`symbols.max=20`은 설정 상한이며 키움 등록 한도를 확인한 값이 아니다.

---

## 4. 스키마

`etl/db/orderbook.sqlite3`

```sql
CREATE TABLE IF NOT EXISTS orderbook_snapshot (
  date     TEXT NOT NULL,          -- YYYYMMDD (KST)
  ticker   TEXT NOT NULL,          -- 6자리 종목코드 (접두 A / 거래소 접미사 제거 후)
  venue    TEXT NOT NULL,          -- 거래소. 등록 코드 규약으로 확정된 값 (D13)
  ts       TEXT NOT NULL,          -- HH:MM:SS. 벽시계 고정 격자 시각 (D16)
  recv_ts  TEXT NOT NULL,          -- HH:MM:SS.mmm. broker 가 그 0D 를 받은 시각 (D15)
  quote_tm TEXT,                   -- FID 21 호가시간 HHmmss (키움 기준). ts와 다를 수 있음

  ask1_px INTEGER, ask2_px INTEGER, ask3_px INTEGER, ask4_px INTEGER, ask5_px INTEGER,
  ask6_px INTEGER, ask7_px INTEGER, ask8_px INTEGER, ask9_px INTEGER, ask10_px INTEGER,
  ask1_qty INTEGER, ask2_qty INTEGER, ask3_qty INTEGER, ask4_qty INTEGER, ask5_qty INTEGER,
  ask6_qty INTEGER, ask7_qty INTEGER, ask8_qty INTEGER, ask9_qty INTEGER, ask10_qty INTEGER,

  bid1_px INTEGER, bid2_px INTEGER, bid3_px INTEGER, bid4_px INTEGER, bid5_px INTEGER,
  bid6_px INTEGER, bid7_px INTEGER, bid8_px INTEGER, bid9_px INTEGER, bid10_px INTEGER,
  bid1_qty INTEGER, bid2_qty INTEGER, bid3_qty INTEGER, bid4_qty INTEGER, bid5_qty INTEGER,
  bid6_qty INTEGER, bid7_qty INTEGER, bid8_qty INTEGER, bid9_qty INTEGER, bid10_qty INTEGER,

  ask_total_qty INTEGER,           -- FID 121 매도호가총잔량
  bid_total_qty INTEGER,           -- FID 125 매수호가총잔량

  -- 예상체결 2벌을 모두 저장한다. 어느 쪽이 동시호가 구간의 유효값인지 문서로
  -- 확정할 수 없어 실측 후 판단한다 (§1.3 주의 2, §12 U5).
  exp_px     INTEGER,              -- FID 23  예상체결가
  exp_qty    INTEGER,              -- FID 24  예상체결량
  exp_px_ca  INTEGER,              -- FID 291 예상체결가 (문서상 "예상체결 시간대 전용")
  exp_qty_ca INTEGER,              -- FID 292 예상체결량

  PRIMARY KEY (date, ticker, venue, ts)
);
```

- **가로형(스냅샷 1개 = 1행)** 을 쓴다. 단수가 항상 10으로 고정이라 세로형(단별 20행)은
  PK 인덱스가 키를 20번 복제해 용량이 10배가 된다. 학습·분석 시에도 상태 벡터 한 줄이
  한 행이라 다루기 쉽다.
- 가격은 부호 포함 문자열로 오므로 **`abs(int(...))`로 정규화**한다
  (`broker/kiwoom/quotes.py:_abs_int`와 동일 규칙).
- 값이 없는 필드는 `NULL`. 특히 장 시작 전에는 잔량이 0이거나 호가가 비어 있을 수 있다.
- 1초 동안 갱신이 없어 직전과 동일한 스냅샷이어도 **`max_stale_sec` 이내라면** 그대로
  기록한다(중복 제거 안 함). forward-fill 없이 시각별 상태를 바로 읽는 쪽이 낫다.
  단 `max_stale_sec`를 넘긴 값은 기록하지 않는다 — 연결 장애를 정상 호가로 위장시키지
  않기 위해서다(D15).
- 재실행·중복 수신에 대비해 삽입은 `INSERT OR REPLACE`를 쓴다(§9 R16).

---

## 5. 대상 종목 선정 (유동적)

**요구사항: 거래대금이 터진 종목으로 고정하지 않는다. 선정 기준을 나중에 자유롭게
바꿀 수 있어야 한다.**

`etl/scripts/orderbook_symbols.py`

아래 resolver는 **오전 합집합을 구성하는 기존 함수 예시**다. 오후의 당일 조회·완료 확인은 별도로 확정한다.

**오전 기준일 규약(D14):** 인자 `date`는 **수집일**(오늘 아침)이다. 각 resolver는 그 날짜를
그대로 쓰지 않고 **직전 데이터가 있는 날**로 되짚는다. 아침 08:45에는 오늘 후보가 아직
없기 때문이다. 거래일 캘린더는 쓰지 않는다 — 데이터에서 직접 뽑으면 휴일·연휴·임시휴장이
자동으로 처리된다.

```python
# mode 이름 → (date) -> list[str] 함수. 새 기준 추가 = 함수 1개 + 이 dict에 1줄.
# date 는 언제나 "수집일"이며, 되짚기는 각 resolver 안에서 한다.
_RESOLVERS: dict[str, Callable[[str], list[str]]] = {
    # 수집일 직전의 가장 최근 후보일 전체.
    #   SELECT MAX(date) FROM llm_scores WHERE date < :date
    # 시총·거래대금 필터는 적용하지 않는다 (탈락 종목도 비교군으로 필요).
    "close_bet_candidates": _candidates,

    # 아직 안 판 매수분 전부. 날짜를 특정하지 않고 미청산 상태로 잡는다.
    #   WHERE status='confirmed' AND sell_status IS NULL AND date < :date
    # run_close_bet_exit.load_unsold_positions 와 동일 조건식이어야 한다.
    "close_bet_selected":   _selected,

    # 현재 잔고 보유 종목 (broker GET /account/balance)
    "holdings":             _holdings,

    "static":               lambda _d: [],  # config 의 static 목록만 사용
}

def resolve_symbols(cfg: dict, date: str) -> list[str]:
    """mode 로 뽑은 종목 + config static 종목의 합집합. 상한 max 로 자른다.

    - 순서: mode 결과(원래 순서) 뒤에 static 중 미포함분을 붙인다.
    - 중복 제거는 순서를 보존한다.
    - max 초과분은 뒤에서 자른다(= static 이 먼저 잘린다).
    - 알 수 없는 mode 는 ValueError. 조용히 빈 목록을 반환하지 않는다.
    """
```

- **`close_bet_candidates`는 오전 합집합의 후보 구성 요소다.** 시총·거래대금 필터를 **적용하지 않은** 후보
  전체를 쓴다. 필터에서 탈락한 종목의 호가창도 비교군으로 필요하기 때문이다
  (예: "거래대금 하한에 걸려 탈락한 종목은 실제로 호가가 얼마나 얇았나").
- `close_bet_selected`는 **날짜를 안 따지고 미청산 포지션을 잡으므로** 며칠 묵은 포지션도
  들어온다. 이게 의도다 — 오늘 아침 팔 대상 전부가 감시 대상이어야 한다.
- 두 mode의 조건식이 청산 워커와 갈라지면 "청산은 하는데 호가 기록이 없는" 종목이
  생긴다. 변경 시 `run_close_bet_exit.load_unsold_positions`와 함께 본다(§9 R18).
- `static`은 mode와 무관하게 **항상 합집합으로 더해진다.** 특정 종목을 계속 관찰하고
  싶을 때 mode를 바꾸지 않고 추가할 수 있다.
- 새 기준(예: "거래대금 상위 20", "테마 편입 종목")이 필요하면 함수 하나 + dict 한 줄이다.
  이 함수 밖의 어떤 코드도 종목 선정을 알지 못한다.

`etl/scripts/orderbook_recorder.json` (신규, `close_bet.json`과 별개 파일)

아래는 오전 단일 구간의 설정 예시다. 두 구간·오전 합집합 설정 형태는 구현 전 확정한다.

```json
{
  "enabled": true,
  "window_start": "08:45:00",
  "window_end": "09:15:00",
  "snapshot_sec": 1,
  "max_stale_sec": 30,
  "venue": "KRX",
  "reg_timeout_sec": 5,
  "reg_retry": 3,
  "symbols": {
    "mode": "close_bet_candidates",
    "static": [],
    "max": 20,
    "max_lookback_days": 5
  }
}
```

로더 `etl/scripts/orderbook_recorder_config.py`는 `close_bet_config.py`와 동일한 규약을
따른다: 파일 없으면 `DEFAULTS` 폴백, 키 누락 시 해당 키만 폴백, 범위 위반은 `ValueError`로
배치를 중단시킨다. 시각은 `close_bet_config._is_hms`와 같은 `HH:MM:SS` 검증을 쓴다.

> `close_bet.json`에 넣지 않는 이유: 그 파일은 매매 파라미터 단일 소스이고
> `/admin/settings` 화면과 묶여 있다. 리서치 수집 설정을 섞으면 매매값 편집 화면에
> 무관한 항목이 뜨고, 잘못 저장했을 때 매매에 영향이 간다.

---

## 6. 구조와 데이터 흐름

```
                     키움 실시간 WS (wss://api.kiwoom.com:10000)
                                  │  0D 메시지 (호가 바뀔 때마다)
                                  ▼
  ┌─────────────────────────── broker (:8001) ───────────────────────────┐
  │  ws/manager.py   REG 전송 / REAL 수신                                 │
  │        │ parse_message() → (channel="0D", {item, values})            │
  │        ▼                                                             │
  │  ws/event_bus.py   publish("0D", payload)                            │
  │        │                                                             │
  │        ├─→ routers/events.py   GET /events?ch=0D  (SSE)              │
  │        └─→ (기존) main.py _fill_sync_loop 는 "00" 만 구독 — 무관       │
  │                                                                      │
  │  routers/realtime.py                                                 │
  │     POST   /realtime/orderbook  {codes:[...]}  → manager REG          │
  │     DELETE /realtime/orderbook                 → manager REMOVE       │
  └──────────────────────────────────────────────────────────────────────┘
                                  │ SSE
                                  ▼
  ┌────────────── etl/scripts/run_orderbook_recorder.py ─────────────────┐
  │  1) resolve_symbols(cfg, date)   대상 종목 + source_date (D14)       │
  │  2) orderbook_run 시작 기록                              (D19)       │
  │  3) SSE GET /events?ch=0D,system 먼저 연결               (D17-1)     │
  │  4) POST /realtime/orderbook     REG 응답 확인, 실패 시 재시도(D17-2)│
  │  5) 거래소 검증  첫 0D  vs  ka10004 1콜                  (D13)       │
  │  6) 수신 태스크   latest[ticker] = (호가창, _recv_ts)                │
  │     ├ system/connected 수신 → 4)로 재등록               (D17-3)     │
  │     └ system/disconnected 수신 → 로그                                │
  │  7) 격자 타이머   벽시계 1초 배수마다 flush              (D16)       │
  │       recv_ts > 격자시각        → 제외 (미래 정보 방지)              │
  │       격자시각 - recv_ts > 30s  → 제외 (D15 신선도)                  │
  │  8) window_end 도달 → finally: DELETE + orderbook_run 종료 기록      │
  └──────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                      etl/db/orderbook.sqlite3
                        orderbook_snapshot / orderbook_run
```

핵심 두 가지:

1. **수신과 저장을 분리한다.** 수신 태스크는 종목별 두 칸(`cur`/`nxt`)만 갱신하고,
   별도 격자 타이머가 `cur`을 모아 배치 INSERT한다. 초당 수십 건이 와도 DB 쓰기는
   1초에 1회다. **격자 직전 값(`cur`)은 그 격자를 flush할 때까지 덮이지 않는다** —
   격자 이후 도착분은 `nxt`로 들어가 다음 회차에 쓰인다(D16).
2. **시각 도장은 broker가 찍는다.** ETL은 `_recv_ts`를 만들지 않고 전달받은 값을 그대로
   쓴다. 그래야 SSE 큐 지연이 신선도 판정을 속이지 못한다(D15).

---

## 7. 변경 목록 (파일 단위)

### 신규

| 파일 | 내용 |
|---|---|
| `broker/routers/realtime.py` | `POST` / `DELETE /realtime/orderbook`. 등록 종목 목록 조회 `GET`도 포함 |
| `etl/scripts/run_orderbook_recorder.py` | 워커 본체. SSE 수신 + 1초 flush |
| `etl/scripts/orderbook_store.py` | 스키마 생성, `insert_snapshots(con, rows)`, FID → 컬럼 매핑 |
| `etl/scripts/orderbook_symbols.py` | `resolve_symbols` + mode별 resolver |
| `etl/scripts/orderbook_recorder_config.py` | config 로더 + 검증 |
| `etl/scripts/orderbook_recorder.json` | config 값 |
| `ops/scheduled-tasks/run-orderbook-recorder.ps1` | 런처. **UTF-8 BOM 필수**(한글 포함 시 PS5.1이 CP949로 오독) |
| `ops/scheduled-tasks/orderbook-recorder.xml` | 스케줄 작업. 오전 트리거 08:44. 오후 완료 신호와의 연결 방식은 구현 전 확정. `<MultipleInstancesPolicy>IgnoreNew` 포함(D17-6) |
| `etl/tests/test_orderbook_*.py` | §9 매핑대로 |

### 수정

| 파일 | 변경 | 주의 |
|---|---|---|
| `broker/kiwoom/ws/channels.py` | `parse_message`가 `item`을 payload에 포함 | `00` 채널 payload에 키가 하나 늘어난다. 소비자(`main.py:_fill_sync_loop`의 `payload["913"]`, broker-web SSE)는 키를 조회만 하므로 무해 — 그래도 R5로 고정 |
| `broker/kiwoom/ws/manager.py` | ① `register_orderbook(codes)` / `unregister_orderbook()` 추가(현재 소켓 참조 보관, REG 응답 대기) ② 재접속 시 `_subscribe`에서 `0D` 재등록 ③ **REAL 수신 즉시 `_recv_ts` 도장** | `grp_no="2"`, `refresh="1"` 고정. `_recv_ts`는 여기서만 찍는다 |
| `broker/routers/events.py` | `?ch=` 쿼리로 구독 채널 지정. **미지정 시 `*`가 아니라 `("system","00")`**. 큐 `maxsize=2000` | `*`를 그대로 두면 `0D`가 모든 브라우저 탭으로 샌다(§8 접점 ②) |
| `broker/main.py` | `realtime` 라우터 등록 | |
| `etl/tests/...` · `broker/test_*.py` | 신규 테스트 | R6·R22·R23은 broker 쪽 테스트다 |

### `parse_message` 변경 형태

```python
# 현재 (channels.py:20-24) — item 을 버린다
for entry in raw.get("data", []):
    channel = entry.get("type")
    values = entry.get("values")
    if channel and isinstance(values, dict):
        out.append((channel, values))

# 변경 — 종목코드를 payload 에 실어 보낸다.
# FID 키는 전부 숫자 문자열이라 "item" 과 충돌하지 않는다.
        out.append((channel, {**values, "item": entry.get("item", "")}))
```

---

## 8. 기존 매매와의 접점 — 검증 대상

| 대상 | 이 변경과의 접점 | 근거 |
|---|---|---|
| 종가베팅 매수 (`run_close_bet.py`) | 없음 | REST `/orders/strategy`만 사용. WS 미사용 |
| 종가베팅 청산 (`run_close_bet_exit.py`) | 없음 | REST `/quotes`, `/orders/*`만 사용 |
| 눌림목 (`run_pullback_order.py`, `run_pullback_exit.py`) | 없음 | REST만 사용. 별도 config·별도 스케줄 작업·별도 테이블 |
| 키움 REST 유량 | 거의 없음 | WS는 `client.request()`를 경유하지 않아 `_MIN_INTERVAL` 카운터와 무관. 단 D13 거래소 검증이 실행당 `ka10004` **1콜**을 쓴다 |
| broker 체결 자동연결 (`main.py:_fill_sync_loop`) | **접점 ①** | `bus.subscribe("00")`. `0D` 등록이 `00` 등록을 해지시키면 멈춘다 |
| broker 이벤트 루프 · 열려 있는 브라우저 탭 | **접점 ②** | 아래 참조 |
| broker-web SSE payload | `item` / `_recv_ts` 키 추가 | 기존 소비자는 특정 키만 조회. `/events` 무파라미터 호출 결과 불변(D18) |
| `watchlist.sqlite3` | 읽기 전용 | `resolve_symbols`가 `llm_scores` / `close_bet_orders`를 `connect_ro`로 읽기만 |

### 접점 ① — `00` 구독 파괴

`_subscribe`는 `refresh:"1"`(기존 유지)로 `00`을 등록한다.
0D도 유지 등록과 별도 그룹을 사용한다. 실제 해제 범위와 00 보존은 R4·R5 및 U3로
검증한다. 전송 전문 mock만으로 실제 구독 안정성이 증명되는 것은 아니다.

### 접점 ② — SSE 팬아웃 (초기 문서에서 빠졌던 것)

**"매매와의 유일한 접점은 `00` 구독"이라고 적었던 것은 틀렸다.** 실제 코드는:

```python
broker/routers/events.py:23        bus.subscribe("*", queue)      # 모든 채널
broker/routers/events.py:22        queue = asyncio.Queue()        # maxsize 없음
broker-web/lib/use-broker-events.tsx:27
                                   new EventSource(`${brokerBase()}/events`)  # 파라미터 없음
```

`0D` 발행을 시작하는 순간, 아무 조치가 없으면 **초당 수십 건 × N종목이 열려 있는 모든
브라우저 탭으로 전송된다.** JSON 직렬화와 큐 메모리가 주문·체결 통보와 **같은 이벤트
루프**에 얹히므로, 이것은 성능 접점이자 매매 접점이다.

대응은 D18: `/events`의 기본 구독을 `*` → `("system", "00")`으로 좁히고 큐에 상한을 둔다.
현재 실제 발행 채널이 그 둘뿐이라 **프론트는 고치지 않아도 받는 데이터가 동일**하다.
검증은 R6(기본 집합)과 R22(0D 미노출), R23(큐 상한)이다.

---

## 9. 요구사항 ↔ 테스트 1:1

| # | 요구사항 | 테스트 |
|---|---|---|
| R1 | 여러 종목의 `0D`를 받아 종목별로 구분해 저장한다 | `parse_message`에 2종목 `0D` 메시지 투입 → 각 payload에 `item`이 실려 나오는지 |
| R2 | FID → 컬럼 매핑이 문서와 일치한다 (특히 10단: 매도 가격 50/잔량 70, 매수 가격 60/잔량 80) | 40개 FID를 서로 다른 값으로 채운 `values` → 매핑 결과가 기대 dict와 완전 일치 |
| R3 | 부호 포함 가격 문자열(`"+264500"`, `"-1200"`)을 절댓값 정수로 정규화한다 | 부호 섞인 입력 → 전부 양의 int |
| R4 | `0D` 등록이 기존 `00` 등록을 해지하지 않는다 | `register_orderbook` 호출 시 전송 전문이 `grp_no != "1"` 이고 `refresh == "1"` 인지 (전송 소켓 mock) |
| R5 | payload에 `item`이 추가돼도 체결 자동연결이 동작한다 | `item` 포함 `00` payload로 `_fill_sync_loop` 판정(`payload["913"] == "체결"`)이 그대로 통과 |
| R6 | SSE는 `?ch=` 지정 시 그 채널들만, 미지정 시 **기본 집합(`system`,`00`)** 을 준다 | 라우터 테스트 2건: 파라미터 유무별 `bus.subscribe` 호출 인자 |
| R7 | 스냅샷은 `snapshot_sec` 간격으로만 기록된다. 이벤트가 100건 와도 그 창에 1행이다 | 가짜 시계로 1초 창에 이벤트 100건 투입 → 종목당 1행 |
| R8 | 갱신이 없어도 `max_stale_sec` 이내면 직전 값으로 기록된다 | 1종목만 갱신, 2종목 구독, 경과 10초(`max_stale_sec`=30) → 2행 |
| R9 | 아직 한 번도 데이터가 안 온 종목은 기록하지 않는다 | 구독만 하고 이벤트 0건 → 0행 |
| R10 | `resolve_symbols`가 mode 결과 + static을 순서 보존 합집합으로 주고 max로 자른다 | mode 3건 + static 2건(1건 중복) + max=3 → 기대 목록과 정확히 일치 |
| R11 | 알 수 없는 mode는 조용히 넘어가지 않는다 | `mode="nope"` → `ValueError` |
| R12 | config 키 누락은 기본값 폴백, 범위 위반은 `ValueError` | `close_bet_config` 테스트와 동일 형태 (시각 형식, `snapshot_sec > 0`, `max > 0`) |
| R13 | `window_end` 도달 시 구독을 해제하고 종료한다 | 가짜 시계로 종료 시각 통과 → `DELETE /realtime/orderbook` 호출됨 + 루프 종료 |
| R14 | 재접속 후 `0D` 구독이 복구된다 | 세션 재시작 시뮬레이션 → `_subscribe`가 `00`과 `0D`를 모두 전송 |
| R15 | 수집기가 죽어도 매매 스크립트는 영향받지 않는다 | 정적 검사: `run_close_bet*.py` / `run_pullback*.py`가 `orderbook_*` 를 import하지 않음 |
| R16 | 같은 (date, ticker, venue, ts)를 두 번 넣어도 깨지지 않는다 | 동일 키 2회 INSERT → `INSERT OR REPLACE` 로 1행 유지 |
| R17 | **오래된 값은 행을 쓰지 않는다** (연결 장애가 정상 호가로 위장하지 않는다) | 마지막 수신 후 `max_stale_sec`+1초 경과 → 그 종목 0행. 같은 창의 신선한 종목은 1행 |
| R18 | `close_bet_selected`가 청산 워커와 같은 포지션 집합을 잡는다 | 동일 픽스처 DB에 `load_unsold_positions`와 `_selected`를 각각 호출 → 종목 집합 일치. 미청산 아닌 행(`sell_status='filled'`)은 양쪽 다 제외 |
| R19 | `close_bet_candidates`가 **수집일이 아니라 직전 후보일**을 잡는다 | `llm_scores`에 D-3·D-1 두 날짜, 수집일 D → D-1 종목만. 수집일 당일 행이 있어도 무시 |
| R20 | 저장된 `venue`가 config 값과 일치하고 PK에 반영된다 | 같은 `(date,ticker,ts)`에 `venue` 다른 2행 INSERT → 2행 유지(덮어쓰지 않음) |
| R21 | 예상체결 FID 2벌이 각각 별도 컬럼으로 매핑된다 | 23/24/291/292를 서로 다른 값으로 준 `values` → 4컬럼이 각각 제 값 |
| R22 | **파라미터 없는 `/events` 구독자에게 `0D`가 가지 않는다** | `0D` 발행 후 기본 구독 큐가 비어 있는지. `00` 발행은 도착 |
| R23 | SSE 큐에 상한이 있다 | 상한+1건 발행 → 예외 없이 진행되고 경고 로그 1건 |
| R24 | 신선도 기준이 broker 수신 시각이다 | `_recv_ts`가 **31초** 과거인 payload를 **지금** 큐에서 꺼내 flush → 행 0개 (ETL 수신 시각을 썼다면 1행이 되어 실패) |
| R24b | 신선도 경계는 배타적이다 (`> max_stale_sec`만 제외) | `max_stale_sec`=30일 때 정확히 30초 → **1행 기록**. 31초 → 0행 |
| R25 | 격자 시각 이후 도착한 값은 그 격자에 안 들어간다 | `_recv_ts` > 격자시각인 payload → 그 회차 0행, 다음 격자에 1행 |
| R26 | 격자는 벽시계 고정이고 `window_end`는 배타적이다 | 가짜 시계로 08:45:01.2에 기동 → 첫 행 `ts`가 08:45:02. 09:15:00 행 없음 |
| R27 | `connected` 수신 시 재등록한다 | `system`/`connected` 주입 → `POST /realtime/orderbook` 재호출 |
| R28 | REG 실패·타임아웃 시 재시도 후 종료한다 | broker가 실패 응답 → `reg_retry`회 재시도 후 워커 종료, `orderbook_run.note`에 사유 |
| R29 | 후보일이 `max_lookback_days`를 넘으면 수집하지 않는다 | `llm_scores` 최신이 6일 전 → 0행 + `orderbook_run.note` 기록 |
| R30 | 거래소는 등록 코드로 확정된다. REG 거부 시 한 행도 쓰지 않는다 | ① REG `item`이 `KRX:` 접두사를 달고 나가는지 ② REG `return_code != 0` → 0행 + `orderbook_run.venue` = NULL ③ 가격 대조 불일치는 **경고 로그만, 수집은 계속** |
| R31 | `orderbook_run`이 **실행마다** 1행 남는다 | 같은 날 2회 실행 → 2행. 첫 행의 실패 `note`가 보존됨(덮어쓰기 아님) |
| R32 | **격자 직전 값이 늦게 온 값에 덮여 사라지지 않는다 — 개수와 무관하게** | `recv_ts`=t-0.1s 1건 수신 → `recv_ts`>t 인 이벤트 **20건** 연속 수신 → `flush(t)` → **t-0.1s 값으로 1행**. 최신값 1칸이나 크기 8 링버퍼였다면 0행이 되어 실패 |
| R32b | 격자 이후 값은 다음 격자에서 쓰인다 | R32에 이어 `flush(t+1)` → 20건 중 최신값으로 1행 |
| R33 | 시작 시 이전 등록을 제거한 뒤 등록한다 | `_ensure_subscribed` 호출 → 전송 순서가 REMOVE(grp_no=2) → REG(grp_no=2) |
| R34 | 워커 중복 실행이 차단된다 | 락을 쥔 상태에서 두 번째 기동 → 즉시 종료, 0행, `orderbook_run` 기록 없음 |
| R34b | **강제 종료된 워커의 락이 재시작을 막지 않는다** | 락을 쥔 핸들을 닫아(=프로세스 사망 모사) 재기동 → 정상 획득. mtime 휴리스틱 없이 성립해야 한다 |

---

## 10. 손 안 대는 것 + 그래도 되는 이유

| 대상 | 왜 안 건드려도 되나 |
|---|---|
| `close_bet.json` / `close_bet_config.py` | 수집기 설정은 별도 파일(§5). 매매값과 섞이면 `/admin/settings` 저장 사고가 매매에 번진다 |
| `run_close_bet_exit.py` | 수집기는 별도 프로세스. 청산 워커의 `/quotes` 3초 폴링은 그대로 둔다 — WS로 갈아타면 청산 판정 경로가 바뀌어 검증 범위가 커진다 |
| `broker/kiwoom/client.py` (`_MIN_INTERVAL`) | WS는 이 limiter를 안 거친다. 실한도를 모르는 상태에서 값을 올리면 매매 REST가 429를 맞는다 |
| `ka10004` / `get_orderbook` 라우트 | 그대로 둔다. 단건 조회용으로 이미 쓰이고 있고, 이번엔 안 쓸 뿐이다 |
| `minute_bars.duckdb` | 별개 저장소. 1분봉 분석은 계속 그대로 |
| 눌림목 전 경로 | 접점 0 (§8) |

---

## 11. 롤백

1. `orderbook_recorder.json`의 `"enabled": false` → 워커가 즉시 종료(수집만 중단, 코드 롤백 불필요)
2. 스케줄 작업 해제: `Unregister-ScheduledTask -TaskPath "\OpenClaw\" -TaskName "orderbook-recorder"`
3. broker 변경 되돌리기: `channels.py` / `manager.py` / `events.py` / `main.py` 4개 파일만 revert하면 원상복구. 신규 파일은 참조되지 않으므로 남겨둬도 무해
4. DB 파일 삭제: `etl/db/orderbook.sqlite3` (다른 어떤 코드도 참조하지 않음)

---

## 12. 리스크 / 미결

| # | 항목 | 대응 |
|---|---|---|
| U1 | **실시간 등록 가능 종목 수 한도가 문서에 없다** | 모의투자 서버(`wss://mockapi.kiwoom.com:10000`)에서 3 → 10 → 30종목 순으로 REG 하며 `return_code` 확인. 결과를 이 문서 §1.5에 추가 기록 |
| U2 | 거래소 — `KRX:` 접두사 등록이 실제로 수락되는지, REAL 응답의 `item`이 접두사를 되돌려주는지 | **가격 대조로 판정하지 않는다(D13).** 접두사를 명시해 등록하고 REG `return_code`로 확인. 거부되면 수집하지 않고 종료한다. 응답 `item` 형태는 첫날 로그로 확인해 §1.5에 기록 |
| U3 | `refresh="1"` + 별도 `grp_no`가 실제로 `00`을 보존하는지 | R4는 전문 형태만 검증한다. 첫 실운영일에 체결 자동연결이 정상인지 로그로 확인해야 함 |
| U4 | SSE 큐 포화 시 이벤트 유실 | 워커는 `0D,system`만 구독하고 큐 상한은 2000(D18). 유실은 `event_bus.py:61` 경고 로그로 관측. 유실과 적체 시간을 기록한다. 지속 적체는 상한 확대만으로 해결하지 않고 §12.2에 따라 판단한다 |
| U5 | 08:45~09:00 동시호가 구간에 `0D`가 오는지, 예상체결가가 23/24와 291/292 중 어디에 실리는지 | 둘 다 저장해 두고(§4) 첫날 실측으로 확정. 어느 한쪽만 채워지면 분석 시 그 컬럼을 쓴다 |
| U6 | 표본 축적 속도 | 하루 4~8종목. 의미 있는 분석까지 최소 1~2개월 |
| U7 | **09:00 시가 단일가를 무엇으로 평가할 것인가** | §12.1 참조. 채택 판정은 1분봉만으로 가능한 ⓪(09:00 open vs 09:01 open)이고, 이 수집기가 주는 ①②는 실행 리스크 지표다 |
| U8 | broker 이벤트 루프에 얹히는 부하 — `0D` 직렬화가 주문·체결 통보와 같은 루프에서 돈다 | D18로 브라우저 팬아웃을 차단하면 소비자는 워커 1개뿐이다. 첫 운영일에 체결 통보 지연이 없는지 로그 시각으로 확인(§13) |

### 12.1 09:00 시가 단일가 — 무엇으로 판정하는가

09:00 청산(장전 동시호가에 시장가 매도를 넣어 시초가로 체결)이 09:01보다 나은지는
아직 미결이다. **개장 전 예상체결가와 실제 시가는 같지 않다** — 동시호가 마지막
순간의 대량 주문·정정으로 크게 벌어질 수 있다. 무엇을 비교하느냐로 필요한 데이터가
달라진다.

| 비교 대상 | 필요한 데이터 | 상태 |
|---|---|---|
| ⓪ **09:00 시가 청산이 09:01 청산보다 수익이 높은가** | 1분봉 09:00 `open` vs 09:01 `open` | **이 수집기 없이 지금 당장 가능.** `minute_bars.duckdb`에 이미 있다 |
| ① 예상체결가가 08:45→09:00 동안 어떻게 움직이는가 | `0D`의 예상체결 FID | 이번 수집으로 확보 |
| ② 예상체결가가 실제 시가와 얼마나 벌어지는가 | ① + 실제 시가(1분봉 09:00 `open`) | 이번 수집으로 확보 |
| ③ 동시호가에 넣은 우리 주문이 실제로 얼마에 체결되는가 | 실제 주문 기록 | **불가.** 운용을 09:00 청산으로 바꾼 뒤에야 측정된다 |

**판정 순서는 ⓪ → ①② 이고, 채택 여부를 결정하는 것은 ⓪다.**

- **⓪이 09:00 청산의 채택 기준이다.** 두 시각의 실현 수익률 차이를 채택 표본
  (시총 상한·거래대금 하한을 통과한 표본)에서 직접 비교한다. 이 수집기와 무관하게
  1분봉만으로 지금 돌릴 수 있다.
- **①②는 채택 기준이 아니라 실행 리스크 지표다.** 예상체결가와 실제 시가가 크게
  벌어진다는 것은 "09:00에 넣는 주문의 결과를 사전에 예측하기 어렵다"는 뜻이지,
  "09:00 청산이 09:01보다 나쁘다"는 뜻이 아니다. ⓪에서 09:00이 유리하게 나왔다면
  괴리가 크더라도 채택할 수 있다 — 어차피 시장가로 넣어 시초가에 체결되기 때문이다.
  ①②는 그 경우 "얼마나 예측 불가한 방식으로 유리한가"를 알려준다.
- ③은 어떤 사전 수집으로도 답이 안 나온다(주문을 내야만 생기는 데이터). ⓪이 09:01 우위로
  나오면 ③은 시도하지 않는다.

> 이전 판본에서 "예상체결가와 실제 시가의 괴리가 1틱보다 크면 기각"이라고 적었던 것은
> **틀렸다.** 1틱 크기는 대상 종목들의 호가 단위일 뿐 09:00과 09:01의 수익 차이와 아무
> 관계가 없다. 두 서로 다른 양을 비교한 오류다.

---

### 12.2 실측과 조건부 대안

**P2 = broker가 SSE 전달 전에 호가를 솎는 방식.** 기본은 ETL의 1초 저장을 유지한다.
실측에서 SSE 직렬화·팬아웃이 병목이면 EventBus 발행 전 종목별 격자 보존 방식으로
솎는 안을 검토한다. 종목 선정·DB 저장은 ETL에 남기고 broker가 정한 snapshot_ts와
원본 recv_ts를 함께 전달한다. 큐 포화 위험은 감소할 뿐 없어지지 않는다.
원시 수신·JSON 파싱 부하가 병목이면 이 방식만으로 해결되지 않는다.

**0B = 시장에서 실제 체결된 가격·수량의 실시간 데이터.** 현재 0D 수집에는 추가하지 않는다.
강화학습에서 체결 흐름이 필요하면 구간별 체결량·거래대금·주도 방향·건수 집계를 검토한다.
마지막 체결 한 건만 저장하면 구간 흐름이 사라진다. 추가해도 개별 주문 대기 순서는 복원되지 않는다.
호가 이력에는 우리 주문에 대한 시장의 반응이 없으므로 1초 데이터만으로 주문 크기별
실제 가격충격이나 강화학습 성과가 검증됐다고 보지 않는다.

**실측:** 약 5종목 하루는 최초 판단 자료이며 운영 규모 전체의 안전 보증이 아니다.
채널 필터·큐 상한 적용 후 수집 전후를 비교한다. 거래일 실측은 아직 수행하지 않았다.

- 종목별/전체 합산 초당 0D 이벤트 수·바이트 수와 최대 폭주량.
- broker 루프 지연 p95·p99·최대, 큐 깊이·유실·SSE 전달 지연·스냅샷 결측.
- 00 수신부터 내부 체결 반영까지의 지연. sold_at만으로 전체 통보 지연을 분리하지 않는다.
- 추가·해제·재접속 후 기존 종목과 00 수신 지속, 신규 종목 첫 수신.
- 5종목 성공은 그 규모의 수용 확인일 뿐 상한 확인이 아니다. 운영 예정 규모도 검증한다.
  모의와 실전 서버 결과를 구분한다.
- 사전 기준선 대비 지속 적체·유실·지연 악화가 있으면 확대를 보류하고 원인을 확인한다.
  운영 허용 지연 수치는 현재 미확정이며 실측 없이 임의의 합격값을 정하지 않는다.

### 12.3 장마감 경계와 데이터 해석

15:30 종료는 배타적이므로 마지막 격자는 15:29:59다. 이 범위는 장마감 직전 예상체결
상태까지 수집하며 **최종 동시호가 체결 결과를 확보했다고 표시하지 않는다.**
실제 종가는 기존 KRX 일봉의 같은 종목·거래일 종가와 사후 대조한다.
15:30 이후 0D 수신까지 확보할 종료 방식은 별도 미결이다.
동시호가 상태에 연속매매의 호가 소진 계산을 적용하지 않는다.

전체 장 확대 시 장전 확정 대상이 필요하다. 전날 후보 + static 등의 집합을 사전에 고정하고
후일 선정된 종목만 골라 과거 학습 상태를 구성하는 선택 편향을 구분한다.
오후 후보 등록 전 시간대는 호가 결측으로 남긴다.

### 12.4 장후 당일 후보 틱 보존 — 후속 과제

사용자가 확인한 ka10079의 약 1개월 조회 제약을 전제로, 당일 후보 틱을 매일 보존할
가치가 있다. 호가 수집기와 별도 후속 과제로 두며 이번 개정에서 배치를 등록하지 않는다.
기존 틱 수집 경로를 우선 활용하고 REST 호출은 다른 배치·매매와 공유됨을 고려한다.
페이지 누락·재시도·중복 검증이 필요하며 같은 초·가격·수량만으로 정상 반복 체결을 지우지 않는다.
ka10079가 0B의 모든 필드·수신 시각을 대체한다고 가정하지 않는다.

## 13. 구현 후 재검토 체크리스트

- [ ] 두 구간 설정·오후 후보 완료 신호·대상 변경 이력 스키마 확정
- [ ] 오전 미청산+직전 후보+static 합집합 및 상한 초과 제외 기록 검증
- [ ] 오후 부분 저장 목록을 확정으로 읽지 않음, 완료 후 당일 후보만 등록 검증
- [ ] 장중 추가 시 REMOVE 없음, 기존 구독 지속·재접속 후 전체 집합 복구 검증
- [ ] 1초 저장 및 15:30 배타 경계, 최종 종가 사후 대조 검증
- [ ] §12.2 실측 기록과 운영 예정 종목 수 검증

- [ ] §2 확정 결정 D1~D19를 코드와 1:1 대조
- [ ] §9 R1~R34(+R24b·R32b·R34b) 전부 테스트 존재 + 통과 (`PYTHONPATH=. uv run python -m unittest`, broker는 broker/.venv)
- [ ] **D14 기준일 — mode별 기대 집합으로 대조**한다. "어제 매수분과 일치"가 아니다:
      - `close_bet_candidates`(오전 후보 구성 요소) → `orderbook_run.source_date` 날짜의 `llm_scores` **전체**와 일치. 시총·거래대금 필터 적용 전이라 실제 매수분보다 많은 게 정상
      - `close_bet_selected` → `close_bet_orders`의 미청산 종목과 일치
      - `holdings` → 잔고와 일치
- [ ] D15 — 웹소켓을 의도적으로 끊어보고 그 구간에 행이 안 쌓이는지 확인
- [ ] **D18 — 브라우저 개발자도구에서 `/events` 스트림에 `0D`가 안 섞이는지 눈으로 확인.** 매매 화면이 평소처럼 뜨는지도 함께
- [ ] D16 — 저장된 `ts`가 전부 `snapshot_sec` 배수이고 `window_end` 행이 없는지 SQL로 확인
- [ ] D19 — `orderbook_run`이 **실행 횟수만큼** 남고 `rows_written`이 그 실행의 행 수와 맞는지
- [ ] D13 — 첫날 로그에서 REG로 나간 `item` 형태와 REAL로 돌아온 `item` 형태를 확인해 §1.5에 기록
- [ ] D17 — 워커를 2개 띄워 두 번째가 락으로 즉시 종료하는지, 그리고 **첫 번째를 강제 종료(Ctrl+C가 아니라 프로세스 kill)한 뒤 재기동이 되는지**
- [ ] U8 — 수집 중 체결 통보 지연이 없는지. `close_bet_orders.sold_at`과 broker 로그 시각 대조
- [ ] §8 표의 "접점 없음" 항목을 grep으로 재확인 (특히 `orderbook_` 문자열이 매매 스크립트에 없는지)
- [ ] U1·U2·U5를 실측하고 결과를 이 문서에 반영
- [ ] 첫 운영일 다음 날: 행 수 · 결측 종목 · 스냅샷 간격 분포 확인
- [ ] `.ps1`이 UTF-8 BOM으로 저장됐는지 (한글 주석 포함 시 PS5.1 CP949 오독)
