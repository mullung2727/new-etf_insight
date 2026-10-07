# daily-swing-pick (평일 19:00 KST)

## 목적

텔레그램·유튜브·증권사 리포트·high52 후보를 모아 **같은 체크리스트**(Jev 2 +
코드 3 + 참고 2)로 매일 판정하고, 상위 **3종목**을 GPT로 요약해 Discord 배치
채널로 보낸다. 탈락 종목 포함 전 판정을 DB에 매일 쌓는다 (나중에 항목별
예측력 검증용). 보유 관점 1주~1달. 자동매매 아님 — 주문 없음.

## 파이프라인

LangGraph 8노드 (직선):

```text
ops/scheduled-tasks/run-swing-pick.ps1
  → etl/.venv/Scripts/python.exe scripts/run_swing_pick.py
      collect → prefilter → judge_jev → judge_code → rank
        → summarize → store → notify
```

- `collect`: 소스 4개 → 후보 풀 (종목별 소스 태그)
- `prefilter`: 전일 거래대금 50억 컷, 스팩·거래정지 제외
- `judge_jev`: 1번 재료 지속성 score + 2번 리스크 noul (탈락 판정)
- `judge_code`: 3번 실적 · 4번 수급 · 5번 차트 + 6번 PER 참고값
- `rank`: 탈락 제외 → 합산 → 동점 규칙 → 상위부터 상태 확인하며 3개 채움
- `summarize`: GPT 1회 (최종 3종목만)
- `store`: 탈락 포함 전 후보 저장 (같은 날 재실행은 그날 행 교체, 멱등)
- `notify`: `channel="batch"` (`DISCORD_BATCH_WEBHOOK_URL`)

## 스케줄

| 작업 | 시각 | 내용 |
|---|---|---|
| `daily-swing-pick` | 평일(월~금) 19:00 KST | 당일 후보 수집·판정·상위 3종목 보고 |

- 휴장일은 파이썬 시작 시 판정 후 저장·전송 없이 exit 0.

## 입력

| 소스 | 조건 | 원천 테이블 |
|---|---|---|
| 텔레그램 | 당일 언급 종목 전부 | `telegram_public.sqlite3` `telegram_stock_insights` (date_kst = D) |
| 유튜브 | 당일 언급 종목 전부 | `youtube_public.sqlite3` `youtube_stock_insights` (date_kst = D) |
| 증권사 리포트 | 직전 거래일 다음 날 ~ D 발행 중 목표가 상향·신규 커버 | `report_metrics.sqlite3` `report_api_facts` (+ 직전값 `report_facts`) |
| high52 | `cand=1` | `watchlist.sqlite3` `high52_screen` |

- 시세 기준: 컷·PER은 D-1 KRX, 5번 오늘 종가만 19:00 키움 일괄시세(`ka10095`).
- 소스 하나가 죽어도 배치는 계속 돌고 `source_failed` 경고를 남긴다.

## 결과

- DB: `etl/db/swing_pick.sqlite3` — `swing_candidates`(전 후보 × 항목별 점수·원값),
  `swing_runs`(일별 집계 + GPT 요약문 + 전송 여부)
- Discord 배치 채널: 상위 3종목 요약 보고

## 선행 배치 의존

| 선행 작업 | 시각 | 이유 |
|---|---|---|
| KRX OHLCV 적재 | Tue-Sat 08:00 | D-1 시세·거래대금·시총 기준 |
| 텔레그램 close 세션 | 매일 16:00 | 당일 언급 종목 |
| 네이버 리서치 | 매일 18:00 | 당일 리포트 목표가·의견 |
| 유튜브 evening digest | 매일 18:00 | 당일 언급 종목 |
| broker :8001 | 상시 | 일괄시세·수급·테마·상태 조회 |

## 비용

- Jev 입력 토큰 하루 ~35만 = 약 $0.015 (2026-09-28 dry-run 실측)
- GPT 1회 (최종 3종목 요약만)

## 수동 실행

```powershell
# cwd = etl. dry-run: 저장·전송 안 함 (Jev·GPT는 돌림)
cd etl
.\.venv\Scripts\python.exe scripts\run_swing_pick.py --dry-run

# 특정일 재실행 (그날 행 교체, 멱등)
.\.venv\Scripts\python.exe scripts\run_swing_pick.py --date 2026-09-28

# GPT 요약 생략
.\.venv\Scripts\python.exe scripts\run_swing_pick.py --dry-run --skip-summary
```

## 실패 알림

- 파이썬 안에서 1차 (`notify(..., channel="batch")`, best-effort)
- 러너 catch에서 2차 (`send_report_messages.py --channel batch --best-effort`) —
  파이썬이 import 단계에서 죽는 경우를 덮는다

## 설계

- `docs/done/PLAN_SWING_PICK.md` (체크리스트 기준·점수·결정 이력)

## 검증

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```
