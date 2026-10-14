# PLAN_STRATEGY_BUDGET — 계좌 안 전략별 예산 (가상 소계좌)

한 줄: 같은 계좌(:8001)의 전략마다 **예산 = 리셋 시점 시드 + 그 뒤 확정 손익**을 자기 config `capital` 에 들고,
안 쓴 몫을 예약 파일에 전략별로 적어 **남의 몫은 못 쓰게** 한다. 리셋은 스크립트 1개 + 스킬로 언제든.

## 진행
- [ ] 구현·테스트 머지 (PR #90 예약 파일, PR # 리셋 도구 · 전략 코드는 비공개 폴더 직접 반영)
- [ ] 운영 등록 (스케줄 작업·config) — 첫 리셋 실행(시드 기록) 포함
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 배경 (2026-10-14 측정)

:8001 총자산 약 2,113만. 실매매 4개 전략이 같은 현금을 쓰는데 칸막이는 유증 하나뿐이다.

| 전략 | 지금 금액 규칙 | 손익 반영 | 남의 몫 보호 |
|---|---|---|---|
| 유증·무상 (`rights_issue/live`) | config `capital` 800만 + `extra` 300만 | ✓ 청산 시 `capital += 손익` (`verify.py:_apply_close`, `capital_applied` 로 중복 방지) | ✓ 미사용분을 예약 (`verify.py:update_reservation`, `order.py:783`) |
| 되돌림 (`intraday_rebound/live`) | config `capital` 300만 고정 ÷ 3 (`order.py:size_orders`) | ✗ (`realized` 열은 원장에 있음) | ✗ |
| 엔벌로프 (`envelope/live`) | (총자산 − 유증 capital_total) × 20% ÷ 5 (`order.py:plan_buys`) | ✗ (원장에 손익 열 없음) | ✗ |
| Jev (`jev_quant_live`) | 1주 × 3종목 (10만~90만), 장후 +1주 | ✗ | ✗ (유증 예약 있을 때만 `available_cash` 안에서) |

문제
- 최대치 합 ≈ 2,143만 > 총자산. 동시에 차면 **주문 시각이 빠른 쪽이 가져간다** (엔벌로프 08:50 → 되돌림 09:00:20 → Jev 15:18)
- 엔벌로프는 총자산 비율이라 남의 이익까지 자기 예산에 넣는다 (이중 계산)
- 전략 추가·삭제 시 고칠 곳이 흩어져 있다

## 사용자 결정

| # | 결정 | 누가 |
|---|---|---|
| U1 | 전략 예산 = 시드 + 확정 손익 (이익이면 늘고 손실이면 준다). 고정 금액·총자산 비율 둘 다 아님 | 사용자 2026-10-14 |
| U2 | 손익은 **오늘(첫 리셋 시점)부터** 센다. 과거 손익(되돌림 +39만 등)은 시드에 안 넣는다 | 사용자 2026-10-14 |
| U3 | 리셋은 언제든 할 수 있게 **스킬 + 스크립트** (MCP 아님 — broker 수정·재기동 위험) | 사용자 2026-10-14 (추천 수락) |
| U4 | 시드: 유증 800만 · **되돌림 600만** · 엔벌로프 100만 · Jev 100만 (합 1,600만, 유증 extra 300만 별도) | 사용자 2026-10-14 (Jev −200만·엔벌로프 −100만 → 되돌림 +300만) |
| U5 | Jev 도 이 체계에 넣는다 (시드 100만) | 사용자 2026-10-14 (U4 에 포함) |

## 설계

### D1. 예산의 단일 출처 = 각 전략 config `capital` (내 판단 — 유증 방식 재사용)
- 유증이 이미 쓰는 방식을 그대로 넓힌다: 청산 확정 시 `capital += 손익`, 원장 행에 `capital_applied=1` 로 두 번 반영 금지
- 새 기준표·합산 계산 없음. 리셋 = config `capital` 을 시드로 덮어쓰기
- 손익 = **키움 실현손익(수수료·세금 차감)** `trading_batch_common.fetch_realized`. 되돌림·유증이 이미 이 값을 쓴다

| 전략 | 손익 반영 위치 (구현) |
|---|---|
| 유증 | 그대로 (`verify.py:_apply_close`). `extra` 는 건별 추가 자금이라 리셋 대상 아님 — 청산 시 손익 별도 기록 유지 |
| 되돌림 | `verify.py:settle` 에서 `realized` 기록 직후 `capital += realized`, `rebound_positions.capital_applied` 열 추가 |
| 엔벌로프 | `verify.py` 청산 확정(잔량 0) 시 `fetch_realized` → `envelope_positions.realized`·`capital_applied` 열 추가 → `capital +=` |
| Jev (U5 예면) | 09:00 청산 체결 후 `fetch_realized` → `live_orders` 매도 행에 `realized`·`capital_applied`. **반영 함수 위치는 구현 첫 단계에서 grep 으로 지목** (exit 계열 `jevq-exit`/`jevq-report` 중) |

### D2. 금액 규칙 변경
- 되돌림: 그대로 (`capital ÷ 3`). capital 이 손익으로 바뀌는 것만 달라짐
- 엔벌로프: `총자산 × share ÷ 5` → **`capital ÷ 5`**, 상한 `capital − 보유원가`. `share`·총자산 조회·`read_reservation` 차감(`order.py:236~246`) 제거
- 유증: 그대로
- Jev: 1주 그대로, **예산 안에 들어가는 만큼만** — 순위순으로 사다가 `capital − 보유원가 − 오늘 주문액` 을 넘는 종목부터 건너뜀(skip 사유 기록). 장후 추가매수(`add_ah`)도 남은 예산 안에서만. 가격 상한(90만)은 그대로 (사용자 2026-10-14)

### D3. 예약 파일을 전략별로 (`etl/db/cash_reservations.json`)
- 형식: `{"127.0.0.1:8001": {"rights_dip": {"reserve", "capital_total", "updated"}, "rebound": {...}, ...}}`
- 예전 형식(`{"reserve", "strategy", ...}` 평면) 읽으면 `{strategy: entry}` 로 간주 — 전환 첫날 호환
- `reserve` = max(0, capital − 보유원가 − 미체결 매수액) = 아직 안 쓴 내 몫
- `write_reservation(url, reserve, capital_total, strategy)` — 시그니처 그대로, 그 전략 키만 갱신 (남의 키 보존)
- `available_cash(url, strategy=...)` — **자기 것 빼고 다른 전략 reserve 합**을 뺀다. `strategy=None`(옛 호출부: 꺼진 종가베팅·눌림목, :8002 high52) = 전부 뺀다(보수적). 유증의 `exclude_reserve=False` 는 `strategy="rights_dip"` 로 대체
- 갱신 시점: 각 전략 order 배치 끝(주문 직후)·verify 끝(청산 반영 후). 파일 깨짐 = 0 (fail closed, 현행 유지)

### D4. 리셋 도구 `etl/scripts/strategy_budget.py` (새 파일)
- 전략 목록 = 파일 안 dict 1개 `{name: (port, config 경로)}`. 전략 추가·삭제 = 이 한 줄
- `status` — 전략별 capital · 보유원가 · reserve · 마지막 리셋 이후 손익, 계좌 총자산, **capital 합 > 총자산이면 경고**
- `reset --all --seed rebound=3000000 ...` 또는 `reset rebound=4000000` — config `capital` 덮어쓰기(원자적, 유증 `ledger.save_config` 방식) + 이력 행
- 이력 표 `strategy_budget_resets(date, port, strategy, old_capital, new_capital, note, created_at)` (`etl/db/watchlist.sqlite3`)
- 장중(08:20~15:35) 실행은 거부 — 배치가 config 를 읽는 중 바뀌는 것 방지. `--force` 없음
- 실행 전 `status` 를 찍고 확인 입력 없이 끝까지 간다(스킬이 사람 확인을 먼저 받음)

### D5. 스킬 `skills/new-etf-insight-strategy-budget/SKILL.md`
- 언제: "전략 예산 리셋/조회", "되돌림 예산 400만으로"
- 순서: `status` 보여줌 → 사용자 확인 → `reset` → `status` 다시
- CLAUDE.md Skills 목록에 1줄 추가

### D6. 구현 순서·위치
- 전략 코드(`research/private/*/live`)는 gitignore 라 **메인 폴더에서 직접** 고친다(PR 없음). 공용 부분(`trading_batch_common.py`·`strategy_budget.py`·스킬·테스트)은 이 브랜치 PR
- 순서: ① 공용 예약 파일 전략별화(호환 유지) 머지 → ② 장 마감 후(20시 이후) 전략 4개 코드 반영 → ③ 리셋 1회(U4 시드) → ④ 다음 거래일 실가동 확인
- ①만 먼저 들어가도 기존 동작 그대로여야 한다(옛 형식 호환·`strategy=None` 보수적)

## 손 안 대는 것 (요구사항 커버 확인)
- high52 (:8002) — 계좌 단독. `available_cash(strategy=None)` 이 :8002 키에 예약 없으면 기존값 그대로 → 영향 없음
- 꺼진 종가베팅·눌림목 — 옛 호출부 그대로 두면 전체 reserve 차감(보수적). 켜게 되면 이 체계에 등록부터

## 테스트 (요구사항 ↔ 케이스)

| 요구 | 테스트 |
|---|---|
| U1 이익이면 예산 증가 | 되돌림 청산 realized +10만 → capital 300만→310만 |
| U1 손실이면 감소 | 엔벌로프 realized −5만 → capital 감소 |
| 두 번 반영 금지 | 같은 verify 재실행 → capital 변화 0 (`capital_applied`) |
| 남의 몫 못 씀 | 예약 {rights 50만, rebound 297만}·현금 400만 → envelope `available_cash` = 53만 |
| 자기 몫은 씀 | 같은 상황 rebound 호출 → 350만 |
| 옛 형식 호환 | 평면 예약 파일 → 유증 1건으로 읽힘 |
| 깨진 파일 | → 0 (fail closed) |
| 리셋 | `reset rebound=4000000` → config 400만, 이력 1행, 다른 전략 config 불변 |
| 장중 리셋 거부 | 10:00 시각 → 거부, config 불변 |
| 합 초과 경고 | capital 합 > 총자산 → status 경고 |
| 엔벌로프 금액 | capital 100만 → 자리 20만, 보유 80만이면 신규 20만까지 |
| Jev 예산 상한 | capital 100만·후보 50만·40만·30만 → 50만·40만 매수, 30만 건너뜀(남은 10만) |
| Jev 장후 추가 | 남은 예산 < 추가 1주 가격 → 추가매수 안 함 |

## 운영 체크 (§7)
- 휴장일: 리셋 도구는 배치 아님 — 해당 없음. 각 전략 휴장 가드는 기존 그대로
- 스케줄 변경 없음. 첫 리셋은 머지 후 장외 시간에 스킬로 1회 실행(U4 시드)
- 다른 전략 보유분 매도 금지·`MAX_ORDER_AMOUNT` 분할 — 기존 각 전략 코드 그대로

## 구현 차이
- 원래 D1 "청산마다 config capital += 손익(capital_applied 플래그)" → 실제 되돌림·엔벌로프·Jev 는 **config capital(시드)은 리셋 때만 바뀌고, 예산 = capital + capital_since 이후 손익을 매번 원장에서 합산** (두 번 반영 방지 플래그가 필요 없어 더 단순·멱등). 유증은 기존 방식(청산 시 capital += 손익) 유지 — 리셋은 capital 덮어쓰기로 같게 동작
- 원래 Jev 손익 = 키움 실현손익 → 실제 **매도대금×(1−0.23%) − 매수대금** (live_orders 체결가). 매수는 date ≥ since, 매도는 date > since 로 리셋 전 보유분 매도대금 제외
- 원래 엔벌로프 상한 = capital − 보유원가 → 같음. 단 확인 전 매수는 기록된 자리 금액(budget 열)을 원가로 봄
- 리셋 since = 다음 거래일 (거래일 08:20 전이면 오늘)
