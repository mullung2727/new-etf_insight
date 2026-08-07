# youtube-digest morning/evening (10:00 / 18:00 KST)

## 목적

`daily-youtube`(01:00)가 만든 **영상별 요약**을 읽어, 아직 전달되지 않은 것만
**시장 이슈별로 통합·중복 제거·정보가치 평가**해서 하루 두 번 Discord로 보낸다.

- 영상별 요약 프롬프트·채널 `summary_hint`는 건드리지 않는다.
- 01:00 배치는 야간 선처리·실패 회복용으로 그대로 유지한다.

| 작업 | 책임 |
|---|---|
| `daily-youtube` 01:00 | 전일 전 채널 수집 + auto 미요약 요약. 본문 digest 없음 |
| `daily-youtube-digest-morning` 10:00 | 당일 수집·요약 보강 후 **미전송 요약 통합 브리핑** |
| `daily-youtube-digest-evening` 18:00 | 같음(당일분만 수집) |

## 파이프라인

```text
ops/scheduled-tasks/run-youtube-digest-session.ps1 -Session morning|evening
  → run_youtube_channels.py --date D-1        # morning만
  → run_youtube_channels.py --date D
  → run_youtube_analysis.py --date D --auto-only --lookback-days 1
  → youtube_digest.py --date D --session <s> --channel telegram_report
```

- DB: `etl/db/youtube_public.sqlite3` (신규 테이블 2개만 추가)
- LLM: 통합 1회 (`scripts/youtube_langgraph/prompts/market_digest.md` + `market_digest_schema.json`)

## 입력 선별 규칙

**미전송 + cutoff 기준 48시간 이내 게시**, 이 하나뿐이다.

- cutoff = 세션 시각(morning 10:00, evening 18:00 KST).
- 세션 명목 window는 항상 48시간 안쪽이라 별도 조건이 되지 못한다(죽은 조건).
- 그래서 STT/LLM이 늦게 성공한 영상도 다음 세션에서 한 번 회수된다.
- 최초 배포에도 과거 전체 backfill은 없다(48시간 컷).
- 요약 행이 있어도 내용이 비었거나 JSON이 깨졌으면 제외 — ledger에 넣지 않고 다음 세션에서 재시도.

## 표시 정책

- 표시 임계 60점, 최대 3개. 항목당 약 450자라 3개면 1,900자 안에 들어간다.
- 길이 초과 시 문자열 절단이 아니라 **낮은 순위 항목부터 제외**.
- 채널명은 LLM 출력이 아니라 `youtube_channels.json`의 `label`로 코드가 치환.
- 점수는 LLM `score_total`을 믿지 않고 세부점수 합으로 코드가 재계산
  (축·배점은 텔레그램 session overview와 동일: 25/25/20/20/10).
- 단일 채널 출처면 `cross_channel`을 0으로 강제, 입력에 없는 `investment_links`는 제거.

## 멱등성·재시도

- Discord 전송 **성공 후에만** `youtube_digest_deliveries`에 기록 → 실패분은 다음 세션 회수.
- 같은 `(date_kst, session)` 재실행:
  - `sent` → 아무것도 하지 않고 exit 0.
  - `generated`(전송만 실패) → 저장된 `message_text` 재전송, LLM 재호출 없음.
    단, 그 사이 다른 세션이 같은 영상을 전부 배달했으면 재전송하지 않고 `sent`로만 마감한다.
- 알려진 한계(at-least-once): `notify.py`는 Discord read timeout을 재시도하지 않고 실패로
  본다. 실제로는 전달됐는데 ledger가 비면 다음 세션에 같은 영상이 다시 통합될 수 있다.

## 성공 기준

- exit 0. 신규 미전송 요약 0건이면 LLM·전송 모두 생략하고 성공.
- 영상은 있으나 60점 이상이 없으면 한 줄 상태 메시지만 전송.

## 등록

```powershell
cd C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\ops\scheduled-tasks
Register-ScheduledTask -Xml (Get-Content -Path youtube-digest-morning.xml -Raw -Encoding UTF8) `
  -TaskName "daily-youtube-digest-morning" -TaskPath "\new-etf_insight\" -Force
Register-ScheduledTask -Xml (Get-Content -Path youtube-digest-evening.xml -Raw -Encoding UTF8) `
  -TaskName "daily-youtube-digest-evening" -TaskPath "\new-etf_insight\" -Force

Get-ScheduledTask -TaskPath "\new-etf_insight\" -TaskName "daily-youtube-digest-*" |
  Get-ScheduledTaskInfo | Select-Object TaskName, NextRunTime, LastTaskResult
```

## 수동 검증 / 운영 조회

```powershell
# cwd = etl. 운영 DB 복사본으로 dry-run (전송·ledger 없음)
Copy-Item db\youtube_public.sqlite3 $env:TEMP\yt_copy.sqlite3
.\.venv\Scripts\python.exe scripts\youtube_digest.py --date 2026-08-07 --session morning `
  --dry-run --db $env:TEMP\yt_copy.sqlite3

# 실패 run 재전송 (같은 메시지 그대로, LLM 재호출 없음)
.\.venv\Scripts\python.exe scripts\youtube_digest.py --date 2026-08-07 --session morning `
  --channel telegram_report

# 러너 통째
powershell -File ..\ops\scheduled-tasks\run-youtube-digest-session.ps1 -Session morning
```

```sql
-- 세션 결과와 경고
SELECT date_kst, session, status, sent_at, warning_json FROM youtube_digest_runs
ORDER BY date_kst DESC, session;

-- 이미 전달된 영상(다시 통합되지 않음)
SELECT digest_date_kst, digest_session, COUNT(*) FROM youtube_digest_deliveries
GROUP BY 1, 2 ORDER BY 1 DESC;
```

## 실패 알림

- 러너 실패 시 `send_report_messages.py --best-effort`로 `telegram_report` 웹훅 전송.
- 전송 실패는 `youtube_digest.py` exit 1 → 러너 catch → 알림.
