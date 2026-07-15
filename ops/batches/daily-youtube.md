# daily-youtube (01:00 KST)

## 목적

등록 유튜브 채널의 **전일(D-1) 영상 대본을 전부 수집**한 뒤,
`summary_mode=auto` 채널의 **미요약**만 LangGraph로 자동 요약한다.

- 수집: 채널 전체 (auto/manual 무관)
- 요약: `summary_mode=auto` 만 (manual은 `/youtube` 대기 탭에서 수동)

## 파이프라인

```text
ops/scheduled-tasks/run-youtube-daily.ps1
  → run_youtube_channels.py --date $target          # 전 채널 RSS+대본
  → run_youtube_analysis.py --date $target --auto-only --lookback-days 2
```

- 대상 채널: `etl/scripts/youtube_channels.json`
- DB: `etl/db/youtube_public.sqlite3`
- LLM: `.env`의 `ETF_LLM_PROVIDER` (기본 codex)

## 스케줄

| Windows Task | 시각(KST) | 대상 date_kst |
|---|---|---|
| daily-youtube | 01:00 | **어제** (`(Get-Date).AddDays(-1)`) |

lookback 2일: 전일·그제 미요약 auto 재시도(실패 회복).

## 성공 기준

- exit 0. collect 통과 + auto 요약 오류 0.
- auto 채널 0개 / 미요약 0건 → 요약 단계 0건 처리, **성공**.

## 실패 알림

- `send_report_messages.py --best-effort` (telegram_report 웹훅 패턴).

## 등록

```powershell
cd C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\ops\scheduled-tasks
Register-ScheduledTask `
  -Xml (Get-Content -Path youtube-daily.xml -Raw -Encoding UTF8) `
  -TaskName "daily-youtube" `
  -TaskPath "\new-etf_insight\" `
  -Force
```

## 수동 검증

```powershell
# cwd = etl
.\.venv\Scripts\python.exe scripts\run_youtube_channels.py --date 2026-07-11
.\.venv\Scripts\python.exe scripts\run_youtube_analysis.py --date 2026-07-11 --auto-only --lookback-days 2

# 러너 통째
powershell -File ..\ops\scheduled-tasks\run-youtube-daily.ps1
```
