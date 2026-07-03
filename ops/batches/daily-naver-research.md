# daily-naver-research

## 목적

증권사 **종목 리포트** PDF를 네이버 리서치 원본에서 다운로드해 종목별로 저장한다.
소스는 네이버 모바일 리서치 API(공식 JSON) — stockinfo7/텔레그램 중간 경유 없음.

## 소스 / 처리규칙

- 목록: `m.stock.naver.com/api/research/company?page=N` (종목분석, 날짜 desc)
- 상세: `m.stock.naver.com/api/research/company/{researchId}` → `attachUrl`(stock.pstatic.net 실제 PDF) + 요약/투자의견/목표가
- 종목코드 있는 행만(비종목 리포트 제외).
- 저장: `etl/exports/stock_reports/<종목명>_<종목코드>/<일자>_<증권사>_<researchId>.pdf`
- 멱등: 파일 존재 시 스킵. 응답이 %PDF 아니면 저장 안 함.

## 스케줄

- 기본: 매일 18:00 KST (당일 발행분 대상).
- 시간단위로 받고 싶으면 registry cron을 `0 * * * *` 등으로. 멱등이라 코드 무변경.

## 실행

```text
ops/scheduled-tasks/run-naver-research.ps1
  → etl/.venv/Scripts/python.exe scripts/download_naver_research.py --date <당일 KST>
  → 결과(종목 리스트 + 건수) telegram_report Discord 채널 보고
```

## 성공 기준

- exit code 0. `listed=0`은 실패 아님(휴장/미발행 가능).
- `no_pdf`(attachUrl 없음/비PDF)는 개별 스킵, 배치 실패 아님.

## 실패 알림

- `send_report_messages.py --channel telegram_report --best-effort` (전용 Discord 웹훅 `TELEGRAM_REPORT_TO_DISCORD_WEBHOOK_URL`).

## 검증

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```

```bash
# 수동 단발(개발용)
uv run python etl/scripts/download_naver_research.py --date 2026-07-02
```
