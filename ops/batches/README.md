# Project Batch Jobs

This directory keeps the project-owned source of truth for production batch jobs.

Windows Task Scheduler is the primary executor for production batches, so the
jobs keep running even when the OpenClaw gateway is down. OpenClaw cron specs
are kept as legacy/fallback metadata for diagnostics and manual recovery.

- `openclaw-cron.registry.json` — job name, id, schedule, timeout, delivery,
  instruction file, and Windows Task binding.
- `*.md` — execution steps, verification, and reporting rules for each job.
- `../scheduled-tasks/*.ps1` — direct batch runners.
- `../scheduled-tasks/*.xml` — Windows Task Scheduler definitions.

When schedule, timeout, delivery, or an instruction-file binding changes, update
`openclaw-cron.registry.json` first and then export/register the Windows task
definitions from that file. Keep OpenClaw cron synchronized only if you still
want the legacy fallback jobs.

## File reading

All files in this directory are UTF-8. OpenClaw agents should read these
instruction files with a platform-neutral file reader, such as OpenClaw
`file_fetch` or the agent's native workspace file access. Do not use shell
commands that depend on the host default encoding, especially Windows
PowerShell 5.1 `Get-Content` without an explicit UTF-8 setting, to read batch
instructions or Korean report bodies.

If Korean text appears corrupted while reading a batch instruction or report
body, stop using that read result and reread the file through a UTF-8-safe
reader before composing the Discord report.

## Jobs

- `daily-etf-watchlist-krx-ohlcv.md`
  - Purpose: fetch previous-day KRX full-market OHLCV only.
- `daily-etf-watchlist-intraday-kiwoom.md`
  - Purpose: build same-day Kiwoom candidates and write D+1 open-rise probability scores to `llm_scores` (feeds the 15:19 close-bet order window).
- `daily-new-etf-insight-batch.md`
  - Purpose: run the ETF daily insight pipeline and sync DuckDB.
- `daily-close-bet-order.md`
  - Purpose: report the Windows Task Scheduler 15:19 close-bet order result.
- `daily-trading-result.md`
  - Purpose: report both strategies' actual filled sells, fees, tax, and broker net realized P/L after the exit workers stop.

See `openclaw-cron.registry.json` for the active schedules, Windows task
bindings, and Discord webhook env key. Do not duplicate schedules in this README.

## Registry checks

Validate the project registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```

Export desired OpenClaw cron specs from the registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-OpenClawCronSpecs.ps1
```

Export Windows Task Scheduler registration specs from the registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-WindowsScheduledTaskSpecs.ps1
```

Export registration commands only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-WindowsScheduledTaskSpecs.ps1 -CommandsOnly
```

