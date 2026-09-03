---
name: new-etf-insight-daily-trading-result
description: Use when creating, running, changing, or verifying the new_etf_insight daily pullback and close-bet realized-result report, including historical-date reruns and the 16:20:00 Windows Scheduled Task.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [new-etf-insight, trading, reporting, windows-task-scheduler]
    related_skills: [new-etf-insight-batch]
---

# 일일 매매 결과 통합 보고

## Overview

눌림목과 종가베팅의 당일 실제 매도 체결 결과를 하나로 합산해 보고하는 프로젝트 전용 절차다.
보고기는 주문·취소·정정·청산을 실행하지 않고 기존 운영 원장을 읽기 전용으로 조회한다.

프로젝트 루트:

```text
C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight
```

Python 실행 디렉터리:

```text
C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl
```

## When to Use

- 오늘 또는 과거 날짜의 눌림목·종가베팅 최종 손익을 조회하거나 전송할 때
- 일일 매매 결과 메시지 형식이나 계산식을 변경할 때
- `daily-trading-result` Windows 예약 작업을 등록·검증·복구할 때
- 실제 비용·세금·순손익 누락 여부를 점검할 때

종목 분석, 주문 실행, 청산 재시도에는 사용하지 않는다.

## Source of Truth

운영 DB:

```text
etl\db\watchlist.sqlite3
```

전략별 원장:

- 종가베팅: `close_bet_orders`
- 눌림목: `pullback_orders`

지정 KST 날짜에 다음 조건을 모두 만족하는 행만 포함한다.

```sql
sell_status = 'filled'
substr(sold_at, 1, 10) = 'YYYY-MM-DD'
```

`ordered`, `missing`, 미체결, 다른 날짜, 매수만 체결된 행은 제외한다.

키움 권위값:

- 실제 매도 수량: `sell_qty`
- 실제 수수료: `sell_cmsn`
- 실제 세금: `sell_tax`
- 실제 순실현손익: `sell_pl_won`

실제 비용 또는 순손익이 `NULL`이면 0원이나 gross 추정값으로 대체하지 않는다. 해당 합계와 수익률을 `미확정`으로 표시한다.

## Calculations

투자원금은 실제로 매도된 수량의 매수원가다.

- 종가베팅: `cntr_price × sell_qty`
- 눌림목: `buy_price × sell_qty`
- 전체 투자원금: 두 전략 투자원금의 합계
- 전체 순실현손익: 두 전략 `sell_pl_won`의 합계
- 투자원금 대비 손익률: `전체 순실현손익 ÷ 전체 투자원금 × 100`

테이블별 `pnl_pct`는 저장 단위가 다르므로 전체 수익률 계산에 사용하지 않는다.

## Output Contract

Discord에서는 Markdown 표와 종목별 장문 목록을 사용하지 않는다. 메시지 순서는 다음으로 고정한다.

1. 최종 순실현손익
2. 투자원금
3. 투자원금 대비 손익률
4. 수수료·세금
5. 종가베팅·눌림목 전략별 2줄 요약

기본 보고서에는 종목코드, 개별 매수가·매도가, 개별 청산 사유를 나열하지 않는다. 이 구조는 메시지가 Discord 1,900자 제한에 걸려 조용히 잘리는 문제도 피한다.

## Commands

### 특정 날짜 조회만 수행

`etl` 디렉터리에서 실행한다.

```powershell
.\.venv\Scripts\python.exe scripts\report_daily_trading_result.py --date 20260824
```

`--date`는 `YYYYMMDD` 또는 `YYYY-MM-DD`를 받는다. 생략하면 실행일 KST를 사용한다.

### 특정 날짜 생성 및 전송

프로젝트 루트에서 실행한다.

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ".\ops\scheduled-tasks\run-daily-trading-result.ps1" -Date 20260824
```

runner의 `-Date`를 생략하면 실행일 KST 결과를 생성한다. 전송은 `scripts\send_report_messages.py`와 `.env`의 `NOTIFY_CHANNEL` 규칙을 재사용한다.

### 테스트

`etl` 디렉터리에서 실행한다.

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_daily_trading_result tests.test_daily_trading_result_schedule tests.test_notify_guard -v
```

완료 기준: 모든 테스트가 통과하고 테스트 중 실제 알림 전송은 notify guard에 의해 차단된다.

