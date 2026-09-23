---
name: new-etf-insight-minute-backfill
description: Run, inspect, resume, or debug the new-etf-insight KRX all-symbol one-minute-bar backfill and its Windows scheduled task.
---

# Minute-bar backfill

- 작업 전에 `ops/batches/daily-minute-bars-backfill.md`를 읽는다.
- 실행은 `etl` 디렉터리에서 `.\.venv\Scripts\python.exe`를 사용한다.
- 운영 실행 주체는 Windows 작업 `\new-etf_insight\daily-minute-bars-backfill`이다.
- 계획 확인은 `scripts\backfill_minute_bars.py --dry-run`으로 한다.
- 임의로 DB 완료행을 삭제하지 않는다. 기존 봉은 기본키 충돌 시 보존된다.
- 실패 조사 시 해당 날짜 로그와 `minute_backfill_failures`를 함께 확인한다.
- `blocked` 재시도는 원인을 확인한 뒤 `--retry-blocked`로 한정 실행한다.
- 실수집을 수동 실행할 때는 기존 작업이 실행 중인지 먼저 확인한다.
- DB 우선: `load_bars`는 `minute_fetched`에 없는 날짜만 `ka10080`으로 조회 후 적재한다. 비용 사전 확인은 `missing_dates`로 한다.
- 반환은 요청 날짜의 전 시간대 봉이다. 15:30 이후 시간외 봉도 포함한다.
- NXT 시간외는 `--market nxt` 패스로 `{종목}_NX` 키에 모은다(ka10099 유니버스, 선행 30분, 실패해도 KRX 계속). KRX 봉은 15:35까지다.
