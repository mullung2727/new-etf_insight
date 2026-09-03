# daily-trading-result

## Purpose

매 거래일 청산 워커 종료 후 눌림목과 종가베팅의 당일 실제 매도 체결 결과를 통합 보고한다.

## Guardrails

- 보고 전용이다. 주문·취소·정정·재시도하지 않는다.
- `etl/db/watchlist.sqlite3`를 읽기 전용으로 조회한다.
- KST 지정일의 `sell_status='filled'`이고 `sold_at` 날짜가 지정일인 행만 포함한다.
- `sell_cmsn`, `sell_tax`, `sell_pl_won`은 청산 워커가 저장한 키움 권위값을 사용한다.
- 실제 비용·손익이 `NULL`이면 0원이나 gross 추정값으로 대체하지 않고 `미확정`으로 보고한다.
- 체결이 0건이어도 정상 결과로 보고한다.

## 등락 원인 분석 (`--cause`)

- 매도 종목마다 공시(DART)·뉴스(구글 뉴스 RSS)·텔레그램 세 소스를 모아 LLM이 원인과
  A/B/C 등급을 판정한다. 수집·판정은 `scripts/collect_trading_result_evidence.py`다.
- 정보 차단선은 당일 16:20 KST다. 그 이후에 생긴 텔레그램·뉴스는 쓰지 않는다.
- **근거 없이 원인을 만들지 않는 것이 이 분석의 유일한 목적이다.** 세 소스가 모두 비면
  등급은 무조건 `C`(원인 불명)로 강등한다.
- `A`는 근거 참조가 반드시 있어야 하고, 그 값이 실제 수집된 공시 접수번호·뉴스 링크·
  텔레그램 post_ref 중에 있어야 한다. 없으면 `C`로 강등하고 경고를 남긴다.
- 보고문 머리에 `A n / B n / C n` 분포를 항상 찍는다. C가 계속 대부분이면 이 분석은
  쓸모가 없다는 뜻이고, A가 갑자기 늘면 근거를 부풀린다는 뜻이다.
- 매수 전 스코어링이 남긴 사전 재료 판단(`llm_catalyst_assessments`)을 같이 넣어,
  당일 등락이 **살 때 본 재료가 이어진 것인지**(`same_sustained`), **그 재료 위에
  새 재료가 붙은 것인지**(`same_plus_new`), **무관한 이유인지**(`different`)를 대조한다.
  사전 재료의 근거 링크는 스코어링 당일 자료라 프롬프트에서 뺀다. 오늘의 근거가 아니다.
- 판정 결과는 `watchlist.sqlite3` 의 `trading_result_causes` 에 남긴다. 보고문에만 찍고
  버리면 이슈별 지속성 비교를 나중에 못 한다. 같은 날 재실행하면 덮어쓴다
  (PK: 매도일·종목·전략). LLM 판정이 실패한 종목은 저장하지 않는다.
- `held_days` 는 매수일부터 매도일까지 달력 일수다. 거래일 수가 아니다. 매수일은
  종가베팅은 `close_bet_orders.date`, 눌림목은 `pullback_orders.bought_at` 이다
  (눌림목의 `watchlist_date` 는 편입일이라 실제 매수일과 다르다).
- 원인 분석이 통째로 실패해도 정산 숫자 보고는 그대로 나간다. 경고 한 줄로 대체한다.

## Execution

작업 디렉터리:

```powershell
C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl
```

결과 생성:

```powershell
.\.venv\Scripts\python.exe scripts\report_daily_trading_result.py --cause
```

원인 분석 없이 정산만:

```powershell
.\.venv\Scripts\python.exe scripts\report_daily_trading_result.py
```

근거 수집만 확인 (LLM 호출 없음, 저장 안 함):

```powershell
.\.venv\Scripts\python.exe scripts\collect_trading_result_evidence.py --date 20260828
```

판정까지 하고 `trading_result_causes` 에 저장 (보고문 전송 없음):

```powershell
.\.venv\Scripts\python.exe scripts\collect_trading_result_evidence.py --date 20260828 --grade
```

특정 일자를 다시 생성·전송:

```powershell
..\ops\scheduled-tasks\run-daily-trading-result.ps1 -Date 20260824
```

`-Date`는 `YYYYMMDD` 또는 `YYYY-MM-DD` 형식을 받는다. 생략하면 실행일 KST를 사용한다.

전송은 `scripts/send_report_messages.py`를 사용하며 채널은 `.env`의 `NOTIFY_CHANNEL` 규칙을 따른다.
Windows Task Scheduler의 실제 시작시각은 평일 16:20:00 KST다.
16:00 텔레그램 장마감 세션과 장 종료 후 공시가 모두 들어온 뒤에 실행하기 위한 시각이다.
