# daily-telegram-stock-digest

## 목적

`feed_role=discovery_source` 텔레그램 채널들의 당일 원문을 크로스채널로 재조합해
종목(ticker) 단위로 탐색→LLM 분석한 뒤, 요약 메시지를 Discord(telegram_report)로 전송한다.
채널단위 요약(구 `summarize_telegram_public`)은 이 크로스채널 방식으로 대체됨.

## 파이프라인

```text
ops/scheduled-tasks/run-telegram-stock-digest.ps1  (당일 KST, session=close)
  → run_telegram_pipeline.py  (오케스트레이션·순서·에러전파, 단위테스트 있음)
      1) discover_telegram_stock_candidates.py   원문 스캔 → 종목후보 upsert (규칙기반, analysis NULL)
      2) telegram_langgraph/telegram_analysis_langgraph.py   후보 LLM 분석 → analysis 채움
      3) send_telegram_stock_digest.py           telegram_stock_insights 읽기 → 요약 메시지 → notify
```

- 대상 채널: `etl/scripts/telegram_channels.json`의 `feed_role=discovery_source`.
- 결과 테이블: `telegram_public.sqlite3` / `telegram_stock_insights`
  (키 `(date_kst, session, ticker)`, 세션별 신규→지속 이력 누적).
- 수집(`daily-telegram-public-channel`, 18:00)이 먼저 돈 뒤 실행 전제.

## 스케줄

- 매일 18:30 KST, `session=close`. (수집 18:00 다음)
- 아침/저녁 세션도 돌리려면 registry에 job 추가(같은 러너, session 인자만 다르게)
  하거나 러너를 세션 인자화. 지금은 close 1회.

## 전송

- `send_telegram_stock_digest.py --channel telegram_report` → Discord 웹훅.
- 분석 종목 0이면 전송 스킵(글 없는 날/후보 없음 정상).
- LLM 프로바이더: `.env`의 `ETF_LLM_PROVIDER`. 키 없으면 analyze 단계 실패 → 실패 보고.

## 성공 기준

- exit code 0. discover/analyze/send 3단계 통과.
- 분석 종목 0 전송 스킵은 실패 아님.

## 실패 알림

- `send_report_messages.py --best-effort` Discord 웹훅(기존 러너 패턴).

## 검증

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```

```bash
# 수동 단발(개발용, uv). LLM 비용 발생 주의. cwd=etl.
uv run python scripts/run_telegram_pipeline.py --date 2026-07-06 --session close --dry-run
# 단계 개별 실행도 가능:
uv run python scripts/discover_telegram_stock_candidates.py --date 2026-07-06 --session close
uv run python scripts/telegram_langgraph/telegram_analysis_langgraph.py --date 2026-07-06 --session close
uv run python scripts/send_telegram_stock_digest.py --date 2026-07-06 --session close --dry-run
```
