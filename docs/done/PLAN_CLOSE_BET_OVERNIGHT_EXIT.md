# PLAN — 종가베팅 청산을 익일 09:01 전량매도로 전환

**한 줄 요약** — 종가베팅 청산에서 장중 TP/SL을 끄고 익일 **09:01 시장가 전량매도**로 바꾼다.
청산 시각과 TP/SL 사용 여부는 `close_bet.json`(웹 `/admin/settings`)에서 바꿀 수 있게 두고,
눌림목(`run_pullback_exit`)은 한 줄도 건드리지 않는다.

근거: `research/private/close_bet_overnight` 백테스트. 같은 표본에서 익일 09:01 청산이
익일 15:19 청산·TP/SL 청산보다 우위였다. **구체 수치는 저장소가 공개라 여기 옮기지 않는다** —
표·표본수·검정통계량은 `research/private/close_bet_overnight/README.md`(gitignore)에 있다.

관련 완료분: Step 1(config 확장), Step 2(진입 필터·정렬)는 이미 반영됨. 이 문서는 Step 3만 다룬다.

---

## 1. 확정 결정 (각 항목 = 그걸 구현하는 파일·함수)

| # | 결정 | 구현 위치 |
|---|---|---|
| D1 | 청산 시각을 config 키 `exit_time`(`"HH:MM:SS"`)로 둔다. 기본 `"09:01:00"` | `etl/scripts/close_bet_config.py` `DEFAULTS`/`_validate`/`load` |
| D2 | 워커 `--force-exit-time` 기본값을 config `exit_time`에서 읽는다. CLI로 주면 override | `etl/scripts/run_close_bet_exit.py` `main()` |
| D3 | `tp`/`sl`이 `None`이면 장중 익절·손절 판정을 건너뛴다 | `run_close_bet_exit.decide_exit()` — 함수 첫 줄 가드 |
| D4 | ps1에서 `--force-exit-time` 인자를 제거한다(= config가 단일 소스). `--stop-time`은 `09:10:00` | `ops/scheduled-tasks/run-close-bet-exit.ps1` |
| D5 | 백스톱 배치 시각을 09:01:30으로 옮긴다 | `ops/scheduled-tasks/close-bet-force-exit.xml` `StartBoundary` |
| D6 | `tp`가 `None`이면 매매노트 `target_price`를 넣지 않는다 | `run_close_bet.record_close_bet_notes()` |
| D7 | 매매노트 `holding_period` 문구를 config `exit_time` 기반으로 만든다 | 같은 함수 |
| D8 | 웹 설정에 "청산 시각" 입력을 추가한다(형식 `HH:MM:SS`) | `broker-web/lib/close-bet-config.ts`, `components/admin/close-bet-panel.tsx` |

### D1 보충 — 왜 config인가
청산 시각을 ps1 인자로 두면 바꿀 때마다 ps1 수정 + `Register-ScheduledTask` 재등록이 필요하다.
config로 두면 값 변경이 파일 한 줄(또는 웹 저장)이고, 워커는 **기동 시점에 읽으므로** 다음 기동부터 반영된다.
단, 백스톱 XML의 `StartBoundary`(D5)는 스케줄러 소관이라 config로 못 뺀다 — **이중 관리 지점이 여기 하나 남는다.**
`exit_time`을 바꾸면 XML도 같이 바꿔야 한다는 주석을 `close_bet_config.py`와 XML 양쪽에 남긴다.

### D3 보충 — 판정 스킵의 정확한 조건
```python
def decide_exit(buy_bid, cntr_price, tp, sl):
    if tp is None or sl is None:   # config 에서 끔
        return None
    ...
```
`tp`만 끄고 `sl`만 켜는 조합은 지원하지 않는다(둘 중 하나라도 None이면 둘 다 끔).
이유: 반쪽 조합은 백테스트에 없는 규칙이고, 지금 필요하지도 않다. 나중에 필요하면 각각 독립 가드로 쪼개면 된다.

---

## 2. 눌림목·기존 매매에 영향 없음 — 근거

"안 닿는다"를 단정하지 않고 실제 경로를 확인한 결과다.

