# daily-minute-bars-backfill

## Purpose

키움 `ka10080`으로 KRX 전 종목 1분봉을 최근 월부터 수집하고
`etl/db/minute_bars.duckdb`에 검증 후 저장한다.

## Execution

- Windows 작업: `\new-etf_insight\daily-minute-bars-backfill`, 매일 02:00.
- Python 수집기: `etl/scripts/backfill_minute_bars.py`.
- PowerShell 러너: `ops/scheduled-tasks/run-minute-bars-backfill.ps1`.
- 기본 범위: 최근 12개월, 현재 월 우선 보충 후 최근 미완료 과거 월 하나.
- 수집 제한: 210분(02:00~05:30), 작업 제한 220분(결과 저장·보고 여유 10분), API 호출 최소 간격 0.5초, 단일 프로세스·BelowNormal 우선순위.

## Completion and recovery

- `minute_fetched`의 `(ticker, scope, date)`가 완료 기준이다.
- 기존 완료 날짜와 기존 `(ticker, scope, timestamp)` 봉은 재삽입·덮어쓰기하지 않는다.
- 부분 조회는 봉만 보존하고 완료 표시하지 않아 다음 실행에서 이어받는다.
- 종목·월별 실패는 `minute_backfill_failures`에 기록한다.
- 같은 종목·월이 3번 실패하면 `blocked`로 보류한다. 원인을 확인한 뒤
  `--retry-blocked`로 수동 재검증할 수 있다.
- HTTP 429는 해당 일 실행을 즉시 끝내고 다음 날 재개한다.

## Manual checks

실행 계획만 확인:

```powershell
cd C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl
.\.venv\Scripts\python.exe scripts\backfill_minute_bars.py --dry-run
```

한 월·소수 종목 검증:

```powershell
.\.venv\Scripts\python.exe scripts\backfill_minute_bars.py --month YYYYMM --max-tickers 3 --max-runtime-min 10
```

운영 러너를 제한시간만 줄여 수동 실행:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\scheduled-tasks\run-minute-bars-backfill.ps1 -MaxRuntimeMin 105
```

결과는 `etl/logs/minute-backfill-YYYYMMDD.*`와 DB의 `minute_fetched`,
`minute_backfill_failures`에서 확인한다.
