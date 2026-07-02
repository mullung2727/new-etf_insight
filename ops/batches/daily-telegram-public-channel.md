# daily-telegram-public-channel

## 목적

Telegram 공개 채널(`t.me/s/<channel>`) 원문을 수집해 SQLite에 누적하고, 증권사 종목
리포트 링크가 있는 채널은 PDF를 종목별로 다운로드한다. Hermes/OpenClaw 없이 Windows
Task Scheduler로 실행한다.

## 대상 채널 / 처리규칙

- source of truth: `etl/scripts/telegram_channels.json`
  - `companyreport`: 증권사 종목 리포트. `attachments` 설정으로 PDF 다운로드.
  - `butler_works`: 예시(수집만, 첨부 없음).
- 채널 추가/제거·패턴 변경은 이 json만 수정한다. runner ps1은 건드리지 않는다.
- 스케줄(시간/간격)은 이 문서/registry cron에서만 정한다. json에는 넣지 않는다.

## 스케줄

- 기본: 매일 18:00 KST (당일 KST 리포트 대상)
- 리포트는 당일 게시되므로 대상 일자는 **당일**.
- 시간단위로 받고 싶으면 registry cron을 `0 * * * *` 등으로 바꾼다. 스크립트는
  post_id 멱등 + PDF 파일 존재 스킵이라 하루 여러 번 실행해도 중복 없음(코드 무변경).

## 실행

```text
ops/scheduled-tasks/run-telegram-public-channel.ps1
  → etl/.venv/Scripts/python.exe scripts/run_telegram_channels.py --date <당일 KST>
  → config 전체 채널 순회: collect(crawl+upsert) + attachments 있으면 PDF 다운로드
```

- PDF 저장: `etl/exports/telegram/stock_reports/<종목명>_<종목코드>/<일자>_<postid>.pdf`
- 원문 DB: `etl/db/telegram_public.sqlite3` (git 제외)

## 성공 기준

- exit code 0 + 채널별 HTTP/파싱 성공.
- `fetched=0`은 실패 아님(글 없는 날 가능). 보고 메시지에 명시.
- 한 채널 실패는 다른 채널 수집을 막지 않는다(러너가 채널 단위로 오류 격리, 마지막에 실패 보고 후 exit 1).

## 실패 알림

- `send_report_messages.py --best-effort`로 Discord 웹훅 보고(기존 runner 패턴).
- webhook env: `DISCORD_WEBHOOK_URL`.

## 검증

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```

```bash
# 수동 단발 실행(개발용, uv)
uv run python etl/scripts/run_telegram_channels.py --date 2026-07-01 --channel companyreport
```