| 축 | 종가베팅 | 눌림목 | 공유 여부 |
|---|---|---|---|
| 청산 스크립트 | `run_close_bet_exit.py` | `run_pullback_exit.py` | **별 파일**. 서로 import 없음 |
| 전략값 파일 | `scripts/close_bet.json` (`close_bet_config.py`) | `scripts/pullback.json` (`pullback_config.py`) | **별 파일·별 로더** |
| 스케줄 작업 | `close-bet-exit.xml` → `run-close-bet-exit.ps1` | `trading-exit.xml` → `run-trading-exit.ps1` | **별 작업**. ps1도 별개 |
| 청산 대상 조회 | `load_unsold_positions` → `close_bet_orders` 테이블만 | 자체 조회 → `pullback_orders` | 테이블 분리 |
| 공용 모듈 | `trading_batch_common`(`in_order_window` 등) | 동일 모듈 | **공유 — 이번 변경에서 손대지 않는다** |

- `run_close_bet_force_exit.py`는 `run_close_bet_exit`에서 `load_unsold_positions`/`execute_sell` 등을 import한다.
  이 함수들의 **시그니처·동작을 바꾸지 않는다**(바꾸는 건 `decide_exit`와 `main()`의 기본값뿐).
  `run_close_bet_force_exit`는 `decide_exit`를 쓰지 않는다 — 잔존분을 시각 판정 없이 매도한다.
- 눌림목 워커의 `--force-exit-time 15:19:00`은 `run-trading-exit.ps1`에 그대로 둔다.

부정 요구("눌림목 청산 시각이 바뀌면 안 된다")는 4장에 **거부 테스트**로 매핑한다.

---

## 3. 변경 목록 (파일 단위)

```
etl/scripts/close_bet_config.py          exit_time 키 + HH:MM:SS 형식 검증 + 이중관리 주석
etl/scripts/close_bet.json               "exit_time": "09:01:00"
etl/scripts/run_close_bet_exit.py        decide_exit None 가드, main() 기본 force-exit-time=config, docstring
etl/scripts/run_close_bet.py             record_close_bet_notes: tp None이면 target_price 생략, holding_period 문구
ops/scheduled-tasks/run-close-bet-exit.ps1     --force-exit-time 제거, --stop-time 09:10:00
ops/scheduled-tasks/close-bet-exit.xml         Description 문구(08:50 기동 유지)
ops/scheduled-tasks/close-bet-force-exit.xml   StartBoundary 09:01:30 + exit_time 연동 주석
broker-web/lib/close-bet-config.ts       exit_time 타입·기본값·검증
broker-web/components/admin/close-bet-panel.tsx  "청산 시각" 입력
etl/tests/test_close_bet_config.py       exit_time 검증 케이스
etl/tests/test_close_bet_exit.py         decide_exit None 가드, config 기본값 배선
etl/tests/test_close_bet.py              노트 payload 케이스
broker-web/__tests__/close-bet-config-api.spec.ts  exit_time 왕복
```

기동 트리거(08:50)는 그대로 둔다 — 09:01 청산에도 09:00 개장 전 기동이 필요하다.

---

## 4. 요구사항 ↔ 테스트 1:1 매핑

| 요구사항 | 테스트 | 파일 |
|---|---|---|
| R1 익일 09:01에 전량 시장가 매도 | config 기본 `exit_time == "09:01:00"`, `main()`이 `--force-exit-time` 미지정 시 config 값을 args에 넣는다 | `test_close_bet_config.py`, `test_close_bet_exit.py` |
| R2 장중 익절·손절 안 함 | `decide_exit(buy_bid, price, None, None) is None` — 이익 +10%/손실 -10% 두 경우 모두 None | `test_close_bet_exit.py` |
| R3 TP/SL을 다시 켜면 예전처럼 동작 | `decide_exit(..., 0.05, 0.03)` 기존 케이스 그대로 통과 | `test_close_bet_exit.py` (기존 케이스 유지) |
| R4 청산 시각을 파일에서 바꾸면 워커가 따라간다 | config `exit_time="10:30:00"` → `main()` args가 `10:30:00` | `test_close_bet_exit.py` |
| R5 잘못된 시각 형식은 배치를 멈춘다 | `"9:1"`, `"25:00:00"`, `123` → `ValueError` | `test_close_bet_config.py` |
| R6 tp가 None이면 노트에 목표가를 안 넣는다 | `record_close_bet_notes` payload에 `target_price` 없음 | `test_close_bet.py` |
| **R7 (부정) 눌림목 청산 시각은 안 바뀐다** | `run_pullback_exit` 인자 기본값이 `15:19:00`, `run-trading-exit.ps1`에 `15:19:00` 문자열 존재 | `test_close_bet_exit.py` 내 별도 케이스(또는 기존 pullback 테스트 유지) |
| **R8 (부정) 백스톱이 이중매도하지 않는다** | 기존 `test_close_bet_force_exit.py` 전량 통과(잔고∩미체결∩DB 3중 대조 불변) | `test_close_bet_force_exit.py` |

