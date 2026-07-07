# daily-telegram-session (morning / close / evening)

## 목적

`feed_role=discovery_source` 텔레그램 채널 당일 원문을 크로스채널로 재조합해
종목(ticker) 단위로 탐색→LLM 분석한 뒤, 요약 메시지를 Discord(telegram_report)로 전송한다.
하루 3세션(아침/종가/저녁)으로 돌려 같은 거래일의 종목 부각 흐름을 누적한다.
채널단위 요약(구 `summarize_telegram_public`)은 이 크로스채널 방식으로 대체됨.

## 파이프라인

```text
ops/scheduled-tasks/run-telegram-session.ps1 -Session <morning|close|evening>
  → run_telegram_channels.py --date $target           원문 scrape (post_id 멱등)
  → run_telegram_pipeline.py --date $target --session $Session --channel telegram_report
      1) discover_telegram_stock_candidates.py   원문 스캔 → 종목후보 upsert (규칙기반, analysis NULL)
      2) telegram_langgraph/telegram_analysis_langgraph.py   후보 LLM 분석 → analysis 채움
      3) send_telegram_stock_digest.py           telegram_stock_insights 읽기 → 요약 → notify(Discord)
```

- 대상 채널: `etl/scripts/telegram_channels.json`의 `feed_role=discovery_source`.
- 결과 테이블: `telegram_public.sqlite3` / `telegram_stock_insights`
  (키 `(date_kst, session, ticker)`, 세션별 신규→지속 이력 누적).
- 수집·분석·전송이 한 러너에 묶여 세션마다 자립 실행(선행 배치 전제 없음).

## 스케줄 (하루 3회)

| Windows Task | 시각(KST) | session | 대상 date_kst |
|---|---|---|---|
| daily-telegram-session-morning | 10:00 | morning | 당일 |
| daily-telegram-session-close   | 16:00 | close   | 당일 |
| daily-telegram-session-evening | 24:00 (00:00) | evening | 전일 |

**날짜 귀속:** 러너가 `$target = (Get-Date).AddHours(-3)` 로 계산 — 10/16시는 당일,
자정(00:00)은 방금 끝난 전일에 귀속. 세션 분기 없이 한 식으로 통일하며, 세 세션이
같은 거래일 date_kst 를 공유해 insights `(date_kst, session)` 이력이 하루 단위로 이어진다.
(하위 스크립트는 전부 `--date` 를 인자로 받으므로 이 한 값이 체인 전체를 정한다.)

## 전송

- `--channel telegram_report` → notify → `send_telegram_report` → Discord 웹훅
  (`.env`의 `TELEGRAM_REPORT_TO_DISCORD_WEBHOOK_URL`). 진입점 load_dotenv 필수.
- 분석 종목 0이면 전송 스킵(글 없는 세션/후보 없음 정상).
- LLM 프로바이더: `.env`의 `ETF_LLM_PROVIDER`. 키 없으면 analyze 단계 실패 → 실패 보고.

## 성공 기준

- exit code 0. collect + discover/analyze/digest 통과.
- 분석 종목 0 전송 스킵은 실패 아님.

## 실패 알림

- `send_report_messages.py --best-effort` Discord 웹훅(기존 러너 패턴).

## 검증

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```

```bash
# 수동 단발(개발용, uv). LLM 비용 발생 주의. cwd=etl. 세션 인자만 다르게.
uv run python scripts/run_telegram_channels.py --date 2026-07-06
uv run python scripts/run_telegram_pipeline.py --date 2026-07-06 --session close --dry-run
# 세션 러너 통째 검증(실제 수집+LLM+전송):
powershell -File .\ops\scheduled-tasks\run-telegram-session.ps1 -Session close
```