## Scheduler

Windows Task Scheduler가 운영 권위값이다. Hermes/OpenClaw cron으로 중복 실행하지 않는다.

- Task path: `\new-etf_insight\`
- Task name: `daily-trading-result`
- 실행시각: 월~금 16:20:00 KST
- 중복 실행: `IgnoreNew`
- XML: `ops\scheduled-tasks\daily-trading-result.xml`
- runner: `ops\scheduled-tasks\run-daily-trading-result.ps1`
- registry: `ops\batches\openclaw-cron.registry.json`

registry의 cron 표현식은 16:20 메타데이터만 보유하며 `enabled=false`다. 실제 시작시각은 Windows Task XML의 `StartBoundary`가 권위값이다.

### 등록

프로젝트 루트에서 실행한다.

```powershell
Register-ScheduledTask `
  -Xml (Get-Content -Path ".\ops\scheduled-tasks\daily-trading-result.xml" -Raw -Encoding UTF8) `
  -TaskName "daily-trading-result" `
  -TaskPath "\new-etf_insight\" `
  -Force
```

### 검증

```powershell
$t = Get-ScheduledTask -TaskName "daily-trading-result" -TaskPath "\new-etf_insight\"
[xml]$x = Export-ScheduledTask -TaskName "daily-trading-result" -TaskPath "\new-etf_insight\"
$t.State
$t.Settings.Enabled
$x.Task.Triggers.CalendarTrigger.StartBoundary
$x.Task.Actions.Exec.Arguments
```

완료 기준:

- 상태가 `Ready`
- `Enabled=True`
- `StartBoundary`가 `T16:20:00`
- action이 `run-daily-trading-result.ps1`을 가리킴

일부 Windows 환경에서 `Get-ScheduledTaskInfo.NextRunTime`은 분 값을 초 위치에 반복 표시한다. 정확한 초는 `Export-ScheduledTask`의 `StartBoundary`로 검증한다.

## Change Scope

보고 형식이나 날짜 실행 기능을 수정할 때 다음 파일만 우선 검토한다.

- `etl\scripts\report_daily_trading_result.py`
- `etl\tests\test_daily_trading_result.py`
- `etl\tests\test_daily_trading_result_schedule.py`
- `ops\scheduled-tasks\run-daily-trading-result.ps1`
- `ops\scheduled-tasks\daily-trading-result.xml`
- `ops\batches\daily-trading-result.md`
- `ops\batches\openclaw-cron.registry.json`

공용 주문함수, 두 전략의 주문·청산 워커, 운영 DB 스키마, 공용 notify 구현은 구체적인 실패 증거 없이 수정하지 않는다.

## Common Pitfalls

1. **매도대금을 투자원금으로 사용** — 투자원금은 매수가와 실제 매도수량으로 계산한다.
2. **NULL을 0원으로 합산** — 실제값 누락을 숨기므로 반드시 `미확정` 처리한다.
3. **저장 `pnl_pct`를 합산** — 전략별 저장 단위가 다르므로 원금과 `sell_pl_won`으로 재계산한다.
4. **종목별 장문 보고 복원** — Discord 가독성과 길이 제한을 다시 악화시킨다.
5. **과거 날짜 전송 시 runner 날짜 누락** — `-Date YYYYMMDD`를 명시한다.
6. **Hermes cron 중복 등록** — Windows Task Scheduler만 활성화한다.
7. **보고 작업에서 주문 호출** — 보고기는 DB 읽기와 알림 전송만 수행해야 한다.

## Verification Checklist

- [ ] 지정 날짜의 `filled` 매도만 포함됨
- [ ] 투자원금이 `매수가 × sell_qty`로 계산됨
- [ ] 실제 순손익이 `sell_pl_won` 합계와 일치함
- [ ] NULL 실제값은 `미확정`으로 표시됨
- [ ] 최종 순손익·투자원금·수익률이 메시지 맨 위에 있음
- [ ] 종목별 장문 목록과 Markdown 표가 없음
- [ ] 조회 경로가 SQLite read-only이고 주문 호출이 없음
- [ ] 관련 unittest와 registry validator가 통과함
- [ ] Windows Task가 평일 16:20:00, Ready, Enabled 상태임
- [ ] 실제 전송을 했다면 `sent=True`를 확인함