---

## 5. 손 안 대는 것 + 그래도 되는 이유

| 안 건드림 | 요구사항을 여전히 커버하나 |
|---|---|
| `trading_batch_common.in_order_window` | 09:00~15:20 창 판정. 09:01은 창 안이라 `force` 성립. 수정 불필요 |
| `run_close_bet_force_exit.py` 본문 | 시각 판정이 없다(잔존분 매도만). XML 시각만 옮기면 됨 |
| `close-bet-exit.xml` 트리거 08:50 | 09:01 청산에도 개장 전 기동이 필요. 그대로 |
| `run_close_bet.py` 주문 시간창 15:19 | 매수는 그대로 15:19. 이번 변경은 청산만 |
| 눌림목 전 경로 | 2장 표대로 파일·테이블·작업이 전부 분리 |

---

## 6. 롤백

```
close_bet.json 에서  "tp": 0.05, "sl": 0.03, "exit_time": "15:19:00"
close-bet-force-exit.xml StartBoundary 15:19:30 로 되돌리고 재등록
run-close-bet-exit.ps1 --stop-time 15:25:00
```
코드 변경 없이 config만으로 옛 동작(TP/SL + 15:19 청산)에 근접 복귀한다. ps1/XML 두 줄만 수동.

---

## 7. 리스크 / 미결

- **09:01 시장가 슬리피지.** 백테스트는 09:01 봉 시가로 근사했다. 소형주 청산봉 거래대금 중앙 1.5~2억,
  09:01에 봉이 없어 밀린 사례가 소형 51건 중 10건. 실제 체결가는 더 나쁠 수 있다.
- **개장 직후 변동성.** 09:00~09:01 사이 급변은 전량 감수한다(TP/SL 껐으므로).
- **이중 관리 1곳.** `exit_time`(config)과 백스톱 XML `StartBoundary`는 자동 연동되지 않는다.
- **미검증.** 09:05 / 09:10 / 09:30 등 다른 청산 시각은 아직 안 쟀다. config로 뺐으니 나중에 값만 바꿔 비교 가능.

---

## 8. 구현 후 재검토 체크리스트 (2026-09-07 완료)

- [x] 1장 D1~D8 각 항목을 실제 코드와 대조 — 8/8 반영
- [x] 4장 R1~R8 테스트가 전부 존재하고 통과
- [x] `etl` 전체 테스트 통과 — 686 OK (직전 672, +14)
- [x] 눌림목 관련 파일 무변경 — `git diff --stat`에 `run_pullback_*`, `pullback.json`,
      `run-trading-exit.ps1`, `trading-exit.xml` 없음
- [x] 드라이런 기동 로그 `force=09:01:00 tp=None sl=None stop=09:10:00` 확인

### 남은 수동 작업 (사용자)

XML 두 개는 **재등록해야 스케줄러에 반영된다**. ps1 변경은 재등록 불필요.

```powershell
cd $HOME\.openclaw\workspace\etl\new-etf_insight\ops\scheduled-tasks
Register-ScheduledTask -Xml (Get-Content -Path close-bet-force-exit.xml -Raw -Encoding UTF8) -TaskName "close-bet-force-exit" -TaskPath "\OpenClaw\" -Force
Register-ScheduledTask -Xml (Get-Content -Path close-bet-exit.xml -Raw -Encoding UTF8) -TaskName "close-bet-exit" -TaskPath "\OpenClaw\" -Force
```

### 구현 중 발견해 같이 고친 것

`tp`를 null로 두면 터지던 두 곳(PLAN 작성 시점엔 없던 항목):
- `run_close_bet.record_close_bet_notes` — `int(cntr_price * (1 + cfg["tp"]))`에서 None 곱셈
- `run_close_bet_exit.decide_exit` — `chg >= None` 비교
