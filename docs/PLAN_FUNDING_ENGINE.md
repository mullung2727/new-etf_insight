# PLAN — 공용 자금 엔진 (전 전략 가용예산 통합 + 유휴현금 CD금리 ETF)

한 줄: 전략은 "X원 필요"만 요청하고, 공용 자금 엔진이 [현금 + CD금리 ETF 평가액]으로 가용을 판단해 모자라면 ETF를 팔아 확보한 뒤 매수를 허가하고, 장 마감엔 남는 돈을 ETF로 넣는다. 1단계는 유증(유상·무상)만 엔진에 넣고, 안정 확인 후 나머지 전략을 하나씩 옮긴다.

> 공용 주문 실행 엔진(`docs/PLAN_SHARED_CHASE_ENGINE.md`: 추격 지정가·마감 시장가)의 **위층**이다. 그 엔진은 "어떻게 주문하나", 이 엔진은 "돈이 있나".
> 반영 조건은 PLAN_SHARED_CHASE_ENGINE 의 "작업·반영 조건" 그대로(작업은 worktree·`research/private/_staging`, 반영은 거래일 20:00~08:00·휴장일, 한 번에 전략 하나).

## 진행

### P0 실측
- [ ] 구현·테스트 머지 (PR #) — 해당 없음 (조회·소량 주문 실측만)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜) — 해당 없음
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P1 엔진 + 유증(유상·무상)
- [x] 구현·테스트 머지 (PR #141, 2026-10-31)
- [x] 운영 등록 (스케줄 작업·config) — `funding-sweep` 15:21 등록, 유무상 config `funding_enabled=true` (2026-10-31 22시)
- [ ] 첫 실가동 확인 (날짜) — 마감 매수 ✅ 11-02(2주)·11-11(1주), 무상 매도 경로 ✅ 11-11 15:00(2주 → 유무상 1,408,371 허가), 수능일 시각 이동 ✅ 11-19 16:31. **유상 장전 매도 경로 미발생**(P0-3 장전 시장가 실측도 이때) → 첫 발생일 확인 후 체크
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

### P2 되돌림(예산 창구만, ETF 제외 D13) + 초소형 msig (D15 — 한 번에 반영)
- [x] 구현·테스트 머지 (PR #168, 2026-11-19 — 공용 members.etf. 비공개 되돌림·msig 연결은 11-19 20:40 live 반영, 백업 `_bak_20261119_fundp2`, live 테스트 되돌림 129·msig 70 OK)
- [x] 운영 등록 (스케줄 작업·config) — 스케줄 작업은 그대로. 반영 후 되돌림·msig live config 에 `funding_enabled: true` (사용자 11-19 "켜둬", 11-19 20:45 반영), 다음 거래일 엔진 원장 members 에 rebound(etf=0)·msig(etf=1) 등록 확인
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P3~P6 나머지 전략 (Jev·엔벌로프·스윙) — 셋 다 중지 상태. 시드 0 리셋(예정 11-20)으로 예약 줄 0 이면 이전 불필요 → P7 전 확인만
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config)
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

### P7 예약 파일 폐기
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음
- [ ] 첫 실가동 확인 (날짜)
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 왜

- 지금 가용예산 판단이 전략마다 따로(`available_cash` + 전략별 상한), 전략 간 조정은 예약 파일(`trading_batch_common.write_reservation`)로 한다.
- 유휴현금을 ETF로 돌리려면 전략마다 매도·예약 보정을 넣어야 함 → 스파게티. 한 곳(엔진)에서 처리한다.

## 2. 확인된 사실 (2026-10-30 코드 기준)

- 가용: `available_cash(url, strategy)` = `100stk_ord_alow_amt`(미정산 매도대금 포함) − 다른 전략 reserve 합 (`etl/scripts/trading_batch_common.py:212`)
- 유증 reserve = max(0, C − 보유원가 − 미체결) + extra 미사용 (`order.py:845`, `verify.py:429`, 비공개)
- 유증 전략 상한 = C + Σextra − 보유원가 − 미체결 (`ledger.strategy_cash`, 비공개)
- 자동 매수 1건 상한 `MAX_ORDER_AMOUNT` = 500만(.env) — broker 가드(`broker/kiwoom/guards.py`). ETF 1주 ≈107만 → 4주 넘으면 분할
- 2026-10-30 11:38 호가: 459580 스프레드 5원(0.0005%), 1호가 잔량 68만주. 357870 은 0.0086%
- :8001 전략 = 유증·되돌림·엔벌로프·Jev·스윙·초소형 (`etl/scripts/strategy_budget.py` STRATEGIES + 초소형)

## 3. 결정 기록

| # | 결정 | 이유 | 누가 |
|---|---|---|---|
| D1 | 대상 = 계좌 전체(전 전략)의 안 쓰는 돈 | 전략마다 ETF 로직 넣으면 스파게티 | 사용자 (2026-10-30) |
| D2 | 순서 = 유증 먼저 → 실가동 확인 → 나머지 전략 하나씩 | 기존 로직 전부 바뀌는 큰 변경 | 사용자 |
| D3 | 이전 중 규칙: **ETF 엔 엔진에 들어온 전략 돈만**. 엔진은 예약 파일에 한 줄(`engine`)로 참여 → 안 옮긴 전략 코드 수정 0 | 엔진이 계좌 현금 전부를 ETF 로 돌리면 안 옮긴 전략 돈이 사라져 주문 막힘 | Claude 제안 · 사용자 수락 |
| D4 | ETF = KODEX CD금리액티브(합성) 459580 | 스프레드 1/18. 1주 107만이라 잔여현금 생김 | 사용자 |
| D5 | 가용 = 현금 + ETF 평가액 × (1 − 매도 슬리피지) | 매도 시 덜 받을 수 있음 | 사용자 (슬리피지 값은 D9) |
| D6 | 장전 매수가 필요한 요청: 08:40 ETF 매도 주문 → 09:00 전량 체결 확인 → 매수 허가 | 미체결 매도는 주문가능에 안 잡힘(P0 실측) | 사용자 |
| D7 | 체결 후에도 모자라면: 원인 확인(미체결 매도 남음 / 예상보다 싸게 체결) → 부족분 시장가 추가 매도 | 정상이면 안 생기는 케이스지만 막히면 안 됨 | 사용자 |
| D8 | 남는 돈 매수 = 15:21 종가 단일가(15:30 체결), 미체결분은 장후 시간외(애프터마켓)에서 매수. 별도 작업 `funding-sweep` | 모든 엔진 전략의 당일 매수·매도가 끝난 뒤 한 번, 못 사면 하룻밤 이자 놓침 | 15:21=Claude 제안, 애프터마켓 보완=사용자 (2026-10-30) |
| D9 | 매도 슬리피지 = 0.02% (config) | 스프레드 0.0005% 대비 넉넉히 | Claude 제안 · 사용자 수락 |
| D10 | 상주 서버 대신 공용 함수 + 파일 잠금 | 전략이 서로 다른 시각에 따로 도는 프로세스, 서버 추가는 운영 부담 | Claude 제안 — 검토 필요 |
| D11 | 유무상 추가자금(extra, 슬롯 찬 대기 건에 사용자가 입금·배정하는 돈)도 엔진 몫에 포함 → `rights-dip-funds` 스킬의 입금 확인을 "현금 + ETF 평가액" 기준으로 갱신 | 매수는 기존대로 배치가 함, 그 전까지 ETF 이자 | 사용자 (2026-10-30) |
| D12 | ETF 실현손익은 어느 전략 돈이었든 **전액 되돌림 예산**에 붙인다 (P1 유무상 단독일 때도) | 배분 계산 없이 단순하게 | 사용자 (2026-10-30) |
| D13 | 되돌림은 엔진 회원(예산 요청·안 쓴 몫·예약 한 줄)이지만 **15:21 ETF 매수 계산에서 제외** (`members.etf = 0`) | 되돌림은 매일 09:01 매수·15:20 전량 청산 → 넣으면 거의 매일 ETF 왕복. 실측(2026-11-11 매도 2주): 수수료 640원 = 편도 0.015%·1주 왕복 약 320원, 보유 이자 하루 약 100원/주(11-02~11 실보유 연 3.4%) → 3~4일 넘게 놀아야 본전 | 사용자 (2026-11-19, 선택지 1) · Claude 제안 |
| D15 | 초소형(msig)도 P2 에 함께 넣어 되돌림과 **같은 날 반영** (D2 "한 반영에 하나" 의 예외). msig 는 `etf=1` | msig 안 쓴 몫은 남는 현금을 매달 되돌림에 대여(LIVE_DESIGN L3c)하고 남은 약 20만뿐 → 혼자서는 ETF 1주(약 107만)도 안 됨. 단 sweep 은 etf=1 회원 몫을 **합친 뒤** 주 수를 정하므로 유무상 몫과 합쳐 1주를 넘기면 주문 수량이 바뀔 수 있다(그 1주 매도·매수는 엔진이 처리, 위험 작음). 목적은 예산 창구 통일(P7) | 사용자 (2026-11-19 "영향없으니 한번에") · etf=1 은 Claude |
| D14 | 되돌림 자금 요청 시점 = 09:00 시가 확보 후 종목당 금액 정한 직후 `request_funds(preopen=False)`(부족하면 장중 시장가 ETF 매도 → 즉시 체결 확인). 늦은 시가 주문도 같은 방식 | 필요액은 시가 가드 뒤 선택 수(nsel)로 정해짐. 되돌림 돈은 ETF 에 없으니 매도는 다른 회원이 현금을 먼저 쓴 날만 생김 | Claude |

## 4. 구조

```
전략 (종목·가격·수량 결정, 원장 기록)          ← 비공개
  │ request_funds / release_funds / budget_status
공용 자금 엔진 funding_engine.py               ← git, etl/scripts
  │ ETF 매도·매수, 엔진 원장, 예약 파일 `engine` 줄
공용 주문 실행 chase_engine · BrokerClient     ← 기존
```

### 4-1. 엔진 원장 (`db_dir()/funding.sqlite3`)
- `members(strategy, joined_date)` — 엔진에 들어온 전략
- `etf_orders(date, side, qty, price, order_no, status, filled_qty, filled_price, reason)`
- `etf_pnl(date, strategy, amount)` — 매도 실현손익 배분 기록
- ETF 보유 = 원장 순보유. 잔고와 다르면 min 을 쓰고 ⚠ 알림 (수동 보유분은 절대 안 팖)

### 4-2. 함수
- 안 쓴 몫: 각 전략이 배치 끝에 자기 기존 함수로 계산해 `set_unused(strategy, amount)` 로 엔진에 알림 (유무상 = C + Σextra − 보유원가 − 미체결). 15:21 sweep 은 전략 코드 없이 이 값 합으로 계산 — 공용 코드가 비공개 전략 코드를 import 하지 않게 (Claude 판단, 2단계 구현 중 결정)
- `available(strategy, 계좌현금, 예약, 잔고수량, ETF가)` → 현금(= 계좌 − 안 옮긴 전략 예약), ETF 매도가능·평가액(×(1−0.02%)), pool, 안 쓴 몫, 허가 가능액 = min(안 쓴 몫, pool)
- `request_funds(strategy, amount, *, preopen: bool) -> granted`
  1. 잠금
  2. 전략 안 쓴 몫 < amount → amount 를 안 쓴 몫으로 줄임 (전략 칸막이 유지)
  3. `cash` = 주문가능 − 안 옮긴 전략 reserve 합
  4. cash ≥ amount → 바로 허가
  5. 부족분 `k` = ceil((amount − cash) / (기준가 × (1 − 슬리피지))), 최대 ETF 보유
     - preopen: 장전 시장가 매도 → 09:00 이후 5초 간격 최대 2분 체결 확인
     - 장중: 시장가 매도 → 즉시 체결 확인
  6. 전량 체결 확인 후 cash 재조회. 그래도 부족 → 원인 기록(미체결 남음 / 체결가 < 예상) → 부족분 시장가 1회 추가 매도 → 재조회
  7. 허가액 = min(amount, cash) 반환, 부족하면 ⚠ 알림. 결과 불명 주문은 재전송 안 함(공용 결과 불명 규칙)
- `sweep()` — `funding-sweep` 15:21
  1. idle = Σ 엔진 전략 안 쓴 몫 − ETF 원가 (미체결 매수금은 전략 안 쓴 몫에서 이미 빠짐)
  2. q = floor(idle / (매도1호가 + 2틱)), 1 이상이면 지정가(매도1호가 + 2틱) 매수, `MAX_ORDER_AMOUNT` 넘으면 분할
  3. 예약 파일 `engine` 줄 갱신
- `write_engine_reservation()` — reserve = max(0, Σ 엔진 전략 안 쓴 몫 − ETF 원가). 엔진 전략 개별 줄(`rights_dip` 등)은 지움
- 손익: ETF 매도 체결 시 실현손익(수수료·세금 포함)을 `etf_pnl` 에 strategy=`rebound` 로 기록. 되돌림 예산 = 시드 + since 이후 (되돌림 실현손익 + `etf_pnl` 합) — 되돌림 `ledger.budget`(비공개)에 합산 한 줄 추가. 보유 중 평가손익은 안 셈

### 4-3. 슬롯이 다 찰 때
- 안 쓴 몫 ≈ 0 → request_funds 가 ETF 전량 매도를 요구, sweep idle 0 → 재매수 없음. 별도 분기 없음

## 5. 단계 내용

### P0 실측 (장중 소량, 사용자 확인 후) — 3~6 은 P1 개발과 병행 (사용자 2026-10-30). 코드는 실측 전 기본값 + 대체 경로를 같이 두고 `# P0 실측 대기` 주석
- 3 이 안 되면: 장전 매도는 하한가 지정가(시가에 체결)로 대체
- 5 가 안 되면: 마감 미체결 매수는 다음날로 넘김(애프터마켓 단계 config 로 끔)
6. 459580 분배금 여부 (52주 고가 2026-08-27 1,075,965 > 10-30 1,073,485)
1. :8001 잔고에 459580 수동 보유 없음 — ✅ 2026-10-30 사용자 확인
2. 장전 매도 미체결 상태에서 `100stk_ord_alow_amt` 가 안 늘어나는지 — ✅ 2026-10-30 14:06 장중 실측: 1주 매수(1,073,485) 후 10,136,657 → 상한가 지정가 매도 접수 후에도 10,136,657 (안 늚) → 취소. D6 가정 맞음. 테스트 1주 보유 중(엔진 원장 편입 필요)
3. 459580 장전 시장가 주문 가능 여부
4. broker `ALLOWED_ORDER_SOURCES` 에 엔진 source 추가 필요 여부 — ✅ 불필요: :8001 은 허용 목록 미설정(전부 허용, `restart_all_servers.ps1` 은 :8002·미국만 설정). source = `funding_buy`/`funding_sell`
5. 459580 장후 시간외(15:40 종가·16:00~ 단일가·애프터마켓 접속매매) 중 어느 시장에서 주문 가능한지, broker 주문 경로가 지원하는지

### P1 엔진 + 유증 (비공개 연결 지점)
- 유상 08:40 주문: allocate 전 `request_funds(preopen=True)` → 허가액으로 allocate. 현금 충분하면 기존과 같이 장전 주문
- 무상 15:00 우선 판단(F7): `request_funds(preopen=False)` 먼저 → 허가액이 그래도 모자랄 때만 기존 유상 미체결 취소
- 유증 자체 예약 쓰기(`order.py:845`·`verify.py:429`) 삭제 → 엔진 `write_engine_reservation()` 호출로 교체
- `strategy_budget.py status`: `rights_dip` 줄 대신 `engine` 줄을 읽어 표시
- 되돌림 `ledger.budget` 에 `etf_pnl` 합산 (D12)
- `rights-dip-funds` 스킬 입금 확인 = 현금 + ETF 평가액 (D11)

### P2 되돌림 (D13·D14)

공용 (`etl/scripts/funding_engine.py`, git):
- `members` 에 `etf INTEGER NOT NULL DEFAULT 1` 열 (`ensure` 에서 없으면 ALTER — 기존 rights_dip 행은 1).
- `add_member(db, strategy, joined, *, etf: bool = True)` — 새 행은 etf 값으로, 이미 있으면 joined 유지·etf 만 갱신.
- `total_unused(db, *, etf_only: bool = False)` — etf_only 면 `etf = 1` 회원만.
- `sweep` idle = `total_unused(etf_only=True)` − ETF 원가 − 대기 매수 (나머지 동일). `engine_reserve`·`request_funds`·`available` 은 전 회원 그대로 (되돌림 돈도 안 옮긴 전략에서 보호).
- `etl/scripts/strategy_budget.py` status: 엔진 회원 줄에 `etf=0` 이면 "(ETF 제외)" 표시.

비공개 (`research/private/intraday_rebound/live/`, gitignore — 20시 후 백업·사본 반영):
- `order.py main`: 시작 시 `fe.ensure` → `fe.add_member(fe.DB, "rebound", today, etf=False)` → 옛 예약 줄 `write_reservation(url, 0, …, "rebound")` 1회(이중 차감 방지, 유무상 `order.py:594` 와 같은 방식).
- 예약 쓰기 3곳(`order.py` 후보 없음 경로·주문 후, `run_late.refresh_reserve`, `verify.py`) → `fe.set_unused(fe.DB, "rebound", max(0, budget − sent − open_cost), now)` + `fe.sync_reservation(fe.DB, io=fe.LiveIO(url), today=today)`.
- 주문가능 조회 2곳(`order.py main` 의 `available_cash(url, strategy="rebound")`, `run_late.settle`) → `set_unused` 최신화 후 `fe.request_funds(fe.DB, "rebound", need, io=fe.LiveIO(url, dry_run=…), today=today, preopen=False)["granted"]`, notes 는 알림에 붙임. `need` = 종목당 금액 × 주문할 종목 수 — 종목당 금액 계산은 `size_orders` 와 같은 함수(`slot_amount(capital, nsel)`)로 뽑아 공유.
- `ledger.budget` 은 그대로(D12 etf_pnl 이미 합산).
- 순서: 오늘 밤(11-19) 반영하는 `live_candfile`(후보 파일·예산 중간안) 이 live 가 된 뒤, 그 live 에서 새 사본을 떠 작업.

중단 스위치: 되돌림 config `funding_enabled`(기본 false) — false 면 기존 `available_cash`·`write_reservation` 경로 그대로 (유무상과 같은 방식).

비공개 msig (`research/private/afa/live/`, D15):
- `monitor.py main` 16:00: `write_reservation(url, scash, ev, "msig")` → `fe.add_member(fe.DB, "msig", today)`(etf 기본 1) · 옛 줄 `write_reservation(url, 0, ev, "msig")` · `fe.set_unused(fe.DB, "msig", max(0, round(scash)), now)` · `fe.sync_reservation(...)`.
- `exec_batch.py cash()`(교체 기간 분할 매수 회차마다): `available_cash(url, strategy="msig")` → `set_unused(msig, max(0, strategy_cash − open_cost))` 후 `fe.request_funds(fe.DB, "msig", max(0, strategy_cash − open_cost), io=fe.LiveIO(url, dry_run=cfg["dry_run"]), today=today, preopen=False)["granted"]` 를 `limit_buy_cash` 의 첫 인자로. notes 는 exec 알림에 붙임.
- 되돌림 대여(`apply_loan`)는 16:00 게시 **전**에 돌아 게시값이 대여 후 값 — 변경 없음. 정산(`settle_if_due`, 손익 20% 이체)은 게시 **뒤**라, 정산이 일어난 날은 정산 후 capital 로 `strategy_cash` 를 다시 계산해 한 번 더 게시한다(다음날 15:21 sweep 이 이체된 돈을 msig 몫으로 세지 않게, CodeRabbit #168).
- 중단 스위치: msig config `funding_enabled`(기본 false).

끄는 절차(되돌림·msig 공통, true → false): config 만 false 로 바꾸면 엔진 원장에 회원·안 쓴 몫이 남아 ① sweep 이 옛 몫으로 ETF 를 사고 ② `cash_view` 가 그 전략을 회원으로 보고 다시 쓰기 시작한 옛 예약 줄을 빼지 않는다. 그래서 끌 때는 ① 그 전략 `unused` 0 + engine 줄 재계산(`set_unused`·`sync_reservation`) ② members 에서 그 전략 행 삭제(지금은 함수 없음 — 수동 SQL 또는 그때 `remove_member` 추가) ③ config false ④ 다음 배치가 옛 예약 줄을 다시 씀 — 순서로. ETF 보유분은 엔진 원장에 남고 남은 회원(유무상) 몫으로 처리하되, 남은 회원 몫보다 많으면 초과분 매도 여부를 그때 사용자가 정한다.
- 반영: 되돌림과 같은 날 20시 후, 각각 백업(`_bak_YYYYMMDD_funding`) 후 사본 복사.

### P3~P6 전략 이전
- 그 전략 `available_cash`·`write_reservation` 호출을 `request_funds`·`budget_status` 로 교체, 엔진 members 에 추가. 한 반영에 하나, 다음 거래일 실로그 확인 후 다음. 매일 전량 청산하는 전략은 D13 처럼 `etf=0` 검토

### P7
- 모든 전략이 members 면 예약 파일 읽기·쓰기 제거

## 6. 운영 체크

- 휴장일: `funding-sweep` 진입부 `is_krx_trading_day` → 조용히 종료
- 실패 알림 1회: 엔진은 호출한 전략의 notes 로 돌려주고 직접 알림 안 함. `funding-sweep` 만 자기 알림(러너 exit code 규약)
- 동시 실행: `request_funds`·`sweep` 은 파일 잠금으로 직렬화 (예약 파일 잠금 방식 재사용)
- 안 옮긴 전략: 엔진 `engine` 줄을 남의 예약으로 빼므로 수정 없음. 459580 을 다른 전략이 사지 않는지 P1 전 grep
- 중단 스위치: 엔진 config `enabled=false` → request_funds 는 현금만으로 허가(ETF 매도 없음), sweep 아무것도 안 함. 보유 ETF 는 그대로(수동 정리)
- 스케줄: `funding-sweep` 은 `openclaw-cron.registry.json` 먼저 → Test → Register (레지스트리 경유)

## 7. 요구사항 → 테스트

| 요구사항 | 테스트 |
|---|---|
| 가용 = 현금 + ETF × (1 − 슬리피지) | 현금 100만·ETF 3주 → 가용 계산값 |
| 현금 충분하면 ETF 안 팖 | 매도 0, 즉시 허가 |
| 부족분만 매도 (107만 단위 올림) | 부족 150만 → 2주 |
| 장전: 전량 체결 확인 후 허가 | 09:00 전 체결 0 → 허가 대기, 체결 후 허가 |
| 체결 후 부족 → 원인 기록 + 추가 시장가 | 체결가 < 예상 → 추가 1주 매도, 원인 "체결가" |
| 전략 칸막이 (부정) | 안 쓴 몫 300만 전략이 500만 요청 → 300만 |
| 안 옮긴 전략 돈은 ETF 로 안 감 (부정) | 안 옮긴 전략 reserve 400만 → sweep idle 에서 제외 |
| 안 옮긴 전략 가용 불변 | ETF 매수 전후 `available_cash(url, "rebound")` 같음 |
| 수동 보유분 안 팖 (부정) | 잔고 10주·원장 3주 → 최대 3주 |
| 1건 상한 분할 | 6주 642만 → 4주 + 2주 |
| 슬롯 다 차면 전량 | 안 쓴 몫 0 → 보유 전량 매도, sweep 0 |
| 무상 우선: ETF 먼저, 유상 취소는 그다음 | ETF 로 충분 → 취소 [] / 부족 → 기존 취소 |
| 결과 불명 재전송 금지 (부정) | 매도 응답 불명 → 재주문 0, ⚠ |
| enabled=false 면 기존 동작 (부정) | ETF 주문 0, 유증 기존 테스트 전부 통과 |
| ETF 손익 → 되돌림 | 매도 체결 → 되돌림 예산 += 실현손익, 유무상 capital 불변 |
| P2 D13 되돌림 몫은 ETF 로 안 감 (부정) | 회원 rights_dip(etf=1) 안 쓴 몫 200만 + rebound(etf=0) 600만, ETF 0 → sweep 매수액 ≤ 200만 |
| P2 되돌림 돈도 안 옮긴 전략에서 보호 | 위 상태 engine 예약 = 800만 − ETF 원가 |
| P2 기존 회원 etf 기본 1 | 옛 members 표(etf 열 없음) ensure 후 rights_dip.etf = 1 |
| P2 add_member etf 갱신·joined 유지 | 이미 있는 rebound 를 etf=False 로 다시 add → joined 그대로, etf 0 |
| P2 되돌림 요청 = 현금 충분하면 매도 0 | 비공개 테스트: request_funds 목 granted=need → size_orders 그대로 |
| P2 되돌림 요청 부족 → 비례 축소 | granted < need → 종목당 비례 축소(기존 규칙) |
| P2 funding_enabled=false 면 기존 경로 (부정) | available_cash·write_reservation 호출, fe 호출 0 |
| P2 옛 rebound 예약 줄 0 | 시작 시 write_reservation(…, 0, …, "rebound") 1회 |
| P2 msig 16:00 안 쓴 몫 게시 | monitor: set_unused(msig, scash)·sync_reservation, 옛 msig 줄 0 |
| P2 msig 분할 매수 회차 자금 요청 | exec cash(): request_funds(msig, strategy_cash − open_cost) granted → limit_buy_cash |
| P2 msig funding_enabled=false 면 기존 경로 (부정) | available_cash·write_reservation 호출, fe 호출 0 |
| P2 msig 정산 뒤 다시 게시 | 정산으로 capital 바뀐 날 publish_unused 2회, 두 번째는 정산 후 capital 기준 |

## 구현 차이
- 원래 엔진이 전략 예산 함수를 직접 받음 → 실제 전략이 배치 끝에 안 쓴 몫을 엔진에 알림(`set_unused`) (공용 코드가 비공개 전략 코드를 import 하지 않게)
- 원래 엔진 참여 전략의 예약 줄 삭제 → 실제 0 으로 덮어씀 (예약 파일에 삭제 함수 없음, 다른 전략의 이중 차감 방지)
- 원래 유상 매수는 체결 확인 "직후" → 실제 ETF 를 판 날만 매수 주문창을 09:05 까지 연장, 매도(익절·철회)는 08:50 그대로 (사용자 2026-10-30)
- 원래 무상은 우선 판단 안에서 처리 → 실제 15:00 우선 판단 직전에 엔진 요청, 우선 판단 로직은 무변경
- 15:00 정리 배치 끝에도 안 쓴 몫 게시 추가 (15:21 마감 매수가 최신 값을 보게)
- 원래 15:21 매수·15:31 확인 별개 → 실제 작업 하나(`funding-sweep`)가 매수 후 15:31 까지 기다려 확인
- 애프터마켓 매수(D8 보완)는 P0-5 실측 전이라 미구현 — 마감 미체결은 다음날로
- 중단 스위치: 엔진 config 대신 유무상 config `funding_enabled` + `funding-sweep` 작업 끄기
- 원래 ETF 손익은 수수료·세금 포함 → 실제 체결가 차이만(수수료 빠짐). 2026-11-11 매도: 엔진 +1,810 vs 증권사 실현(`/orders/realized`) +1,170(수수료 640). 되돌림 예산이 그만큼 많게 잡힘 — 별도 수정 필요(P2 와 분리, 미결)
