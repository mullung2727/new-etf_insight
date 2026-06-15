# daily-close-bet-order

## 실행 방식

**Windows 작업 스케줄러**가 직접 실행. OpenClaw cron은 결과 보고만 담당.

- 스케줄 정의: `ops/scheduled-tasks/close-bet-order.xml`
- 파라미터 변경 또는 스케줄 변경 시 XML 수정 후 재등록:
  ```powershell
  $xml = Get-Content ops\scheduled-tasks\close-bet-order.xml -Raw -Encoding UTF8
  Register-ScheduledTask -Xml $xml -TaskName "close-bet-order" -TaskPath "\OpenClaw\" -Force
  ```
- 로그: `etl/logs/close-bet-YYYYMMDD.log`

## Purpose

평일 15:19에 `llm_scores`에서 score >= 80 종목을 score DESC / 최대 5개로 선별해
broker(`http://localhost:8001`)를 통해 시장가 1주 매수. 결과를 `close_bet_orders`와
`kiwoom_trade_history`에 기록.

15:10 scoring 배치(`daily-etf-watchlist-intraday-kiwoom`)가 먼저 완료되어 있어야 한다.
precondition 체크는 스크립트 내부에서 수행 — llm_scores 0건이면 자동 abort.

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\ops\scheduled-tasks\close-bet-order.xml`

## 결과 확인 (DB-First)

로그 확인:
```powershell
Get-Content C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\logs\close-bet-<YYYYMMDD>.log
```

DB 확인:
```powershell
@'
import duckdb
from datetime import datetime
import zoneinfo
date = datetime.now(zoneinfo.ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
con = duckdb.connect(r"C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\db\watchlist.duckdb", read_only=True)
rows = con.execute(
    f"SELECT ticker, score, status, order_no, message FROM close_bet_orders WHERE date='{date}' ORDER BY score DESC"
).fetchall()
print(f"총 {len(rows)}건")
for r in rows: print(r)
con.close()
'@ | C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\.venv\Scripts\python.exe -
```

로그나 DB 중 하나라도 확인 후 보고. stdout 요약만으로 보고 금지.

## Discord Report

```
[종가배팅] {date} 주문 결과
```

각 종목:
- 종목명 `(ticker)`, score, 현재가
- 주문 결과: submitted / skipped / failed
- 주문번호 / 실패 사유

합계: 시도 N건, 성공 N건, 스킵 N건, 실패 N건.
