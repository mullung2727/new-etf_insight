---
name: new-etf-insight-strategy-budget
description: Use when the user asks to view or reset per-strategy budgets on a shared account ("전략 예산 보여줘", "8001 예산 리셋", "되돌림 예산 600만으로"). Runs etl/scripts/strategy_budget.py status/reset.
---

# 전략별 예산 조회·리셋

같은 계좌(:8001)의 전략마다 예산 = config `capital`(시드) + `capital_since` 이후 확정 손익.
각 전략은 안 쓴 몫을 `<데이터루트>/db/cash_reservations.json` (<데이터루트> = .env NEW_ETF_DATA_ROOT, 현재 E:\new-etf_insight_data — data_paths.db_dir()) 에 예약하고, 남의 예약은 못 쓴다. 설계: `docs/done/PLAN_STRATEGY_BUDGET.md`.

## 조회

```bash
cd etl && uv run python scripts/strategy_budget.py status
```

전략별 시드·since·예약(안 쓴 몫), 계좌 총자산, 시드 합 > 총자산 경고, 최근 리셋 이력.

## 리셋 (순서 고정)

1. `status` 를 먼저 보여준다 — 전략별 지금 예산(시드+손익)과 예약을 사용자에게 금액·기준과 함께 요약. status 의 `예산`은 마지막 배치 기준 값임을 같이 알린다
2. 사용자에게 **전략별 새 시드 금액**을 확인받는다. 금액은 내가 정하지 않는다. 유증 `extra`(건별 추가 자금)는 리셋 대상 아님
3. 실행:
   ```bash
   cd etl && uv run python scripts/strategy_budget.py reset rebound=6000000 envelope=1000000 --note "사유"
   ```
4. 출력된 `status` 로 결과 보고. 손익은 since(다음 거래일, 08:20 전이면 오늘)부터 다시 센다

- 장중(거래일 08:20~15:35)은 도구가 거부한다 — 장 마감 뒤에 실행
- 예약 파일은 각 전략의 다음 배치가 새 예산으로 다시 쓴다
- 이력: `<데이터루트>/db/watchlist.sqlite3` 표 `strategy_budget_resets`

## 전략 추가·삭제

`etl/scripts/strategy_budget.py` 의 `STRATEGIES` dict 한 줄 + 그 전략 config 에 `capital`·`capital_since` + 전략 코드가
`available_cash(url, strategy=이름)`·`write_reservation(..., 이름)` 를 쓰게 한다. 기존 4개(`rights_dip`·`rebound`·`envelope`·`jevq`) 코드가 본보기.