Export one job:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-OpenClawCronSpecs.ps1 -JobName daily-new-etf-insight-batch
```

## Windows Task Scheduler execution

Production execution does not rely on OpenClaw. Windows Task Scheduler runs the
project scripts directly and the scripts report through `DISCORD_WEBHOOK_URL`.

- `\new-etf_insight\daily-new-etf-insight-batch` — daily 07:00,
  `ops/scheduled-tasks/run-new-etf-insight-batch.ps1`.
- `\new-etf_insight\daily-etf-watchlist-krx-ohlcv` — Tue-Sat 08:00,
  `ops/scheduled-tasks/run-krx-ohlcv.ps1`.
- `\new-etf_insight\daily-etf-watchlist-intraday-kiwoom` — Mon-Fri 14:59 시작, 15:00 스냅샷,
  `ops/scheduled-tasks/run-watchlist-intraday.ps1`.
- `\new-etf_insight\daily-close-bet-order-report` — Mon-Fri 15:21,
  `ops/scheduled-tasks/run-close-bet-order-report.ps1`.
- `\new-etf_insight\daily-trading-result` — Mon-Fri 16:20,
  `ops/scheduled-tasks/run-daily-trading-result.ps1` (눌림목·종가베팅 실제 매도 통합 보고).
- `\OpenClaw\close-bet-order` — Mon-Fri 15:19, `etl/scripts/run_close_bet.py`
  (defined in `ops/scheduled-tasks/close-bet-order.xml`).
- `\OpenClaw\close-bet-verify` — Mon-Fri 16:00, `etl/scripts/run_verify.py`
  (defined in `ops/scheduled-tasks/close-bet-verify.xml`).

The 15:21 report task is report-only and must not place or retry orders.

## 자동매매 전략과 Windows 작업 매핑

눌림목과 종가베팅은 동시에 운영하는 **서로 다른 전략**이다. 작업 이름을 보고
`신규`/`구형` 전략으로 부르지 말고, 아래 전략명으로 구분한다.

### 눌림목 (`lower_low_bullish_reversal`)

| 단계 | Windows 작업 | 시각 | 실행 파일 |
| --- | --- | --- | --- |
| 청산 감시 | `\OpenClaw\trading-exit` | 평일 08:50 | `run-trading-exit.ps1` |
| 매수 | `\OpenClaw\trading-order` | 평일 15:19 | `run-trading-order.ps1` |
| 체결 검증 | `\OpenClaw\trading-verify` | 평일 16:00 | `run-trading-verify.ps1` |

- XML 원본은 `ops/scheduled-tasks/trading-*.xml`이다.
- 청산 워커는 08:50에 시작해 15:25까지 동작한다.

### 종가베팅 (`close_bet`)

| 단계 | Windows 작업 | 시각 | 실행 파일 |
| --- | --- | --- | --- |
| 청산 감시 | `\OpenClaw\close-bet-exit` | 평일 08:50 | `run-close-bet-exit.ps1` |
| 매수 | `\OpenClaw\close-bet-order` | 평일 15:19 | `run-close-bet-order.ps1` |
| 강제청산 백스톱 | `\OpenClaw\close-bet-force-exit` | 평일 15:19:30 | `run-close-bet-force-exit.ps1` |
| 체결 검증 | `\OpenClaw\close-bet-verify` | 평일 16:00 | `run-close-bet-verify.ps1` |
| 주문 결과 보고 | `\new-etf_insight\daily-close-bet-order-report` | 평일 15:21 | `run-close-bet-order-report.ps1` |

- 15:21 작업은 보고 전용이며 주문을 넣거나 재시도하면 안 된다.
- 두 전략의 매수 작업은 모두 15:19에 실행되므로 주문 가능 현금을 함께 사용한다.
- 로그는 `etl/logs`에 저장한다.

### 기대 상태와 실제 등록 상태 구분

- 운영 의도: 눌림목과 종가베팅을 모두 실행한다.
- 전략·단계·작업 매핑은 이 문서를 기준으로 한다.
- 활성화 여부, 마지막/다음 실행 시각, `Last Result`는 변경 가능한 운영 상태이므로
  분석 시 **실제 Windows Task Scheduler 등록값을 조회**한다. XML이나 이 문서만으로
  현재 활성 상태를 단정하지 않는다.
- 2026-08-10 조회 스냅샷: 눌림목 3개 작업은 활성화. 종가베팅은 매수 작업과
  15:21 보고 작업만 활성화되어 있고 청산 감시·강제청산·체결 검증은 비활성화.
  이 줄은 당시 상태 기록이며 현재 상태의 권위값이 아니다.

### 워커 프로세스 확인 시 주의 (python.exe 2개는 정상)

`run_pullback_exit.py` 워커가 뜨면 `python.exe` 프로세스가 **부모-자식 2개**로
보인다. 중복 실행이 아니라 uv venv 구조상 항상 이렇게 뜬다.

| 프로세스 | ExecutablePath | 스레드 | 역할 |
| --- | --- | --- | --- |
| 부모 | `etl/.venv/Scripts/python.exe` | 1 | uv 트램폴린(껍데기). 자식 종료까지 대기만 |
| 자식 | `C:\Python314\python.exe`(base 인터프리터) | 다수 | 실제 폴링·매도 판단·주문 |

- uv가 만든 venv의 `python.exe`는 실행파일 복사본이 아니라 **트램폴린**이라,
  실제 base 인터프리터를 자식 프로세스로 재실행한다. 두 프로세스는 명령줄이
  동일하고 시작시각이 수십 ms 차이다.
- **실제 워커는 자식(스레드 여러 개, broker :8001로 TCP 연결 보유)** 하나뿐이다.
  중복 매도 위험 없음. 둘 중 하나를 kill해서 "정리"하려 하지 말 것.
- 워커를 식별·kill할 때는 base 인터프리터 경로(`C:\Python314\python.exe`) PID나
  스레드 많은 쪽을 기준으로 삼는다.
